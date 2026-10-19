"""mock LLM 整局流程测试：6 人局从开局打到结束，覆盖真人输入、断点恢复。"""
from __future__ import annotations

import asyncio
import re
import time

import pytest

from app.engine.store import Store
from app.events import GameHub
from app.llm.client import LlmClient, LlmError, LlmFatalError
from app.models import (
    AiPersona,
    BadgeDecision,
    BoardConfig,
    Game,
    GuardProtect,
    ModelPreset,
    Phase,
    Player,
    Role,
    SeerCheck,
    SelfExplodeDecision,
    SheriffRun,
    SheriffVote,
    SpeechOrderChoice,
    VoteDecision,
    WinCondition,
    WitchPoison,
    WitchSave,
    HunterDecision,
    WolfProposal,
)
from app.runner import GameRunner


class FakeLlm(LlmClient):
    """不联网的假 LLM：发言固定文本，决策取目标清单里第一个座位号（决策目标必须合法，暂停机制不再兜底）。"""

    async def speak(self, preset, messages, on_thinking=None):
        text = messages[-1]["content"]
        if on_thinking is not None:
            on_thinking("（测试思考）我先想想怎么说")
        yield "我觉得3号发言有点飘。"
        if "遗言" in text:
            yield "大家帮我报仇。"

    # schema → 提示词里的目标清单段落标题（决策目标从清单取号，保证合法）
    _SECTION = {
        VoteDecision: "可投票对象",
        SheriffVote: "候选人",
        SeerCheck: "可查验对象",
        WitchPoison: "可毒对象",
        HunterDecision: "可开枪对象",
        GuardProtect: "可守护对象",
        BadgeDecision: "可移交对象",
    }

    @staticmethod
    def _section_seats(text: str, header: str) -> list[int]:
        m = re.search(rf"【{header}】([^【]*)", text)
        return [int(s) for s in re.findall(r"(\d+)号", m.group(1))] if m else []

    async def decide(self, preset, messages, schema, on_thinking=None):
        text = messages[-1]["content"]
        if on_thinking is not None:
            on_thinking("（测试思考）我先分析一下")
        seats = [int(m) for m in re.findall(r"(\d+)号", text)]
        target = seats[0] if seats else 1
        section = self._SECTION.get(schema)
        if section:
            cands = self._section_seats(text, section)
            if cands:
                target = cands[0]
        if schema is WitchSave:
            return WitchSave(use=False)
        if schema is WitchPoison:
            return WitchPoison(use=False, target=0)
        if schema is HunterDecision:
            return HunterDecision(shoot=False, target=0)
        if schema is SeerCheck:
            return SeerCheck(target=target)
        if schema is GuardProtect:
            return GuardProtect(target=target)
        if schema is WolfProposal:
            return WolfProposal(target=target, reason="测试刀口")
        if schema is VoteDecision:
            return VoteDecision(target=target, reason="测试投票")
        if schema is SheriffRun:
            sys_text = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
            m = re.search(r"你的座位是 (\d+)号", sys_text)
            return SheriffRun(run=bool(m and int(m.group(1)) % 2 == 1))
        if schema is SelfExplodeDecision:
            return SelfExplodeDecision(explode=False)
        if schema is SheriffVote:
            return SheriffVote(target=target)
        if schema is BadgeDecision:
            return BadgeDecision(destroy=False, target=target)
        if schema is SpeechOrderChoice:
            return SpeechOrderChoice(direction="left")
        raise AssertionError(f"未支持的 schema {schema}")


class ExileVoteLlm(FakeLlm):
    """AI 统一投票放逐指定座位、狼刀避开该座位，用于把真人猎人推上放逐台。

    指定座位出局后自动改用提示词里的首个存活目标（暂停机制不再兜底非法目标）。
    """

    def __init__(self, exile_seat: int, knife_seat: int):
        super().__init__()
        self.exile_seat = exile_seat
        self.knife_seat = knife_seat

    @staticmethod
    def _alive_seats(text: str) -> list[int]:
        m = re.search(r"存活玩家：([^【\n。]*)", text)
        return [int(s) for s in re.findall(r"(\d+)号", m.group(1))] if m else []

    async def decide(self, preset, messages, schema, on_thinking=None):
        text = messages[-1]["content"]
        if schema is WolfProposal:
            alive = self._alive_seats(text)
            target = self.knife_seat if self.knife_seat in alive else alive[0]
            return WolfProposal(target=target, reason="测试刀口")
        if schema is VoteDecision:
            m = re.search(r"【可投票对象】([^【]*)", text)
            cands = [int(s) for s in re.findall(r"(\d+)号", m.group(1))] if m else []
            target = self.exile_seat if self.exile_seat in cands else cands[0]
            return VoteDecision(target=target, reason="测试投票")
        return await super().decide(preset, messages, schema, on_thinking)


class AbstainVoteLlm(FakeLlm):
    """AI 一律弃票（放逐 + 警长竞选），用于验证弃票链路与全弃票兜底。"""

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is VoteDecision:
            return VoteDecision(target=0, reason="先不投")
        if schema is SheriffVote:
            return SheriffVote(target=0)
        return await super().decide(preset, messages, schema, on_thinking)


def make_runner(tmp_path, llm=None) -> GameRunner:
    store = Store(tmp_path)
    store.save_settings(
        _settings_with_preset()
    )
    return GameRunner(store, GameHub(), llm or FakeLlm())


def _settings_with_preset():
    from app.models import Settings

    s = Settings()
    s.presets = [ModelPreset(id="p1", name="测试模型", base_url="http://localhost:9", api_key="k", model="fake")]
    s.personas = []
    return s


def make_personas(n: int) -> list[AiPersona]:
    return [AiPersona(id=f"a{i}", name=f"AI{i}", avatar="🐺", persona="冷静的逻辑流", preset_id="p1") for i in range(n)]


async def auto_play(runner: GameRunner, gid: str, deadline: float = 60, abstain_vote: bool = False):
    """模拟真人： whenever 有 pending 输入就立刻提交一个合法动作。"""
    waited = 0.0
    while waited < deadline:
        await asyncio.sleep(0.01)
        waited += 0.01
        info = runner.pending_info(gid)
        if info:
            kind, allowed = info["kind"], info["allowed"]
            payload: dict = {"kind": kind}
            if kind in ("speech", "last_words"):
                payload["text"] = "大家好，我是好人，请投3号。"
            elif kind in ("vote", "sheriff_vote") and abstain_vote:
                payload["target"] = 0
            elif kind == "witch_save":
                payload["use"] = False
            elif kind == "witch_poison":
                payload["use"] = False
            elif kind == "hunter":
                payload["shoot"] = False
            elif kind == "sheriff_run":
                payload["run"] = True
            elif kind == "speech_order":
                payload["direction"] = "left"
            elif kind == "badge_pass":
                payload["destroy"] = False
                if allowed:
                    payload["target"] = allowed[0]
            elif allowed:
                payload["target"] = allowed[0]
            ok, msg = runner.submit(gid, payload)
            assert ok, f"自动提交失败 kind={kind}: {msg}"
        game = runner.get_game(gid)
        if game is not None and game.phase == Phase.GAME_OVER:
            return game
    raise TimeoutError("游戏未在限时内结束")


@pytest.mark.asyncio
async def test_six_player_full_game(tmp_path):
    runner = make_runner(tmp_path)
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂")
    gid = game.id
    ok, msg = await runner.start(gid)
    assert ok, msg
    finished = await auto_play(runner, gid)

    assert finished.phase == Phase.GAME_OVER
    assert finished.winner in ("wolf", "good", "draw")
    assert all(p.role is not None for p in finished.players)
    assert all(p.role_revealed for p in finished.players)
    types = [e.type for e in finished.events]
    assert "speech" in types and "vote_result" in types and "night_settle" in types
    assert "game_over" in types
    # 真人活到白天一定发过言（首夜被刀出局则无发言，属正常流程）
    human = finished.human()
    spoke = any(e.type == "speech" and e.actor == human.seat for e in finished.events)
    died_night1 = any(
        e.type == "night_settle" and e.day == 1 and human.seat in (e.payload.get("deaths") or [])
        for e in finished.events
    )
    assert spoke or died_night1


@pytest.mark.asyncio
async def test_preferred_role_full_game(tmp_path):
    """玩家自选角色：整局走完，真人始终是自己选的身份，存档往返不丢。"""
    runner = make_runner(tmp_path)
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂", human_role="seer")
    gid = game.id
    ok, msg = await runner.start(gid)
    assert ok, msg
    # start 操作的是 store 重新加载的实例，须用 runner.get_game 断言
    assert runner.get_game(gid).human().role == Role.SEER

    # 从磁盘读回 preferred_role 仍在（存档往返）
    from_disk = runner.store.get_game(gid)
    assert from_disk.preferred_role == Role.SEER

    finished = await auto_play(runner, gid)
    assert finished.phase == Phase.GAME_OVER
    assert finished.human().role == Role.SEER


@pytest.mark.asyncio
async def test_last_words_only_first_night_and_exile(tmp_path):
    """经典遗言规则：只有第一晚夜死与被放逐者有遗言；枪杀、第二晚起的夜死无遗言。"""
    runner = make_runner(tmp_path)
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂")
    gid = game.id
    ok, msg = await runner.start(gid)
    assert ok, msg
    finished = await auto_play(runner, gid)
    assert finished.phase == Phase.GAME_OVER

    night1_dead: set[int] = set()
    exiled_seats: set[int] = set()
    shot_targets: set[int] = set()
    for e in finished.events:
        if e.type == "night_settle" and e.day == 1:
            night1_dead.update(e.payload.get("deaths", []))
        elif e.type == "vote_result" and e.payload.get("exiled") is not None:
            exiled_seats.add(e.payload["exiled"])
        elif e.type == "hunter_shot":
            shot_targets.add(e.payload["target"])

    for e in finished.events:
        if e.type != "last_words":
            continue
        assert e.actor in night1_dead or e.actor in exiled_seats, f"座位 {e.actor} 不该有遗言"
        assert e.actor not in shot_targets, f"被枪带走的座位 {e.actor} 不该有遗言"


