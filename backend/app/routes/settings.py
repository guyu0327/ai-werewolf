from __future__ import annotations

import asyncio
import random
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..llm.client import LlmError
from ..models import AiPersona, GeneratedPersona, ModelPreset, Settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(request: Request):
    return request.app.state.store.load_settings()


@router.put("")
def put_settings(request: Request, settings: Settings):
    seen: set[str] = set()
    for p in settings.presets:
        if p.id in seen:
            raise HTTPException(400, "模型预设 id 重复")
        seen.add(p.id)
        if not p.name.strip() or not p.base_url.strip() or not p.model.strip():
            raise HTTPException(400, "模型预设的名称、Base URL、模型名不能为空")
    seen = set()
    for a in settings.personas:
        if a.id in seen:
            raise HTTPException(400, "AI 玩家 id 重复")
        seen.add(a.id)
        if not a.name.strip():
            raise HTTPException(400, "AI 玩家名字不能为空")
    request.app.state.store.save_settings(settings)
    return settings


class TestPresetRequest(BaseModel):
    base_url: str
    api_key: str = ""
    model: str


@router.post("/test-preset")
async def test_preset(request: Request, body: TestPresetRequest):
    preset = ModelPreset(id="tmp", name="tmp", base_url=body.base_url, api_key=body.api_key, model=body.model)
    try:
        reply = await request.app.state.llm.ping(preset)
        return {"ok": True, "message": reply or "连接成功"}
    except Exception as e:
        raise HTTPException(400, f"连接失败：{e}")


class GeneratePersonaRequest(BaseModel):
    preset_id: str
    hint: str = ""


PERSONA_STYLE_SEEDS = (
    "老谋深算、滴水不漏的老江湖",
    "直来直去、火力全开的暴脾气",
    "嘻嘻哈哈、话里藏刀的喜剧人",
    "冷静寡言、一开口就见血的逻辑流",
    "谨慎多疑、总在盘算别人动机的怀疑论者",
    "热情外向、喜欢带节奏的社交悍将",
    "文静内向、关键时刻一针见血的观察者",
    "飘忽洒脱、让人捉摸不透的散人",
)


def _persona_messages(existing: str, requirement: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你为狼人杀桌游设计 AI 玩家角色。"
                "名字要像真实的中国玩家：可以是常见中文人名（如「王大力」「陈雨桐」），也可以是有味道的中文网名"
                "（如「夜风微凉」「一梦三年」「老王不秃」）；禁止用物品名、动物名、食物名（如「铁锅」「二哈」「汤圆」）。"
                "人设要立体、有记忆点，像给一个真实老玩家写的角色卡，避免空泛的形容词堆砌。"
                "无论性格多跳，这个角色都必须是懂狼人杀的老玩家：性格只体现在说话腔调、用词习惯和节目效果上，"
                "绝不体现在游戏素养上——怀疑要有依据、会盘票型、发言有结论，这些基本功全员在线；"
                "禁止把「乱怀疑人、说话不过脑子、不看票型就踩人、情绪化乱指认」这类会被当真执行的降智缺陷写进人设。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请设计一个狼人杀 AI 玩家角色。\n"
                f"设定要求：{requirement}\n"
                f"已有玩家（名字不要重复）：{existing}\n"
                "人设要求：200~500 字，用「你」开头直接对这个玩家说话，依次包含："
                "性格底色、说话风格、狼人杀打法（如何伪装或如何找狼、投票习惯）、一个记忆点（口头禅或怪癖）。\n"
                '输出 JSON：{"name": "玩家名字", "persona": "人设文本"}'
            ),
        },
    ]


@router.post("/generate-persona")
async def generate_persona(request: Request, body: GeneratePersonaRequest):
    """用选定模型按一句话设定生成 AI 玩家的名字与人设；无设定时随机生成。"""
    settings: Settings = request.app.state.store.load_settings()
    preset = next((p for p in settings.presets if p.id == body.preset_id), None)
    if preset is None:
        raise HTTPException(400, "请先添加并选择可用的模型预设")
    hint = body.hint.strip()
    requirement = (
        f"用户给了一句话设定：「{hint}」。请围绕这句话设计角色，可适当补充细节让它更立体。"
        if hint
        else "用户没有给任何设定，请自由随机发挥，创造一个有创意、有辨识度的角色。"
    )
    existing = "、".join(a.name for a in settings.personas if a.name.strip()) or "（还没有其他玩家）"
    try:
        result = await request.app.state.llm.decide(
            preset, _persona_messages(existing, requirement), GeneratedPersona, reasoning_effort="low"
        )
    except LlmError as e:
        raise HTTPException(400, f"生成失败：{e}")
    return {"name": result.name.strip(), "persona": result.persona.strip()}


