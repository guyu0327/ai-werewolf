"""AI 玩家 prompt 构建：人设 + 角色说明 + 该座位可见事件的序列化。

核心原则：喂给每个 AI 的信息严格等于它该知道的（公开事件 + 自身私密事件），杜绝作弊。
"""
from __future__ import annotations

import re

from ..models import Game, GameEvent, Player, Role, ROLE_LABELS, WinCondition
from ..engine.game import view_events
from . import strategy

SPEECH_LIMIT = strategy.SPEECH_LIMIT


def render_event(game: Game, ev: GameEvent) -> str | None:
    p_type = ev.payload.get("text") or ""
    seat_of = ev.actor

    def name_of(seat: int | None) -> str:
        if seat is None:
            return "?"
        try:
            return f"{seat}号 {game.player(seat).name}"
        except KeyError:
            return f"{seat}号"

    if ev.type == "role_assign":
        return None  # 身份已在 system prompt 说明
    if ev.type == "host_line":
        return p_type
    if ev.type == "speech":
        return f"{name_of(seat_of)}：{p_type}"
    if ev.type == "last_words":
        return f"{name_of(seat_of)}（遗言）：{p_type}"
    if ev.type == "wolf_proposal":
        reason = ev.payload.get("reason") or ""
        return f"【狼队频道】{name_of(seat_of)} 提议刀 {ev.payload.get('target')}号" + (f"：{reason}" if reason else "")
    if ev.type == "wolf_target":
        return f"【狼队频道】今晚刀口定为 {ev.payload.get('target')}号"
    if ev.type == "guard_protect":
        return f"【私密】你今晚守护了 {ev.payload.get('target')}号"
    if ev.type == "seer_result":
        camp = "狼人" if ev.payload.get("is_wolf") else "好人"
        return f"【私密】你查验了 {ev.payload.get('target')}号：{camp}"
    if ev.type == "witch_knife":
        return f"【私密】今晚 {ev.payload.get('target')}号 被袭击了。" + (
            "（你已没有解药）" if not ev.payload.get("can_save") else "（解药仍可用，但不能自救）"
        )
    if ev.type == "witch_save":
        return f"【私密】你使用了解药，救下了 {ev.payload.get('target')}号"
    if ev.type == "witch_poison":
        return f"【私密】你用毒药毒了 {ev.payload.get('target')}号"
    if ev.type == "night_settle":
        return p_type
    if ev.type == "hunter_shot":
        return f"{name_of(seat_of)} 亮明猎人身份，开枪带走了 {ev.payload.get('target')}号！"
    if ev.type == "wolf_explode":
        mode = ev.payload.get("mode")
        if mode == "single":
            return f"{name_of(seat_of)} 警上自爆吞警徽！警长竞选终止，警徽本局流失，直接进入黑夜。"
        if mode == "double_first":
            return f"{name_of(seat_of)} 警上自爆！警长竞选终止，明天白天将重新竞选警长。"
        if mode == "double_second":
            return f"{name_of(seat_of)} 警上自爆，警徽被彻底吞掉，本局不再有警长，直接进入黑夜。"
        return f"{name_of(seat_of)} 亮出狼人身份，自爆出局！白天发言立即结束，直接进入黑夜。"
    if ev.type == "sheriff_candidates":
        seats = ev.payload.get("seats") or []
        if not seats:
            return "警长竞选：无人上警，警徽作废。"
        return "警长竞选：上警的有 " + "、".join(name_of(s) for s in seats)
    if ev.type == "sheriff_speech":
        return f"{name_of(seat_of)}（竞选宣言）：{p_type}"
    if ev.type == "sheriff_vote_cast":
        target = ev.payload.get("target")
        vote_desc = "弃票" if target == 0 else f"投给 {target}号"
        return f"【警长竞选】{name_of(ev.payload.get('seat'))} {vote_desc}"
    if ev.type == "sheriff_result":
        if ev.payload.get("destroyed"):
            return f"警长竞选结束：{ev.payload.get('reason') or '警徽作废'}，本局没有警长。"
        return f"{name_of(ev.payload.get('sheriff'))} 当选警长！警徽在手，投票计 1.5 票。"
    if ev.type == "badge_pass":
        if ev.payload.get("destroyed"):
            return f"{name_of(seat_of)} 撕毁了警徽，本局再无警长。"
        return f"{name_of(seat_of)}（警长）出局，警徽移交给 {ev.payload.get('to')}号"
    if ev.type == "speech_order":
        start = ev.payload.get("start")
        return f"警长决定首日从 {start}号 开始发言（警长最后发言）。" if start else ""
    if ev.type == "vote_result":
        votes = ev.payload.get("votes", [])
        lines = [
            "、".join(
                (f"{v['seat']}号弃票" if v["target"] == 0 else f"{v['seat']}号→{v['target']}号") for v in votes
            )
            or "无人投票"
        ]
        if ev.payload.get("tie"):
            lines.append("平票，无人出局。")
        elif ev.payload.get("idiot_survived"):
            lines.append(f"{ev.payload['exiled']}号翻牌是：【白痴】，翻牌免死失去投票权，仍留在场上。")
        elif ev.payload.get("exiled") is not None:
            role = ev.payload.get("role") or ""
            if role:
                lines.append(f"{ev.payload['exiled']}号被放逐出局，翻牌是：【{role}】")
            else:
                lines.append(f"{ev.payload['exiled']}号被放逐出局（不翻牌）。")
        return "白天投票结果：" + "。".join(lines)
    if ev.type == "game_over":
        return p_type
    return None


