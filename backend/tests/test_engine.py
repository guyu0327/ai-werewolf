from app.engine.board import BUILTIN_BOARDS, validate_board
from app.engine.game import (
    apply_exile,
    apply_self_explode,
    check_win,
    emit,
    flatten_event,
    resolve_night_deaths,
    sheriff_weighted_counts,
    start_game,
    strip_vote_reason,
    tally_wolf_votes,
    visible_to,
    view_events,
)
from app.models import BoardConfig, Game, Player, Role, WinCondition


def make_game(board: BoardConfig | None = None, n: int = 6) -> Game:
    b = board or BUILTIN_BOARDS[0]
    players = [Player(seat=i, name=f"P{i}", is_human=(i == 1)) for i in range(1, n + 1)]
    return Game(id="t1", created_at="now", board=b, players=players, seed=42)


def test_assign_roles_deterministic():
    g1, g2 = make_game(), make_game()
    start_game(g1)
    start_game(g2)
    assert [p.role for p in g1.players] == [p.role for p in g2.players]
    assert sorted(p.role.value for p in g1.players) == [
        "seer",
        "villager",
        "villager",
        "werewolf",
        "werewolf",
        "witch",
    ]
    assert g1.day == 1 and g1.phase.value == "night_guard"


def test_assign_roles_preferred_werewolf():
    g = make_game()
    g.preferred_role = Role.WEREWOLF
    start_game(g)
    assert g.human().role == Role.WEREWOLF
    # 换牌不增减：全场角色仍是板子配置的多重集
    assert sorted(p.role.value for p in g.players) == sorted(r.value for r in g.board.roles)


def test_assign_roles_preferred_villager_two_copies():
    # 板子里有两张村民：自选村民只占用一张，另一张仍随机分给 AI
    g = make_game()
    g.preferred_role = Role.VILLAGER
    start_game(g)
    assert g.human().role == Role.VILLAGER
    assert [p.role for p in g.players].count(Role.VILLAGER) == 2


def test_assign_roles_preferred_not_in_board_falls_back_random():
    # preferred 不在板子里：静默回退随机发牌，不抛错
    g = make_game()
    g.preferred_role = Role.HUNTER  # 6 人板无猎人
    start_game(g)
    assert g.human().role != Role.HUNTER
    assert sorted(p.role.value for p in g.players) == sorted(r.value for r in g.board.roles)


def test_role_assign_events_are_private():
    g = make_game()
    start_game(g)
    for p in g.players:
        mine = [e for e in view_events(g, p.seat) if e.type == "role_assign"]
        assert len(mine) == 1
        assert mine[0].payload["role"] == p.role.value


def test_check_win_city():
    g = make_game()
    start_game(g)
    roles = {p.seat: p.role for p in g.players}
    wolf_seats = [s for s, r in roles.items() if r == Role.WEREWOLF]
    good_seats = [s for s, r in roles.items() if r != Role.WEREWOLF]
    # 杀掉一个好人：2狼 vs 3好，未达成
    g.player(good_seats[0]).alive = False
    assert check_win(g) is None
    # 再杀一个：2狼 vs 2好，屠城狼胜
    g.player(good_seats[1]).alive = False
    assert check_win(g) == "wolf"
    # 狼全灭好人胜
    for s in wolf_seats:
        g.player(s).alive = False
    assert check_win(g) == "good"


def test_check_win_side():
    g = make_game(BUILTIN_BOARDS[1], n=9)
    start_game(g)
    for p in g.players:
        if p.role in (Role.SEER, Role.WITCH, Role.HUNTER):
            p.alive = False
    assert check_win(g) == "wolf"  # 神职全灭（屠边）
    g2 = make_game(BUILTIN_BOARDS[1], n=9)
    start_game(g2)
    for p in g2.players:
        if p.role == Role.VILLAGER:
            p.alive = False
    assert check_win(g2) == "wolf"  # 村民全灭（屠边）


