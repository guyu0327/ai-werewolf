from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..engine.board import all_boards, build_custom_board, find_board
from ..engine.game import flatten_event, public_state, strip_vote_reason, view_events, visible_to
from ..models import AiPersona, BoardConfig, Game, Phase, ROLE_LABELS, Role, Settings, WinCondition

router = APIRouter(prefix="/api/games", tags=["games"])


def ctx(request: Request):
    return request.app.state


class InlineBoard(BaseModel):
    name: str = ""
    roles: list[Role]
    win_condition: WinCondition = WinCondition.CITY
    max_days: int = 20


class CreateGameRequest(BaseModel):
    board_id: str | None = None
    board: InlineBoard | None = None
    persona_ids: list[str] = Field(default_factory=list)
    human_name: str = "你"
    human_avatar: str = "🙂"
    mode: str = "normal"  # normal: 1真人+AI；god: 上帝观战（全AI）
    human_role: str | None = None  # 真人自选角色，None=随机
    sheriff: bool = False  # 是否开启警长竞选
    badge_swallow: str = "none"  # 吞警徽规则：none/single/double（需开启警长竞选）


class ActionRequest(BaseModel):
    kind: str
    text: str | None = None
    target: int | None = None
    use: bool | None = None
    shoot: bool | None = None
    run: bool | None = None
    destroy: bool | None = None
    direction: str | None = None


class SummaryRequest(BaseModel):
    preset_id: str


class LlmResumeRequest(BaseModel):
    action: str  # retry: 重跑失败的 LLM 调用；abort: 按故障终止对局


@router.get("/boards")
def list_boards(request: Request):
    state = ctx(request)
    settings: Settings = state.store.load_settings()
    return [b.model_dump() for b in all_boards(settings)]