def render_event_god(game: Game, ev: GameEvent) -> str | None:
    """复盘分析用事件行：上帝视角，私密行动标注行动者，投票附理由。"""

    def name_of(seat: int | None) -> str:
        if seat is None:
            return "?"
        try:
            return f"{seat}号 {game.player(seat).name}"
        except KeyError:
            return f"{seat}号"

    t = ev.type
    if t in ("role_assign", "thinking"):
        return None
    if t == "guard_protect":
        return f"【守卫】{name_of(ev.actor)} 守护了 {ev.payload.get('target')}号"
    if t == "seer_result":
        camp = "狼人" if ev.payload.get("is_wolf") else "好人"
        return f"【预言家】{name_of(ev.actor)} 查验 {ev.payload.get('target')}号：{camp}"
    if t == "witch_knife":
        potion = "解药可用" if ev.payload.get("can_save") else "解药已用"
        return f"【女巫】{name_of(ev.actor)} 得知今晚 {ev.payload.get('target')}号 被袭击（{potion}）"
    if t == "witch_save":
        return f"【女巫】{name_of(ev.actor)} 使用解药，救下了 {ev.payload.get('target')}号"
    if t == "witch_poison":
        return f"【女巫】{name_of(ev.actor)} 使用毒药，毒了 {ev.payload.get('target')}号"
    if t == "vote_cast":
        target = ev.payload.get("target")
        reason = ev.payload.get("reason") or ""
        desc = "弃票" if target == 0 else f"投给 {target}号"
        return f"【投票】{name_of(ev.payload.get('seat'))} {desc}" + (f"（{reason}）" if reason else "")
    return render_event(game, ev)