def test_resolve_night_deaths():
    assert resolve_night_deaths(3, None, False) == [("knife", 3)]
    assert resolve_night_deaths(3, None, True) == []
    assert resolve_night_deaths(3, 5, False) == [("knife", 3), ("poison", 5)]
    # 同一刀毒目标去重
    assert resolve_night_deaths(3, 3, False) == [("knife", 3)]
    assert resolve_night_deaths(3, 3, True) == [("poison", 3)]


def test_tally_wolf_votes_majority_and_tie():
    votes = {"2": {"target": 4}, "3": {"target": 4}, "5": {"target": 6}}
    target, randomized = tally_wolf_votes(votes, seed=1, day=1)
    assert target == 4 and not randomized
    tie = {"2": {"target": 4}, "3": {"target": 6}}
    t1, r1 = tally_wolf_votes(tie, seed=99, day=1)
    t2, r2 = tally_wolf_votes(tie, seed=99, day=1)
    assert r1 and r2
    assert t1 == t2  # 同 seed 同 day 伪随机结果一致（恢复确定性）
    assert t1 in (4, 6)


def test_visibility_isolation():
    g = make_game()
    start_game(g)
    wolf = next(p for p in g.players if p.role == Role.WEREWOLF)
    seer = next(p for p in g.players if p.role == Role.SEER)
    villager = next(p for p in g.players if p.role == Role.VILLAGER)

    e_wolf = emit(g, "wolf_proposal", {"target": 2}, actor=wolf.seat, visibility="wolves")
    e_seer = emit(g, "seer_result", {"target": 2, "is_wolf": True}, actor=seer.seat, visibility=f"seat:{seer.seat}")
    e_pub = emit(g, "host_line", {"text": "天亮了"})

    assert visible_to(g, e_wolf, wolf.seat)
    assert not visible_to(g, e_wolf, villager.seat)
    assert visible_to(g, e_seer, seer.seat)
    assert not visible_to(g, e_seer, wolf.seat)
    assert visible_to(g, e_pub, villager.seat)


def test_validate_board_rules():
    validate_board([Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH], WinCondition.CITY, 20)
    try:
        validate_board([Role.WEREWOLF] * 5, WinCondition.CITY, 20)
        assert False, "狼人占满应报错"
    except ValueError:
        pass
    try:
        validate_board([Role.WEREWOLF, Role.VILLAGER, Role.VILLAGER, Role.SEER, Role.WITCH, Role.SEER], WinCondition.SIDE, 20)
        assert False, "双预言家应报错"
    except ValueError:
        pass
    try:
        validate_board([Role.WEREWOLF, Role.WEREWOLF, Role.SEER, Role.WITCH, Role.HUNTER], WinCondition.SIDE, 20)
        assert False, "屠边局无村民应报错"
    except ValueError:
        pass


# ---------- 白痴 ----------


def twelve_board() -> BoardConfig:
    return next(b for b in BUILTIN_BOARDS if b.id == "twelve")


def test_apply_exile_idiot_first_flip_survives():
    g = make_game(twelve_board(), n=12)
    g.preferred_role = Role.IDIOT
    start_game(g)
    idiot = g.human()
    assert idiot.role == Role.IDIOT
    info = apply_exile(g, idiot.seat)
    assert info == {"idiot_survived": True}
    assert idiot.alive and idiot.role_revealed and idiot.vote_lost


def test_apply_exile_idiot_second_exile_dies():
    g = make_game(twelve_board(), n=12)
    g.preferred_role = Role.IDIOT
    start_game(g)
    idiot = g.human()
    assert apply_exile(g, idiot.seat) == {"idiot_survived": True}
    assert apply_exile(g, idiot.seat) == {"idiot_survived": False}
    assert not idiot.alive


def test_apply_exile_normal_role_dies():
    g = make_game()
    start_game(g)
    villager = next(p for p in g.players if p.role == Role.VILLAGER)
    assert apply_exile(g, villager.seat) == {"idiot_survived": False}
    # 出局不翻牌
    assert not villager.alive and not villager.role_revealed


