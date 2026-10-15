"""游戏主循环：状态机推进 + 真人输入等待（Future）+ AI 调用与兜底。

每个 phase 处理器都必须可重入：进度记录在 game.phase_ctx 中，
进程崩溃/重启后从持久化状态恢复，跳过已完成的部分继续。
"""
from __future__ import annotations

import asyncio
import random
import re
import sys
import traceback
import uuid
from datetime import datetime

from .engine.game import (
    apply_exile,
    apply_self_explode,
    check_win,
    emit,
    enter_night,
    finish,
    resolve_night_deaths,
    sheriff_weighted_counts,
    start_game,
    tally_wolf_votes,
)
from .engine.store import Store
from .events import GameHub
from .llm.client import LlmClient, LlmError, LlmFatalError
from .llm import prompts
from .models import (
    AiPersona,
    BadgeDecision,
    BoardConfig,
    Game,
    GameEvent,
    HunterDecision,
    ModelPreset,
    Phase,
    Player,
    Role,
    GuardProtect,
    SeerCheck,
    SelfExplodeDecision,
    SheriffRun,
    SheriffVote,
    SpeechOrderChoice,
    VoteDecision,
    WitchPoison,
    WitchSave,
    WolfProposal,
    GOD_ROLES,
    ROLE_LABELS,
    WinCondition,
)

INPUT_KINDS = {
    "speech",
    "last_words",
    "vote",
    "wolf_vote",
    "seer_check",
    "witch_save",
    "witch_poison",
    "hunter",
    "guard",
    "sheriff_run",
    "sheriff_vote",
    "badge_pass",
    "speech_order",
}

# 需要 target 的输入类型（badge_pass 撕毁时不需要 target）
TARGET_KINDS = {"vote", "wolf_vote", "seer_check", "witch_poison", "hunter", "guard", "sheriff_vote", "badge_pass", "speech_order"}

# 查杀/验狼句式：用于检测发言是否把某座位报成狼（保守匹配，宁漏勿误）
_CLAIM_RE = re.compile(
    r"查杀"  # 查杀点名
    r"|验.{0,6}狼"  # 验出是狼
    r"|(?<![不])(?:是狼|像狼)"  # 直接/概率指狼（避开「不是狼/不像狼」）
    r"|狼面|悍跳|聊爆|爆狼"  # 狼面指控与暴露判定
    r"|归票|放逐|票死|冲票"  # 淘汰指向（建议归票/放逐的目标）
    r"|(?<![别不])(?:出他|投他)"  # 口语淘汰指向（避开「别投他/不投他」）
)


def _claimed_wolf_seats(text: str) -> set[int]:
    """发言文本中疑似被施压的座位号：座位号前后 12 字内出现查杀/验狼、指狼（是狼/像狼/狼面）、
    悍跳/聊爆/爆狼、归票/放逐等淘汰指向即命中。

    覆盖面从「铁查杀」放宽到一般施压句式——误触发无害（狼按实锤纪律答 false，只多一次小决策调用），
    宁可多给真被围剿的狼一次自爆权衡机会。"""
    seats: set[int] = set()
    for m in re.finditer(r"(\d{1,2})号", text):
        if _CLAIM_RE.search(text[max(0, m.start() - 12): m.end() + 12]):
            n = int(m.group(1))
            if n >= 1:
                seats.add(n)
    return seats


def speech_text(game: Game, seat: int, ev_type: str = "speech") -> str:
    """取该玩家最近一条指定类型发言的文本（查杀触发检测用）。"""
    for e in reversed(game.events):
        if e.type == ev_type and e.actor == seat:
            return str(e.payload.get("text") or "")
    return ""


class GameFatalStop(Exception):
    """对局终止信号：LLM 失败暂停后用户选择终止（对局已按故障收尾），_run 捕获后静默退出。"""


class PendingInput:
    def __init__(self, kind: str, allowed: list[int], future: asyncio.Future):
        self.kind = kind
        self.allowed = allowed
        self.future = future


