"""狼人杀规则引擎：纯状态逻辑，不依赖 LLM 与网络，可单测。"""
from __future__ import annotations

import random

from ..models import Game, GameEvent, Phase, Role, GOD_ROLES, PHASE_LABELS, ROLE_LABELS, WinCondition
from .board import validate_board


def emit(game: Game, type_: str, payload: dict | None = None, actor: int | None = None, visibility: str = "public") -> GameEvent:
    game.event_count += 1
    ev = GameEvent(
        seq=game.event_count,
        day=game.day,
        phase=game.phase,
        type=type_,
        actor=actor,
        payload=payload or {},
        visibility=visibility,
    )
    game.events.append(ev)
    return ev


def flatten_event(event: GameEvent) -> dict:
    """事件序列化为前端渲染结构（SSE 快照与复盘共用）：业务字段从 payload 提到顶层。"""
    return {"seq": event.seq, "day": event.day, "type": event.type, "actor": event.actor, **event.payload}


def strip_vote_reason(event: dict, type_: str) -> dict:
    """投票理由仅上帝视角可见（观战/复盘）：非上帝视角的事件副本剥掉 reason 字段。

    vote_cast 的 reason 在顶层，vote_result 的在 votes[*].reason；其余事件原样返回。
    """
    if type_ == "vote_cast":
        return {k: v for k, v in event.items() if k != "reason"}
    if type_ == "vote_result":
        votes = [{k: v for k, v in vote.items() if k != "reason"} for vote in event.get("votes", [])]
        return {**event, "votes": votes}
    return event


def visible_to(game: Game, event: GameEvent, seat: int) -> bool:
    v = event.visibility
    if v == "public":
        return True
    if v.startswith("seat:"):
        return int(v.split(":", 1)[1]) == seat
    p = game.player(seat)
    if v == "wolves":
        return p.role == Role.WEREWOLF
    if v == "seer":
        return p.role == Role.SEER
    if v == "witch":
        return p.role == Role.WITCH
    return False


def view_events(game: Game, seat: int) -> list[GameEvent]:
    return [e for e in game.events if visible_to(game, e, seat)]


def assign_roles(game: Game) -> None:
    validate_board(game.board.roles, game.board.win_condition, game.board.max_days)
    roles = list(game.board.roles)
    random.Random(game.seed).shuffle(roles)
    ordered = sorted(game.players, key=lambda x: x.seat)
    # 真人自选角色：把该角色的一张换到真人位置，其余角色相对顺序仍由 seed 决定
    preferred = game.preferred_role
    if preferred is not None and preferred in roles and game.human_seat() is not None:
        idx = ordered.index(game.human())
        roles.remove(preferred)
        roles.insert(idx, preferred)
    for p, r in zip(ordered, roles):
        p.role = r
        p.role_revealed = False
        p.alive = True


def start_game(game: Game) -> None:
    """发牌并进入第 1 夜。"""
    assign_roles(game)
    game.day = 1
    game.phase_ctx = {}
    game.speech_start_seat = 1
    for p in sorted(game.players, key=lambda x: x.seat):
        emit(game, "role_assign", {"role": p.role.value if p.role else ""}, visibility=f"seat:{p.seat}")
    enter_night(game)


def enter_night(game: Game) -> None:
    game.phase = Phase.NIGHT_GUARD
    game.phase_ctx = {"wolf_votes": {}}
    emit(game, "host_line", {"text": f"—— 第 {game.day} 夜 · 天黑请闭眼 ——"})


def check_win(game: Game) -> str | None:
    """在夜死结算后、猎人开枪后、放逐后调用。返回 "wolf" / "good" / None。"""
    alive = game.alive_players()
    wolves = [p for p in alive if p.role == Role.WEREWOLF]
    if not wolves:
        return "good"
    good = [p for p in alive if p.role != Role.WEREWOLF]
    if game.board.win_condition == WinCondition.CITY:
        if len(wolves) >= len(good):
            return "wolf"
    else:
        gods = [p for p in good if p.role in GOD_ROLES]
        villagers = [p for p in good if p.role not in GOD_ROLES]  # 村民+白痴同属民桶
        if not gods or not villagers:
            return "wolf"
    return None


def apply_exile(game: Game, seat: int) -> dict:
    """白天放逐结算。白痴首次被放逐：翻牌免死、失去投票权，留在场上；其余正常死亡，不翻牌。"""
    p = game.player(seat)
    if p.role == Role.IDIOT and not p.role_revealed:
        p.role_revealed = True
        p.vote_lost = True
        return {"idiot_survived": True}
    p.alive = False
    return {"idiot_survived": False}