def test_apply_self_explode():
    """狼人自爆：翻牌亮明身份，立即死亡，无遗言。"""
    g = make_game()
    start_game(g)
    wolf = next(p for p in g.players if p.role == Role.WEREWOLF)
    apply_self_explode(g, wolf.seat)
    assert not wolf.alive
    assert wolf.role_revealed
    assert not g.player(wolf.seat).vote_lost


def test_check_win_side_idiot_in_villager_bucket():
    g = make_game(twelve_board(), n=12)
    start_game(g)
    assert sorted(p.role.value for p in g.players) == sorted(r.value for r in g.board.roles)
    # 村民全灭，存活 4狼+预言家+翻牌白痴：神职与民桶各有活人，未达成屠边
    for p in g.players:
        if p.role == Role.VILLAGER:
            p.alive = False
    idiot = next(p for p in g.players if p.role == Role.IDIOT)
    apply_exile(g, idiot.seat)
    assert check_win(g) is None
    # 白痴再出局：民桶空，屠边狼胜
    idiot.alive = False
    assert check_win(g) == "wolf"


def test_validate_twelve_board():
    b = twelve_board()
    assert len(b.roles) == 12
    validate_board(b.roles, b.win_condition, b.max_days)  # 不抛错
    try:
        validate_board(
            [Role.WEREWOLF, Role.VILLAGER, Role.SEER, Role.WITCH, Role.HUNTER, Role.IDIOT, Role.IDIOT],
            WinCondition.SIDE,
            20,
        )
        assert False, "双白痴应报错"
    except ValueError:
        pass
    # 屠边局没有村民但有白痴：白痴算民桶，合法
    validate_board([Role.WEREWOLF, Role.SEER, Role.WITCH, Role.HUNTER, Role.IDIOT], WinCondition.SIDE, 20)


# ---------- 守卫 ----------


def guard_board() -> BoardConfig:
    return next(b for b in BUILTIN_BOARDS if b.id == "twelve_guard")


def test_resolve_night_deaths_guard():
    # 守卫守刀口：刀被挡
    assert resolve_night_deaths(3, None, False, guard=3) == []
    # 守卫守别人：刀照常
    assert resolve_night_deaths(3, None, False, guard=5) == [("knife", 3)]
    # 同守同救：仍死（经典规则）
    assert resolve_night_deaths(3, None, True, guard=3) == [("knife", 3)]
    # 救药但守卫守别人：救活
    assert resolve_night_deaths(3, None, True, guard=5) == []
    # 守护不防毒
    assert resolve_night_deaths(None, 3, False, guard=3) == [("poison", 3)]
    # 刀被守 + 毒同一人：毒仍生效
    assert resolve_night_deaths(3, 3, False, guard=3) == [("poison", 3)]
    # 刀被守 + 毒别人
    assert resolve_night_deaths(3, 5, False, guard=3) == [("poison", 5)]


def test_validate_twelve_guard_board():
    b = guard_board()
    assert len(b.roles) == 12
    assert b.roles.count(Role.GUARD) == 1
    validate_board(b.roles, b.win_condition, b.max_days)  # 不抛错
    try:
        validate_board(
            [Role.WEREWOLF, Role.VILLAGER, Role.SEER, Role.WITCH, Role.HUNTER, Role.GUARD, Role.GUARD],
            WinCondition.SIDE,
            20,
        )
        assert False, "双守卫应报错"
    except ValueError:
        pass


def test_check_win_guard_in_gods_bucket():
    g = make_game(guard_board(), n=12)
    start_game(g)
    assert sorted(p.role.value for p in g.players) == sorted(r.value for r in g.board.roles)
    # 守卫属神职：屠边局守卫+预女猎全灭 → 神职全灭狼胜（民还活着）
    for p in g.players:
        if p.role in (Role.SEER, Role.WITCH, Role.HUNTER, Role.GUARD):
            p.alive = False
    assert check_win(g) == "wolf"