class GameRunner:
    def __init__(self, store: Store, hub: GameHub, llm: LlmClient):
        self.store = store
        self.hub = hub
        self.llm = llm
        self.tasks: dict[str, asyncio.Task] = {}
        self.pending: dict[str, PendingInput] = {}
        # LLM 失败暂停态：gid -> (用户选择 future, 报错信息)；前端弹窗经 resume_llm 唤醒
        self.paused: dict[str, tuple[asyncio.Future, str]] = {}
        self.explode_futures: dict[str, asyncio.Future] = {}
        self.summary_jobs: dict[str, asyncio.Task] = {}
        self.summary_error: dict[str, str] = {}

    # ---------- 查询 ----------

    def get_game(self, gid: str) -> Game | None:
        task = self.tasks.get(gid)
        if task is not None:
            ctx_game = getattr(task, "_game", None)
            if ctx_game is not None:
                return ctx_game
        return self.store.get_game(gid)

    def is_active(self, gid: str) -> bool:
        t = self.tasks.get(gid)
        return t is not None and not t.done()

    def pending_info(self, gid: str) -> dict | None:
        p = self.pending.get(gid)
        if p is None:
            return None
        return {"kind": p.kind, "allowed": p.allowed}

    def paused_info(self, gid: str) -> dict | None:
        entry = self.paused.get(gid)
        if entry is None:
            return None
        return {"message": entry[1]}

    # ---------- 生命周期 ----------

    def create_game(
        self,
        board: BoardConfig,
        personas: list[AiPersona],
        human_name: str = "",
        human_avatar: str = "",
        mode: str = "normal",
        human_role: str | None = None,
        sheriff: bool = False,
        badge_swallow: str = "none",
    ) -> Game:
        n = len(board.roles)
        if mode == "god":
            if len(personas) != n:
                raise ValueError(f"观战模式需要 {n} 名 AI 玩家，当前选择了 {len(personas)} 名")
        elif len(personas) != n - 1:
            raise ValueError(f"该板子需要 {n - 1} 名 AI 玩家，当前选择了 {len(personas)} 名")
        seed = random.SystemRandom().randrange(1 << 30)
        game_id = uuid.uuid4().hex[:12]
        seats = list(range(1, n + 1))
        random.Random(seed).shuffle(seats)
        players: list[Player] = []
        for i, seat in enumerate(seats):
            if mode != "god" and i == 0:
                players.append(Player(seat=seat, name=human_name.strip() or "你", avatar=human_avatar or "🙂", is_human=True))
            else:
                ap = personas[i - 1 if mode != "god" else i]
                players.append(Player(seat=seat, name=ap.name, avatar=ap.avatar, persona=ap.persona, preset_id=ap.preset_id))
        players.sort(key=lambda p: p.seat)
        game = Game(
            id=game_id,
            created_at=datetime.now().isoformat(timespec="seconds"),
            board=board,
            players=players,
            seed=seed,
            mode="god" if mode == "god" else "normal",
            preferred_role=Role(human_role) if human_role else None,
            sheriff_enabled=bool(sheriff),
            badge_swallow=badge_swallow if sheriff else "none",
        )
        self.store.save_game(game)
        return game

    async def start(self, gid: str) -> tuple[bool, str]:
        game = self.store.get_game(gid)
        if game is None:
            return False, "游戏不存在"
        if game.phase != Phase.LOBBY:
            return False, "游戏已经开始"
        since = game.event_count
        start_game(game)
        self._publish_since(game, since)
        self.store.save_game(game)
        self._spawn(gid, game)
        return True, "ok"

    async def resume(self, gid: str) -> tuple[bool, str]:
        game = self.store.get_game(gid)
        if game is None:
            return False, "游戏不存在"
        if game.phase == Phase.GAME_OVER or game.phase == Phase.LOBBY:
            return False, "游戏不在进行中"
        if self.is_active(gid):
            return True, "已在运行"
        self._spawn(gid, game)
        return True, "ok"

    async def abort(self, gid: str, reason: str = "房主中断了游戏") -> tuple[bool, str]:
        """中断进行中的对局：停掉主循环，按和局收尾并固化存档。"""
        game = self.get_game(gid)
        if game is None:
            return False, "游戏不存在"
        if game.phase == Phase.GAME_OVER:
            return False, "游戏已结束"
        if game.phase == Phase.LOBBY:
            return False, "游戏尚未开始"
        task = self.tasks.pop(gid, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        pend = self.pending.pop(gid, None)
        if pend is not None and not pend.future.done():
            pend.future.cancel()
        paused = self.paused.pop(gid, None)
        if paused is not None and not paused[0].done():
            paused[0].cancel()
        self.explode_futures.pop(gid, None)
        self._call_engine(game, finish, "draw", reason)
        return True, "ok"

    async def resume_llm(self, gid: str, action: str) -> tuple[bool, str]:
        """暂停弹窗的用户选择：retry 重跑失败的 LLM 调用，abort 按故障终止对局。"""
        if action not in ("retry", "abort"):
            return False, "未知操作"
        entry = self.paused.get(gid)
        if entry is not None:
            if not entry[0].done():
                entry[0].set_result(action)
            return True, "ok"
        # 进程重启后暂停态丢失：retry 等价于续跑主循环（失败动作会重新执行），abort 走中断收尾
        game = self.get_game(gid)
        if game is None or game.phase == Phase.GAME_OVER:
            return False, "对局不在暂停状态"
        if action == "abort":
            return await self.abort(gid, "用户终止了对局")
        return await self.resume(gid)

    def request_explode(self, gid: str) -> tuple[bool, str]:
        """真人狼玩家申请自爆：白天发言阶段任意时刻；吞警徽规则下警长竞选发言阶段也可。"""
        game = self.get_game(gid)
        if game is None:
            return False, "游戏不存在"
        if game.phase == Phase.SHERIFF_ELECTION:
            if game.badge_swallow == "none":
                return False, "本局未开启吞警徽规则，警上不能自爆"
        elif game.phase != Phase.DAY_SPEECH:
            return False, "只能在白天发言阶段或警上竞选发言阶段自爆"
        h = game.human()
        if h is None or not h.alive or h.role != Role.WEREWOLF:
            return False, "只有存活的狼人玩家可以自爆"
        fut = self.explode_futures.get(gid)
        if fut is None or fut.done():
            return False, "当前不能自爆"
        fut.set_result(True)
        return True, "ok"

    def start_summary(self, gid: str, preset_id: str) -> tuple[bool, str]:
        """后台任务生成复盘分析：请求立即返回，切页/断开连接都不会中断生成。"""
        game = self.store.get_game(gid)
        if game is None:
            return False, "游戏不存在"
        if game.phase != Phase.GAME_OVER:
            return False, "对局结束后才能生成复盘分析"
        if gid in self.summary_jobs:
            return False, "已有分析在后台生成中"
        preset = next((p for p in self.store.load_settings().presets if p.id == preset_id), None)
        if preset is None:
            return False, "模型预设不存在"
        self.summary_error.pop(gid, None)
        task = asyncio.create_task(self._run_summary(gid, preset))
        self.summary_jobs[gid] = task
        task.add_done_callback(lambda _t: self.summary_jobs.pop(gid, None))
        return True, "已开始后台生成"

    async def _run_summary(self, gid: str, preset) -> None:
        game = self.store.get_game(gid)
        if game is None:
            return
        try:
            chunks: list[str] = []
            async for delta in self.llm.speak(preset, prompts.summary_messages(game)):
                chunks.append(delta)
            text = "".join(chunks).strip()
            if not text:
                self.summary_error[gid] = "模型没有返回内容"
                return
            game.summary = text
            game.summary_model = preset.name
            self.store.save_game(game)
        except LlmFatalError as e:
            self.summary_error[gid] = f"生成失败：{e}"
        except LlmError as e:
            self.summary_error[gid] = f"生成失败：{e}"

    def summary_status(self, gid: str) -> str:
        return "running" if gid in self.summary_jobs else "idle"

    def _spawn(self, gid: str, game: Game) -> None:
        task = asyncio.get_running_loop().create_task(self._run(gid, game))
        task._game = game  # type: ignore[attr-defined]
        self.tasks[gid] = task

    def submit(self, gid: str, payload: dict) -> tuple[bool, str]:
        pend = self.pending.get(gid)
        if pend is None or pend.future.done():
            return False, "当前没有等待你的操作"
        kind = payload.get("kind")
        if kind != pend.kind:
            return False, f"当前等待的操作类型是 {pend.kind}"
        if kind in ("speech", "last_words"):
            text = str(payload.get("text") or "").strip()
            if not text:
                return False, "内容不能为空"
            result: dict = {"text": text[:600]}
        elif kind == "witch_save":
            result = {"use": bool(payload.get("use"))}
        elif kind == "witch_poison":
            use = bool(payload.get("use"))
            target = int(payload.get("target") or 0)
            if use and target not in pend.allowed:
                return False, "目标不合法"
            result = {"use": use, "target": target if use else 0}
        elif kind == "hunter":
            shoot = bool(payload.get("shoot"))
            target = int(payload.get("target") or 0)
            if shoot and target not in pend.allowed:
                return False, "目标不合法"
            result = {"shoot": shoot, "target": target if shoot else 0}
        elif kind == "sheriff_run":
            result = {"run": bool(payload.get("run"))}
        elif kind == "badge_pass":
            destroy = bool(payload.get("destroy"))
            target = int(payload.get("target") or 0)
            if not destroy and target not in pend.allowed:
                return False, "目标不合法"
            result = {"destroy": destroy, "target": target if not destroy else 0}
        elif kind == "speech_order":
            direction = str(payload.get("direction") or "")
            if direction not in ("left", "right"):
                return False, "方向必须是 left 或 right"
            result = {"direction": direction}
        else:
            target = int(payload.get("target") or 0)
            may_abstain = kind in ("vote", "sheriff_vote")
            if (target != 0 or not may_abstain) and target not in pend.allowed:
                return False, "目标不合法"
            result = {"target": target}
        pend.future.set_result(result)
        self.hub.publish(gid, "input_accepted", {"kind": kind})
        return True, "ok"

    # ---------- 主循环 ----------

    async def _run(self, gid: str, game: Game) -> None:
        try:
            while game.phase != Phase.GAME_OVER:
                handler = _PHASE_HANDLERS[game.phase]
                await handler(self, game)
                if game.phase == Phase.GAME_OVER:
                    break
                winner = check_win(game)
                if winner:
                    self._call_engine(game, finish, winner, self._win_reason(game, winner))
                    break
                if game.phase == Phase.NIGHT_WOLF and game.day > game.board.max_days:
                    self._call_engine(game, finish, "draw", f"达到天数上限（{game.board.max_days} 天）")
                    break
        except asyncio.CancelledError:
            raise
        except GameFatalStop:
            pass
        except Exception:
            traceback.print_exc(file=sys.stderr)
            try:
                self.hub.publish(gid, "error", {"message": "游戏引擎内部错误，已暂停。可尝试「继续游戏」恢复。"})
            except Exception:
                pass
        finally:
            self.tasks.pop(gid, None)
            self.explode_futures.pop(gid, None)
            pend = self.pending.pop(gid, None)
            if pend is not None and not pend.future.done():
                pend.future.cancel()
            paused = self.paused.pop(gid, None)
            if paused is not None and not paused[0].done():
                paused[0].cancel()

    def _win_reason(self, game: Game, winner: str) -> str:
        if winner == "good":
            return "所有狼人都已出局"
        alive_good = [p for p in game.alive_players() if p.role != Role.WEREWOLF]
        if game.board.win_condition == WinCondition.CITY:
            return "存活狼人数量已追平好人（屠城达成）"
        gods = [p for p in alive_good if p.role in GOD_ROLES]
        if not gods:
            return "神职阵营全灭（屠边达成）"
        return "村民阵营全灭（屠边达成）"

    # ---------- 阶段处理器 ----------

    async def _phase_night_wolf(self, game: Game) -> None:
        ctx = game.phase_ctx
        votes: dict = ctx.setdefault("wolf_votes", {})
        if not ctx.get("announced"):
            self._emit(game, "host_line", {"text": "狼人请睁眼，正在选择今晚的猎物……"})
            ctx["announced"] = True
            self._save(game)
        wolves = game.alive_wolves()
        alive_seats = [p.seat for p in game.alive_players()]
        human_wolf = next((w for w in wolves if w.is_human), None)

        # 真人狼的输入通道与 AI 决策并行开放：AI 狼不被玩家阻塞，玩家也可等队友亮票后再拍板
        human_fut = None
        if human_wolf is not None and str(human_wolf.seat) not in votes:
            human_fut = self._open_human(
                game, "wolf_vote", alive_seats, note="狼人时间：你的这一票就是今晚的刀口（可自刀）；可以等队友先亮票再定"
            )

        async def ai_vote(w: Player) -> None:
            key = str(w.seat)
            if key in votes:
                return
            d = await self._decide(game, w, WolfProposal, prompts.wolf_proposal_messages(game, w), alive_seats, "wolf_vote")
            votes[key] = {"target": d.target, "reason": d.reason}
            self._emit(
                game,
                "wolf_proposal",
                {"target": d.target, "reason": d.reason},
                actor=w.seat,
                visibility="wolves",
            )
            ctx["wolf_votes"] = votes
            self._save(game)

        ai_tasks = [asyncio.create_task(ai_vote(w)) for w in wolves if not w.is_human]
        try:
            if human_fut is not None:
                try:
                    ans = await human_fut
                finally:
                    pend = self.pending.get(game.id)
                    if pend is not None and pend.future is human_fut:
                        self.pending.pop(game.id, None)
                # 与 AI 狼同构：真人提议也要落事件，队友 prompt / 上帝视角 / 复盘才能看到
                votes[str(human_wolf.seat)] = {"target": int(ans["target"]), "reason": ""}
                self._emit(
                    game,
                    "wolf_proposal",
                    {"target": int(ans["target"]), "reason": ""},
                    actor=human_wolf.seat,
                    visibility="wolves",
                )
                ctx["wolf_votes"] = votes
                self._save(game)
            if ai_tasks:
                await asyncio.gather(*ai_tasks)
        except BaseException:
            for t in ai_tasks:
                t.cancel()
            await asyncio.gather(*ai_tasks, return_exceptions=True)
            raise

        if human_wolf is not None:
            # 真人狼一票定刀口：无论 AI 队友投谁，最终按真人的选择执行
            target = votes[str(human_wolf.seat)]["target"]
            randomized = False
        else:
            target, randomized = tally_wolf_votes(votes, game.seed, game.day)
        self._emit(game, "wolf_target", {"target": target, "randomized": randomized}, visibility="wolves")
        ctx["knife"] = target
        self._advance(game, Phase.NIGHT_SEER, ctx)

    async def _phase_night_guard(self, game: Game) -> None:
        ctx = game.phase_ctx
        # 流程播报只看板子组成、不看死活：跳过环节等于向全场宣告该角色已出局，属场外信息
        if not ctx.get("guard_announced"):
            if any(p.role == Role.GUARD for p in game.players):
                self._emit(game, "host_line", {"text": "守卫请睁眼，正在决定守护对象……"})
            ctx["guard_announced"] = True
            self._save(game)
        guards = [p for p in game.alive_players() if p.role == Role.GUARD]
        if not guards:
            self._advance(game, Phase.NIGHT_WOLF, ctx)
            return
        guard = guards[0]
        if not ctx.get("guard_done"):
            last = next(
                (e.payload["target"] for e in reversed(game.events) if e.type == "guard_protect" and e.day == game.day - 1),
                None,
            )
            candidates = [p.seat for p in game.alive_players() if p.seat != last]
            if guard.is_human:
                note = "守卫时间：选择今晚要守护的玩家（可以守自己，但不能连守同一人）"
                ans = await self._wait_human(game, "guard", candidates, note=note)
                target = int(ans["target"])
            else:
                d = await self._decide(game, guard, GuardProtect, prompts.guard_messages(game, guard, candidates), candidates, "guard")
                target = d.target
            ctx["guard"] = target
            self._emit(game, "guard_protect", {"target": target}, actor=guard.seat, visibility=f"seat:{guard.seat}")
            ctx["guard_done"] = True
            self._save(game)
        self._advance(game, Phase.NIGHT_WOLF, ctx)

    async def _phase_night_seer(self, game: Game) -> None:
        ctx = game.phase_ctx
        # 播报只看板子组成、不看死活，理由同守卫
        if not ctx.get("seer_announced"):
            if any(p.role == Role.SEER for p in game.players):
                self._emit(game, "host_line", {"text": "预言家请睁眼，正在查验玩家……"})
            ctx["seer_announced"] = True
            self._save(game)
        seers = [p for p in game.alive_players() if p.role == Role.SEER]
        if not seers:
            self._advance(game, Phase.NIGHT_WITCH_SAVE, ctx)
            return
        seer = seers[0]
        if not ctx.get("seer_done"):
            candidates = [p.seat for p in game.alive_players() if p.seat != seer.seat]
            if seer.is_human:
                ans = await self._wait_human(game, "seer_check", candidates, note="预言家时间：选择要查验的玩家")
                target = int(ans["target"])
            else:
                d = await self._decide(game, seer, SeerCheck, prompts.seer_messages(game, seer, candidates), candidates, "seer_check")
                target = d.target
            is_wolf = game.player(target).role == Role.WEREWOLF
            self._emit(game, "seer_result", {"target": target, "is_wolf": is_wolf}, actor=seer.seat, visibility=f"seat:{seer.seat}")
            ctx["seer_done"] = True
            self._save(game)
        self._advance(game, Phase.NIGHT_WITCH_SAVE, ctx)

    async def _phase_night_witch_save(self, game: Game) -> None:
        ctx = game.phase_ctx
        # 播报只看板子组成、不看死活，理由同守卫
        if not ctx.get("witch_announced"):
            if any(p.role == Role.WITCH for p in game.players):
                self._emit(game, "host_line", {"text": "女巫请睁眼，正在决定是否用药……"})
            ctx["witch_announced"] = True
            self._save(game)
        witches = [p for p in game.alive_players() if p.role == Role.WITCH]
        if not witches:
            self._advance(game, Phase.NIGHT_WITCH_POISON, ctx)
            return
        witch = witches[0]
        knife = ctx.get("knife")
        used_save = self._witch_used(game, "witch_save")
        can_save = knife is not None and not used_save
        # 女巫每晚都能看到刀口（无论有无解药）；带重入保护，避免恢复时重复通知
        if not ctx.get("knife_shown"):
            self._emit(game, "witch_knife", {"target": knife, "can_save": can_save}, actor=witch.seat, visibility="seat:%d" % witch.seat)
            ctx["knife_shown"] = True
        if can_save:
            if witch.is_human:
                note = "女巫时间：今晚倒下的是你自己，女巫不能自救。" if knife == witch.seat else f"女巫时间：今晚 {knife}号 被袭击，是否使用解药？"
                ans = await self._wait_human(game, "witch_save", [], note=note)
                use = bool(ans["use"])
            else:
                d = await self._decide(game, witch, WitchSave, prompts.witch_save_messages(game, witch, knife, True), [], "witch_save")
                use = bool(d.use)
            if use:
                ctx["saved"] = True
                self._emit(game, "witch_save", {"target": knife}, actor=witch.seat, visibility="seat:%d" % witch.seat)
        self._save(game)
        self._advance(game, Phase.NIGHT_WITCH_POISON, ctx)

    async def _phase_night_witch_poison(self, game: Game) -> None:
        ctx = game.phase_ctx
        witches = [p for p in game.alive_players() if p.role == Role.WITCH]
        if not witches:
            self._advance(game, Phase.DAWN, ctx)
            return
        witch = witches[0]
        used_poison = self._witch_used(game, "witch_poison")
        used_save_tonight = bool(ctx.get("saved"))
        if not used_poison and not used_save_tonight:
            candidates = [p.seat for p in game.alive_players() if p.seat != witch.seat]
            if witch.is_human:
                ans = await self._wait_human(game, "witch_poison", candidates, note="女巫时间：是否使用毒药？")
                use = bool(ans["use"])
                target = int(ans.get("target") or 0)
            else:
                d = await self._decide(game, witch, WitchPoison, prompts.witch_poison_messages(game, witch, candidates), candidates, "witch_poison")
                use = bool(d.use) and d.target in candidates
                target = d.target if use else 0
            if use:
                ctx["poison"] = target
                self._emit(game, "witch_poison", {"target": target}, actor=witch.seat, visibility="seat:%d" % witch.seat)
        self._save(game)
        self._advance(game, Phase.DAWN, ctx)

    async def _phase_dawn(self, game: Game) -> None:
        ctx = game.phase_ctx
        if not ctx.get("settled"):
            knife = ctx.get("knife")
            poison = ctx.get("poison")
            deaths = resolve_night_deaths(knife, poison, bool(ctx.get("saved")), ctx.get("guard"))
            for _, seat in deaths:
                game.player(seat).alive = False
            if deaths:
                names = "、".join(f"{s}号 {game.player(s).name}" for _, s in deaths)
                text = f"天亮了。昨晚，{names} 倒在了血泊中。"
            else:
                text = "天亮了。昨晚是平安夜，无人死亡。"
            self._emit(game, "night_settle", {"text": text, "deaths": [s for _, s in deaths]})
            ctx["settled"] = True
            ctx["deaths"] = deaths
            self._save(game)

        deaths: list = ctx["deaths"]
        i = int(ctx.get("i", 0))
        while i < len(deaths):
            cause, seat = deaths[i]
            p = game.player(seat)
            shot_key = f"shot_done_{i}"
            lw_key = f"lw_done_{i}"
            badge_key = f"badge_done_{i}"
            # 警长夜间出局：先处置警徽（移交/撕毁），再走开枪/遗言
            if game.sheriff_seat == seat and not p.alive and not ctx.get(badge_key):
                await self._badge_flow(game, p)
                ctx[badge_key] = True
                self._save(game)
            if cause == "knife" and p.role == Role.HUNTER and not ctx.get(shot_key):
                candidates = [q.seat for q in game.alive_players()]
                if p.is_human:
                    ans = await self._wait_human(
                        game, "hunter", candidates, note="你昨晚被刀杀出局！猎人可以开枪带走一名玩家，或放弃开枪。"
                    )
                    shoot = bool(ans["shoot"])
                    target = int(ans.get("target") or 0)
                else:
                    d = await self._decide(game, p, HunterDecision, prompts.hunter_messages(game, p, candidates), candidates, "hunter")
                    shoot = bool(d.shoot) and d.target in candidates
                    target = d.target if shoot else 0
                if shoot and target:
                    game.player(target).alive = False
                    deaths.append(("shot", target))
                    self._emit(game, "hunter_shot", {"target": target}, actor=seat)
                ctx[shot_key] = True
                self._save(game)
            # 经典遗言规则：仅第一晚夜晚死亡（刀杀/毒杀）有遗言；枪杀与后续夜晚死亡无遗言
            if game.day == 1 and cause in ("knife", "poison") and not ctx.get(lw_key):
                await self._last_words(game, p)
                ctx[lw_key] = True
                self._save(game)
            i += 1
            ctx["i"] = i
            self._save(game)

        order = self._speech_order(game)
        # 警长竞选在第 1 天天亮后进行；双爆吞警徽第一步后的次日重选一次；竞选中/竞选后由 _phase_sheriff_election 推进白天发言
        if game.sheriff_enabled and (game.day == 1 or game.sheriff_revote) and order:
            self._advance(game, Phase.SHERIFF_ELECTION, {"stage": "declare", "runs": {}, "candidates": [], "round": 1, "votes": {}})
            return
        self._emit(game, "host_line", {"text": f"—— 第 {game.day} 天 · 白天 · 从 {order[0]}号 开始发言 ——"} if order else {"text": "无人存活"})
        self._advance(game, Phase.DAY_SPEECH, {"order": order, "i": 0})

    async def _phase_sheriff_election(self, game: Game) -> None:
        ctx = game.phase_ctx
        stage = ctx.get("stage", "declare")

        if stage == "declare":
            if not ctx.get("declared"):
                title = "—— 警长竞选（重新竞选）——" if game.sheriff_revote else "—— 警长竞选 ——"
                self._emit(game, "host_line", {"text": title})
                ctx["declared"] = True
                self._save(game)
            alive = game.alive_players()
            runs: dict = ctx.setdefault("runs", {})
            todo = [p for p in alive if str(p.seat) not in runs]

            async def one(p: Player):
                if p.is_human:
                    ans = await self._wait_human(game, "sheriff_run", [], note="警长竞选：是否上警参选警长？")
                    return bool(ans["run"])
                d = await self._decide(game, p, SheriffRun, prompts.sheriff_run_messages(game, p), [], "sheriff_run")
                return bool(d.run)

            # 报名互相独立（prompt 不含他人报名结果），全部并行决策
            results = await asyncio.gather(*(one(p) for p in todo))
            for p, run in zip(todo, results):
                runs[str(p.seat)] = run
            ctx["runs"] = runs
            self._save(game)
            candidates = sorted(int(s) for s, r in runs.items() if r)
            ctx["candidates"] = candidates
            self._emit(game, "sheriff_candidates", {"seats": candidates})
            if len(candidates) <= 1:
                await self._finish_election(game, candidates, 1)
                return
            ctx["stage"] = "speech"
            ctx["speech_i"] = 0
            self._save(game)
            stage = "speech"

        if stage == "speech":
            raw = ctx["tied"] if ctx.get("round", 1) >= 2 and ctx.get("tied") else ctx["candidates"]
            candidates = [int(s) for s in raw]
            i = int(ctx.get("speech_i", 0))
            # 吞警徽规则：竞选发言阶段狼人可自爆，请求 future 跨发言回合存活
            swallow_on = game.badge_swallow != "none"
            explode_fut: asyncio.Future | None = None
            if swallow_on:
                explode_fut = asyncio.get_running_loop().create_future()
                self.explode_futures[game.id] = explode_fut
            try:
                while i < len(candidates):
                    p = game.player(candidates[i])
                    if not p.alive:
                        i += 1
                        ctx["speech_i"] = i
                        continue
                    ended = await self._sheriff_speech(game, p, "竞选发言", explode_fut)
                    if ended:  # 警上自爆：竞选终止直接入夜
                        return
                    i += 1
                    ctx["speech_i"] = i
                    self._save(game)
            finally:
                if swallow_on:
                    self.explode_futures.pop(game.id, None)
            # 进入投票：候选人不能投票，未上警的存活玩家投票
            ctx["stage"] = "vote"
            ctx["votes"] = {}
            self._save(game)
            stage = "vote"

        if stage == "vote":
            raw = ctx["candidates"] if ctx.get("round", 1) == 1 else ctx["tied"]
            candidates = [int(s) for s in raw]
            votes: dict = ctx.setdefault("votes", {})
            voters = [p for p in game.alive_players() if p.seat not in candidates]
            if not voters:
                await self._finish_election(game, candidates, int(ctx.get("round", 1)), tied=True)
                return
            todo = [p for p in voters if str(p.seat) not in votes]
            votes.update(
                await self._collect_votes(
                    game,
                    todo,
                    candidates,
                    kind="sheriff_vote",
                    schema=SheriffVote,
                    messages_fn=prompts.sheriff_vote_messages,
                    cast_event="sheriff_vote_cast",
                    human_note="警长竞选：请投票支持一名候选人",
                    with_reason=False,
                    show_pending=False,
                )
            )
            counts: dict[int, int] = {}
            for v in votes.values():
                if v["target"] == 0:  # 弃票
                    continue
                counts[v["target"]] = counts.get(v["target"], 0) + 1
            rnd = int(ctx.get("round", 1))
            if not counts:  # 全员弃票：无人得票，警徽作废
                await self._finish_election(game, candidates, rnd, tied=True, reason="全员弃票")
                return
            max_n = max(counts.values())
            top = sorted(t for t, n in counts.items() if n == max_n)
            if len(top) == 1:
                await self._finish_election(game, top, rnd)
            elif rnd >= 2:
                await self._finish_election(game, top, rnd, tied=True)
            else:
                # 平票加赛：平票候选人再发言一轮，投票只在平票者之间重投
                self._emit(game, "host_line", {"text": f"{'、'.join(str(s) + '号' for s in top)} 平票，进入加赛。"})
                ctx["tied"] = top
                ctx["round"] = 2
                ctx["stage"] = "speech"
                ctx["speech_i"] = 0
                ctx["votes"] = {}
                self._save(game)

    async def _finish_election(
        self, game: Game, candidates: list[int], round_: int, tied: bool = False, reason: str | None = None
    ) -> None:
        """竞选收尾：唯一胜者当选（含加赛胜出）；加赛仍平票/无人上警/无人可投/全员弃票则警徽作废。"""
        ctx = game.phase_ctx
        game.sheriff_revote = False  # 竞选未被自爆终止、走完流程：不再重选
        if not tied and len(candidates) == 1 and game.player(candidates[0]).alive:
            seat = candidates[0]
            game.sheriff_seat = seat
            self._emit(game, "sheriff_result", {"sheriff": seat, "round": round_, "destroyed": False})
            ctx["stage"] = "order"
            self._save(game)
            await self._sheriff_order_stage(game)
            return
        if tied and round_ >= 2:
            reason = reason or "加赛后仍平票"
        elif tied:
            reason = reason or "没有可投票的玩家"
        else:
            reason = "无人上警"
        self._emit(game, "sheriff_result", {"sheriff": None, "destroyed": True, "round": round_, "reason": reason})
        self._advance_to_day(game)

    def _advance_to_day(self, game: Game) -> None:
        order = self._speech_order(game)
        self._emit(game, "host_line", {"text": f"—— 第 {game.day} 天 · 白天 · 从 {order[0]}号 开始发言 ——"} if order else {"text": "无人存活"})
        self._advance(game, Phase.DAY_SPEECH, {"order": order, "i": 0})

    async def _sheriff_order_stage(self, game: Game) -> None:
        """警长当选后决定首日发言方向：从左/右开始，警长最后发言。"""
        ctx = game.phase_ctx
        sheriff = game.player(game.sheriff_seat or 0)
        if sheriff.is_human:
            ans = await self._wait_human(
                game, "speech_order", [], note="你当选警长！选择首日发言从你的左边还是右边开始（你最后一个发言）"
            )
            direction = str(ans["direction"])
        else:
            d = await self._decide(game, sheriff, SpeechOrderChoice, prompts.speech_order_messages(game, sheriff), [], "speech_order")
            direction = d.direction if d.direction in ("left", "right") else "right"
        order = self._sheriff_order(game, direction)
        side = "左边" if direction == "left" else "右边"
        self._emit(game, "speech_order", {"direction": direction, "start": order[0] if order else None})
        self._emit(game, "host_line", {"text": f"警长决定从{side}（{order[0]}号）开始发言，警长最后发言。"})
        self._advance(game, Phase.DAY_SPEECH, {"order": order, "i": 0})

    def _sheriff_order(self, game: Game, direction: str) -> list[int]:
        """首日发言顺序：从警长左/右邻居开始沿该方向绕桌，警长垫底；死亡座位跳过。"""
        n = len(game.players)
        step = -1 if direction == "left" else 1
        s = game.sheriff_seat or 1
        order = [((s - 1 + step * k) % n) + 1 for k in range(1, n)]
        order = [x for x in order if x != s and game.player(x).alive]
        order.append(s)
        return order

    async def _sheriff_speech(self, game: Game, p: Player, tag: str, explode_fut: asyncio.Future | None = None) -> bool:
        """警上竞选发言（宣言/平票加赛）。吞警徽规则开启时狼人可在此自爆：
        狼人回合决策、真人按钮（任意发言间隙）、查杀触发三种入口。
        返回 True 表示本白天已因警上自爆结束并进入黑夜。"""
        swallow = explode_fut is not None
        text = ""
        if swallow and not p.is_human and p.role == Role.WEREWOLF:
            d = await self._decide(
                game, p, SelfExplodeDecision, prompts.self_explode_campaign_messages(game, p), [], "self_explode"
            )
            if d.explode:
                await self._apply_campaign_explode(game, p)
                return True
        if p.is_human:
            if swallow:
                input_task = asyncio.create_task(self._wait_human(game, "speech", [], note=f"轮到你的{tag}（不要暴露具体身份）"))
                done, _ = await asyncio.wait({input_task, explode_fut}, return_when=asyncio.FIRST_COMPLETED)
                if explode_fut in done:
                    input_task.cancel()
                    try:
                        await input_task
                    except asyncio.CancelledError:
                        pass
                    await self._apply_campaign_explode(game, game.human())
                    return True
                ans = input_task.result()
            else:
                ans = await self._wait_human(game, "speech", [], note=f"轮到你的{tag}（不要暴露具体身份）")
            text = str(ans.get("text", "")).strip() or "……"
            self._emit(game, "sheriff_speech", {"text": text, "tag": tag}, actor=p.seat)
        else:
            speech_task = asyncio.create_task(
                self._stream_speech(game, p, prompts.sheriff_campaign_messages(game, p), "sheriff_speech", tag=tag)
            )
            if swallow:
                done, _ = await asyncio.wait({speech_task, explode_fut}, return_when=asyncio.FIRST_COMPLETED)
                if explode_fut in done:
                    speech_task.cancel()
                    try:
                        await speech_task
                    except asyncio.CancelledError:
                        pass
                    self._emit(game, "host_line", {"text": f"{p.name} 的竞选发言被打断：狼人自爆！"})
                    await self._apply_campaign_explode(game, game.human())
                    return True
            await speech_task
        if swallow:
            spoken = text if p.is_human else speech_text(game, p.seat, "sheriff_speech")
            if await self._check_claim_explode(game, p.seat, spoken, explode_fut, campaign=True):
                return True
        return False

    async def _apply_campaign_explode(self, game: Game, p: Player) -> None:
        """警上自爆（吞警徽规则）：翻牌即死、无遗言，竞选立即终止，直接入夜。
        single 一爆终局；double 第一步后次日重选，重选中再爆才彻底吞掉警徽。"""
        apply_self_explode(game, p.seat)
        if game.badge_swallow == "double":
            if game.sheriff_revote:
                game.sheriff_revote = False
                mode = "double_second"
                line = "警上自爆：警徽被彻底吞掉，本局不再有警长，直接进入黑夜。"
            else:
                game.sheriff_revote = True
                mode = "double_first"
                line = "警上自爆：警长竞选终止，直接进入黑夜；明天白天将重新进行警长竞选。"
        else:
            mode = "single"
            line = "警上自爆：警长竞选终止，警徽本局流失，直接进入黑夜。"
        self._emit(game, "wolf_explode", {"seat": p.seat, "name": p.name, "where": "sheriff", "mode": mode}, actor=p.seat)
        self._emit(game, "host_line", {"text": line})
        self._save(game)
        game.speech_start_seat = p.seat % len(game.players) + 1
        game.day += 1
        self._call_engine(game, enter_night)

    async def _phase_day_speech(self, game: Game) -> None:
        ctx = game.phase_ctx
        order: list[int] = ctx["order"]
        i = int(ctx.get("i", 0))
        # 自爆请求 future 跨回合存活：真人狼在任意发言间隙的请求都不会丢
        explode_fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.explode_futures[game.id] = explode_fut
        try:
            while i < len(order):
                p = game.player(order[i])
                if not p.alive:
                    i += 1
                    ctx["i"] = i
                    continue
                if p.is_human:
                    input_task = asyncio.create_task(self._wait_human(game, "speech", [], note="轮到你发言了"))
                    done, _ = await asyncio.wait({input_task, explode_fut}, return_when=asyncio.FIRST_COMPLETED)
                    if explode_fut in done:
                        input_task.cancel()
                        try:
                            await input_task
                        except asyncio.CancelledError:
                            pass
                        await self._apply_explode(game, game.human())
                        return
                    ans = input_task.result()
                    text = str(ans.get("text", "")).strip() or "……"
                    self._emit(game, "speech", {"text": text}, actor=p.seat)
                    if await self._check_claim_explode(game, p.seat, text, explode_fut):
                        return
                else:
                    if p.role == Role.WEREWOLF:
                        d = await self._decide(
                            game, p, SelfExplodeDecision, prompts.self_explode_messages(game, p), [], "self_explode"
                        )
                        if d.explode:
                            await self._apply_explode(game, p)
                            return
                    speech_task = asyncio.create_task(
                        self._stream_speech(game, p, prompts.speech_messages(game, p), "speech")
                    )
                    done, _ = await asyncio.wait({speech_task, explode_fut}, return_when=asyncio.FIRST_COMPLETED)
                    if explode_fut in done:
                        speech_task.cancel()
                        try:
                            await speech_task
                        except asyncio.CancelledError:
                            pass
                        self._emit(game, "host_line", {"text": f"{p.name} 的发言被打断：狼人自爆！"})
                        await self._apply_explode(game, game.human())
                        return
                    await speech_task  # 已完成则立即返回（并透传 GameFatalStop 等异常），未完成则等它收尾
                    if await self._check_claim_explode(game, p.seat, speech_text(game, p.seat), explode_fut):
                        return
                i += 1
                ctx["i"] = i
                self._save(game)
        finally:
            self.explode_futures.pop(game.id, None)
        self._emit(game, "host_line", {"text": "发言结束，请所有存活玩家投票。"})
        self._advance(game, Phase.VOTE, {"votes": {}})

    async def _check_claim_explode(self, game: Game, speaker: int, text: str, explode_fut: asyncio.Future, campaign: bool = False) -> bool:
        """查杀触发自爆：发言以查杀/验狼句式点名存活 AI 狼时，被点名者在其回合外同时思考，
        谁先决意自爆谁结算（同批完成取座位号小者）。

        误触发无害（狼按实锤纪律答 false），返回 True 表示本白天已因自爆结束并进入黑夜。
        竞选发言阶段（campaign=True）触发的是吞警徽结算。
        """
        apply = self._apply_campaign_explode if campaign else self._apply_explode
        wolves = [
            game.player(seat)
            for seat in sorted(_claimed_wolf_seats(text))
            if game.player(seat).alive and not game.player(seat).is_human and game.player(seat).role == Role.WEREWOLF
        ]
        if not wolves:
            return False
        tasks: dict[asyncio.Task, Player] = {
            asyncio.create_task(
                self._decide(
                    game,
                    w,
                    SelfExplodeDecision,
                    prompts.self_explode_trigger_messages(game, w, speaker, text, campaign=campaign),
                    [],
                    "self_explode",
                    pause=False,
                )
            ): w
            for w in wolves
        }

        async def cancel_rest(skip: asyncio.Task | None = None) -> None:
            rest = [t for t in tasks if t is not skip]
            for t in rest:
                t.cancel()
            await asyncio.gather(*rest, return_exceptions=True)

        try:
            while tasks:
                done, _ = await asyncio.wait({*tasks.keys(), explode_fut}, return_when=asyncio.FIRST_COMPLETED)
                if explode_fut in done:
                    await cancel_rest()
                    await apply(game, game.human())
                    return True
                winner: Player | None = None
                for t in sorted(done, key=lambda t: tasks[t].seat):
                    d = t.result()
                    if d.explode:
                        winner = tasks.pop(t)
                        break
                    tasks.pop(t)
                if winner is not None:
                    await cancel_rest()
                    await apply(game, winner)
                    return True
            return False
        except BaseException:
            await cancel_rest()
            raise

    async def _apply_explode(self, game: Game, p: Player) -> None:
        """自爆结算：翻牌即死、无遗言；警长走警徽流程；白天中断直接入夜。"""
        apply_self_explode(game, p.seat)
        self._emit(game, "wolf_explode", {"seat": p.seat, "name": p.name}, actor=p.seat)
        self._save(game)
        if game.sheriff_seat == p.seat:
            await self._badge_flow(game, p)
        game.speech_start_seat = p.seat % len(game.players) + 1
        game.day += 1
        self._call_engine(game, enter_night)

    async def _collect_votes(
        self,
        game: Game,
        voters: list[Player],
        candidates: list[int],
        kind: str,
        schema,
        messages_fn,
        cast_event: str,
        human_note: str,
        with_reason: bool,
        show_pending: bool,
        exclude_self: bool = False,
    ) -> dict:
        """并行收集所有选票：AI 票决策互不依赖（prompt 不含他人票面），gather 同时出票；真人仍走等待输入。

        全部决策完成后按座位顺序逐个亮票，保持原有亮票节奏；中断时随外层任务一起取消。
        """
        if show_pending:
            for p in voters:
                if not p.is_human:
                    self._transient(game, "vote_pending", {"seat": p.seat, "name": p.name})

        async def one(p: Player):
            allowed = [s for s in candidates if s != p.seat] if exclude_self else candidates
            if p.is_human:
                ans = await self._wait_human(game, kind, allowed, note=human_note)
                return int(ans["target"]), ""
            d = await self._decide(game, p, schema, messages_fn(game, p, allowed), allowed, kind)
            reason = getattr(d, "reason", "") if with_reason else ""
            return int(d.target), reason

        results = await asyncio.gather(*(one(p) for p in voters))
        votes: dict = {}
        for p, (target, reason) in zip(voters, results):
            votes[str(p.seat)] = {"seat": p.seat, "target": target, "reason": reason}
            payload = {"seat": p.seat, "name": p.name, "target": target}
            if with_reason:
                payload["reason"] = reason
            self._emit(game, cast_event, payload)
            self._save(game)
        return votes

    async def _phase_vote(self, game: Game) -> None:
        ctx = game.phase_ctx
        votes: dict = ctx.setdefault("votes", {})
        todo = [p for p in game.alive_players() if str(p.seat) not in votes and not p.vote_lost]  # 白痴翻牌免死后失去投票权
        votes.update(
            await self._collect_votes(
                game,
                todo,
                [q.seat for q in game.alive_players()],
                kind="vote",
                schema=VoteDecision,
                messages_fn=prompts.vote_messages,
                cast_event="vote_cast",
                human_note="请投票放逐一名玩家",
                with_reason=True,
                show_pending=True,
                exclude_self=True,
            )
        )
        ordered = [votes[str(p.seat)] for p in game.alive_players() if str(p.seat) in votes]
        ordered.sort(key=lambda v: v["seat"])
        counts = sheriff_weighted_counts(ordered, game.sheriff_seat)
        # 弃票不计入得票；全员弃票时 counts 为空，按无人出局处理
        max_n = max(counts.values()) if counts else 0
        top = sorted(t for t, n in counts.items() if n == max_n)
        if len(top) == 1:
            exiled = top[0]
            p = game.player(exiled)
            info = apply_exile(game, exiled)
            idiot_survived = info["idiot_survived"]
            self._emit(
                game,
                "vote_result",
                {
                    "votes": ordered,
                    "tie": False,
                    "exiled": exiled,
                    "role": p.role_label() if p.role_revealed else "",  # 出局不翻牌，白痴翻牌除外
                    "idiot_survived": idiot_survived,
                },
            )
            if idiot_survived:
                self._advance(game, Phase.VOTE_RESULT, {"exiled": exiled, "lw_done": True})
            else:
                game.speech_start_seat = exiled % len(game.players) + 1
                self._advance(game, Phase.VOTE_RESULT, {"exiled": exiled, "lw_done": False})
        else:
            self._emit(game, "vote_result", {"votes": ordered, "tie": True, "exiled": None, "role": ""})
            self._advance(game, Phase.VOTE_RESULT, {"exiled": None, "lw_done": True})

    async def _phase_vote_result(self, game: Game) -> None:
        ctx = game.phase_ctx
        exiled = ctx.get("exiled")
        if exiled is not None and not ctx.get("lw_done"):
            p = game.player(exiled)
            # 警长被放逐（白痴免死除外）：先处置警徽，再留遗言
            if game.sheriff_seat == exiled and not p.alive and not ctx.get("badge_done"):
                await self._badge_flow(game, p)
                ctx["badge_done"] = True
                self._save(game)
            await self._last_words(game, p)
            ctx["lw_done"] = True
            self._save(game)
        # 经典规则：猎人被放逐（非毒杀）可开枪带走一人；带重入保护
        if exiled is not None and not ctx.get("shot_done"):
            p = game.player(exiled)
            if not p.alive and p.role == Role.HUNTER:
                candidates = [q.seat for q in game.alive_players()]
                if p.is_human:
                    ans = await self._wait_human(
                        game, "hunter", candidates, note="你被投票放逐出局！猎人可以开枪带走一名玩家，或放弃开枪。"
                    )
                    shoot = bool(ans["shoot"])
                    target = int(ans.get("target") or 0)
                else:
                    d = await self._decide(game, p, HunterDecision, prompts.hunter_messages(game, p, candidates), candidates, "hunter")
                    shoot = bool(d.shoot) and d.target in candidates
                    target = d.target if shoot else 0
                if shoot and target:
                    game.player(target).alive = False
                    self._emit(game, "hunter_shot", {"target": target}, actor=exiled)
            ctx["shot_done"] = True
            self._save(game)
        game.day += 1
        self._call_engine(game, enter_night)

    # ---------- 工具 ----------

    def _cycle_from(self, game: Game):
        n = len(game.players)
        for off in range(n):
            yield (game.speech_start_seat - 1 + off) % n + 1

    def _speech_order(self, game: Game) -> list[int]:
        alive = {p.seat for p in game.alive_players()}
        return [s for s in self._cycle_from(game) if s in alive]

    def _witch_used(self, game: Game, event_type: str) -> bool:
        return any(e.type == event_type for e in game.events)

    async def _last_words(self, game: Game, p: Player) -> None:
        if p.is_human:
            ans = await self._wait_human(game, "last_words", [], note="你已出局，请留下遗言")
            text = str(ans.get("text", "")).strip() or "……"
            self._emit(game, "last_words", {"text": text}, actor=p.seat)
        else:
            await self._stream_speech(game, p, prompts.last_words_messages(game, p), "last_words")

    async def _badge_flow(self, game: Game, p: Player) -> None:
        """警长出局：自主决定移交警徽给一名存活玩家，或撕毁警徽。"""
        candidates = [q.seat for q in game.alive_players() if q.seat != p.seat]
        if p.is_human:
            ans = await self._wait_human(game, "badge_pass", candidates, note="你出局了。选择移交警徽给一名存活玩家，或撕毁警徽。")
            destroy = bool(ans.get("destroy"))
            target = int(ans.get("target") or 0)
        else:
            d = await self._decide(game, p, BadgeDecision, prompts.badge_messages(game, p, candidates), candidates, "badge_pass")
            destroy = bool(d.destroy)
            target = int(d.target or 0)
        if not destroy and target not in candidates:
            destroy = True
        if destroy:
            game.sheriff_seat = None
            self._emit(game, "badge_pass", {"from": p.seat, "to": None, "destroyed": True})
        else:
            game.sheriff_seat = target
            self._emit(game, "badge_pass", {"from": p.seat, "to": target})

    async def _stream_speech(self, game: Game, p: Player, messages: list[dict], kind: str, tag: str = "") -> None:
        """流式发言；思考型模型的 reasoning 阶段以 thinking 瞬态广播并固化（god 可见，玩家模式被视图过滤）。

        speech_start 延迟到首段正文才发；在此之前以 thinking_start 瞬态占位（无思考内容），
        玩家视角也能看到「正在思考」动画，避免模型长延迟被误认为卡死。
        LLM 失败或零输出时暂停对局弹窗报错等用户处理；流中断但已有半截正文时保留真实输出继续。
        """
        speech_started = False
        sealed = True
        thinking: list[str] = []

        def open_speech() -> None:
            nonlocal speech_started
            if not speech_started:
                speech_started = True
                self._transient(game, "speech_start", {"seat": p.seat, "kind": kind, "name": p.name, "avatar": p.avatar})

        def on_thinking(delta: str) -> None:
            thinking.append(delta)
            self._transient(game, "thinking_delta", {"seat": p.seat, "kind": kind, "text": delta})

        def seal_thinking() -> None:
            nonlocal sealed
            if sealed:
                return
            sealed = True
            self._seal_thinking(game, p, kind, thinking)

        text = ""
        while True:
            preset = self._preset_for(p)
            if preset is None:
                await self._pause_for_llm(game, f"{p.name} 未绑定模型预设，请为其配置后重试")
                continue
            sealed = False
            thinking = []
            self._transient(game, "thinking_start", {"seat": p.seat, "name": p.name, "kind": kind})
            text = ""
            try:
                async for delta in self.llm.speak(preset, messages, on_thinking=on_thinking):
                    seal_thinking()
                    open_speech()
                    text += delta
                    self._transient(game, "speech_delta", {"seat": p.seat, "kind": kind, "text": delta})
            except LlmFatalError as e:
                seal_thinking()
                print(f"[llm] {p.name} 发言失败: {e}", file=sys.stderr)
                await self._pause_for_llm(game, f"{p.name} 的发言请求失败：{e}")
                continue
            except Exception as e:
                seal_thinking()
                if text.strip():
                    break  # 半截真实输出：保留继续，不代打也不重新生成
                print(f"[llm] {p.name} 发言失败: {e}", file=sys.stderr)
                await self._pause_for_llm(game, f"{p.name} 的发言请求失败：{e}")
                continue
            if not text.strip():
                print(f"[llm] {p.name} 发言无输出", file=sys.stderr)
                await self._pause_for_llm(game, f"{p.name} 的发言没有返回内容")
                continue
            break
        seal_thinking()
        open_speech()
        ev_type = {"speech": "speech", "last_words": "last_words", "sheriff_speech": "sheriff_speech"}.get(kind, "speech")
        payload: dict = {"text": text}
        if tag:
            payload["tag"] = tag
        self._emit(game, ev_type, payload, actor=p.seat)

    def _seal_thinking(self, game: Game, p: Player, kind: str, thinking: list[str]) -> None:
        """收口一次思考：有内容才固化 god 可见事件；无论有无内容都广播结束瞬态（全员可见，用于收起占位动画）。"""
        text = "".join(thinking)
        if text:
            self._emit(game, "thinking", {"seat": p.seat, "kind": kind, "text": text}, actor=p.seat, visibility="god")
        self._transient(game, "thinking_end", {"seat": p.seat, "kind": kind})

    _KIND_LABELS = {
        "vote": "放逐投票",
        "wolf_vote": "狼刀投票",
        "seer_check": "查验",
        "witch_save": "解药决策",
        "witch_poison": "毒药决策",
        "hunter": "开枪决策",
        "sheriff_run": "上警决策",
        "sheriff_vote": "警长投票",
        "badge_pass": "警徽移交",
        "speech_order": "发言方向",
        "self_explode": "自爆权衡",
    }

    async def _decide(self, game: Game, p: Player, schema, messages: list[dict], allowed: list[int], kind: str, pause: bool = True):
        """AI 结构化决策；LLM 失败或目标非法时暂停对局弹窗报错，等用户重试或终止。

        决策期间的思考过程（reasoning_content）以 thinking_start/delta 瞬态事件实时广播，
        结束经 _seal_thinking 收口（有内容固化 god 事件，thinking_end 瞬态全员可见）。
        pause=False 用于机会性旁路决策（查杀触发自爆）：失败直接放弃，不打断对局。
        """
        label = self._KIND_LABELS.get(kind, kind)
        while True:
            preset = self._preset_for(p)
            if preset is None:
                if not pause:
                    return SelfExplodeDecision(explode=False)
                await self._pause_for_llm(game, f"{p.name} 未绑定模型预设（{label}），请为其配置后重试")
                continue
            self._transient(game, "thinking_start", {"seat": p.seat, "name": p.name, "kind": kind})
            thinking: list[str] = []

            def on_thinking(delta: str) -> None:
                thinking.append(delta)
                self._transient(game, "thinking_delta", {"seat": p.seat, "kind": kind, "text": delta})

            err = ""
            try:
                d = await self.llm.decide(preset, messages, schema, on_thinking=on_thinking)
                self._seal_thinking(game, p, kind, thinking)
                target = getattr(d, "target", None)
                if kind in ("witch_save", "sheriff_run", "speech_order", "self_explode"):
                    return d
                if kind == "witch_poison":
                    if not d.use or d.target in allowed:
                        return d
                    err = f"毒药目标非法（{d.target}）"
                elif kind == "hunter":
                    if not d.shoot or d.target in allowed:
                        return d
                    err = f"开枪目标非法（{d.target}）"
                elif kind == "badge_pass":
                    if d.destroy or d.target in allowed:
                        return d
                    err = f"移交目标非法（{d.target}）"
                elif target == 0 and kind in ("vote", "sheriff_vote"):
                    return d
                elif target in allowed:
                    return d
                else:
                    err = f"目标非法（{target}）"
            except LlmFatalError as e:
                self._seal_thinking(game, p, kind, thinking)
                err = str(e)
            except LlmError as e:
                self._seal_thinking(game, p, kind, thinking)
                err = str(e)
            if not pause:
                return SelfExplodeDecision(explode=False)
            print(f"[llm] {p.name} {label}失败：{err}", file=sys.stderr)
            await self._pause_for_llm(game, f"{p.name} 的{label}失败：{err}")

    def _preset_for(self, p: Player) -> ModelPreset | None:
        if not p.preset_id:
            return None
        settings = self.store.load_settings()
        for preset in settings.presets:
            if preset.id == p.preset_id:
                return preset
        return None

    # ---------- 事件与持久化 ----------

    def _save(self, game: Game) -> None:
        self.store.save_game(game)

    def _publish(self, game: Game, ev: GameEvent) -> None:
        self.hub.publish(
            game.id,
            ev.type,
            {"seq": ev.seq, "day": ev.day, "type": ev.type, "actor": ev.actor, **ev.payload},
        )

    def _publish_since(self, game: Game, since_seq: int) -> None:
        for ev in game.events:
            if ev.seq > since_seq:
                self._publish(game, ev)

    def _emit(self, game: Game, type_: str, payload: dict, actor: int | None = None, visibility: str = "public") -> GameEvent:
        ev = emit(game, type_, payload, actor, visibility)
        self._publish(game, ev)
        return ev

    def _transient(self, game: Game, event: str, data: dict) -> None:
        self.hub.publish(game.id, event, data)

    def _advance(self, game: Game, phase: Phase, ctx: dict) -> None:
        game.phase = phase
        game.phase_ctx = ctx
        self._transient(game, "phase_change", {"day": game.day, "phase": phase.value})
        self._save(game)

    def _call_engine(self, game: Game, fn, *args) -> None:
        since = game.event_count
        fn(game, *args)
        self._publish_since(game, since)
        self._save(game)

    async def _pause_for_llm(self, game: Game, message: str) -> None:
        """LLM 调用失败暂停对局：广播 llm_paused 事件（前端弹窗展示报错），等用户选择。

        retry → 返回，调用方重跑失败的请求；abort → 对局按故障收尾并抛 GameFatalStop。
        并行决策同时失败时共享同一次暂停（首个失败建 future 并发事件，后来者直接加入等待），
        用户的一次选择同时唤醒所有失败者。
        """
        entry = self.paused.get(game.id)
        if entry is None:
            self._emit(game, "llm_paused", {"message": message})
            self._save(game)
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self.paused[game.id] = (fut, message)
        else:
            fut = entry[0]
        try:
            action = await fut
        finally:
            cur = self.paused.get(game.id)
            if cur is not None and cur[0] is fut:
                self.paused.pop(game.id, None)
        if action == "abort":
            self._emit(game, "host_line", {"text": "❌ 对局已终止。"})
            self._call_engine(game, finish, "error", message)
            raise GameFatalStop from None
        self._transient(game, "llm_resumed", {})

    def _open_human(self, game: Game, kind: str, allowed: list[int], note: str = "") -> asyncio.Future:
        """打开真人输入通道但不等待：用于与 AI 决策并行的场景（如狼刀拍板）。"""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[game.id] = PendingInput(kind, allowed, fut)
        self._transient(game, "await_input", {"kind": kind, "allowed": allowed, "note": note})
        return fut

    async def _wait_human(self, game: Game, kind: str, allowed: list[int], note: str = "") -> dict:
        fut = self._open_human(game, kind, allowed, note)
        try:
            return await fut
        finally:
            if self.pending.get(game.id) is not None and self.pending[game.id].future is fut:
                self.pending.pop(game.id, None)


_PHASE_HANDLERS = {
    Phase.NIGHT_GUARD: GameRunner._phase_night_guard,
    Phase.NIGHT_WOLF: GameRunner._phase_night_wolf,
    Phase.NIGHT_SEER: GameRunner._phase_night_seer,
    Phase.NIGHT_WITCH_SAVE: GameRunner._phase_night_witch_save,
    Phase.NIGHT_WITCH_POISON: GameRunner._phase_night_witch_poison,
    Phase.DAWN: GameRunner._phase_dawn,
    Phase.SHERIFF_ELECTION: GameRunner._phase_sheriff_election,
    Phase.DAY_SPEECH: GameRunner._phase_day_speech,
    Phase.VOTE: GameRunner._phase_vote,
    Phase.VOTE_RESULT: GameRunner._phase_vote_result,
}