def apply_self_explode(game: Game, seat: int) -> None:
    """狼人自爆：翻牌亮明身份，立即死亡，无遗言。"""
    p = game.player(seat)
    p.alive = False
    p.role_revealed = True


def finish(game: Game, winner: str, reason: str) -> None:
    game.winner = winner  # type: ignore[assignment]
    game.end_reason = reason
    game.phase = Phase.GAME_OVER
    game.phase_ctx = {}
    for p in game.players:
        p.role_revealed = True
    roles_summary = "、".join(f"{p.seat}号{p.name}：{p.role_label()}" for p in sorted(game.players, key=lambda x: x.seat))
    if winner == "good":
        text = f"🎉 游戏结束：好人阵营获胜！{reason}"
    elif winner == "wolf":
        text = f"🐺 游戏结束：狼人阵营获胜！{reason}"
    elif winner == "error":
        text = f"🛑 游戏结束：系统异常。{reason}"
    else:
        text = f"🤝 游戏结束：和局。{reason}"
    emit(game, "game_over", {"winner": winner, "reason": reason, "text": text, "roles": roles_summary})


def resolve_night_deaths(knife: int | None, poisoned: int | None, saved: bool, guard: int | None = None) -> list[tuple[str, int]]:
    """返回 [(cause, seat)]，顺序：刀杀在前、毒杀在后，去重。

    守护只防刀不防毒；救药或守护任一可挡刀，但同守同救（守+救同目标）仍死（经典规则）。
    """
    deaths: list[tuple[str, int]] = []
    if knife is not None:
        same_guard_save = saved and guard == knife
        if same_guard_save or (not saved and guard != knife):
            deaths.append(("knife", knife))
    if poisoned is not None and all(s != poisoned for _, s in deaths):
        deaths.append(("poison", poisoned))
    return deaths


def tally_wolf_votes(votes: dict[str, dict], seed: int, day: int) -> tuple[int, bool]:
    """狼队票型 → (刀口座位, 是否伪随机裁定)。平票时用 seed 派生确定性伪随机。"""
    counts: dict[int, int] = {}
    for v in votes.values():
        t = int(v["target"])
        counts[t] = counts.get(t, 0) + 1
    if not counts:
        raise ValueError("狼队没有任何刀口投票")
    max_n = max(counts.values())
    top = sorted(t for t, n in counts.items() if n == max_n)
    if len(top) == 1:
        return top[0], False
    rng = random.Random(f"{seed}:{day}:wolf")
    return rng.choice(top), True


def sheriff_weighted_counts(votes: list[dict], sheriff_seat: int | None) -> dict[int, float]:
    """放逐票面 → 加权得票表：警长一票计 1.5，其余 1；弃票（target=0）不计入。"""
    counts: dict[int, float] = {}
    for v in votes:
        if v["target"] == 0:
            continue
        w = 1.5 if v["seat"] == sheriff_seat else 1.0
        counts[v["target"]] = counts.get(v["target"], 0.0) + w
    return counts


def public_state(game: Game, reveal: bool = False) -> dict:
    """客户端可见的游戏状态：身份仅在有资格时下发（本人 / 狼队队友 / 翻牌后 / 结束后 / 上帝视角）。

    对局中出局不翻牌：死者身份保密，直到白痴翻牌或游戏结束；观战局（god）全公开；
    真人是狼时，队友的狼身份对其公开（狼人互认）。
    """
    god_mode = game.mode == "god"
    human = game.human_seat()
    human_player = game.player(human) if human is not None else None
    viewer_is_wolf = human_player is not None and human_player.role == Role.WEREWOLF
    players = []
    for p in sorted(game.players, key=lambda x: x.seat):
        role = None
        if reveal or god_mode or p.is_human or p.role_revealed or (viewer_is_wolf and p.role == Role.WEREWOLF):
            role = p.role.value if p.role else None
        players.append(
            {
                "seat": p.seat,
                "name": p.name,
                "avatar": p.avatar,
                "is_human": p.is_human,
                "alive": p.alive,
                "role": role,
                "role_label": ROLE_LABELS.get(p.role, "?") if role else None,
                "role_revealed": p.role_revealed,
                "vote_lost": p.vote_lost,
            }
        )
    return {
        "id": game.id,
        "created_at": game.created_at,
        "board": game.board.model_dump(),
        "day": game.day,
        "phase": game.phase.value,
        "phase_label": PHASE_LABELS.get(game.phase, game.phase.value),
        "winner": game.winner,
        "end_reason": game.end_reason,
        "players": players,
        "human_seat": game.human_seat(),
        "mode": game.mode,
        "sheriff_enabled": game.sheriff_enabled,
        "sheriff_seat": game.sheriff_seat,
        "badge_swallow": game.badge_swallow,
    }