def test_assign_roles_preferred_guard():
    g = make_game(guard_board(), n=12)
    g.preferred_role = Role.GUARD
    start_game(g)
    assert g.human().role == Role.GUARD
    assert sorted(p.role.value for p in g.players) == sorted(r.value for r in g.board.roles)


# ---------- 警长竞选 ----------


def test_sheriff_weighted_counts():
    votes = [
        {"seat": 1, "target": 3},
        {"seat": 2, "target": 3},
        {"seat": 3, "target": 5},
        {"seat": 4, "target": 5},
        {"seat": 5, "target": 1},
    ]
    # 警长 3 号投票计 1.5：5号 得 1+1.5=2.5，3号 得 2
    counts = sheriff_weighted_counts(votes, sheriff_seat=3)
    assert counts[3] == 2.0
    assert counts[5] == 2.5
    assert counts[1] == 1.0
    # 无警长时权重全为 1
    counts = sheriff_weighted_counts(votes, sheriff_seat=None)
    assert counts == {3: 2.0, 5: 2.0, 1: 1.0}
    # 警长自己被投：1.5 权作用于他投出的票（1号投3号 → 3号 2.5），他被投不受影响
    counts = sheriff_weighted_counts(votes, sheriff_seat=1)
    assert counts[3] == 2.5
    assert counts[1] == 1.0
    # 弃票（target=0）不计入任何人的得票
    counts = sheriff_weighted_counts(votes + [{"seat": 6, "target": 0}], sheriff_seat=3)
    assert 0 not in counts
    assert counts[3] == 2.0 and counts[5] == 2.5 and counts[1] == 1.0

def test_strip_vote_reason():
    """投票理由仅上帝视角可见：非上帝视角的事件副本剥掉 reason，其余事件不受影响。"""
    cast = {"seq": 1, "day": 1, "type": "vote_cast", "actor": 2, "seat": 2, "name": "P2", "target": 5, "reason": "5号像神"}
    stripped = strip_vote_reason(cast, "vote_cast")
    assert "reason" not in stripped and stripped["target"] == 5 and stripped["seq"] == 1
    assert cast["reason"] == "5号像神"  # 原事件不被改动（上帝视角仍可见）

    result = {
        "seq": 2,
        "day": 1,
        "type": "vote_result",
        "votes": [{"seat": 2, "target": 5, "reason": "5号像神"}, {"seat": 3, "target": 0, "reason": ""}],
        "tie": False,
        "exiled": 5,
        "role": "村民",
    }
    stripped = strip_vote_reason(result, "vote_result")
    assert all("reason" not in v for v in stripped["votes"])
    assert stripped["tie"] is False and stripped["exiled"] == 5 and stripped["role"] == "村民"

    # 其他事件（如 sheriff_result 的 reason 是公开作废原因）原样返回
    other = {"type": "sheriff_result", "destroyed": True, "reason": "无人上警"}
    assert strip_vote_reason(other, "sheriff_result") is other


def test_render_vote_result_hides_reason():
    """AI 上下文渲染 vote_result 只含票面，不含任何投票理由文本。"""
    from app.llm.prompts import render_event

    game = make_game()
    game.day = 1
    ev = emit(
        game,
        "vote_result",
        {"votes": [{"seat": 2, "target": 5, "reason": "5号发言像神"}], "tie": False, "exiled": 5, "role": "村民"},
    )
    text = render_event(game, ev)
    assert "像神" not in text and "理由" not in text
    assert "2号→5号" in text and "【村民】" in text
    # 事件本体仍带 reason（flatten 后供上帝视角/复盘使用）
    assert flatten_event(ev)["votes"][0]["reason"] == "5号发言像神"