class GeneratePersonasRequest(BaseModel):
    preset_id: str
    count: int = Field(ge=1, le=5)


# 快速添加是纯创作任务：不吃预设的决策思考力度（可能很高），固定 low，别让写人设等深思考
PERSONA_EFFORT = "low"


@router.post("/generate-personas")
async def generate_personas(request: Request, body: GeneratePersonasRequest):
    """快速添加：启动后台任务并行生成 count 个互不重名的 AI 玩家，立即返回。

    生成进度经 /generate-personas/status 轮询；每个角色完成即落盘并进入 added 增量列表，
    前端逐个上屏。单活跃任务，运行中拒绝重复启动。
    """
    app = request.app
    old = getattr(app.state, "persona_job", None)
    if old and not old["task"].done():
        raise HTTPException(400, "已有生成任务进行中，请等它完成")
    settings: Settings = app.state.store.load_settings()
    preset = next((p for p in settings.presets if p.id == body.preset_id), None)
    if preset is None:
        raise HTTPException(400, "请先添加并选择可用的模型预设")
    seeds = random.sample(PERSONA_STYLE_SEEDS, k=body.count)
    requirements = [f"请自由随机发挥，创造一个有创意、有辨识度的角色。风格方向：{seed}。" for seed in seeds]
    taken: set[str] = {a.name.strip() for a in settings.personas if a.name.strip()}
    state: dict = {"task": None, "total": body.count, "added": [], "error": ""}
    app.state.persona_job = state
    state["task"] = asyncio.create_task(_run_generate(app, preset, requirements, taken))
    return {"started": True, "total": body.count}


async def _run_generate(app, preset: ModelPreset, requirements: list[str], taken: set[str]) -> None:
    """后台生成任务：并行发车、逐个落盘；撞名的槽位带完整排除名单串行补生成一轮。

    每次落盘前重读 settings：生成窗口可能长达一两分钟，用户同期改的其他设置不能被启动时的快照覆盖。
    """
    state = app.state.persona_job
    llm = app.state.llm

    async def one(requirement: str, exclude: str) -> GeneratedPersona:
        return await llm.decide(
            preset, _persona_messages(exclude, requirement), GeneratedPersona, reasoning_effort=PERSONA_EFFORT
        )

    async def accept(requirement: str, name: str, persona: str) -> None:
        if not name or name in taken:
            exclude = "、".join(sorted(taken)) or "（还没有其他玩家）"
            try:
                fixed = await one(requirement + " 注意：名字必须避开上面列出的所有人。", exclude)
                name = fixed.name.strip() or name
                persona = fixed.persona.strip() or persona
            except LlmError:
                pass  # 补生成失败就保留原名，用户可在编辑里手改
        if not name or not persona.strip():
            return
        p = AiPersona(id=f"a-{uuid.uuid4().hex[:10]}", name=name, persona=persona.strip(), preset_id=preset.id)
        settings = app.state.store.load_settings()
        settings.personas.append(p)
        app.state.store.save_settings(settings)
        taken.add(name)
        state["added"].append(p.model_dump())

    async def slot(i: int) -> tuple[int, GeneratedPersona]:
        exclude = "、".join(sorted(taken)) or "（还没有其他玩家）"
        return i, await one(requirements[i], exclude)

    try:
        for fut in asyncio.as_completed([slot(i) for i in range(len(requirements))]):
            try:
                i, r = await fut
                await accept(requirements[i], r.name.strip(), r.persona)
            except LlmError:
                continue
    except Exception as e:  # 防任务静默死亡
        state["error"] = f"生成失败：{e}"
        return
    if not state["added"]:
        state["error"] = "生成结果为空，请重试"


@router.get("/generate-personas/status")
def generate_personas_status(request: Request):
    job = getattr(request.app.state, "persona_job", None)
    if not job:
        return {"status": "idle", "total": 0, "added": [], "error": ""}
    status = "error" if job["error"] else ("running" if not job["task"].done() else "done")
    return {"status": status, "total": job["total"], "added": job["added"], "error": job["error"]}