@pytest.mark.asyncio
async def test_exiled_hunter_can_shoot(tmp_path):
    """经典规则：猎人被投票放逐后可开枪带走一人（被毒杀仍不能开枪）。"""
    from app.engine.board import BUILTIN_BOARDS

    board9 = BUILTIN_BOARDS[1]  # 9 人板含猎人
    runner = make_runner(tmp_path)
    game = runner.create_game(board9, make_personas(8), human_name="玩家", human_avatar="🙂", human_role="hunter")
    gid = game.id
    my_seat = game.human_seat()
    # 狼刀避开真人，AI 全票放逐真人猎人
    runner.llm = ExileVoteLlm(exile_seat=my_seat, knife_seat=my_seat % 9 + 1)
    ok, msg = await runner.start(gid)
    assert ok, msg
    assert runner.get_game(gid).human().role == Role.HUNTER

    shot_submitted: list[int] = []

    async def auto_shoot(deadline: float = 60):
        waited = 0.0
        while waited < deadline:
            await asyncio.sleep(0.01)
            waited += 0.01
            info = runner.pending_info(gid)
            if info:
                kind, allowed = info["kind"], info["allowed"]
                payload: dict = {"kind": kind}
                if kind in ("speech", "last_words"):
                    payload["text"] = "我是猎人，大家帮我报仇。"
                elif kind == "witch_save":
                    payload["use"] = False
                elif kind == "witch_poison":
                    payload["use"] = False
                elif kind == "hunter":
                    payload["shoot"] = True
                    payload["target"] = allowed[0]
                    shot_submitted.append(allowed[0])
                elif allowed:
                    payload["target"] = allowed[0]
                ok2, m = runner.submit(gid, payload)
                assert ok2, f"自动提交失败 kind={kind}: {m}"
            cur = runner.get_game(gid)
            if cur is not None and cur.phase == Phase.GAME_OVER:
                return cur
        raise TimeoutError("游戏未在限时内结束")

    finished = await auto_shoot()
    assert finished.phase == Phase.GAME_OVER
    shots = [e for e in finished.events if e.type == "hunter_shot"]
    assert shots and shots[0].actor == my_seat, "被放逐的猎人应有开枪机会"
    target = shots[0].payload["target"]
    assert shot_submitted == [target]
    assert not finished.player(target).alive


@pytest.mark.asyncio
async def test_twelve_board_preferred_idiot(tmp_path):
    """12 人经典板：真人自选白痴，发牌多重集不变，整局可正常打完。"""
    from app.engine.board import BUILTIN_BOARDS

    board = next(b for b in BUILTIN_BOARDS if b.id == "twelve")
    runner = make_runner(tmp_path)
    game = runner.create_game(board, make_personas(11), human_name="玩家", human_avatar="🙂", human_role="idiot")
    gid = game.id
    ok, msg = await runner.start(gid)
    assert ok, msg
    started = runner.get_game(gid)
    assert started.human().role == Role.IDIOT
    assert sorted(p.role.value for p in started.players) == sorted(r.value for r in board.roles)

    finished = await auto_play(runner, gid)
    assert finished.phase == Phase.GAME_OVER


@pytest.mark.asyncio
async def test_human_wolf_vote_emits_proposal(tmp_path):
    """真人狼的刀口投票要落成 wolf_proposal 事件，AI 队友/上帝视角/复盘才可见。"""
    runner = make_runner(tmp_path)
    game = runner.create_game(_board6(), make_personas(5), human_name="狼玩家", human_role="werewolf")
    gid = game.id
    await runner.start(gid)

    waited = 0.0
    while waited < 10:
        await asyncio.sleep(0.01)
        waited += 0.01
        info = runner.pending_info(gid)
        if info and info["kind"] == "wolf_vote":
            break
    else:
        raise TimeoutError("未等到狼人夜聊输入")

    g = runner.get_game(gid)
    ok, msg = runner.submit(gid, {"kind": "wolf_vote", "target": g.human_seat()})
    assert ok, msg

    # submit 返回后 runner 协程需再调度一次才真正落事件，轮询等待
    waited = 0.0
    while waited < 2:
        g = runner.get_game(gid)
        mine = [e for e in g.events if e.type == "wolf_proposal" and e.actor == g.human_seat()]
        if mine:
            break
        await asyncio.sleep(0.01)
        waited += 0.01
    assert mine, "真人狼的刀口投票应产生 wolf_proposal 事件"
    assert all(e.visibility == "wolves" for e in mine)
    # AI 队友的可见视角必须包含真人提议（狼队频道互通）
    from app.engine.game import view_events

    ai_wolf = next(p for p in g.players if p.role == Role.WEREWOLF and not p.is_human)
    assert any(e.actor == g.human_seat() for e in view_events(g, ai_wolf.seat) if e.type == "wolf_proposal")

    task = runner.tasks.get(gid)
    if task is not None:
        task.cancel()


class SlowWolfKnifeLlm(FakeLlm):
    """AI 狼刀决策延迟并投指定座位：验证真人通道不被 AI 阻塞、且真人一票定刀口。"""

    def __init__(self, knife_seat: int, delay: float = 0.4):
        super().__init__()
        self.knife_seat = knife_seat
        self.delay = delay

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is WolfProposal:
            await asyncio.sleep(self.delay)
            return WolfProposal(target=self.knife_seat, reason="测试刀口")
        return await super().decide(preset, messages, schema, on_thinking)


@pytest.mark.asyncio
async def test_human_wolf_knife_decisive_and_non_blocking(tmp_path):
    """真人狼刀口：输入通道与 AI 决策并行开放（不等 AI 亮票）；无论队友投谁，最终按真人票结算。"""
    llm = SlowWolfKnifeLlm(knife_seat=0)
    runner = make_runner(tmp_path, llm=llm)
    game = runner.create_game(_board6(), make_personas(5), human_name="狼玩家", human_role="werewolf")
    gid = game.id
    await runner.start(gid)

    g = runner.get_game(gid)
    ai_wolf = next(p for p in g.players if p.role == Role.WEREWOLF and not p.is_human)
    villager_seat = next(p.seat for p in g.players if p.role != Role.WEREWOLF)
    llm.knife_seat = villager_seat
    human_target = ai_wolf.seat  # 真人刀 AI 队友，与 AI 的提议必然不同

    # 等真人输入通道开启：此时 AI 狼仍在延迟思考，尚未亮票 → 通道开启不被 AI 阻塞
    waited = 0.0
    while waited < 10:
        await asyncio.sleep(0.01)
        waited += 0.01
        info = runner.pending_info(gid)
        if info and info["kind"] == "wolf_vote":
            break
    else:
        raise TimeoutError("未等到狼刀输入")
    g = runner.get_game(gid)
    assert not [e for e in g.events if e.type == "wolf_proposal" and e.actor != g.human_seat()], (
        "真人狼刀输入通道应先于 AI 亮票开放"
    )

    ok, msg = runner.submit(gid, {"kind": "wolf_vote", "target": human_target})
    assert ok, msg

    waited = 0.0
    while waited < 5:
        g = runner.get_game(gid)
        if any(e.type == "wolf_target" for e in g.events):
            break
        await asyncio.sleep(0.01)
        waited += 0.01
    g = runner.get_game(gid)
    assert any(e.type == "wolf_target" for e in g.events), "狼刀应完成结算"
    wt = [e for e in g.events if e.type == "wolf_target"][-1]
    assert wt.payload["target"] == human_target, "无论队友投谁，最终刀口必须按真人狼的选择结算"
    ai_prop = [e for e in g.events if e.type == "wolf_proposal" and e.actor == ai_wolf.seat]
    assert ai_prop and ai_prop[0].payload["target"] == villager_seat, "AI 队友应照常亮票（仅作提议）"

    task = runner.tasks.get(gid)
    if task is not None:
        task.cancel()


@pytest.mark.asyncio
async def test_twelve_guard_full_game(tmp_path):
    """守卫整局：12 人守卫板可正常打完；守护仅守卫本人可见；不连守同一人。"""
    from app.engine.board import BUILTIN_BOARDS

    board = next(b for b in BUILTIN_BOARDS if b.id == "twelve_guard")
    runner = make_runner(tmp_path)
    game = runner.create_game(board, make_personas(11), human_name="玩家", human_avatar="🙂")
    gid = game.id
    ok, msg = await runner.start(gid)
    assert ok, msg
    finished = await auto_play(runner, gid)
    assert finished.phase == Phase.GAME_OVER

    protects = [e for e in finished.events if e.type == "guard_protect"]
    assert protects, "应有守卫守护事件"
    for e in protects:
        assert e.visibility.startswith("seat:"), "守护动作只对守卫本人可见"
    for a, b in zip(protects, protects[1:]):
        assert a.payload["target"] != b.payload["target"], "不能连续两晚守同一人"