def test_render_history_layered_compression():
    """分层历史：老昼夜只留结局摘要（垫话/发言/狼队讨论/票型明细丢弃），近两昼夜保留细节。"""
    from app.llm.prompts import render_history

    game = make_game()
    start_game(game)

    game.day = 1
    emit(game, "host_line", {"text": "守卫请睁眼"})
    emit(game, "speech", {"text": "我觉得1号很可疑"}, actor=2)
    emit(game, "vote_result", {"votes": [{"seat": 2, "target": 5}, {"seat": 3, "target": 5}], "tie": False, "exiled": 5, "role": "村民"})

    game.day = 2
    emit(game, "host_line", {"text": "预言家请睁眼"})
    emit(game, "wolf_proposal", {"target": 6, "reason": "6号带节奏"}, actor=2, visibility="wolves")
    emit(game, "wolf_target", {"target": 6}, visibility="wolves")
    emit(game, "vote_result", {"votes": [], "tie": True, "exiled": None, "role": ""})

    game.day = 3
    emit(game, "speech", {"text": "昨天放逐5号是错的"}, actor=4)
    emit(game, "vote_result", {"votes": [{"seat": 4, "target": 3}], "tie": False, "exiled": 3, "role": "预言家"})
    game.day = 4

    text = render_history(game, 6)  # 好人视角
    assert "守卫请睁眼" not in text and "预言家请睁眼" not in text  # 垫话丢弃
    assert "我觉得1号很可疑" not in text  # 老发言丢弃
    assert "（第 1 天白天众人依次发言，内容从略）" in text
    assert "放逐 5号（村民）" in text and "平票，无人出局" in text  # 结局摘要保留
    assert "2号→5号" not in text  # 老票型明细丢弃
    assert "4号→3号" in text and "【预言家】" in text  # 前一昼夜票型明细与结局完整
    assert "（第 3 天白天众人依次发言，内容从略）" in text  # 前一昼夜发言仍按既有规则从略

    wtext = render_history(game, 2)  # 狼人视角
    assert "6号带节奏" not in wtext  # 老狼队讨论过程丢弃
    assert "【狼队频道】今晚刀口定为 6号" in wtext  # 刀口结论保留

