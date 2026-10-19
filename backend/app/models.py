from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Role(str, Enum):
    WEREWOLF = "werewolf"
    VILLAGER = "villager"
    SEER = "seer"
    WITCH = "witch"
    HUNTER = "hunter"
    IDIOT = "idiot"
    GUARD = "guard"


ROLE_LABELS: dict[Role, str] = {
    Role.WEREWOLF: "狼人",
    Role.VILLAGER: "村民",
    Role.SEER: "预言家",
    Role.WITCH: "女巫",
    Role.HUNTER: "猎人",
    Role.IDIOT: "白痴",
    Role.GUARD: "守卫",
}

# 白痴属神职：屠边局的「神职侧」以 GOD_ROLES 为准（翻牌免死后仍存活的白痴同样撑住神桶）
GOD_ROLES = {Role.SEER, Role.WITCH, Role.HUNTER, Role.GUARD, Role.IDIOT}


class WinCondition(str, Enum):
    CITY = "city"  # 屠城：存活狼人数 >= 存活好人数
    SIDE = "side"  # 屠边：神职全灭或村民全灭


WIN_LABELS = {WinCondition.CITY: "屠城", WinCondition.SIDE: "屠边"}


class Phase(str, Enum):
    LOBBY = "lobby"
    NIGHT_GUARD = "night_guard"
    NIGHT_WOLF = "night_wolf"
    NIGHT_SEER = "night_seer"
    NIGHT_WITCH_SAVE = "night_witch_save"
    NIGHT_WITCH_POISON = "night_witch_poison"
    DAWN = "dawn"
    SHERIFF_ELECTION = "sheriff_election"
    DAY_SPEECH = "day_speech"
    VOTE = "vote"
    VOTE_RESULT = "vote_result"
    GAME_OVER = "game_over"


PHASE_LABELS: dict[Phase, str] = {
    Phase.LOBBY: "等待开始",
    Phase.NIGHT_GUARD: "夜晚·守卫守护",
    Phase.NIGHT_WOLF: "夜晚·狼人行动",
    Phase.NIGHT_SEER: "夜晚·预言家验人",
    Phase.NIGHT_WITCH_SAVE: "夜晚·女巫（解药）",
    Phase.NIGHT_WITCH_POISON: "夜晚·女巫（毒药）",
    Phase.DAWN: "天亮结算",
    Phase.SHERIFF_ELECTION: "警长竞选",
    Phase.DAY_SPEECH: "白天·轮流发言",
    Phase.VOTE: "白天·投票",
    Phase.VOTE_RESULT: "白天·投票结果",
    Phase.GAME_OVER: "游戏结束",
}


class BoardConfig(BaseModel):
    id: str
    name: str
    win_condition: WinCondition = WinCondition.CITY
    roles: list[Role] = Field(default_factory=list)
    max_days: int = 20


class ModelPreset(BaseModel):
    id: str
    name: str
    base_url: str
    api_key: str = ""
    model: str
    speech_temperature: float = 0.9
    decision_temperature: float = 0.3
    # 思考力度（reasoning_effort）：推理型模型的思考深度，请求时透传；发言建议 medium，决策建议 low
    speech_reasoning_effort: str = "medium"
    decision_reasoning_effort: str = "low"


class AiPersona(BaseModel):
    id: str
    name: str
    avatar: str = "🙂"
    persona: str = ""
    preset_id: str = ""


class Settings(BaseModel):
    presets: list[ModelPreset] = Field(default_factory=list)
    personas: list[AiPersona] = Field(default_factory=list)
    boards: list[BoardConfig] = Field(default_factory=list)


class Player(BaseModel):
    seat: int
    name: str
    avatar: str = "🙂"
    persona: str = ""
    preset_id: str = ""
    is_human: bool = False
    role: Optional[Role] = None
    role_revealed: bool = False
    alive: bool = True
    vote_lost: bool = False  # 白痴翻牌免死后失去投票权

    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, "?") if self.role else "?"


class GameEvent(BaseModel):
    seq: int
    day: int
    phase: Phase
    type: str
    actor: Optional[int] = None
    payload: dict = Field(default_factory=dict)
    visibility: str = "public"  # public | wolves | seer | witch | seat:N