@pytest.mark.asyncio
async def test_sheriff_election_full_game(tmp_path):
    """警长竞选整局：竞选事件齐全、警长当选、首日发言从警长邻座开始且警长最后发言。"""
    from app.engine.board import BUILTIN_BOARDS

    board = next(b for b in BUILTIN_BOARDS if b.id == "twelve_guard")
    runner = make_runner(tmp_path)
    game = runner.create_game(board, make_personas(11), human_name="玩家", human_avatar="🙂", sheriff=True)
    gid = game.id
    ok, msg = await runner.start(gid)
    assert ok, msg
    finished = await auto_play(runner, gid)
    assert finished.phase == Phase.GAME_OVER
    assert finished.sheriff_enabled

    types = [e.type for e in finished.events]
    assert "sheriff_candidates" in types
    assert "sheriff_speech" in types, "上警者应发表竞选宣言"
    assert "sheriff_result" in types

    result = next(e for e in finished.events if e.type == "sheriff_result")
    assert not result.payload.get("destroyed"), "FakeLlm 投票集中应唯一当选"
    sheriff = result.payload["sheriff"]
    # 警长中途出局可能移交/撕毁警徽，只有警徽未易主时终局警长仍是当选者
    transfers = [e for e in finished.events if e.type == "badge_pass" and e.payload.get("from") == sheriff]
    if not transfers:
        assert finished.sheriff_seat == sheriff

    order_ev = next(e for e in finished.events if e.type == "speech_order")
    n = len(finished.players)
    step = -1 if order_ev.payload.get("direction") == "left" else 1
    rot = [((sheriff - 1 + step * k) % n) + 1 for k in range(1, n)]

    # 首夜出局者不发言；首日发言序列 = 幸存者按方向绕桌，警长压轴
    deaths1: set[int] = set()
    for e in finished.events:
        if e.type == "night_settle" and e.day == 1:
            deaths1.update(e.payload.get("deaths") or [])
    assert sheriff not in deaths1
    expected = [s for s in rot if s not in deaths1]
    expected.append(sheriff)

    speeches = [e.actor for e in finished.events if e.type == "speech" and e.day == 1]
    assert speeches == expected, f"首日发言顺序不符：{speeches} != {expected}"
    assert len(set(speeches)) == len(speeches), "每人首日只发言一次"


@pytest.mark.asyncio
async def test_abstain_vote_full_game(tmp_path):
    """全员弃票：放逐票无人出局按平票走；警长竞选全员弃票警徽作废；整局可正常打完。"""
    runner = make_runner(tmp_path, AbstainVoteLlm())
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂", sheriff=True)
    gid = game.id
    ok, msg = await runner.start(gid)
    assert ok, msg
    finished = await auto_play(runner, gid, abstain_vote=True)
    assert finished.phase == Phase.GAME_OVER

    for e in finished.events:
        if e.type == "vote_result":
            assert e.payload.get("exiled") is None, "全弃票不应有人被放逐"
            assert all(v["target"] == 0 for v in e.payload.get("votes", []))
    result = next(e for e in finished.events if e.type == "sheriff_result")
    assert result.payload.get("destroyed"), "全员弃票应警徽作废"
    assert result.payload.get("reason") == "全员弃票"
    assert finished.sheriff_seat is None


@pytest.mark.asyncio
async def test_abort_running_game(tmp_path):
    """进行中的对局可中断：主循环停止、按和局收尾、存档落盘、无法重复中断。"""
    runner = make_runner(tmp_path)
    # 固定村民：夜晚无真人动作，游戏才能自动推进到白天（否则会一直等待真人输入）
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂", human_role="villager")
    gid = game.id
    await runner.start(gid)
    waited = 0.0
    while waited < 20:
        await asyncio.sleep(0.01)
        waited += 0.01
        # 真人（村民）的首个输入点——首夜遗言（被刀）或首日发言（存活）——必然出现且会停住游戏；
        # 不能等 DAY_SPEECH 相位：全员 AI 的白天 FakeLlm 瞬间跑完，轮询会整段错过
        pend = runner.pending.get(gid)
        if pend is not None and pend.kind in ("last_words", "speech"):
            break
    else:
        raise TimeoutError("游戏未进入可中断的运行状态（无真人输入点）")

    ok, msg = await runner.abort(gid)
    assert ok, msg
    g = runner.get_game(gid)
    assert g.phase == Phase.GAME_OVER
    assert g.winner == "draw"
    assert not runner.is_active(gid)
    assert runner.pending_info(gid) is None
    assert all(p.role_revealed for p in g.players)
    # 存档落盘，重开应用也是结束态
    from_disk = runner.store.get_game(gid)
    assert from_disk is not None and from_disk.phase == Phase.GAME_OVER
    # 已结束的对局不能再中断
    ok2, _ = await runner.abort(gid)
    assert not ok2


def _board6():
    from app.engine.board import BUILTIN_BOARDS

    return BUILTIN_BOARDS[0]


@pytest.mark.asyncio
async def test_resume_after_crash(tmp_path):
    runner = make_runner(tmp_path)
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂")
    gid = game.id
    await runner.start(gid)

    # 等到第 1 天白天发言阶段，杀掉循环模拟崩溃
    waited = 0.0
    while waited < 20:
        await asyncio.sleep(0.01)
        waited += 0.01
        g = runner.get_game(gid)
        if g.phase == Phase.DAY_SPEECH:
            break
    task = runner.tasks.get(gid)
    assert task is not None
    task.cancel()
    await asyncio.sleep(0.05)
    assert not runner.is_active(gid)

    # 新 runner 从磁盘恢复并打到结束
    runner2 = make_runner(tmp_path)
    ok, msg = await runner2.resume(gid)
    assert ok, msg
    finished = await auto_play(runner2, gid)
    assert finished.phase == Phase.GAME_OVER
    # 恢复后不应有 seq 重复的事件
    seqs = [e.seq for e in finished.events]
    assert len(seqs) == len(set(seqs))


@pytest.mark.asyncio
async def test_information_isolation_in_events(tmp_path):
    """狼队私密事件绝不能出现在好人真人玩家的可见流里。"""
    runner = make_runner(tmp_path)
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂")
    gid = game.id
    await runner.start(gid)
    await auto_play(runner, gid)
    game = runner.get_game(gid)

    from app.engine.game import visible_to

    for p in game.players:
        for e in game.events:
            if not visible_to(game, e, p.seat):
                # 不可见事件绝不能是狼队提议等私密内容泄漏给非狼玩家 —— 校验失败即泄漏
                # god=思考过程（仅上帝视角可见），同样不属于泄漏
                assert (
                    e.visibility == "public" or e.visibility.startswith("seat:") or e.visibility == "wolves" or e.visibility == "god"
                )


@pytest.mark.asyncio
async def test_god_mode_spectator_game(tmp_path):
    """上帝观战局：全 AI 无真人，思考过程与逐个亮票事件齐全，身份全公开。"""
    runner = make_runner(tmp_path)
    game = runner.create_game(_board6(), make_personas(6), mode="god")
    assert game.mode == "god"
    assert all(not p.is_human for p in game.players)
    gid = game.id
    ok, msg = await runner.start(gid)
    assert ok, msg

    # 无真人：全程不应出现等待输入
    waited = 0.0
    while waited < 60:
        await asyncio.sleep(0.01)
        waited += 0.01
        assert runner.pending_info(gid) is None, "观战局不应等待真人输入"
        g = runner.get_game(gid)
        if g is not None and g.phase == Phase.GAME_OVER:
            finished = g
            break
    else:
        raise TimeoutError("观战局未在限时内结束")

    assert finished.winner in ("wolf", "good", "draw")
    types = [e.type for e in finished.events]
    assert "thinking" in types, "观战局应有思考过程事件"
    assert "vote_cast" in types, "观战局应有逐个亮票事件"
    assert "game_over" in types
    # 思考事件仅 god 可见
    assert all(e.visibility == "god" for e in finished.events if e.type == "thinking")
    # 亮票按座位顺序
    casts = [e for e in finished.events if e.type == "vote_cast"]
    first_day_casts = [e for e in casts if e.day == 1]
    seats_in_order = [e.payload["seat"] for e in first_day_casts]
    assert seats_in_order == sorted(seats_in_order), "同一天内亮票应按座位顺序"

    # 观战局 public_state 全身份公开
    from app.engine.game import public_state

    st = public_state(finished, reveal=False)
    assert st["human_seat"] is None
    assert st["mode"] == "god"
    assert all(p["role"] is not None for p in st["players"])


def test_public_state_reveals_dead_players(tmp_path):
    """对局中出局不翻牌：未翻牌死者 public_state 不下发身份；已翻牌者（白痴）仍可见。"""
    from app.engine.game import public_state

    game = Game(
        id="t",
        created_at="now",
        board=_board6(),
        players=[],
        seed=1,
    )
    from app.models import Player, Role

    game.players = [
        Player(seat=1, name="甲", role=Role.WEREWOLF, alive=False, role_revealed=True),
        Player(seat=2, name="乙", role=Role.SEER, alive=False, role_revealed=False),
        Player(seat=3, name="丙", role=Role.VILLAGER, alive=True, role_revealed=False),
    ]
    st = public_state(game, reveal=False)
    by_seat = {p["seat"]: p for p in st["players"]}
    assert by_seat[1]["role"] == "werewolf", "已翻牌的死者身份仍公开"
    assert by_seat[2]["role"] is None, "对局中出局不翻牌，死者身份保密"
    assert by_seat[2]["role_label"] is None
    assert by_seat[3]["role"] is None, "活人未翻牌不下发身份"
    assert by_seat[3]["role_label"] is None


def test_public_state_wolf_viewer_sees_teammates(tmp_path):
    """真人玩家是狼时：队友的狼身份对本人公开，神职/村民身份仍保密。"""
    from app.engine.game import public_state
    from app.models import Player, Role

    game = Game(
        id="t",
        created_at="now",
        board=_board6(),
        players=[],
        seed=1,
    )
    game.players = [
        Player(seat=1, name="狼甲", role=Role.WEREWOLF, is_human=True),
        Player(seat=2, name="狼乙", role=Role.WEREWOLF),
        Player(seat=3, name="预", role=Role.SEER),
        Player(seat=4, name="民", role=Role.VILLAGER),
    ]
    st = public_state(game, reveal=False)
    by_seat = {p["seat"]: p for p in st["players"]}
    assert by_seat[1]["role"] == "werewolf"
    assert by_seat[2]["role"] == "werewolf", "狼人玩家应看到队友的狼身份"
    assert by_seat[3]["role"] is None, "预言家身份对狼玩家保密"
    assert by_seat[4]["role"] is None

    # 好人视角不能看到狼身份
    game.players[0].is_human = False
    game.players[3].is_human = True
    st2 = public_state(game, reveal=False)
    by_seat2 = {p["seat"]: p for p in st2["players"]}
    assert by_seat2[1]["role"] is None and by_seat2[2]["role"] is None, "好人玩家不应看到狼人身份"