def test_strategy_injection():
    """发言策略注入：发言类请求带技巧引导，投票/夜决策保持精简；竞选宣言按角色分支。"""
    from app.llm.prompts import (
        last_words_messages,
        sheriff_campaign_messages,
        speech_messages,
        system_prompt,
        vote_messages,
        SPEECH_LIMIT,
    )

    assert "200 字" in SPEECH_LIMIT
    game = make_game(BUILTIN_BOARDS[1], n=9)
    start_game(game)
    seer = next(p for p in game.players if p.role == Role.SEER)
    wolf = next(p for p in game.players if p.role == Role.WEREWOLF)
    villager = next(p for p in game.players if p.role == Role.VILLAGER)
    witch = next(p for p in game.players if p.role == Role.WITCH)

    text = speech_messages(game, villager)[1]["content"]
    assert "站边" in text and "归票" in text and "示例" in text  # 结构要求 + 话术
    assert "支持者结构" in text  # 场面解读：对跳三面判断（心路自洽/支持者结构/查杀方向）
    assert "跟票查杀本身不算洗白" in text  # 票型解读：丢车保帅的逆向识别
    assert "信任放大器" in text  # 警徽解读：接徽的新警长没经过竞选检验
    assert "自爆是白送的复盘" in text and "银水标签也可能掺假" in text  # 自爆逆向信息 + 银水可伪造
    assert "倒钩狼" in text and "深水狼" in text and "重点观察" in text  # 好人发言注入狼型识别
    assert "装神" in text  # 村民角色发言篇按角色注入
    assert "200 字" in text
    # 怀疑纪律挂在 system_prompt 通用段：所有请求（发言/遗言/投票等）天然带上
    assert "怀疑纪律" in speech_messages(game, villager)[0]["content"]
    assert "怀疑纪律" in system_prompt(game, villager)
    # 板子配置公示进通用层：人数、屠边/屠城、具体身份配置、警长开关全部开局公开
    bp = system_prompt(game, villager)
    assert "板子配置" in bp
    assert "9 名玩家" in bp and "屠边局" in bp and "身份配置" in bp
    assert "狼人×3" in bp and "预言家" in bp and "猎人" in bp
    assert "不设警长竞选" in bp  # 未开警长竞选时显式说明
    g6 = make_game(BUILTIN_BOARDS[0], n=6)
    assert "屠城局" in system_prompt(g6, g6.players[0])  # 屠城板的胜负规则公示
    # 狼的白天战术搬进狼的发言请求；狼 system 只留身份层（队友/保密/伪装）
    assert "悍跳" in speech_messages(game, wolf)[1]["content"]
    assert "重点观察" not in speech_messages(game, wolf)[1]["content"]  # 狼型识别只注入好人侧
    wsp = system_prompt(game, wolf)
    assert "狼队友" in wsp and "伪装好人" in wsp
    assert "别抢着声张" in wsp  # 银水认领纪律：没有女巫认领不主动声张被刀被救
    assert "割席" not in wsp  # 割席纪律唯一所有权在狼投票 craft
    assert "盲死" in last_words_messages(game, villager)[1]["content"]  # 好人遗言不硬凑怀疑
    assert "绝不承认" in last_words_messages(game, wolf)[1]["content"]  # 狼遗言=伪装延续
    lw = lambda r: last_words_messages(game, next(p for p in game.players if p.role == r))[1]["content"]
    assert "警徽流建议" in lw(Role.SEER)  # 预言家遗言必报
    assert "解药随你入土" in lw(Role.WITCH)  # 女巫遗言看药：解药在身偏藏
    assert "别亮猎人身份" in lw(Role.HUNTER)  # 猎人遗言藏枪
    g12 = make_game(guard_board(), n=12)
    start_game(g12)
    guard = next(p for p in g12.players if p.role == Role.GUARD)
    assert "免费威慑" in last_words_messages(g12, guard)[1]["content"]  # 守卫遗言默认不跳

    vt = vote_messages(game, villager, [2, 3, 4])[1]["content"]
    assert "身份判断" not in vt and "示例" not in vt  # 投票请求不注入，保持精简
    assert "割席" not in vt  # 好人投票不含狼队纪律
    assert "替指控者背书" in vt  # 无据指控不跟票
    assert "分票" in vt  # 好人防分票收敛纪律
    assert "冲锋狼" in vt  # 好人投票注入狼型识别
    assert "身份背书" in vt  # 警长头衔不是身份背书，接徽狼防盲从
    assert "白痴" not in vt  # 无白痴板不注入白痴条款

    wt = vote_messages(game, wolf, [2, 3, 4])[1]["content"]
    assert "割席" in wt and "丢车保帅" in wt  # 狼人投票注入丢车保帅纪律

    assert "警徽流" in sheriff_campaign_messages(game, seer)[1]["content"]
    assert "悍跳" in sheriff_campaign_messages(game, wolf)[1]["content"]
    assert "上警目的" in sheriff_campaign_messages(game, villager)[1]["content"]
    assert "交代" in last_words_messages(game, villager)[1]["content"]

    assert "药型" in system_prompt(game, witch)
    assert "自刀骗药" in speech_messages(game, witch)[1]["content"]  # 女巫发言篇：银水非铁证
    assert "票型" in system_prompt(game, villager)  # 村民 system 只留身份层职责，发言技巧在发言请求

    # 警上决策按角色分支；选警长与警徽移交带 craft
    from app.llm.prompts import (
        badge_messages,
        self_explode_messages,
        sheriff_run_messages,
        sheriff_vote_messages,
    )
    assert "悍跳" in sheriff_run_messages(game, wolf)[1]["content"]
    assert "查验" in sheriff_run_messages(game, seer)[1]["content"]
    assert "警徽流" in sheriff_vote_messages(game, villager, [2, 3])[1]["content"]
    assert "撕毁" in badge_messages(game, seer, [2, 3])[1]["content"]
    assert "机械执行" in badge_messages(game, seer, [2, 3])[1]["content"]  # 警徽流被怀疑时不死板执行
    assert "队友" in badge_messages(game, wolf, [2, 3])[1]["content"]  # 狼警长传徽权衡
    # 自爆纪律唯一所有权：explode 请求有完整 craft，狼 system 无（规则手册的机制句除外）
    assert "自爆纪律" in self_explode_messages(game, wolf)[1]["content"]
    assert "自爆纪律" not in wsp

    # 警上自爆（吞警徽）：决策请求按规则状态分支，system 公示随板子规则走
    from app.llm.prompts import self_explode_campaign_messages, self_explode_trigger_messages
    game.sheriff_enabled = True
    game.badge_swallow = "single"
    assert "单爆吞警徽" in system_prompt(game, wolf)
    camp = self_explode_campaign_messages(game, wolf)[1]["content"]
    assert "警长竞选进行中" in camp and "单爆吞警徽" in camp and "吞警徽战术" in camp
    game.badge_swallow = "double"
    assert "重新竞选警长" in system_prompt(game, wolf)
    assert "双爆吞警徽第一步" in self_explode_campaign_messages(game, wolf)[1]["content"]
    game.sheriff_revote = True
    assert "双爆吞警徽最后一步" in self_explode_campaign_messages(game, wolf)[1]["content"]
    game.sheriff_revote = False
    game.badge_swallow = "single"
    trig = self_explode_trigger_messages(game, wolf, 2, "我查验3号是狼人", campaign=True)[1]["content"]
    assert "刚结束的竞选发言里" in trig and "单爆吞警徽" in trig
    game.badge_swallow = "none"
    swallow_off = system_prompt(game, wolf)
    assert "未开启吞警徽" in swallow_off and "不能自爆" in swallow_off  # 关闭状态也显式公示
    assert "刚结束的发言里" in self_explode_trigger_messages(game, wolf, 2, "我查验3号是狼人")[1]["content"]

    # 有白痴的板子才注入白痴条款
    g12 = make_game(BUILTIN_BOARDS[2], n=12)
    start_game(g12)
    v12 = next(p for p in g12.players if p.role == Role.VILLAGER)
    assert "白痴" in vote_messages(g12, v12, [2, 3, 4])[1]["content"]



