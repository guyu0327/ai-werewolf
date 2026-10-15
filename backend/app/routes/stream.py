"""游戏 SSE 流：连接即推全量快照，之后增量事件；按真人玩家视角过滤私密事件。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..engine.game import flatten_event, public_state, strip_vote_reason, visible_to
from ..events import sse_format
from ..models import Game, GameEvent, Phase

router = APIRouter(prefix="/api/games", tags=["stream"])

# 私密行动的决策 kind：其思考信号（谁在思考）会暴露行动者身份，对玩家视角全程屏蔽。
# self_explode 仅狼有（白天发言回合先决策再发言），不屏蔽会让狼的「连续两次思考」在玩家眼里自曝身份
_PRIVATE_THINKING_KINDS = {"wolf_vote", "guard", "seer_check", "witch_save", "witch_poison", "hunter", "self_explode"}


def _passes_view(current: Game, event: str, data: dict, full_view: bool, human_seat: int | None, vis_cache: dict) -> bool:
    """单条实时事件是否对当前订阅者可见。

    持久化事件（带 seq）默认拒绝、一律按 visible_to 判定——类型白名单会漏登记新私密类型
    （如 guard_protect 曾漏过，狼人能看到守卫保谁）；纯瞬态事件均为公开信息；
    thinking_delta 内容与固化的 thinking 事件仅 god 可见；thinking_start/thinking_end
    无内容的占位/收口信号放行公开 kind（发言/白天投票等），私密 kind 屏蔽。
    """
    if full_view:
        return True
    if event in ("thinking_start", "thinking_end"):
        return data.get("kind") not in _PRIVATE_THINKING_KINDS
    if event == "thinking_delta":
        return False
    seq = data.get("seq")
    if seq is None:
        return True
    ev = vis_cache.get(seq) or _find_event(current, seq)
    if ev is None:
        return False  # 找不到持久化事件按不可见处理：宁可不推，不可泄露
    vis_cache[ev.seq] = ev
    return visible_to(current, ev, human_seat)


@router.get("/{gid}/stream")
async def stream(request: Request, gid: str, view: str = "auto"):
    state = request.app.state
    game: Game | None = state.runner.get_game(gid)
    if game is None:
        return StreamingResponse(iter([sse_format("error", {"message": "游戏不存在"})]), media_type="text/event-stream")

    human_seat = game.human_seat()
    # 观战局（无真人）天然上帝视角，全量推送
    full_view = view == "full" or human_seat is None

    async def gen():
        q = state.hub.subscribe(gid)
        vis_cache: dict[int, GameEvent] = {}
        try:
            current = state.runner.get_game(gid)
            if current is None:
                yield sse_format("error", {"message": "游戏已删除"})
                return
            for e in current.events:
                vis_cache[e.seq] = e
            reveal = full_view or current.phase == Phase.GAME_OVER
            visible_events = [
                strip_vote_reason(flatten_event(e), e.type)
                for e in current.events
                if full_view or visible_to(current, e, human_seat)
            ]
            yield sse_format(
                "snapshot",
                {
                    "state": public_state(current, reveal=reveal),
                    "events": visible_events,
                    "pending": state.runner.pending_info(gid),
                    "paused": state.runner.paused_info(gid),
                    "active": state.runner.is_active(gid),
                },
            )
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if not _passes_view(current, event, data, full_view, human_seat, vis_cache):
                    continue
                if not full_view:
                    data = strip_vote_reason(data, event)  # 投票理由仅上帝视角可见
                yield sse_format(event, data)
        finally:
            state.hub.unsubscribe(gid, q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _find_event(game: Game, seq: int) -> GameEvent | None:
    for e in game.events:
        if e.seq == seq:
            return e
    return None