class FatalLlm(FakeLlm):
    """所有 LLM 调用抛账号级致命错误（欠费/订阅失效）：应暂停对局弹窗报错而非代打。"""

    async def decide(self, preset, messages, schema, on_thinking=None):
        raise LlmFatalError("订阅失效或账户欠费")

    async def speak(self, preset, messages, on_thinking=None):
        raise LlmFatalError("订阅失效或账户欠费")
        yield  # noqa: B901 — 保持异步生成器形态，raise 在首次 __anext__ 时触发


async def _wait_paused(runner: GameRunner, gid: str, timeout: float = 10) -> dict:
    """等待对局进入 LLM 暂停态，返回 paused_info。"""
    waited = 0.0
    while waited < timeout:
        info = runner.paused_info(gid)
        if info is not None:
            return info
        await asyncio.sleep(0.01)
        waited += 0.01
    raise TimeoutError("对局未进入暂停状态")


@pytest.mark.asyncio
async def test_fatal_llm_error_pauses_then_abort(tmp_path):
    """LLM 账号级致命错误：对局暂停并广播报错；用户选择终止后按故障收尾，全程无系统代打。"""
    runner = make_runner(tmp_path, FatalLlm())
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂", human_role="villager")
    gid = game.id
    await runner.start(gid)
    game = runner.get_game(gid)  # start 会从存档重载实例，后续事件都落在新实例上
    info = await _wait_paused(runner, gid)
    assert "订阅失效或账户欠费" in info["message"]
    assert runner.is_active(gid), "暂停时主循环应仍在等待用户选择"
    assert game.phase != Phase.GAME_OVER
    paused_events = [ev for ev in game.events if ev.type == "llm_paused"]
    assert paused_events and "订阅失效" in paused_events[-1].payload["message"]

    ok, _ = await runner.resume_llm(gid, "abort")
    assert ok
    # 等任务真正退出（GAME_OVER 置位后主循环还需几轮事件循环走完清理链）
    for _ in range(500):
        if game.phase == Phase.GAME_OVER and not runner.is_active(gid):
            break
        await asyncio.sleep(0.01)
    g = runner.get_game(gid)
    assert g.phase == Phase.GAME_OVER
    assert g.winner == "error"
    assert "订阅失效或账户欠费" in g.end_reason
    assert not runner.is_active(gid)
    assert runner.pending_info(gid) is None
    assert runner.paused_info(gid) is None
    texts = [ev.payload.get("text", "") for ev in g.events if ev.type == "host_line"]
    assert any("对局已终止" in t for t in texts)
    assert not any("系统代为行动" in t for t in texts)


@pytest.mark.asyncio
async def test_fatal_llm_error_in_speech_pause_and_abort(tmp_path):
    """发言路径的账号级致命错误同样暂停对局；选择终止后 GameFatalStop 上抛、按故障收尾。"""
    from app.runner import GameFatalStop

    class FatalSpeechLlm(FakeLlm):
        async def speak(self, preset, messages, on_thinking=None):
            raise LlmFatalError("订阅失效或账户欠费")
            yield

    runner = make_runner(tmp_path, FatalSpeechLlm())
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂", human_role="villager")
    ai = next(p for p in game.players if not p.is_human)
    task = asyncio.create_task(runner._stream_speech(game, ai, [{"role": "user", "content": "请发言"}], "speech"))
    await _wait_paused(runner, game.id)
    ok, _ = await runner.resume_llm(game.id, "abort")
    assert ok
    with pytest.raises(GameFatalStop):
        await task
    assert game.phase == Phase.GAME_OVER
    assert game.winner == "error"
    assert "订阅失效或账户欠费" in game.end_reason


class FlakyDecideLlm(FakeLlm):
    """前 N 次 decide 抛非致命错误（超时/网络类），之后恢复 FakeLlm 正常行为。"""

    def __init__(self, fail_times: int = 2):
        super().__init__()
        self.fail_times = fail_times

    async def decide(self, preset, messages, schema, on_thinking=None):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise LlmError("模拟决策超时")
        return await super().decide(preset, messages, schema, on_thinking)


@pytest.mark.asyncio
async def test_decision_failure_pauses_and_retry_completes_game(tmp_path):
    """决策失败暂停对局：不代打、不发空票；换回正常模型后重试可跑完整局。"""
    runner = make_runner(tmp_path, FlakyDecideLlm(fail_times=2))
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂", human_role="villager")
    gid = game.id
    await runner.start(gid)
    game = runner.get_game(gid)  # start 会从存档重载实例，后续事件都落在新实例上
    # 第一批并行狼刀决策同时失败 → 只产生一次暂停（共享暂停，不产生孤儿等待）
    info = await _wait_paused(runner, gid)
    assert "狼刀投票" in info["message"]
    assert len([ev for ev in game.events if ev.type == "llm_paused"]) == 1

    runner.llm = FakeLlm()
    ok, _ = await runner.resume_llm(gid, "retry")
    assert ok
    finished = await auto_play(runner, gid)
    assert finished.phase == Phase.GAME_OVER
    assert finished.winner in ("wolf", "good", "draw")
    texts = [ev.payload.get("text", "") for ev in finished.events if ev.type == "host_line"]
    assert not any("系统代为行动" in t for t in texts)
    # 失败的行动不落任何事件：暂停期间没有 wolf_proposal / vote_cast 之类的代打产物
    assert all("（系统代打）" not in str(ev.payload) for ev in finished.events)


@pytest.mark.asyncio
async def test_invalid_target_pauses_and_retry_returns_valid(tmp_path):
    """决策目标非法同样暂停（不随机代打）；重试后返回合法目标。"""
    from app.models import VoteDecision

    class BadTargetOnceLlm(FakeLlm):
        def __init__(self):
            super().__init__()
            self.bad = True

        async def decide(self, preset, messages, schema, on_thinking=None):
            if self.bad and schema is VoteDecision:
                self.bad = False
                return VoteDecision(target=99, reason="越界目标")
            return await super().decide(preset, messages, schema, on_thinking)

    runner = make_runner(tmp_path, BadTargetOnceLlm())
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂", human_role="villager")
    voter = next(p for p in game.players if not p.is_human)
    allowed = [q.seat for q in game.players if q.seat != voter.seat]
    # 提示词带出合法座位清单，重试时 FakeLlm 从清单取号（否则固定取 1 号，撞上 voter 自身会二次暂停挂死）
    messages = [{"role": "user", "content": "请投票，可选：" + "、".join(f"{s}号" for s in allowed)}]
    task = asyncio.create_task(runner._decide(game, voter, VoteDecision, messages, allowed, "vote"))
    info = await _wait_paused(runner, game.id)
    assert "目标非法（99）" in info["message"]
    ok, _ = await runner.resume_llm(game.id, "retry")
    assert ok
    d = await task
    assert d.target in allowed


@pytest.mark.asyncio
async def test_self_explode_bypass_failure_does_not_pause(tmp_path):
    """查杀触发的自爆旁路决策失败：静默放弃（不暂停对局、不代打）。"""

    class FatalExplodeLlm(FakeLlm):
        async def decide(self, preset, messages, schema, on_thinking=None):
            if schema is SelfExplodeDecision:
                raise LlmError("模拟决策超时")
            return await super().decide(preset, messages, schema, on_thinking)

    runner = make_runner(tmp_path, FatalExplodeLlm())
    game = runner.create_game(_board6(), make_personas(5), human_name="玩家", human_avatar="🙂", human_role="villager")
    wolf = next(p for p in game.players if not p.is_human)
    d = await runner._decide(game, wolf, SelfExplodeDecision, [{"role": "user", "content": "是否自爆？"}], [], "self_explode", pause=False)
    assert d.explode is False
    assert runner.paused_info(game.id) is None


def test_raise_if_fatal_detection():
    """_raise_if_fatal：账号/配置级错误转 LlmFatalError；限流、超时、普通 400 不误伤。"""
    import httpx
    from openai import AuthenticationError, BadRequestError, RateLimitError

    from app.llm.client import LlmFatalError, _raise_if_fatal

    def make_err(cls, status, message):
        resp = httpx.Response(status, request=httpx.Request("POST", "http://t/v1/chat/completions"))
        return cls(message, response=resp, body=None)

    with pytest.raises(LlmFatalError):
        _raise_if_fatal(make_err(BadRequestError, 400, "Error code: 400 - {'error': {'code': 'InvalidSubscription', 'message': 'subscription has expired'}}"))
    with pytest.raises(LlmFatalError):
        _raise_if_fatal(make_err(AuthenticationError, 401, "Error code: 401 - Incorrect API key provided"))
    with pytest.raises(LlmFatalError):
        _raise_if_fatal(make_err(RateLimitError, 429, "Error code: 429 - insufficient_quota: You exceeded your current quota"))
    # 非致命：正常限流 / 普通 400 / 其他异常 —— 原样放行，走暂停弹窗等用户处理
    _raise_if_fatal(make_err(RateLimitError, 429, "Error code: 429 - rate limit exceeded, please retry later"))
    _raise_if_fatal(make_err(BadRequestError, 400, "Error code: 400 - messages array is too long"))
    _raise_if_fatal(RuntimeError("boom"))


def _night_game(roles: list[Role]) -> Game:
    """手工构造进入夜晚的对局（不依赖发牌）：全 AI、指定角色表。"""
    players = [Player(seat=i, name=f"AI{i}", role=r) for i, r in enumerate(roles, start=1)]
    board = BoardConfig(id="t", name="测试板", win_condition=WinCondition.CITY, roles=roles)
    game = Game(id="night-flow", created_at="", board=board, players=players, seed=1)
    game.day = 2
    game.phase = Phase.NIGHT_GUARD
    return game