def test_private_ledger():
    """私有账本：预言家/女巫/守卫/狼人的夜间记录汇总成表，守卫与狼的刀口附结局归因。"""
    from app.llm.prompts import private_ledger

    game = make_game(BUILTIN_BOARDS[3], n=12)  # 12 人守卫板
    start_game(game)
    seer = next(p for p in game.players if p.role == Role.SEER)
    witch = next(p for p in game.players if p.role == Role.WITCH)
    guard = next(p for p in game.players if p.role == Role.GUARD)
    wolf = next(p for p in game.players if p.role == Role.WEREWOLF)

    emit(game, "seer_result", {"target": 4, "is_wolf": True}, actor=seer.seat, visibility=f"seat:{seer.seat}")
    emit(game, "seer_result", {"target": 5, "is_wolf": False}, actor=seer.seat, visibility=f"seat:{seer.seat}")
    ledger = private_ledger(game, seer)
    assert "查验记录" in ledger and "4号=狼人" in ledger and "5号=好人" in ledger

    emit(game, "witch_knife", {"target": 4, "can_save": True}, actor=witch.seat, visibility=f"seat:{witch.seat}")
    emit(game, "witch_save", {"target": 4}, actor=witch.seat, visibility=f"seat:{witch.seat}")
    emit(game, "witch_poison", {"target": 8}, actor=witch.seat, visibility=f"seat:{witch.seat}")
    ledger = private_ledger(game, witch)
    assert "解药已用" in ledger and "毒药已用" in ledger
    assert "已知刀口" in ledger and "刀 4号" in ledger
    assert "救 4号" in ledger and "毒 8号" in ledger

    emit(game, "guard_protect", {"target": 6}, actor=guard.seat, visibility=f"seat:{guard.seat}")
    emit(game, "night_settle", {"text": "天亮了", "deaths": [6]})
    ledger = private_ledger(game, guard)
    assert "守护记录" in ledger and "守 6号（目标当夜出局）" in ledger

    emit(game, "wolf_target", {"target": 7}, visibility="wolves")
    emit(game, "night_settle", {"text": "天亮了", "deaths": []})
    ledger = private_ledger(game, wolf)
    assert "狼队刀口" in ledger and "刀 7号（目标存活：被守或被救）" in ledger