def summary_messages(game: Game) -> list[dict]:
    """整局复盘分析的提示词：上帝视角全量事件 + 底牌 + 结果，要求分析胜负关键。"""
    roles = "、".join(f"{p.seat}号 {p.name}（{p.role_label()}）" for p in sorted(game.players, key=lambda x: x.seat))
    winner = {"wolf": "狼人阵营", "good": "好人阵营", "draw": "和局"}.get(str(game.winner), "未知")
    log = "\n".join(filter(None, (render_event_god(game, e) for e in game.events)))
    system = (
        "你是一名资深的狼人杀复盘分析师，擅长从完整对局记录中还原博弈脉络。"
        "请基于给出的对局信息输出结构化复盘分析：\n"
        "1）整局回顾：按时间线梳理关键昼夜与转折点；\n"
        "2）胜负分析：获胜方为什么赢、落败方为什么输，指出决定性的一手或致命误判；\n"
        "3）关键玩家表现：精彩操作与明显失误，各举其要；\n"
        "4）给落败方的改进建议。\n"
        "分析必须落到具体座位号和具体发言/行动上，不要泛泛而谈。"
    )
    user = (
        f"对局：{game.board.name}（{'屠城局' if game.board.win_condition == 'city' else '屠边局'}），"
        f"共进行 {game.day} 个白天，结果：{winner}获胜。{game.end_reason}\n"
        f"身份底牌：{roles}\n\n"
        "完整事件流（上帝视角，含全部私密行动与投票理由）：\n"
        f"{log}\n\n请输出复盘分析。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


DETAIL_DAYS = 1  # 当前天往前 N 个昼夜保留完整细节，更早的压成结局摘要

# 老昼夜直接丢弃的事件：主持垫话、长篇发言、狼队讨论过程、票型明细以外的流程噪声
_DIGEST_SKIP = {"host_line", "speech", "last_words", "sheriff_speech", "wolf_proposal", "speech_order", "sheriff_vote_cast"}


def _vote_digest(ev: GameEvent) -> str:
    """老昼夜的投票结果压成一行结局，票型明细只保留最近昼夜。"""
    if ev.payload.get("tie"):
        return "白天投票：平票，无人出局。"
    exiled = ev.payload.get("exiled")
    if exiled is None:
        return "白天投票：无人出局。"
    if ev.payload.get("idiot_survived"):
        return f"白天投票：放逐 {exiled}号，翻牌是【白痴】免死，失去投票权。"
    return f"白天投票：放逐 {exiled}号（{ev.payload.get('role') or '不翻牌'}）。"


def render_history(game: Game, seat: int) -> str:
    """按天分组的可见事件时间线。控制上下文长度：历史白天的长篇发言压为省略行；
    更早的昼夜（当前天往前 DETAIL_DAYS 个以外）只留结局类事件——死亡、放逐、
    技能结果、狼队刀口等，主持垫话与讨论过程丢弃，私密查验/用药记录保留。"""
    events = view_events(game, seat)
    lines: list[str] = []
    current_day = 0
    day_has_speech = False
    compressed_header_shown = False
    detail_from = game.day - DETAIL_DAYS

    def flush_day():
        nonlocal compressed_header_shown
        if day_has_speech and current_day < game.day:
            if not compressed_header_shown:
                lines.append(f"（第 {current_day} 天白天众人依次发言，内容从略）")
                compressed_header_shown = True

    for ev in events:
        if ev.day != current_day:
            flush_day()
            day_has_speech = False
            compressed_header_shown = False
            current_day = ev.day
            if ev.day > 0 and ev.type != "role_assign":
                lines.append(f"—— 第 {ev.day} 天 ——")
        if ev.day < game.day and ev.type == "speech":
            day_has_speech = True
            continue
        if ev.day < detail_from:
            if ev.type in _DIGEST_SKIP:
                continue
            text = _vote_digest(ev) if ev.type == "vote_result" else render_event(game, ev)
        else:
            text = render_event(game, ev)
        if text:
            lines.append(text)
    flush_day()
    return "\n".join(lines) or "（游戏刚开始，还没有任何事件）"


def alive_list(game: Game) -> str:
    parts = []
    for p in sorted(game.alive_players(), key=lambda x: x.seat):
        parts.append(f"{p.seat}号 {p.name}{'（你）' if p.is_human else ''}")
    return "、".join(parts)


def _board_line(game: Game) -> str:
    """开局公示：人数、板子名、屠边/屠城规则、全场公开的身份配置。"""
    counts: dict[Role, int] = {}
    for r in game.board.roles:
        counts[r] = counts.get(r, 0) + 1
    comp = "、".join(
        f"{ROLE_LABELS[r]}×{n}" if n > 1 else ROLE_LABELS[r] for r, n in counts.items()
    )
    win = (
        "屠城局（狼人需杀光全部好人：存活狼数不少于存活好人数即狼胜）"
        if game.board.win_condition == WinCondition.CITY
        else "屠边局（狼人杀光全部神职、或杀光全部村民即狼胜；白痴归村民侧）"
    )
    return (
        f"本局共 {len(game.players)} 名玩家。板子配置：{game.board.name}，{win}。"
        f"身份配置全场公开：{comp}——开局即可据此数狼坑，配合出局情况推算剩余阵营。"
    )


def system_prompt(game: Game, player: Player) -> str:
    role = player.role
    label = ROLE_LABELS.get(role, "?") if role else "?"
    lines = [
        f"你是「{player.name}」，正在和其他玩家玩一局狼人杀。",
        f"你的性格设定：{player.persona or '普通玩家'}。发言和决策都要符合这个设定。",
        f"你的座位是 {player.seat}号。你的秘密身份是：{label}。",
        _board_line(game),
        "节奏要求：这是实时对局，所有玩家都在等你的行动。无论发言、投票还是用技能，"
        "思考只保留结论和关键依据，几句话定案就行动，不要长篇分析、反复权衡或自我推翻。",
    ]
    if role == Role.WEREWOLF:
        mates = "、".join(f"{p.seat}号 {p.name}" for p in game.players if p.role == Role.WEREWOLF and p.seat != player.seat) or "（没有队友）"
        lines.append(
            f"你是狼人，你的狼队友是：{mates}（仅你可知，发言中不得暴露，除非你决定悍跳或自爆）。"
            "每晚你和队友投票决定刀口；白天你要伪装好人，用发言和投票带节奏。"
            "若你夜里被刀又被女巫救下，你和女巫之间会形成一层只有你们俩知道的银水关系——她会把你当好人，这是帮你洗白的隐形资产。"
            "但刀口是谁只有狼队知道：没有女巫公开认领时，你主动宣称「昨晚我被刀被救」等于自曝知道刀口内情，反而坐实狼面；"
            "女巫没救你却谎称被救，她一句话就能戳穿。银水要含蓄地用：等女巫先认领再顺势接过，或让这层关系自然发酵，别抢着声张。"
        )
    elif role == Role.SEER:
        lines.append(
            "你是预言家，每晚可以查验一名玩家的阵营（狼人/好人）。"
            "你可以选择跳预言家报查杀带队，也可以藏身份，自行权衡。"
        )
    elif role == Role.WITCH:
        lines.append(
            "你是女巫，拥有一瓶解药和一瓶毒药（整局各限一次，同一晚只能用一瓶，不能自救）。"
            "解药可以救活当晚被袭击的玩家；毒药可以毒死一名玩家。用药信息完全保密，"
            "白天以逻辑发言为主，不主动暴露药型，不要说「我是神」这类容易送刀的话。"
        )
    elif role == Role.HUNTER:
        lines.append(
            "你是猎人，当你出局时（被毒杀除外）可以开枪带走一名玩家。是否开枪、带谁由你决定，平时藏好身份。"
        )
    elif role == Role.GUARD:
        lines.append(
            "你是守卫，每晚可以守护一名玩家（包括自己），被守护的玩家当晚不会被狼刀杀（守护不防毒药）。"
            "你不能连续两晚守护同一名玩家。若同晚既守护又被女巫救（同守同救），玩家仍会死亡。"
        )
    elif role == Role.VILLAGER:
        lines.append("你是普通村民，没有夜间技能。靠听发言、看票型找出狼人，用投票保护好人阵营。")
    elif role == Role.IDIOT:
        lines.append(
            "你是白痴，平时和村民一样没有夜间技能。"
            "若你白天被投票放逐，会翻牌亮明白痴身份并免死，但从此失去投票权，仍可继续发言；"
            "若再次被放逐或夜晚被害，你就会真正出局。"
        )

    lines.append(
        "规则：夜晚守卫守护、狼人刀人、预言家验人、女巫用药；白天全员依次发言后投票放逐一人（平票无人出局）；"
        "守卫守护只防刀不防毒，同守同救仍死，不能连续两晚守同一人；"
        "对局中出局不翻牌（白痴被放逐翻牌免死除外），游戏结束后公开所有身份；猎人出局可开枪（被毒除外）；"
        "狼人白天发言阶段可自爆：翻牌亮明狼人身份立即出局（无遗言），当天发言立即结束直接入夜，狼队当晚照常行动；"
        "白痴被投票放逐时翻牌免死并失去投票权（夜晚被害或再次被放逐则真出局）；"
        "遗言：只有第一晚夜晚死亡的玩家与被放逐者有遗言，其余死亡没有遗言。"
        "他人的身份声称只是口头声明，翻牌才算公开确认，被投票出局也不等于验明是狼。"
        "引用别人的发言要给对天数和座位号，不要编造别人没说过的话。"
        "严格只依据系统提供给你的信息行动，绝不能引用你视角之外的信息，不要编造没发生的事。"
        f"{strategy.EVIDENCE_DISCIPLINE}"
    )
    if game.sheriff_enabled:
        lines.append(
            "本局开启警长竞选：第 1 天天亮后先上警竞选警长，候选人依次发表竞选宣言，未上警的玩家投票选出警长"
            "（平票加赛一轮，仍平票则警徽作废）；警长投票计 1.5 票；警长出局时自主决定移交警徽给一名存活玩家或撕毁警徽；"
            "警长决定首日发言从自己的左边还是右边开始，警长永远最后一个发言。"
        )
        if game.badge_swallow == "single":
            lines.append("本局启用单爆吞警徽：警长竞选发言期间狼人在警上自爆，竞选立即终止、警徽本局流失，直接入夜。")
        elif game.badge_swallow == "double":
            lines.append(
                "本局启用双爆吞警徽：警长竞选发言期间狼人在警上自爆会终止竞选直接入夜，次日白天重新竞选警长；"
                "狼队需在警上先后自爆两次，警徽才被彻底吞掉。"
            )
        else:
            lines.append("本局未开启吞警徽：警长竞选期间不能自爆，自爆只能在白天发言阶段使用。")
    else:
        lines.append("本局不设警长竞选，第 1 天天亮后直接进入白天发言。")
    return "\n".join(lines)


def _night_deaths_by_day(events: list[GameEvent]) -> dict[int, set[int]]:
    deaths: dict[int, set[int]] = {}
    for e in events:
        if e.type == "night_settle":
            deaths[e.day] = set(e.payload.get("deaths") or [])
    return deaths


def private_ledger(game: Game, player: Player) -> str:
    """角色私有账本：把散在时间线里的夜间动作汇总成表；狼人/守卫的刀口附结局归因（被守或被救）。"""
    events = view_events(game, player.seat)
    lines: list[str] = []
    if player.role == Role.SEER:
        checks = [e for e in events if e.type == "seer_result"]
        if checks:
            rows = [f"第{e.day}夜 {e.payload.get('target')}号=" + ("狼人" if e.payload.get("is_wolf") else "好人") for e in checks]
            lines.append("查验记录：" + "；".join(rows))
    elif player.role == Role.WITCH:
        used_save = any(e.type == "witch_save" for e in events)
        used_poison = any(e.type == "witch_poison" for e in events)
        lines.append(
            "药水状态：" + ("解药已用" if used_save else "解药可用") + "、" + ("毒药已用" if used_poison else "毒药可用")
        )
        knives = [e for e in events if e.type == "witch_knife"]
        if knives:
            lines.append("已知刀口：" + "；".join(f"第{e.day}夜刀 {e.payload.get('target')}号" for e in knives))
        acts = [f"第{e.day}夜救 {e.payload.get('target')}号" for e in events if e.type == "witch_save"] + [
            f"第{e.day}夜毒 {e.payload.get('target')}号" for e in events if e.type == "witch_poison"
        ]
        if acts:
            lines.append("用药记录：" + "；".join(acts))
    elif player.role == Role.GUARD:
        guards = [e for e in events if e.type == "guard_protect"]
        if guards:
            deaths = _night_deaths_by_day(events)
            rows = []
            for e in guards:
                t = e.payload.get("target")
                rows.append(f"第{e.day}夜守 {t}号（{'目标当夜出局' if t in deaths.get(e.day, set()) else '目标当夜存活'}）")
            lines.append("守护记录：" + "；".join(rows))
    elif player.role == Role.WEREWOLF:
        knives = [e for e in events if e.type == "wolf_target" and e.payload.get("target")]
        if knives:
            deaths = _night_deaths_by_day(events)
            rows = []
            for e in knives:
                t = e.payload.get("target")
                rows.append(f"第{e.day}夜刀 {t}号（{'目标出局' if t in deaths.get(e.day, set()) else '目标存活：被守或被救'}）")
            lines.append("狼队刀口：" + "；".join(rows))
    return "\n".join(lines)


def grounding_facts(game: Game, player: Player) -> str:
    """基于事件计算的客观事实锚点：点名提及、昨日同票、相邻座位死亡（座位学）。"""
    events = view_events(game, player.seat)
    facts: list[str] = []
    mentioned = []
    for e in events:
        if e.day == game.day and e.type == "speech" and e.actor != player.seat:
            text = e.payload.get("text") or ""
            if re.search(rf"(?<![0-9]){player.seat}号", text) or (player.name and player.name in text):
                mentioned.append(e.actor)
    if mentioned:
        facts.append("今天点名提到你：" + "、".join(f"{s}号" for s in mentioned[:3]))
    if game.day >= 2:
        for e in events:
            if e.type == "vote_result" and e.day == game.day - 1:
                votes = {v["seat"]: v.get("target") for v in e.payload.get("votes", [])}
                mine = votes.get(player.seat)
                if mine:
                    same = sorted(s for s, t in votes.items() if t == mine and s != player.seat)
                    if same:
                        facts.append(f"昨天你与{'、'.join(f'{s}号' for s in same[:3])}同票（都投{mine}号）")
                break
    n = len(game.players)
    adj = sorted(p.seat for p in game.players if not p.alive and (player.seat - p.seat) % n in (1, n - 1))
    if adj:
        facts.append("与你相邻座位的出局玩家（仅客观信息，座位相邻本身不构成怀疑理由）：" + "、".join(f"{s}号" for s in adj[:4]))
    return "；".join(facts)


def _user(game: Game, player: Player, task: str, extra: str = "") -> str:
    parts = [f"【当前局势】第 {game.day} 天。存活玩家：{alive_list(game)}。"]
    ledger = private_ledger(game, player)
    if ledger:
        parts.append(f"【你的私有记录】{ledger}")
    facts = grounding_facts(game, player)
    if facts:
        parts.append(f"【与你的关联】{facts}")
    parts.append(f"【你知道的全部信息】\n{render_history(game, player.seat)}")
    if extra:
        parts.append(extra)
    parts.append(f"【你的任务】{task}")
    return "\n\n".join(parts)


def _messages(game: Game, player: Player, task: str, extra: str = "") -> list[dict]:
    return [
        {"role": "system", "content": system_prompt(game, player)},
        {"role": "user", "content": _user(game, player, task, extra)},
    ]


_SPEECH_CRAFTS = {
    Role.WEREWOLF: strategy.SPEECH_WOLF,
    Role.SEER: strategy.SPEECH_SEER,
    Role.WITCH: strategy.SPEECH_WITCH,
    Role.HUNTER: strategy.SPEECH_HUNTER,
    Role.GUARD: strategy.SPEECH_GUARD,
    Role.VILLAGER: strategy.SPEECH_VILLAGER,
    Role.IDIOT: strategy.SPEECH_IDIOT,
}


def speech_messages(game: Game, player: Player) -> list[dict]:
    role_craft = _SPEECH_CRAFTS.get(player.role, "")
    anti_wolf = "" if player.role == Role.WEREWOLF else strategy.WOLF_ARCHETYPES
    return _messages(
        game,
        player,
        f"轮到你发言了。{SPEECH_LIMIT}{strategy.SPEECH_CRAFT}{role_craft}{strategy.GAME_READ}{anti_wolf}{strategy.TABLETALK}"
        "直接输出发言内容，不要任何旁白或引号。",
    )


_LAST_WORDS_NOTES = {
    Role.SEER: strategy.LAST_WORDS_SEER,
    Role.WITCH: strategy.LAST_WORDS_WITCH,
    Role.HUNTER: strategy.LAST_WORDS_HUNTER,
    Role.GUARD: strategy.LAST_WORDS_GUARD,
}


def last_words_messages(game: Game, player: Player) -> list[dict]:
    if player.role == Role.WEREWOLF:
        craft = strategy.WOLF_LAST_WORDS
    else:
        craft = strategy.GOOD_LAST_WORDS + _LAST_WORDS_NOTES.get(player.role, "")
    return _messages(game, player, f"你已出局，请留下遗言。{SPEECH_LIMIT}{craft}直接输出内容。")


def vote_messages(game: Game, player: Player, candidates: list[int]) -> list[dict]:
    extra = f"【可投票对象】{'、'.join(f'{s}号 {game.player(s).name}' for s in candidates)}"
    has_idiot = Role.IDIOT in game.board.roles
    if player.role == Role.WEREWOLF:
        craft = strategy.WOLF_VOTE_CRAFT + (strategy.IDIOT_NOTE_WOLF if has_idiot else "")
    else:
        craft = strategy.VOTE_CRAFT + strategy.WOLF_ARCHETYPES + (strategy.IDIOT_NOTE_GOOD if has_idiot else "")
    return _messages(
        game,
        player,
        f'请投票放逐一人；如果暂时没有把握，也可以弃票。{craft}输出 JSON：{{"target": 座位号（弃票填 0）, "reason": "简短投票理由"}}。',
        extra,
    )


def wolf_proposal_messages(game: Game, wolf: Player) -> list[dict]:
    extra = "【可刀对象】所有存活玩家（包括自刀）。"
    humans = [p for p in game.players if p.is_human]  # 上帝观战局无真人，human() 会抛错，这里直接扫
    if humans and humans[0].alive and humans[0].role == Role.WEREWOLF:
        h = humans[0]
        extra += f"真人队友（{h.seat}号）拥有最终拍板权：你的提议供参考协商，最终刀口以其选择为准。"
    idiot_note = strategy.IDIOT_NOTE_WOLF if Role.IDIOT in game.board.roles else ""
    return _messages(
        game,
        wolf,
        f'狼队夜聊：请提议今晚的刀口。{strategy.WOLF_KNIFE_CRAFT}{idiot_note}输出 JSON：{{"target": 座位号, "reason": "给队友的一句话理由"}}。',
        extra,
    )


def seer_messages(game: Game, seer: Player, candidates: list[int]) -> list[dict]:
    extra = f"【可查验对象】{'、'.join(f'{s}号 {game.player(s).name}' for s in candidates)}"
    return _messages(game, seer, f'请选择今晚查验的对象。{strategy.SEER_CHECK_CRAFT}输出 JSON：{{"target": 座位号}}。', extra)


def witch_save_messages(game: Game, witch: Player, victim: int, can_save: bool) -> list[dict]:
    note = "今晚被袭击的正是你自己，女巫不能自救。" if victim == witch.seat else f"今晚 {victim}号 {game.player(victim).name} 被袭击了。"
    if not can_save:
        note += "（你的解药已经用完）"
    task = (
        f'请决定是否使用解药。{strategy.WITCH_SAVE_CRAFT}输出 JSON：{{"use": true/false}}。'
        if can_save
        else '解药不可用。输出 JSON：{"use": false}。'
    )
    return _messages(game, witch, task, note)


def witch_poison_messages(game: Game, witch: Player, candidates: list[int]) -> list[dict]:
    extra = f"【可毒对象】{'、'.join(f'{s}号 {game.player(s).name}' for s in candidates)}（也可以不用）"
    return _messages(
        game,
        witch,
        f'请决定是否使用毒药。{strategy.WITCH_POISON_CRAFT}输出 JSON：{{"use": true/false, "target": 座位号（不用毒时为 0）}}。',
        extra,
    )


def hunter_messages(game: Game, hunter: Player, candidates: list[int]) -> list[dict]:
    extra = f"【可开枪对象】{'、'.join(f'{s}号 {game.player(s).name}' for s in candidates)}"
    return _messages(
        game,
        hunter,
        f'你出局了（非毒杀），可以选择开枪带走一人或放弃。{strategy.HUNTER_CRAFT}输出 JSON：{{"shoot": true/false, "target": 座位号（不开枪为 0）}}。',
        extra,
    )


def guard_messages(game: Game, guard: Player, candidates: list[int]) -> list[dict]:
    extra = f"【可守护对象】{'、'.join(f'{s}号 {game.player(s).name}' for s in candidates)}（可以守自己）"
    return _messages(game, guard, f'请选择今晚守护的对象。{strategy.GUARD_CRAFT}输出 JSON：{{"target": 座位号}}。', extra)


def self_explode_messages(game: Game, wolf: Player) -> list[dict]:
    return _messages(
        game,
        wolf,
        f"轮到你发言了，你可以选择自爆。{strategy.SELF_EXPLODE_CRAFT}"
        '输出 JSON：{"explode": true/false}。',
    )


def _sheriff_explode_rule_line(game: Game) -> str:
    """警上自爆的规则说明：按单爆/双爆当前进度分支。"""
    if game.badge_swallow == "single":
        return "单爆吞警徽：你在警上自爆后，竞选立即终止、警徽本局流失，直接进入黑夜。"
    if game.sheriff_revote:
        return "现在是重新竞选（双爆吞警徽最后一步）：你在警上自爆，警徽将被彻底吞掉，本局不再有警长。"
    return (
        "双爆吞警徽第一步：你在警上自爆后，竞选立即终止、今晚直接入夜；明天白天将重新竞选警长，"
        "狼队还需再有一人在明天警上自爆，才能彻底吞掉警徽。"
    )


def self_explode_campaign_messages(game: Game, wolf: Player) -> list[dict]:
    """警上竞选宣言回合的自爆决策（仅吞警徽规则开启时调用）。"""
    return _messages(
        game,
        wolf,
        f"警长竞选进行中，轮到你发表竞选宣言，你也可以选择不发言直接自爆。{_sheriff_explode_rule_line(game)}"
        f"{strategy.SELF_EXPLODE_SHERIFF_CRAFT}输出 JSON：{{\"explode\": true/false}}。",
    )


def self_explode_trigger_messages(game: Game, wolf: Player, speech_seat: int, text: str, campaign: bool = False) -> list[dict]:
    """查杀触发版：他人发言点名你是狼时，在自己的回合之外立即决策是否自爆。"""
    if campaign:
        craft = f"{_sheriff_explode_rule_line(game)}{strategy.SELF_EXPLODE_SHERIFF_CRAFT}"
        stage = "竞选发言"
    else:
        craft = strategy.SELF_EXPLODE_CRAFT
        stage = "发言"
    return _messages(
        game,
        wolf,
        f"刚结束的{stage}里，{speech_seat}号 说：「{text[:120]}」。你现在可以立即自爆"
        f"（无需等你的发言回合）。{craft}"
        "注意：对方只是口头声称，若并不构成你的实锤，就否认并继续游戏。"
        '输出 JSON：{"explode": true/false}。',
    )


def sheriff_run_messages(game: Game, player: Player) -> list[dict]:
    if player.role == Role.SEER:
        craft = strategy.SHERIFF_RUN_SEER
    elif player.role == Role.WEREWOLF:
        craft = strategy.SHERIFF_RUN_WOLF
    elif player.role == Role.VILLAGER:
        craft = strategy.SHERIFF_RUN_VILLAGER
    else:
        craft = strategy.SHERIFF_RUN_GOD
    return _messages(
        game,
        player,
        f'警长竞选开始：是否上警参选（上警需发表竞选宣言并接受投票）？{craft}输出 JSON：{{"run": true/false}}。',
    )


def sheriff_campaign_messages(game: Game, player: Player) -> list[dict]:
    if player.role == Role.SEER:
        craft = strategy.CAMPAIGN_SEER
    elif player.role == Role.WEREWOLF:
        craft = strategy.CAMPAIGN_WOLF
    else:
        craft = strategy.CAMPAIGN_OTHER
    return _messages(
        game,
        player,
        f"轮到你发表竞选宣言了。说明你的视角与判断、为什么值得信任警徽。{SPEECH_LIMIT}{craft}"
        "直接输出内容，不要旁白或引号。",
    )


def sheriff_vote_messages(game: Game, voter: Player, candidates: list[int]) -> list[dict]:
    extra = f"【候选人】{'、'.join(f'{s}号 {game.player(s).name}' for s in candidates)}"
    return _messages(
        game,
        voter,
        f'请投票选出警长（候选人不能投票），也可以弃票。{strategy.SHERIFF_VOTE_CRAFT}输出 JSON：{{"target": 座位号（弃票填 0）}}。',
        extra,
    )


def badge_messages(game: Game, player: Player, candidates: list[int]) -> list[dict]:
    extra = f"【可移交对象】{'、'.join(f'{s}号 {game.player(s).name}' for s in candidates)}"
    craft = strategy.BADGE_CRAFT
    if player.role == Role.SEER:
        craft += strategy.BADGE_SEER_NOTE
    elif player.role == Role.WEREWOLF:
        craft += strategy.BADGE_WOLF_NOTE
    return _messages(
        game,
        player,
        f'你出局了，警徽在你手里。{craft}输出 JSON：{{"destroy": true/false, "target": 座位号（撕毁时为 0）}}。',
        extra,
    )


def speech_order_messages(game: Game, sheriff: Player) -> list[dict]:
    return _messages(
        game,
        sheriff,
        '你当选警长，请决定首日发言方向：left=从你上一位（座位号小的一侧）开始，right=从你下一位（座位号大的一侧）开始（你永远最后发言）。输出 JSON：{"direction": "left" 或 "right"}。',
    )