@pytest.mark.asyncio
async def test_night_announcement_does_not_leak_death(tmp_path):
    """神职已死仍要播报环节：流程播报只看板子组成，跳过播报等于宣告角色出局（场外信息）。"""
    runner = make_runner(tmp_path)
    game = _night_game([Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH, Role.GUARD])
    runner.store.save_game(game)
    for seat in (5, 6, 7):  # 预言家 / 女巫 / 守卫全部已死
        game.player(seat).alive = False

    await runner._phase_night_guard(game)
    await runner._phase_night_seer(game)
    await runner._phase_night_witch_save(game)

    lines = [e.payload.get("text", "") for e in game.events if e.type == "host_line"]
    assert any("守卫请睁眼" in t for t in lines), lines
    assert any("预言家请睁眼" in t for t in lines), lines
    assert any("女巫请睁眼" in t for t in lines), lines


@pytest.mark.asyncio
async def test_night_announcement_absent_role_stays_silent(tmp_path):
    """板子上没有的角色不播报：板子组成是公开信息，喊了反而误导。"""
    runner = make_runner(tmp_path)
    game = _night_game([Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH])
    runner.store.save_game(game)

    await runner._phase_night_guard(game)

    lines = [e.payload.get("text", "") for e in game.events if e.type == "host_line"]
    assert not any("守卫请睁眼" in t for t in lines), lines


def _isolation_game(mode: str = "normal") -> Game:
    """信息隔离测试用：7 人局，1 号真人狼，5/6/7 号神职，带私密事件与 phase_ctx。"""
    from app.engine.game import emit

    roles = [Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH, Role.GUARD]
    players = [Player(seat=i, name=f"P{i}", role=r) for i, r in enumerate(roles, start=1)]
    players[0].is_human = True
    board = BoardConfig(id="t", name="测试板", win_condition=WinCondition.CITY, roles=roles)
    game = Game(id="iso", created_at="", board=board, players=players, seed=1, mode=mode)
    game.day = 2
    game.phase = Phase.DAY_SPEECH
    emit(game, "speech", {"text": "大家好"}, actor=3)
    emit(game, "wolf_proposal", {"target": 5, "reason": "刀预言家"}, actor=2, visibility="wolves")
    emit(game, "guard_protect", {"target": 3}, actor=7, visibility="seat:7")
    emit(game, "seer_result", {"target": 6, "is_wolf": False}, actor=5, visibility="seat:5")
    emit(game, "witch_knife", {"target": 3, "can_save": True}, actor=6, visibility="seat:6")
    game.phase_ctx = {"knife": 3, "guard": 3}
    return game


def test_stream_view_deny_by_default():
    """实时流过滤默认拒绝：带 seq 的持久化事件一律按 visible_to 判定，类型不在白名单也不得泄露。"""
    from app.routes.stream import _passes_view

    game = _isolation_game()
    cache: dict = {}
    args = dict(current=game, full_view=False, human_seat=1, vis_cache=cache)
    events = {e.seq: e for e in game.events}
    assert _passes_view(data={"seq": 1}, event="game_event", **args)  # 公开发言
    assert _passes_view(data={"seq": 2}, event="game_event", **args)  # 狼队频道：真人就是狼
    assert not _passes_view(data={"seq": 3}, event="game_event", **args)  # 守卫守护：只有守卫可见
    assert not _passes_view(data={"seq": 4}, event="game_event", **args)  # 预言家验人：只有预言家可见
    assert not _passes_view(data={"seq": 5}, event="game_event", **args)  # 女巫刀口：只有女巫可见
    assert _passes_view(data={"seat": 3}, event="vote_pending", **args)  # 无 seq 瞬态：公开
    assert not _passes_view(data={}, event="thinking_delta", **args)  # 思考流：玩家视角不可见
    assert all(_passes_view(data={"seq": s}, event="game_event", current=game, full_view=True, human_seat=1, vis_cache=cache) for s in events)  # 上帝视角
    # 缺失持久化事件按不可见处理（fail-closed）
    assert not _passes_view(data={"seq": 999}, event="game_event", **args)


@pytest.mark.asyncio
async def test_review_midgame_normal_isolated(tmp_path):
    """进行中的普通局复盘按真人视角过滤：私密事件与他人身份不泄露；结束后全量。"""
    from app.engine.game import emit
    from app.routes.games import review_payload

    game = _isolation_game()
    emit(game, "vote_cast", {"seat": 2, "name": "狼二", "target": 5, "reason": "5号发言像神"}, actor=2)
    payload = review_payload(game)
    types = {e["type"] for e in payload["events"]}
    assert "speech" in types and "wolf_proposal" in types  # 公开 + 本阵营
    assert "guard_protect" not in types and "seer_result" not in types and "witch_knife" not in types
    assert "phase_ctx" not in payload  # 不含刀口等中间态
    # 投票理由仅上帝视角可见
    assert all("reason" not in e for e in payload["events"] if e["type"] == "vote_cast")
    roles = {pd["seat"]: pd["role"] for pd in payload["players"]}
    assert roles[1] == Role.WEREWOLF.value and roles[2] == Role.WEREWOLF.value  # 本人 + 狼队友
    assert roles[5] is None and roles[6] is None and roles[7] is None  # 神职身份保密

    # 结束后全量公开（含投票理由）
    from app.engine.game import finish
    finish(game, "good", "测试结束")
    payload = review_payload(game)
    assert {e["type"] for e in payload["events"]} >= {"guard_protect", "seer_result", "witch_knife"}
    assert all(pd["role"] for pd in payload["players"])
    assert next(e["reason"] for e in payload["events"] if e["type"] == "vote_cast") == "5号发言像神"


class KillDecisionLlm(FakeLlm):
    """毒药/开枪决策固定指向 self.target，验证 _decide 对合法目标直接采信不兜底。"""

    def __init__(self, target: int = 0):
        super().__init__()
        self.target = target

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is WitchPoison:
            return WitchPoison(use=True, target=self.target)
        if schema is HunterDecision:
            return HunterDecision(shoot=True, target=self.target)
        return await super().decide(preset, messages, schema, on_thinking)


@pytest.mark.asyncio
async def test_decide_accepts_valid_poison_and_hunter_targets(tmp_path):
    """回归：毒药/开枪调用点必须把真实候选座位传给 _decide，否则模型决策全被判非法、
    永远卡在暂停弹窗——表现为女巫整局不毒、猎人整局不开枪。"""
    from app.engine.board import BUILTIN_BOARDS
    from app.llm import prompts

    store = Store(tmp_path)
    store.save_settings(_settings_with_preset())
    llm = KillDecisionLlm()
    runner = GameRunner(store, GameHub(), llm)
    game = runner.create_game(BUILTIN_BOARDS[1], make_personas(8), human_name="玩家", human_avatar="🙂")
    from app.engine.game import assign_roles
    assign_roles(game)

    def ensure_ai(p: Player) -> Player:
        # 真人无 preset_id 会暂停等配置，把目标角色换给 AI（多重集不变）
        if not p.is_human:
            return p
        other = next(q for q in game.players if not q.is_human)
        p.role, other.role = other.role, p.role
        return other

    witch = ensure_ai(next(p for p in game.players if p.role == Role.WITCH))
    candidates = [q.seat for q in game.alive_players() if q.seat != witch.seat]
    llm.target = candidates[0]
    d = await runner._decide(
        game, witch, WitchPoison, prompts.witch_poison_messages(game, witch, candidates), candidates, "witch_poison"
    )
    assert d.use and d.target == candidates[0]

    hunter = ensure_ai(next(p for p in game.players if p.role == Role.HUNTER))
    h_cands = [q.seat for q in game.alive_players()]
    llm.target = h_cands[-1]
    d2 = await runner._decide(
        game, hunter, HunterDecision, prompts.hunter_messages(game, hunter, h_cands), h_cands, "hunter"
    )
    assert d2.shoot and d2.target == h_cands[-1]

class ExplodeLlm(FakeLlm):
    """第一只轮到发言的狼自爆，其余决策同 FakeLlm。"""

    def __init__(self):
        super().__init__()
        self.exploded = False

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is SelfExplodeDecision:
            answer = SelfExplodeDecision(explode=not self.exploded)
            self.exploded = True
            return answer
        return await super().decide(preset, messages, schema, on_thinking)


class GatedSpeechLlm(FakeLlm):
    """发言在 gate 上挂起，用于制造「AI 发言中途」的竞态窗口。"""

    def __init__(self, gate: asyncio.Event):
        super().__init__()
        self.gate = gate

    async def speak(self, preset, messages, on_thinking=None):
        await self.gate.wait()
        async for chunk in super().speak(preset, messages, on_thinking):
            yield chunk


def _day_game(roles: list[Role], gid: str = "day-flow", sheriff_seat: int | None = None) -> Game:
    """手工构造白天发言阶段的对局：2 号 AI 狼先发言，1 号真人。"""
    # AI 玩家必须带 preset_id，否则 _decide/_stream_speech 走系统兜底，LLM 决策不生效
    players = [
        Player(seat=i, name=f"P{i}", role=r, is_human=(i == 1), preset_id=("" if i == 1 else "p1"))
        for i, r in enumerate(roles, start=1)
    ]
    board = BoardConfig(id="t", name="测试板", win_condition=WinCondition.CITY, roles=roles)
    game = Game(id=gid, created_at="", board=board, players=players, seed=1)
    game.day = 1
    game.phase = Phase.DAY_SPEECH
    game.phase_ctx = {"order": [2, 1, 3, 4, 5, 6], "i": 0}
    if sheriff_seat:
        game.sheriff_seat = sheriff_seat
    return game