@router.post("")
def create_game(request: Request, body: CreateGameRequest):
    state = ctx(request)
    settings: Settings = state.store.load_settings()

    board: BoardConfig | None = None
    if body.board_id:
        board = find_board(settings, body.board_id)
        if board is None:
            raise HTTPException(400, "板子不存在")
    elif body.board is not None:
        try:
            board = build_custom_board(
                f"custom-{uuid.uuid4().hex[:8]}", body.board.name, body.board.roles, body.board.win_condition, body.board.max_days
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
    else:
        raise HTTPException(400, "请指定板子")

    if body.mode not in ("normal", "god"):
        raise HTTPException(400, "mode 必须是 normal 或 god")
    if body.badge_swallow not in ("none", "single", "double"):
        raise HTTPException(400, "吞警徽规则必须是 none、single 或 double")
    if body.badge_swallow != "none" and not body.sheriff:
        raise HTTPException(400, "吞警徽规则需要先开启警长竞选")

    if body.human_role:
        if body.mode == "god":
            raise HTTPException(400, "观战模式没有真人玩家，无法指定角色")
        try:
            preferred = Role(body.human_role)
        except ValueError:
            raise HTTPException(400, f"未知角色：{body.human_role}")
        if preferred not in board.roles:
            raise HTTPException(400, f"该板子没有 {ROLE_LABELS.get(preferred, body.human_role)} 这个角色")

    personas: list[AiPersona] = []
    for pid in body.persona_ids:
        found = next((p for p in settings.personas if p.id == pid), None)
        if found is None:
            raise HTTPException(400, f"AI 玩家不存在：{pid}")
        personas.append(found)

    try:
        game = state.runner.create_game(
            board, personas, body.human_name, body.human_avatar, body.mode, body.human_role, body.sheriff, body.badge_swallow
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return public_state(game)


@router.get("")
def list_games(request: Request):
    return ctx(request).store.list_game_meta()


@router.get("/{gid}")
def get_game(request: Request, gid: str):
    state = ctx(request)
    game = state.runner.get_game(gid)
    if game is None:
        raise HTTPException(404, "游戏不存在")
    reveal = game.phase == Phase.GAME_OVER
    return {
        "state": public_state(game, reveal=reveal),
        "active": state.runner.is_active(gid),
        "pending": state.runner.pending_info(gid),
        "paused": state.runner.paused_info(gid),
    }


@router.post("/{gid}/start")
async def start_game_route(request: Request, gid: str):
    ok, msg = await ctx(request).runner.start(gid)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/{gid}/resume")
async def resume_game(request: Request, gid: str):
    ok, msg = await ctx(request).runner.resume(gid)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/{gid}/abort")
async def abort_game(request: Request, gid: str):
    ok, msg = await ctx(request).runner.abort(gid)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/{gid}/llm-resume")
async def llm_resume(request: Request, gid: str, body: LlmResumeRequest):
    """LLM 失败暂停弹窗的用户选择：retry 重跑失败调用，abort 按故障终止对局。"""
    ok, msg = await ctx(request).runner.resume_llm(gid, body.action)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/{gid}/explode")
def explode_game(request: Request, gid: str):
    """真人狼自爆：白天发言阶段任意时刻。"""
    ok, msg = ctx(request).runner.request_explode(gid)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/{gid}/action")
def submit_action(request: Request, gid: str, body: ActionRequest):
    state = ctx(request)
    payload = {
        "kind": body.kind,
        "text": body.text,
        "target": body.target,
        "use": body.use,
        "shoot": body.shoot,
        "run": body.run,
        "destroy": body.destroy,
        "direction": body.direction,
    }
    ok, msg = state.runner.submit(gid, payload)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.delete("/{gid}")
def delete_game(request: Request, gid: str):
    state = ctx(request)
    if state.runner.is_active(gid):
        raise HTTPException(400, "游戏进行中，无法删除")
    if state.runner.summary_status(gid) == "running":
        raise HTTPException(400, "复盘分析后台生成中，请稍后再删除")
    if not state.store.delete_game(gid):
        raise HTTPException(404, "游戏不存在")
    return {"ok": True}


@router.get("/{gid}/review")
def review(request: Request, gid: str):
    state = ctx(request)
    game: Game | None = state.store.get_game(gid)
    if game is None:
        raise HTTPException(404, "游戏不存在")
    return {
        **review_payload(game),
        "summary_status": state.runner.summary_status(gid),
        "summary_error": state.runner.summary_error.get(gid, ""),
    }


@router.post("/{gid}/summary")
async def make_summary(request: Request, gid: str, body: SummaryRequest):
    """启动后台复盘总结任务（立即返回，轮询 summary/status 获取进度）。

    必须是 async def：start_summary 里 asyncio.create_task 需要事件循环，
    同步路由跑在线程池里没有 loop，会直接 500。
    """
    ok, msg = ctx(request).runner.start_summary(gid, body.preset_id)
    if not ok:
        raise HTTPException(400, msg)
    return {"started": True}


@router.get("/{gid}/summary/status")
def summary_status(request: Request, gid: str):
    state = ctx(request)
    game: Game | None = state.store.get_game(gid)
    if game is None:
        raise HTTPException(404, "游戏不存在")
    return {
        "status": state.runner.summary_status(gid),
        "error": state.runner.summary_error.get(gid, ""),
        "summary": game.summary,
        "summary_model": game.summary_model,
    }


def review_payload(game: Game) -> dict:
    """复盘数据。结束后/观战局全量公开；进行中的普通局按真人视角过滤——
    复盘接口若对进行中对局吐全量事件（狼队频道/守卫守护/他人身份），等于可越权看穿全场。"""
    data = game.model_dump(exclude={"events"})
    if game.mode == "god" or game.phase == Phase.GAME_OVER:
        return {**data, "events": [flatten_event(e) for e in game.events]}
    seat = game.human_seat()
    viewer_is_wolf = seat is not None and game.player(seat).role == Role.WEREWOLF
    for pd in data["players"]:
        p = game.player(pd["seat"])
        if not (pd["is_human"] or p.role_revealed or (viewer_is_wolf and p.role == Role.WEREWOLF)):
            pd["role"] = None
    data.pop("phase_ctx", None)  # 进行中的 phase_ctx 含刀口/守护等私密中间态
    events = (
        [strip_vote_reason(flatten_event(e), e.type) for e in view_events(game, seat)] if seat is not None else []
    )
    return {**data, "events": events}