class Game(BaseModel):
    id: str
    created_at: str
    board: BoardConfig
    players: list[Player]
    seed: int
    mode: Literal["normal", "god"] = "normal"  # normal: 1真人+AI；god: 纯观战（全AI，上帝视角）
    preferred_role: Optional[Role] = None  # 真人自选角色（normal 模式），None=随机发牌
    sheriff_enabled: bool = False  # 是否开启警长竞选（首日上警竞选警长）
    sheriff_seat: Optional[int] = None  # 当前警长座位；None=无警长（未竞选/警徽作废或撕毁）
    badge_swallow: Literal["none", "single", "double"] = "none"  # 吞警徽规则（需开启警长竞选）：single=单爆吞徽，double=双爆吞徽
    sheriff_revote: bool = False  # 双爆吞警徽第一步已发生：次日白天重新进行警长竞选
    day: int = 0
    phase: Phase = Phase.LOBBY
    phase_ctx: dict = Field(default_factory=dict)
    speech_start_seat: int = 1
    event_count: int = 0
    events: list[GameEvent] = Field(default_factory=list)
    winner: Optional[Literal["wolf", "good", "draw", "error"]] = None
    end_reason: str = ""
    summary: str = ""  # AI 复盘总结（生成后持久化，可换模型重新生成覆盖）
    summary_model: str = ""  # 生成总结所用模型预设名

    def player(self, seat: int) -> Player:
        for p in self.players:
            if p.seat == seat:
                return p
        raise KeyError(f"座位 {seat} 不存在")

    def human(self) -> Player:
        for p in self.players:
            if p.is_human:
                return p
        raise KeyError("没有真人玩家")

    def human_seat(self) -> Optional[int]:
        return next((p.seat for p in self.players if p.is_human), None)

    def alive_players(self) -> list[Player]:
        return [p for p in self.players if p.alive]

    def alive_wolves(self) -> list[Player]:
        return [p for p in self.players if p.alive and p.role == Role.WEREWOLF]


# ---------- LLM 结构化决策 ----------


class WolfProposal(BaseModel):
    """狼人夜聊：提议的刀口与理由（即本狼的击杀投票）"""

    target: int = Field(description="今晚要击杀的玩家座位号")
    reason: str = Field(default="", description="一句话说明为什么刀这个目标")


class SeerCheck(BaseModel):
    target: int = Field(description="要查验的玩家座位号")


class GuardProtect(BaseModel):
    target: int = Field(description="今晚要守护的玩家座位号")


class WitchSave(BaseModel):
    use: bool = Field(description="是否使用解药救活今晚被袭击的玩家")


class WitchPoison(BaseModel):
    use: bool = Field(description="是否使用毒药")
    target: int = Field(default=0, description="要毒杀的玩家座位号，use=false 时为 0")


class VoteDecision(BaseModel):
    target: int = Field(description="要投票放逐的玩家座位号，0 表示弃票")
    reason: str = Field(default="", description="投票理由，简短一两句")


class HunterDecision(BaseModel):
    shoot: bool = Field(description="是否开枪带走一名玩家")
    target: int = Field(default=0, description="开枪目标的座位号，shoot=false 时为 0")


class SheriffRun(BaseModel):
    """警长竞选：是否上警"""

    run: bool = Field(description="是否上警参选警长")


class SelfExplodeDecision(BaseModel):
    """狼人白天发言回合或警上竞选：是否自爆"""

    explode: bool = Field(description="是否自爆出局（翻牌亮明狼人身份，立即死亡，白天结束直接入夜）")


class SheriffVote(BaseModel):
    """警长竞选投票"""

    target: int = Field(description="投票支持的警长候选人座位号，0 表示弃票")


class BadgeDecision(BaseModel):
    """警长出局后的警徽处置：移交或撕毁"""

    destroy: bool = Field(description="是否撕毁警徽（true=撕毁，本局再无警长）")
    target: int = Field(default=0, description="警徽移交目标的座位号，destroy=false 时生效")


class SpeechOrderChoice(BaseModel):
    """警长决定首日发言方向"""

    direction: str = Field(description="发言方向：left=从警长上一位（座位号小的一侧）开始，right=从警长下一位开始")


class SpeechText(BaseModel):
    text: str = Field(description="发言内容")


class GeneratedPersona(BaseModel):
    """一句话生成 AI 玩家（名字 + 人设）的结构化输出"""

    name: str = Field(
        description=(
            "玩家名字，2~4 个汉字，像真实中国玩家的名字或网名（人名如「王大力」「陈雨桐」，"
            "网名如「夜风微凉」「一梦三年」「老王不秃」），禁止用物品名或动物名（如「铁锅」「二哈」）"
        )
    )
    persona: str = Field(
        description=(
            "角色人设，200~500 字，第二人称「你」开头，包含四部分："
            "性格底色、说话风格（语速/句式/习惯用词）、狼人杀打法倾向（如何伪装或找狼、投票习惯）、一个记忆点（口头禅或怪癖）"
        )
    )