@pytest.mark.asyncio
async def test_ai_wolf_explode_interrupts_day(tmp_path):
    """AI 狼发言回合自爆：翻牌死亡、无遗言、当天无投票、直接入夜，对局继续。"""
    from app.engine.board import BUILTIN_BOARDS

    runner = make_runner(tmp_path, ExplodeLlm())
    game = runner.create_game(BUILTIN_BOARDS[1], make_personas(8), human_name="玩家", human_avatar="🙂")
    ok, msg = await runner.start(game.id)
    assert ok, msg
    finished = await auto_play(runner, game.id)

    assert finished.phase == Phase.GAME_OVER
    exp = next(e for e in finished.events if e.type == "wolf_explode")
    wolf = finished.player(exp.actor)
    assert wolf.role == Role.WEREWOLF and not wolf.alive and wolf.role_revealed
    exp_day = exp.day
    assert not any(e.type == "last_words" and e.actor == exp.actor for e in finished.events)
    assert not any(e.type == "vote_result" and e.day == exp_day for e in finished.events)
    # 爆炸当晚狼队照常行动
    assert any(e.type == "wolf_target" and e.day == exp_day + 1 for e in finished.events)


@pytest.mark.asyncio
async def test_human_wolf_explode_mid_ai_speech(tmp_path):
    """真人狼在 AI 发言中途自爆：打断流式发言、翻牌出局、直接入夜。"""
    gate = asyncio.Event()
    runner = make_runner(tmp_path, GatedSpeechLlm(gate))
    game = _day_game([Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH])
    runner.store.save_game(game)

    task = asyncio.create_task(runner._phase_day_speech(game))
    await asyncio.sleep(0.05)
    ok, msg = runner.request_explode(game.id)
    assert ok, msg
    await asyncio.wait_for(task, 5)

    assert game.phase == Phase.NIGHT_GUARD
    assert game.day == 2
    human = game.human()
    assert not human.alive and human.role_revealed and human.role == Role.WEREWOLF
    assert any(e.type == "wolf_explode" and e.actor == 1 for e in game.events)
    # 2 号发言被打断，没有固化 speech 事件
    assert not any(e.type == "speech" and e.actor == 2 for e in game.events)
    assert any("发言被打断" in e.payload.get("text", "") for e in game.events if e.type == "host_line")
    assert game.id not in runner.explode_futures


@pytest.mark.asyncio
async def test_request_explode_rejects(tmp_path):
    """非发言阶段 / 非狼真人申请自爆被拒。"""
    runner = make_runner(tmp_path)
    game = _day_game([Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH])
    runner.store.save_game(game)

    game.phase = Phase.VOTE
    runner.store.save_game(game)  # get_game 读的是存档副本，改动须回存
    ok, msg = runner.request_explode(game.id)
    assert not ok and "发言阶段" in msg

    game.phase = Phase.DAY_SPEECH
    game.player(1).role = Role.VILLAGER
    runner.store.save_game(game)
    ok, msg = runner.request_explode(game.id)
    assert not ok and "狼人" in msg


def test_claimed_wolf_seats_detection():
    """施压句式检测：报狼/狼面/悍跳/归票指向命中，好人/否认/无据怀疑不命中。"""
    from app.runner import _claimed_wolf_seats

    assert _claimed_wolf_seats("我昨晚查验了3号，他是狼人，铁查杀") == {3}
    assert _claimed_wolf_seats("3号是查杀，今天出他") == {3}
    assert _claimed_wolf_seats("我查验3号是好人，给他金水") == set()
    assert _claimed_wolf_seats("我觉得5号发言有问题，听感最差") == set()
    assert _claimed_wolf_seats("5号狼面最大，建议今天归票5号") == {5}
    assert _claimed_wolf_seats("我认为7号不是狼，大家别乱踩") == set()
    assert _claimed_wolf_seats("9号这么发言就是聊爆了") == {9}
    assert _claimed_wolf_seats("2号悍跳痕迹明显，验人心路对不上") == {2}
    assert _claimed_wolf_seats("1号是我的金水，我保他") == set()
    assert _claimed_wolf_seats("6号不像狼，别投他") == set()


class ClaimExplodeLlm(FakeLlm):
    """发言报3号查杀；触发式自爆决策（带「刚结束的…」标记）按开关作答，回合内常规决策不爆。"""

    def __init__(self, explode_on_trigger: bool = True):
        super().__init__()
        self.explode_on_trigger = explode_on_trigger

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is SelfExplodeDecision:
            triggered = any(
                isinstance(m, dict) and "刚结束的" in str(m.get("content", "")) for m in messages
            )
            return SelfExplodeDecision(explode=triggered and self.explode_on_trigger)
        return await super().decide(preset, messages, schema, on_thinking)

    async def speak(self, preset, messages, on_thinking=None):
        yield "我昨晚查验了3号，他是狼人，铁查杀，今天归票3号。"


class ParallelExplodeLlm(FakeLlm):
    """触发式自爆决策可观测：记录每个座位被询问的时间区间与是否自爆，用于验证并行思考。"""

    def __init__(self, explode_seats: set[int] | None = None, delay: float = 0.25):
        super().__init__()
        self.explode_seats = explode_seats  # None = 被点名一律爆
        self.delay = delay
        self.spans: dict[int, tuple[float, float]] = {}

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is SelfExplodeDecision:
            text = "\n".join(str(m.get("content", "")) for m in messages)
            if "刚结束的" in text:
                m = re.search(r"你的座位是 (\d+)号", text)
                seat = int(m.group(1)) if m else 0
                start = time.monotonic()
                await asyncio.sleep(self.delay)
                self.spans[seat] = (start, time.monotonic())
                return SelfExplodeDecision(explode=self.explode_seats is None or seat in self.explode_seats)
            return SelfExplodeDecision(explode=False)  # 发言回合内的常规自爆决策不爆
        return await super().decide(preset, messages, schema, on_thinking)

    async def speak(self, preset, messages, on_thinking=None):
        yield "我昨晚查验了3号是狼人，5号也是狼人，两个都是铁查杀，今天先出3号。"


class SheriffSwallowLlm(FakeLlm):
    """竞选宣言回合一律自爆（吞警徽），白天回合与触发决策不爆。"""

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is SelfExplodeDecision:
            return SelfExplodeDecision(explode="警长竞选进行中" in str(messages[-1]["content"]))
        return await super().decide(preset, messages, schema, on_thinking)


class FirstOnlySwallowLlm(FakeLlm):
    """只在双爆第一步窗口自爆，重选窗口不再爆（用于验证重选正常走完选出警长）。"""

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is SelfExplodeDecision:
            return SelfExplodeDecision(explode="双爆吞警徽第一步" in str(messages[-1]["content"]))
        return await super().decide(preset, messages, schema, on_thinking)


class ExplodeAskCountLlm(ClaimExplodeLlm):
    """仅2号发言报3号查杀；记录各座位自爆权衡询问（触发/回合内分开计），验证同阶段只权衡一次。"""

    def __init__(self):
        super().__init__(explode_on_trigger=False)
        self.trigger_asks: list[int] = []
        self.turn_asks: list[int] = []

    @staticmethod
    def _seat_of(messages: list) -> int:
        m = re.search(r"你的座位是 (\d+)号", "\n".join(str(x.get("content", "")) for x in messages))
        return int(m.group(1)) if m else 0

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is SelfExplodeDecision:
            seat = self._seat_of(messages)
            (self.trigger_asks if "刚结束的" in str(messages[-1]["content"]) else self.turn_asks).append(seat)
        return await super().decide(preset, messages, schema, on_thinking)

    async def speak(self, preset, messages, on_thinking=None):
        if self._seat_of(messages) == 2:
            yield "我昨晚查验了3号，他是狼人，铁查杀，今天归票3号。"
        else:
            yield "大家好，我先听一听。"


@pytest.mark.asyncio
async def test_triggered_wolf_skips_own_turn_explode_ask(tmp_path):
    """被查杀触发权衡过的狼，同一阶段自己发言回合不再重复询问；未被触发的狼照常询问。"""
    llm = ExplodeAskCountLlm()
    runner = make_runner(tmp_path, llm)
    game = _day_game([Role.VILLAGER, Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.SEER, Role.WITCH])
    runner.store.save_game(game)

    task = asyncio.create_task(runner._phase_day_speech(game))
    # 真人发言回合到达时喂一条无查杀的发言
    for _ in range(300):
        await asyncio.sleep(0.01)
        info = runner.pending_info(game.id)
        if info and info["kind"] == "speech":
            ok, msg = runner.submit(game.id, {"kind": "speech", "text": "大家好，我先听一听。"})
            assert ok, msg
            break
    await asyncio.wait_for(task, 10)

    assert llm.trigger_asks == [3]  # 2号发言查杀3号 → 3号被触发权衡（仅此一次）
    assert llm.turn_asks == [2]  # 2号回合内常规询问照常；3号回合内被跳过


class LastWolfProbeLlm(FakeLlm):
    """5号发言报2号（场上唯一存活狼）查杀；统计2号被询问自爆的次数（回合内+触发）。"""

    def __init__(self):
        super().__init__()
        self.asks = 0

    @staticmethod
    def _seat_of(messages: list) -> int:
        m = re.search(r"你的座位是 (\d+)号", "\n".join(str(x.get("content", "")) for x in messages))
        return int(m.group(1)) if m else 0

    async def decide(self, preset, messages, schema, on_thinking=None):
        if schema is SelfExplodeDecision:
            self.asks += 1
            return SelfExplodeDecision(explode=True)  # 一旦被询问必爆：以「从未询问」断言兜底
        return await super().decide(preset, messages, schema, on_thinking)

    async def speak(self, preset, messages, on_thinking=None):
        if self._seat_of(messages) == 5:
            yield "我昨晚查验了2号，他是狼人，铁查杀，今天归票2号。"
        else:
            yield "大家好，我先听一听。"


