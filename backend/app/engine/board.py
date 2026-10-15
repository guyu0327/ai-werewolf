from __future__ import annotations

from ..models import BoardConfig, Role, Settings, WinCondition

BUILTIN_BOARDS: list[BoardConfig] = [
    BoardConfig(
        id="six",
        name="6人标准局：2狼·2民·预言家·女巫",
        win_condition=WinCondition.CITY,
        roles=[
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.SEER,
            Role.WITCH,
        ],
    ),
    BoardConfig(
        id="nine",
        name="9人经典局：3狼·3民·预言家·女巫·猎人",
        win_condition=WinCondition.SIDE,
        roles=[
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.SEER,
            Role.WITCH,
            Role.HUNTER,
        ],
    ),
    BoardConfig(
        id="twelve",
        name="12人经典局：4狼·4民·预言家·女巫·猎人·白痴",
        win_condition=WinCondition.SIDE,
        roles=[
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.SEER,
            Role.WITCH,
            Role.HUNTER,
            Role.IDIOT,
        ],
    ),
    BoardConfig(
        id="twelve_guard",
        name="12人守卫局：4狼·4民·预言家·女巫·猎人·守卫",
        win_condition=WinCondition.SIDE,
        roles=[
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.WEREWOLF,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.VILLAGER,
            Role.SEER,
            Role.WITCH,
            Role.HUNTER,
            Role.GUARD,
        ],
    ),
]


def all_boards(settings: Settings) -> list[BoardConfig]:
    return BUILTIN_BOARDS + settings.boards


def find_board(settings: Settings, board_id: str) -> BoardConfig | None:
    for b in all_boards(settings):
        if b.id == board_id:
            return b
    return None


def validate_board(roles: list[Role], win_condition: WinCondition, max_days: int) -> None:
    if not 5 <= len(roles) <= 12:
        raise ValueError("板子人数需在 5~12 人之间")
    if max_days < 1 or max_days > 99:
        raise ValueError("天数上限需在 1~99 之间")
    wolves = roles.count(Role.WEREWOLF)
    if wolves < 1:
        raise ValueError("至少要有 1 名狼人")
    if wolves >= len(roles):
        raise ValueError("狼人不能占满全场")
    if roles.count(Role.SEER) > 1:
        raise ValueError("预言家最多 1 名")
    if roles.count(Role.WITCH) > 1:
        raise ValueError("女巫最多 1 名")
    if roles.count(Role.HUNTER) > 1:
        raise ValueError("猎人最多 1 名")
    if roles.count(Role.IDIOT) > 1:
        raise ValueError("白痴最多 1 名")
    if roles.count(Role.GUARD) > 1:
        raise ValueError("守卫最多 1 名")
    if win_condition == WinCondition.SIDE and roles.count(Role.VILLAGER) + roles.count(Role.IDIOT) < 1:
        raise ValueError("屠边局至少要有 1 名村民或白痴")


def build_custom_board(board_id: str, name: str, roles: list[Role], win_condition: WinCondition, max_days: int) -> BoardConfig:
    validate_board(roles, win_condition, max_days)
    if not name.strip():
        name = f"自定义{len(roles)}人局"
    return BoardConfig(id=board_id, name=name.strip(), win_condition=win_condition, roles=list(roles), max_days=max_days)