def test_grounding_facts():
    """事实锚点：今天点名提到你、昨日同票、相邻座位死亡；并汇入 user prompt 分区。"""
    from app.llm.prompts import _user, grounding_facts, system_prompt

    game = make_game(BUILTIN_BOARDS[1], n=9)
    start_game(game)
    me = game.player(3)

    game.day = 1
    emit(game, "vote_result", {"votes": [{"seat": 3, "target": 6}, {"seat": 5, "target": 6}], "tie": False, "exiled": 6, "role": "村民"})
    game.day = 2
    emit(game, "speech", {"text": "我觉得3号发言有问题"}, actor=2)
    game.player(2).alive = False

    facts = grounding_facts(game, me)
    assert "今天点名提到你：2号" in facts
    assert "昨天你与5号同票（都投6号）" in facts
    assert "与你相邻座位的出局玩家" in facts and "仅客观信息" in facts  # 座位事实标注非怀疑理由

    content = _user(game, me, "测试任务")
    assert "【与你的关联】" in content and "点名提到你" in content
    assert "【你知道的全部信息】" in content and "【你的任务】测试任务" in content

    seer = next(p for p in game.players if p.role == Role.SEER)
    emit(game, "seer_result", {"target": 8, "is_wolf": False}, actor=seer.seat, visibility=f"seat:{seer.seat}")
    assert "【你的私有记录】" in _user(game, seer, "测试任务")
    assert "翻牌才算公开确认" in system_prompt(game, seer)


def test_decision_crafts():
    """决策层短策略：7 类决策请求各注入 1~2 句对应策略。"""
    from app.llm.prompts import (
        guard_messages,
        hunter_messages,
        seer_messages,
        system_prompt,
        vote_messages,
        witch_poison_messages,
        witch_save_messages,
        wolf_proposal_messages,
    )

    game = make_game(BUILTIN_BOARDS[3], n=12)
    start_game(game)
    seer = next(p for p in game.players if p.role == Role.SEER)
    witch = next(p for p in game.players if p.role == Role.WITCH)
    guard = next(p for p in game.players if p.role == Role.GUARD)
    wolf = next(p for p in game.players if p.role == Role.WEREWOLF)

    assert "票型" in vote_messages(game, seer, [2, 3])[1]["content"]
    assert "分票" in vote_messages(game, seer, [2, 3])[1]["content"]  # 好人防分票收敛纪律
    assert "刀口优先级" in wolf_proposal_messages(game, wolf)[1]["content"]
    assert "管住嘴" in wolf_proposal_messages(game, wolf)[1]["content"]  # 自刀次日不声张银水
    assert "查验优先级" in seer_messages(game, seer, [2, 3])[1]["content"]
    assert "值得救" in witch_save_messages(game, witch, 4, True)[1]["content"]
    assert "同守同救" in witch_save_messages(game, witch, 4, True)[1]["content"]  # 奶穿镜像提醒
    assert "果断" in witch_poison_messages(game, witch, [2, 3])[1]["content"]
    assert "确认度" in hunter_messages(game, seer, [2, 3])[1]["content"]
    # 连守禁令是守卫 system 身份层机制；craft 只留策略（含独有的同守同救提示）
    assert "连续两晚" in system_prompt(game, guard)
    assert "同守同救" in guard_messages(game, guard, [2, 3])[1]["content"]

    # 场上有真人狼存活时，AI 狼的刀口提议要被告知真人队友拥有最终拍板权
    human = game.player(1)
    if human.role != Role.WEREWOLF:
        human.role = Role.WEREWOLF
    ai_wolf = next(p for p in game.players if p.role == Role.WEREWOLF and not p.is_human)
    assert "拍板权" in wolf_proposal_messages(game, ai_wolf)[1]["content"]