@pytest.mark.asyncio
async def test_last_wolf_never_asked_to_explode(tmp_path):
    """场上只剩一匹 AI 狼：回合内与被查杀触发都不询问自爆（自爆 = 0 狼当场好人胜）。"""
    llm = LastWolfProbeLlm()
    runner = make_runner(tmp_path, llm)
    game = _day_game([Role.VILLAGER, Role.WEREWOLF, Role.VILLAGER, Role.SEER, Role.WITCH, Role.VILLAGER])
    runner.store.save_game(game)

    task = asyncio.create_task(runner._phase_day_speech(game))
    # 真人发言回合到达时喂一条无查杀的发言
    for _ in range(300):
        await asyncio.sleep(0.01)
        info = runner.pending_info(game.id)
        if info and info["kind"] == "speech":
            ok, msg = runner.submit(game.id, {"kind": "speech", "text": "大家好，我先听一听。"})
            assert ok, msg
            break
    await asyncio.wait_for(task, 10)

    assert llm.asks == 0  # 2号（独狼）回合内不被询问；5号发言查杀2号也不触发
    assert game.phase == Phase.VOTE
    assert game.player(2).alive and not game.player(2).role_revealed
    assert not any(e.type == "wolf_explode" for e in game.events)


@pytest.mark.asyncio
async def test_claim_triggers_ai_wolf_explode(tmp_path):
    """AI 发言报查杀点名 AI 狼 → 被点名者在他人回合自爆：翻牌死亡、白天直接入夜。"""
    runner = make_runner(tmp_path, ClaimExplodeLlm())
    game = _day_game([Role.VILLAGER, Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.SEER, Role.WITCH])
    runner.store.save_game(game)

    await asyncio.wait_for(runner._phase_day_speech(game), 10)

    assert game.day == 2 and game.phase == Phase.NIGHT_GUARD
    w3 = game.player(3)
    assert not w3.alive and w3.role_revealed and w3.role == Role.WEREWOLF
    assert any(e.type == "wolf_explode" and e.actor == 3 for e in game.events)
    # 触发后当天不再有后续发言（1 号真人没轮到）
    assert not any(e.type == "speech" and e.actor != 2 for e in game.events)


@pytest.mark.asyncio
async def test_claim_trigger_declined_continues_day(tmp_path):
    """被点名狼判定非实锤不自爆 → 白天正常走完进入投票。"""
    runner = make_runner(tmp_path, ClaimExplodeLlm(explode_on_trigger=False))
    game = _day_game([Role.VILLAGER, Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.SEER, Role.WITCH])
    runner.store.save_game(game)

    task = asyncio.create_task(runner._phase_day_speech(game))
    # 真人发言回合到达时喂一条无查杀的发言
    for _ in range(300):
        await asyncio.sleep(0.01)
        info = runner.pending_info(game.id)
        if info and info["kind"] == "speech":
            ok, msg = runner.submit(game.id, {"kind": "speech", "text": "大家好，我先听一听。"})
            assert ok, msg
            break
    await asyncio.wait_for(task, 10)

    assert game.phase == Phase.VOTE
    assert not any(e.type == "wolf_explode" for e in game.events)
    assert all(game.player(s).alive for s in (2, 3, 4, 5, 6))


@pytest.mark.asyncio
async def test_claim_two_wolves_think_in_parallel(tmp_path):
    """发言同时点名两只狼：两只同时思考（时间区间重叠），先决意者（同批取座位小者）结算。"""
    llm = ParallelExplodeLlm()
    runner = make_runner(tmp_path, llm)
    game = _day_game([Role.VILLAGER, Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.WEREWOLF, Role.SEER])
    runner.store.save_game(game)

    await asyncio.wait_for(runner._phase_day_speech(game), 10)

    assert sorted(llm.spans.keys()) == [3, 5]  # 两只被点名狼都被询问
    (a0, a1), (b0, b1) = llm.spans[3], llm.spans[5]
    assert a0 < b1 and b0 < a1  # 思考区间重叠 = 并行而非串行
    w3, w5 = game.player(3), game.player(5)
    assert not w3.alive and w3.role_revealed and w3.role == Role.WEREWOLF  # 先决意者结算
    assert w5.alive and not w5.role_revealed
    assert game.phase == Phase.NIGHT_GUARD and game.day == 2
    assert any(e.type == "wolf_explode" and e.actor == 3 for e in game.events)


@pytest.mark.asyncio
async def test_claim_two_wolves_only_one_explodes(tmp_path):
    """两只被点名狼同时思考，仅一只决意自爆：它结算，另一只继续存活。"""
    llm = ParallelExplodeLlm(explode_seats={5}, delay=0.05)
    runner = make_runner(tmp_path, llm)
    game = _day_game([Role.VILLAGER, Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.WEREWOLF, Role.SEER])
    runner.store.save_game(game)

    await asyncio.wait_for(runner._phase_day_speech(game), 10)

    assert sorted(llm.spans.keys()) == [3, 5]
    w3, w5 = game.player(3), game.player(5)
    assert w3.alive and not w3.role_revealed
    assert not w5.alive and w5.role_revealed and w5.role == Role.WEREWOLF
    assert any(e.type == "wolf_explode" and e.actor == 5 for e in game.events)
    assert game.phase == Phase.NIGHT_GUARD


def test_thinking_signal_view_filter():
    """思考占位信号按 kind 放行：公开行动全员可见，私密行动（夜间行动 + 狼自爆决策）屏蔽，内容流只对上帝视角。"""
    from app.routes.stream import _PRIVATE_THINKING_KINDS, _passes_view

    vis_cache: dict = {}
    for kind in ("vote", "speech", "last_words", "sheriff_run", "badge_pass"):
        assert _passes_view(None, "thinking_start", {"kind": kind}, False, 1, vis_cache)
        assert _passes_view(None, "thinking_end", {"kind": kind}, False, 1, vis_cache)
    # self_explode 只有狼有：不屏蔽的话玩家看到「连续两次思考」等于自曝狼身份
    assert "self_explode" in _PRIVATE_THINKING_KINDS
    for kind in _PRIVATE_THINKING_KINDS:
        assert not _passes_view(None, "thinking_start", {"kind": kind}, False, 1, vis_cache)
        assert not _passes_view(None, "thinking_end", {"kind": kind}, False, 1, vis_cache)
    assert not _passes_view(None, "thinking_delta", {"kind": "vote", "text": "推理内容"}, False, 1, vis_cache)
    for ev in ("thinking_start", "thinking_delta", "thinking_end"):
        assert _passes_view(None, ev, {"kind": "seer_check"}, True, 1, vis_cache)


@pytest.mark.asyncio
async def test_speech_thinking_placeholder_flow(tmp_path):
    """发言阶段广播无内容思考占位：AI 开口前 thinking_start、正文出现后 thinking_end，成对收口。"""
    roles = [Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH]
    players = [Player(seat=i, name=f"P{i}", role=r, is_human=False, preset_id="p1") for i, r in enumerate(roles, start=1)]
    board = BoardConfig(id="t", name="测试板", win_condition=WinCondition.CITY, roles=roles)
    game = Game(id="think-flow", created_at="", board=board, players=players, seed=1)
    game.day = 1
    game.phase = Phase.DAY_SPEECH
    game.phase_ctx = {"order": [1, 2, 3, 4, 5, 6], "i": 0}
    runner = make_runner(tmp_path)
    runner.store.save_game(game)

    q = runner.hub.subscribe(game.id)
    await asyncio.wait_for(runner._phase_day_speech(game), 10)

    transients = []
    while not q.empty():
        transients.append(q.get_nowait())
    runner.hub.unsubscribe(game.id, q)

    starts = [d for ev, d in transients if ev == "thinking_start"]
    ends = [d for ev, d in transients if ev == "thinking_end"]
    speech_seats = {d["seat"] for ev, d in transients if ev == "speech_start"}
    # 6 人各一次发言占位（狼另有自爆决策占位，只多不少），且全部收口
    assert len(starts) >= 6 and len(ends) == len(starts)
    assert speech_seats == {d["seat"] for d in ends}
    # 每个座位：先收思考占位，后开发言卡
    first_speech: dict[int, int] = {}
    first_end: dict[int, int] = {}
    for i, (ev, d) in enumerate(transients):
        if ev == "speech_start" and d["seat"] not in first_speech:
            first_speech[d["seat"]] = i
        if ev == "thinking_end" and d["seat"] not in first_end:
            first_end[d["seat"]] = i
    assert all(first_end[s] < first_speech[s] for s in first_speech)
    # 固化思考带内容（god 可见），不产生空思考事件
    think = [e for e in game.events if e.type == "thinking"]
    assert think and all(e.payload.get("text") for e in think)


@pytest.mark.asyncio
async def test_summary_generate_and_persist(tmp_path):
    """结束后后台任务生成复盘总结：持久化、复盘载荷可读、重新生成覆盖、并发与非法请求拒绝。"""
    from app.engine.board import BUILTIN_BOARDS
    from app.llm import prompts
    from app.routes.games import review_payload

    runner = make_runner(tmp_path)
    game = runner.create_game(BUILTIN_BOARDS[1], make_personas(8), human_name="玩家", human_avatar="🙂")
    await runner.start(game.id)
    finished = await auto_play(runner, game.id)
    assert finished.phase == Phase.GAME_OVER

    # 提示词包含底牌与上帝视角事件流
    msgs = prompts.summary_messages(finished)
    assert len(msgs) == 2 and "身份底牌" in msgs[1]["content"]

    # 后台任务：启动立即返回，等待任务完成校验持久化
    ok, _ = runner.start_summary(game.id, "p1")
    assert ok and runner.summary_status(game.id) == "running"
    await runner.summary_jobs[game.id]
    saved = runner.store.get_game(game.id)
    assert saved.summary and saved.summary_model == "测试模型"
    assert review_payload(saved)["summary"] == saved.summary

    # 生成进行中拒绝重复启动
    blocker = asyncio.Event()
    fake_job = asyncio.create_task(blocker.wait())
    runner.summary_jobs[game.id] = fake_job
    ok_dup, _ = runner.start_summary(game.id, "p1")
    assert not ok_dup
    blocker.set()
    await fake_job
    runner.summary_jobs.pop(game.id, None)  # 占位任务无 done_callback，手动清理

    # 重新生成覆盖旧总结
    ok2, _ = runner.start_summary(game.id, "p1")
    assert ok2
    await runner.summary_jobs[game.id]
    assert runner.store.get_game(game.id).summary

    # 不存在的对局拒绝生成
    ok3, _ = runner.start_summary("no-such-game", "p1")
    assert not ok3


@pytest.mark.asyncio
async def test_explode_sheriff_badge_flow(tmp_path):
    """自爆的是警长：走警徽移交流程后再入夜。"""
    runner = make_runner(tmp_path, ExplodeLlm())
    game = _day_game(
        [Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH],
        gid="badge-flow",
        sheriff_seat=2,
    )
    runner.store.save_game(game)
    await runner._phase_day_speech(game)

    assert game.phase == Phase.NIGHT_GUARD
    exp = next(e for e in game.events if e.type == "wolf_explode")
    assert exp.actor == 2
    bp = next(e for e in game.events if e.type == "badge_pass")
    assert bp.payload.get("from") == 2
    assert game.sheriff_seat != 2


def _election_game(roles: list[Role], gid: str = "swallow", swallow: str = "single", revote: bool = False) -> Game:
    """手工构造警长竞选阶段的对局（全 AI，无真人）：1、3、5 号狼，单数位上警。"""
    players = [Player(seat=i, name=f"P{i}", role=r, is_human=False, preset_id="p1") for i, r in enumerate(roles, start=1)]
    board = BoardConfig(id="t", name="测试板", win_condition=WinCondition.CITY, roles=roles)
    game = Game(id=gid, created_at="", board=board, players=players, seed=1)
    game.day = 1
    game.sheriff_enabled = True
    game.badge_swallow = swallow
    game.sheriff_revote = revote
    game.phase = Phase.SHERIFF_ELECTION
    game.phase_ctx = {"stage": "declare", "runs": {}, "candidates": [], "round": 1, "votes": {}}
    return game


# 三狼配置：双爆两枪打完仍留一狼，末狼不再自爆的闸门不干扰吞警徽机制验证
_SWALLOW_ROLES = [
    Role.WEREWOLF,
    Role.VILLAGER,
    Role.WEREWOLF,
    Role.VILLAGER,
    Role.WEREWOLF,
    Role.SEER,
    Role.WITCH,
]


@pytest.mark.asyncio
async def test_campaign_single_swallow(tmp_path):
    """单爆吞警徽：狼人警上自爆，竞选终止、警徽流失、直接入夜，之后不再有竞选。"""
    runner = make_runner(tmp_path, SheriffSwallowLlm())
    game = _election_game(_SWALLOW_ROLES, gid="single-swallow", swallow="single")
    runner.store.save_game(game)
    await asyncio.wait_for(runner._phase_sheriff_election(game), 10)

    assert game.phase == Phase.NIGHT_GUARD and game.day == 2
    w1 = game.player(1)
    assert not w1.alive and w1.role_revealed and w1.role == Role.WEREWOLF
    exp = next(e for e in game.events if e.type == "wolf_explode")
    assert exp.actor == 1 and exp.payload.get("mode") == "single" and exp.payload.get("where") == "sheriff"
    assert not game.sheriff_revote and game.sheriff_seat is None
    assert not any(e.type == "sheriff_result" for e in game.events), "竞选被自爆终止，不产生结果事件"
    # 被打断的候选人没有发出竞选宣言
    assert not any(e.type == "sheriff_speech" for e in game.events)
    assert game.id not in runner.explode_futures


@pytest.mark.asyncio
async def test_campaign_double_swallow(tmp_path):
    """双爆吞警徽：第一步自爆后入夜且次日重选；重选窗口再爆才彻底吞掉警徽。"""
    runner = make_runner(tmp_path, SheriffSwallowLlm())
    game = _election_game(_SWALLOW_ROLES, gid="double-swallow", swallow="double")
    runner.store.save_game(game)

    # 第一步：day 1 竞选被 1 号狼终止
    await asyncio.wait_for(runner._phase_sheriff_election(game), 10)
    exps = [e for e in game.events if e.type == "wolf_explode"]
    assert len(exps) == 1 and exps[0].payload.get("mode") == "double_first"
    assert game.sheriff_revote and game.day == 2 and game.phase == Phase.NIGHT_GUARD
    # day-advance 路由条件成立：次日会重进竞选
    assert game.sheriff_enabled and (game.day == 1 or game.sheriff_revote)

    # 第二步：day 2 重新竞选，重选窗口狼再爆一次
    game.phase = Phase.SHERIFF_ELECTION
    game.phase_ctx = {"stage": "declare", "runs": {}, "candidates": [], "round": 1, "votes": {}}
    runner.store.save_game(game)
    await asyncio.wait_for(runner._phase_sheriff_election(game), 10)

    exps = [e for e in game.events if e.type == "wolf_explode"]
    assert len(exps) == 2 and exps[1].payload.get("mode") == "double_second"
    assert not game.sheriff_revote, "第二步后不再重选"
    assert game.day == 3 and game.phase == Phase.NIGHT_GUARD
    assert game.sheriff_seat is None
    assert not any(e.type == "sheriff_result" for e in game.events)


@pytest.mark.asyncio
async def test_double_swallow_without_second_explode(tmp_path):
    """双爆只爆第一步：次日重选正常走完并选出警长，revote 清除、不再重选。"""
    runner = make_runner(tmp_path, FirstOnlySwallowLlm())
    game = _election_game(_SWALLOW_ROLES, gid="double-once", swallow="double")
    runner.store.save_game(game)

    await asyncio.wait_for(runner._phase_sheriff_election(game), 10)
    exps = [e for e in game.events if e.type == "wolf_explode"]
    assert len(exps) == 1 and exps[0].payload.get("mode") == "double_first"
    assert game.day == 2 and game.sheriff_revote

    game.phase = Phase.SHERIFF_ELECTION
    game.phase_ctx = {"stage": "declare", "runs": {}, "candidates": [], "round": 1, "votes": {}}
    runner.store.save_game(game)
    await asyncio.wait_for(runner._phase_sheriff_election(game), 10)

    assert game.phase == Phase.DAY_SPEECH, "无人再爆：重选完成进入白天发言"
    result = next(e for e in game.events if e.type == "sheriff_result")
    assert not result.payload.get("destroyed") and result.payload["sheriff"] == 3
    assert game.sheriff_seat == 3 and not game.sheriff_revote
    assert any(e.type == "sheriff_speech" for e in game.events), "重选中候选人正常发表宣言"


@pytest.mark.asyncio
async def test_campaign_claim_triggers_swallow(tmp_path):
    """竞选宣言报查杀点名 AI 狼 → 被点名者回合外警上自爆（吞警徽结算）。"""
    runner = make_runner(tmp_path, ClaimExplodeLlm())
    game = _election_game(
        [Role.SEER, Role.VILLAGER, Role.WEREWOLF, Role.VILLAGER, Role.WEREWOLF, Role.WITCH],
        gid="campaign-claim",
        swallow="single",
    )
    runner.store.save_game(game)
    await asyncio.wait_for(runner._phase_sheriff_election(game), 10)

    assert game.phase == Phase.NIGHT_GUARD and game.day == 2
    exp = next(e for e in game.events if e.type == "wolf_explode")
    assert exp.actor == 3 and exp.payload.get("mode") == "single"
    w3 = game.player(3)
    assert not w3.alive and w3.role_revealed and w3.role == Role.WEREWOLF


@pytest.mark.asyncio
async def test_campaign_explode_gating(tmp_path):
    """警上自爆入口门禁：未开吞警徽规则拒绝；规则开启但无等待窗口（不在发言间隙）拒绝。"""
    runner = make_runner(tmp_path)
    game = _election_game(_SWALLOW_ROLES, gid="gate", swallow="none")
    game.player(1).is_human = True
    game.player(1).preset_id = ""
    runner.store.save_game(game)

    ok, msg = runner.request_explode(game.id)
    assert not ok and "吞警徽" in msg

    game.badge_swallow = "double"
    runner.store.save_game(game)
    ok, msg = runner.request_explode(game.id)
    assert not ok and "当前不能自爆" in msg


@pytest.mark.asyncio
async def test_human_wolf_explode_during_campaign(tmp_path):
    """真人狼在竞选发言中途按钮自爆：打断 AI 候选人宣言，按双爆第一步结算入夜。"""
    gate = asyncio.Event()
    runner = make_runner(tmp_path, GatedSpeechLlm(gate))
    game = _election_game(_SWALLOW_ROLES, gid="human-swallow", swallow="double")
    game.player(1).is_human = True
    game.player(1).preset_id = ""
    runner.store.save_game(game)

    task = asyncio.create_task(runner._phase_sheriff_election(game))
    # 报名阶段：真人不上警，放行 3、5 号 AI 候选人进入宣言发言
    for _ in range(500):
        await asyncio.sleep(0.01)
        info = runner.pending_info(game.id)
        if info and info["kind"] == "sheriff_run":
            ok, msg = runner.submit(game.id, {"kind": "sheriff_run", "run": False})
            assert ok, msg
            break
    # 等 3 号进入竞选宣言（发言挂起、自爆窗口打开）
    for _ in range(500):
        await asyncio.sleep(0.01)
        if game.id in runner.explode_futures and runner.pending_info(game.id) is None:
            break
    ok, msg = runner.request_explode(game.id)
    assert ok, msg
    await asyncio.wait_for(task, 5)

    assert game.phase == Phase.NIGHT_GUARD and game.day == 2
    human = game.player(1)
    assert not human.alive and human.role_revealed and human.role == Role.WEREWOLF
    exp = next(e for e in game.events if e.type == "wolf_explode")
    assert exp.actor == 1 and exp.payload.get("mode") == "double_first"
    assert game.sheriff_revote and game.sheriff_seat is None
    assert not any(e.type == "sheriff_speech" for e in game.events), "AI 候选人宣言被打断未固化"
    assert game.id not in runner.explode_futures
