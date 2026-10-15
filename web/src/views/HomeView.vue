<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Avatar from '@/components/common/Avatar.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import BoardEditor from '@/components/BoardEditor.vue'
import SettingsModal from '@/components/SettingsModal.vue'
import ReviewModal from '@/components/ReviewModal.vue'
import { abortGame, createGame, deleteGame, fetchBoards, listGames, startGame, type BoardInfo, type GameMeta } from '@/api/game'
import { fetchSettings, type AiPersona } from '@/api/settings'
import { showToast } from '@/composables/toast'
import { setBgmTrack } from '@/composables/useBgm'
import { ROLE_LABELS } from '@/utils/feed'

const router = useRouter()
const route = useRoute()

const MODE_KEY = 'home-mode'

const boards = ref<BoardInfo[]>([])
const personas = ref<AiPersona[]>([])
const games = ref<GameMeta[]>([])
const selectedBoardId = ref('')
const customBoard = ref<{ name: string; roles: string[]; win_condition: string; max_days: number } | null>(null)
const editorOpen = ref(false)
const selectedPersonas = ref<string[]>([])
const creating = ref(false)
const abortTarget = ref<GameMeta | null>(null)
const deleteTarget = ref<GameMeta | null>(null)
const mode = ref<'normal' | 'god'>(localStorage.getItem(MODE_KEY) === 'god' ? 'god' : 'normal')
watch(mode, (m) => localStorage.setItem(MODE_KEY, m))
const settingsOpen = ref(false)
const reviewId = ref('')
const preferredRole = ref('')
const sheriffEnabled = ref(false)
const badgeSwallow = ref<'none' | 'single' | 'double'>('none')

watch(sheriffEnabled, (v) => {
  if (!v) badgeSwallow.value = 'none'
})

const currentBoard = computed(() => {
  if (customBoard.value) return null
  return boards.value.find((b) => b.id === selectedBoardId.value) ?? null
})

const boardSize = computed(() => {
  if (customBoard.value) return customBoard.value.roles.length
  return currentBoard.value?.roles.length ?? 0
})

const needAiCount = computed(() => {
  if (!boardSize.value) return 0
  return mode.value === 'god' ? boardSize.value : boardSize.value - 1
})

const boardLabel = computed(() => {
  if (customBoard.value) return `${customBoard.value.name}（自定义）`
  return currentBoard.value?.name ?? '请选择板子'
})

const roleOptions = computed<string[]>(() => {
  if (mode.value !== 'normal') return []
  const roles = customBoard.value ? customBoard.value.roles : (currentBoard.value?.roles ?? [])
  return [...new Set(roles)]
})

watch(roleOptions, (roles) => {
  if (preferredRole.value && !roles.includes(preferredRole.value)) preferredRole.value = ''
})

const canCreate = computed(
  () => (customBoard.value || currentBoard.value) && selectedPersonas.value.length === needAiCount.value && !creating.value,
)

const ongoing = computed(() => games.value.filter((g) => g.phase !== 'game_over' && g.mode === mode.value))
const finished = computed(() => games.value.filter((g) => g.phase === 'game_over' && g.mode === mode.value))
const historyStats = computed(() => {
  const list = finished.value
  const wolf = list.filter((g) => g.winner === 'wolf').length
  const good = list.filter((g) => g.winner === 'good').length
  const draw = list.length - wolf - good
  const pct = (n: number) => (list.length ? Math.round((n / list.length) * 100) : 0)
  return { total: list.length, wolf, good, draw, wolfPct: pct(wolf), goodPct: pct(good) }
})
const otherModeCount = computed(() => games.value.filter((g) => g.mode !== mode.value).length)

onMounted(() => {
  setBgmTrack('lobby')
  load()
  // 对局结束页「查看复盘」回大厅并直接弹出复盘
  const q = route.query.review
  if (typeof q === 'string' && q) {
    reviewId.value = q
    router.replace({ query: {} })
  }
})

onBeforeUnmount(() => setBgmTrack(null))

async function load() {
  try {
    const [b, s, g] = await Promise.all([fetchBoards(), fetchSettings(), listGames()])
    boards.value = b
    personas.value = s.personas
    games.value = g
    if (!selectedBoardId.value && b.length) selectedBoardId.value = b[0].id
    if (!selectedPersonas.value.length) autoPickPersonas()
  } catch (e) {
    showToast((e as Error).message)
  }
}

function boardWinnerLabel(g: GameMeta): string {
  if (g.winner === 'wolf') return '狼人阵营胜'
  if (g.winner === 'good') return '好人阵营胜'
  if (g.winner === 'error') return '系统异常'
  if (g.winner === 'draw') return (g.end_reason ?? '').includes('中断') ? '已中断' : '和局'
  return ''
}

function winnerClass(g: GameMeta): string {
  if (g.winner === 'wolf') return 'wolf'
  if (g.winner === 'good') return 'good'
  return 'draw'
}

function roleClass(role: string): string {
  if (role === 'werewolf') return 'wolf'
  if (role === 'villager' || role === 'idiot') return 'civil'
  return 'god'
}

function roleCount(roles: string[]): { role: string; n: number }[] {
  const counts: Record<string, number> = {}
  for (const r of roles) counts[r] = (counts[r] ?? 0) + 1
  return Object.entries(counts).map(([role, n]) => ({ role, n }))
}

function togglePersona(id: string) {
  const i = selectedPersonas.value.indexOf(id)
  if (i >= 0) selectedPersonas.value.splice(i, 1)
  else if (selectedPersonas.value.length < needAiCount.value) selectedPersonas.value.push(id)
  else showToast(mode.value === 'god' ? `观战模式需要 ${needAiCount.value} 名 AI 全部上场` : `该板子只需要 ${needAiCount.value} 名 AI 玩家`)
}

/** 随机选中符合当前板子人数的 AI 玩家（不足则全选），可再手动调整 */
function autoPickPersonas() {
  const pool = [...personas.value]
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }
  selectedPersonas.value = pool.slice(0, needAiCount.value).map((p) => p.id)
}

function switchMode(m: 'normal' | 'god') {
  if (mode.value === m) return
  mode.value = m
  preferredRole.value = ''
  autoPickPersonas()
}

function useCustom(board: { name: string; roles: string[]; win_condition: string; max_days: number }) {
  customBoard.value = board
  selectedBoardId.value = ''
  autoPickPersonas()
}

function pickPreset(id: string) {
  if (!customBoard.value && selectedBoardId.value === id) return
  customBoard.value = null
  selectedBoardId.value = id
  autoPickPersonas()
}

async function onCreate() {
  if (!canCreate.value) return
  creating.value = true
  try {
    const state = await createGame({
      board_id: customBoard.value ? undefined : selectedBoardId.value,
      board: customBoard.value ?? undefined,
      persona_ids: selectedPersonas.value,
      human_name: '玩家',
      human_avatar: '',
      mode: mode.value,
      human_role: preferredRole.value || undefined,
      sheriff: sheriffEnabled.value,
      badge_swallow: badgeSwallow.value,
    })
    await startGame(state.id)
    router.push(`/game/${state.id}`)
  } catch (e) {
    showToast((e as Error).message)
  } finally {
    creating.value = false
  }
}

function onRemove(g: GameMeta) {
  deleteTarget.value = g
}

async function doRemove() {
  const g = deleteTarget.value
  deleteTarget.value = null
  if (!g) return
  try {
    await deleteGame(g.id)
    await load()
  } catch (e) {
    showToast((e as Error).message)
  }
}

function onAbort(g: GameMeta) {
  abortTarget.value = g
}

async function doAbort() {
  const g = abortTarget.value
  abortTarget.value = null
  if (!g) return
  try {
    await abortGame(g.id)
    await load()
  } catch (e) {
    showToast((e as Error).message)
  }
}
</script>

<template>
  <div class="home">
    <header class="hero view-header">
      <div class="hero-moon">🌙</div>
      <div class="hero-text">
        <h1>AI 狼人杀</h1>
        <p class="hero-slogan">天黑请闭眼 —— 与你的 AI 朋友们同桌博弈</p>
      </div>
      <button class="btn settings-btn" @click="settingsOpen = true">⚙ 设置</button>
    </header>

    <div class="home-scroll">
      <div class="home-body">
      <!-- 建局 -->
      <section class="card create-card">
        <h2>开新一局</h2>

        <div class="block">
          <div class="mode-row">
            <div class="mode-seg">
              <button :class="{ active: mode === 'normal' }" @click="switchMode('normal')">🎮 我是玩家</button>
              <button :class="{ active: mode === 'god' }" @click="switchMode('god')">👁 上帝观战</button>
            </div>
            <span class="mode-desc">
              {{ mode === 'god' ? '全体 AI 对局：你以上帝视角观看全程，身份、狼队夜聊与思考过程一览无余' : '你将亲自上场，与 AI 同桌博弈' }}
            </span>
          </div>
        </div>

        <div class="block">
          <div class="section-label">选择板子</div>
          <div class="board-list">
            <button
              v-for="b in boards"
              :key="b.id"
              class="board-chip"
              :class="{ active: !customBoard && selectedBoardId === b.id }"
              @click="pickPreset(b.id)"
            >
              <span class="board-name">{{ b.name }}</span>
              <span class="board-roles">
                <span v-for="rc in roleCount(b.roles)" :key="rc.role" class="role-badge" :class="roleClass(rc.role)">
                  {{ ROLE_LABELS[rc.role] ?? rc.role }}×{{ rc.n }}
                </span>
              </span>
              <span class="board-sub">
                {{ b.roles.length }} 人
                <i class="dot" />
                <em :class="b.win_condition === 'city' ? 'city' : 'side'">{{ b.win_condition === 'city' ? '屠城局' : '屠边局' }}</em>
              </span>
            </button>
            <button class="board-chip custom" :class="{ active: !!customBoard }" @click="editorOpen = true">
              <span class="board-name">{{ customBoard ? boardLabel : '＋ 自定义板子' }}</span>
              <span v-if="customBoard" class="board-roles">
                <span v-for="rc in roleCount(customBoard.roles)" :key="rc.role" class="role-badge" :class="roleClass(rc.role)">
                  {{ ROLE_LABELS[rc.role] ?? rc.role }}×{{ rc.n }}
                </span>
              </span>
              <span v-if="customBoard" class="board-sub">
                {{ customBoard.roles.length }} 人
                <i class="dot" />
                <em :class="customBoard.win_condition === 'city' ? 'city' : 'side'">{{ customBoard.win_condition === 'city' ? '屠城局' : '屠边局' }}</em>
              </span>
              <span v-else class="board-sub">自由编排角色与规则</span>
            </button>
          </div>
        </div>

        <div class="block">
          <div class="sheriff-row" @click="sheriffEnabled = !sheriffEnabled">
            <span class="sheriff-icon">🎖</span>
            <div class="sheriff-text">
              <div class="sheriff-title">
                警长竞选
                <span class="switch" :class="{ on: sheriffEnabled }"><i /></span>
              </div>
              <div class="sheriff-desc">
                首日上警竞选警长：警长投票计 1.5 票、决定首日发言方向且最后发言；警长出局自主移交或撕毁警徽
              </div>
            </div>
            <span class="sheriff-state">{{ sheriffEnabled ? '已开启' : '未开启' }}</span>
          </div>
          <div v-if="sheriffEnabled" class="swallow-row">
            <span class="swallow-label">吞警徽：</span>
            <button
              class="swallow-chip"
              :class="{ active: badgeSwallow === 'none' }"
              @click="badgeSwallow = 'none'"
            >
              不开启
            </button>
            <button
              class="swallow-chip"
              :class="{ active: badgeSwallow === 'single' }"
              @click="badgeSwallow = 'single'"
            >
              单爆吞徽
            </button>
            <button
              class="swallow-chip"
              :class="{ active: badgeSwallow === 'double' }"
              @click="badgeSwallow = 'double'"
            >
              双爆吞徽
            </button>
            <span class="swallow-desc">{{
              badgeSwallow === 'single'
                ? '狼人警上自爆：竞选终止、警徽流失、直接入夜'
                : badgeSwallow === 'double'
                  ? '首爆终止竞选入夜，次日重选；再爆一次警徽才被彻底吞掉'
                  : '竞选发言期间狼人不可自爆'
            }}</span>
          </div>
        </div>

        <div v-if="mode === 'normal' && (customBoard || currentBoard)" class="block">
          <div class="section-label">我的角色<span class="role-hint">（默认随机分配）</span></div>
          <div class="role-select">
            <button class="role-chip random" :class="{ active: preferredRole === '' }" @click="preferredRole = ''">
              🎲 随机
            </button>
            <button
              v-for="r in roleOptions"
              :key="r"
              class="role-chip"
              :class="[roleClass(r), { active: preferredRole === r }]"
              @click="preferredRole = r"
            >
              {{ ROLE_LABELS[r] ?? r }}
            </button>
          </div>
        </div>

        <div class="block">
          <div class="section-label">
            {{ mode === 'god' ? '上场 AI 玩家' : '选择 AI 玩家' }}（{{ selectedPersonas.length }}/{{ needAiCount }}）
          </div>
          <div v-if="personas.length === 0" class="empty-guide">
            <div class="empty-moon">🌑</div>
            <div class="empty-text">
              <div class="empty-title">还没有 AI 玩家</div>
              <div class="empty-desc">狼人杀需要 5~11 位 AI 同桌。先到设置页创建人设、绑定模型，再把他们拉上牌桌。</div>
            </div>
            <button class="btn primary" @click="settingsOpen = true">去设置创建</button>
          </div>
          <div v-else class="persona-list">
            <button
              v-for="p in personas"
              :key="p.id"
              class="persona-chip"
              :class="{ active: selectedPersonas.includes(p.id) }"
              @click="togglePersona(p.id)"
            >
              <Avatar :name="p.name" :avatar="p.avatar" :size="24" />
              <span class="persona-name">{{ p.name }}</span>
              <span v-if="selectedPersonas.includes(p.id)" class="persona-check">✓</span>
            </button>
          </div>
        </div>

        <div class="block create-bar">
          <button class="btn primary create-btn" :disabled="!canCreate" @click="onCreate">
            {{ creating ? '发牌中…' : '开始游戏' }}
          </button>
          <span v-if="!canCreate && (customBoard || currentBoard)" class="create-hint">
            还需选择 {{ needAiCount - selectedPersonas.length }} 名 AI 玩家
          </span>
        </div>
      </section>

      <!-- 进行中 -->
      <section v-if="ongoing.length" class="card">
        <h2>进行中</h2>
        <div v-for="g in ongoing" :key="g.id" class="game-row">
          <div class="game-info">
            <div class="game-title">{{ g.board_name }} · 第 {{ g.day }} 天</div>
            <div class="game-players">
              <span v-for="p in g.players" :key="p.seat" class="mini-player" :class="{ dead: !p.alive }">
                {{ p.name }}
              </span>
            </div>
          </div>
          <router-link :to="`/game/${g.id}`" class="btn primary">继续</router-link>
          <button class="btn danger" @click="onAbort(g)">中断结束</button>
        </div>
      </section>

      <!-- 历史 -->
      <section v-if="finished.length" class="card">
        <div class="history-head">
          <h2>历史对局</h2>
          <span class="history-stats">
            共 {{ historyStats.total }} 局
            <span class="stat wolf">狼人 {{ historyStats.wolf }} 胜 · {{ historyStats.wolfPct }}%</span>
            <span class="stat good">好人 {{ historyStats.good }} 胜 · {{ historyStats.goodPct }}%</span>
            <span v-if="historyStats.draw" class="stat draw">和局/中断 {{ historyStats.draw }}</span>
          </span>
        </div>
        <div v-for="g in finished" :key="g.id" class="game-row">
          <div class="game-info">
            <div class="game-title">
              <span class="winner-badge" :class="winnerClass(g)">{{ boardWinnerLabel(g) }}</span>
              {{ g.board_name }}
            </div>
            <div class="game-sub">{{ g.created_at }}</div>
          </div>
          <button class="btn" @click="reviewId = g.id">复盘</button>
          <button class="btn danger" @click="onRemove(g)">删除</button>
        </div>
      </section>

      <section v-if="!ongoing.length && !finished.length && otherModeCount" class="card mode-empty">
        {{ mode === 'god' ? '上帝观战模式下暂无对局记录' : '我是玩家模式下暂无对局记录' }}
        ，切换上方模式可查看其他{{ otherModeCount }}局
      </section>
    </div>
    </div>

    <BoardEditor v-model="editorOpen" @confirm="useCustom" />

    <ConfirmDialog
      :model-value="!!abortTarget"
      title="中断结束这局？"
      :message="abortTarget ? `「${abortTarget.board_name}」已进行到第 ${abortTarget.day} 天，中断后将按和局收尾并公开所有身份，之后可在历史对局中查看复盘。` : ''"
      confirm-text="中断结束"
      danger
      @update:model-value="abortTarget = null"
      @confirm="doAbort"
    />
    <ConfirmDialog
      :model-value="!!deleteTarget"
      title="删除这局记录？"
      :message="deleteTarget ? `「${deleteTarget.board_name}」的对局记录与复盘将被删除，无法恢复。` : ''"
      confirm-text="删除"
      danger
      @update:model-value="deleteTarget = null"
      @confirm="doRemove"
    />

    <SettingsModal v-if="settingsOpen" @close="settingsOpen = false" />
    <ReviewModal v-if="reviewId" :gid="reviewId" @close="reviewId = ''" />
  </div>
</template>

<style scoped lang="scss">
.home {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.hero {
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: $spacing-lg;
  padding: 52px $spacing-xxl 30px;
  background:
    radial-gradient(ellipse 420px 200px at 14% -30%, rgba(226, 190, 118, 0.14), transparent 70%),
    linear-gradient(180deg, #1c1733 0%, #1c1733 36px, #201936 90px, $bg-base 100%);
  border-bottom: 1px solid $border-color;
  overflow: hidden;

  &::after {
    content: '';
    position: absolute;
    inset: 0;
    pointer-events: none;
    background:
      radial-gradient(circle 1.5px at 12% 30%, rgba(255, 255, 255, 0.5), transparent 100%),
      radial-gradient(circle 1px at 28% 62%, rgba(255, 255, 255, 0.35), transparent 100%),
      radial-gradient(circle 1.5px at 45% 22%, rgba(255, 255, 255, 0.4), transparent 100%),
      radial-gradient(circle 1px at 66% 48%, rgba(255, 255, 255, 0.3), transparent 100%),
      radial-gradient(circle 1.5px at 82% 70%, rgba(255, 255, 255, 0.35), transparent 100%);
  }
}

.hero-moon {
  font-size: 42px;
  filter: drop-shadow(0 0 18px rgba(226, 190, 118, 0.55));
  z-index: 1;
}

.hero-text {
  flex: 1;
  z-index: 1;

  h1 {
    font-size: 26px;
    font-weight: 700;
    letter-spacing: 2px;
    color: $text-primary;
  }
}

.hero-slogan {
  margin-top: 4px;
  font-size: $font-size-sm;
  color: $text-secondary;
  letter-spacing: 1px;
}

.settings-btn {
  z-index: 1;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: $text-secondary;
  transition: background $transition-fast, border-color $transition-fast, color $transition-fast;

  &:hover {
    background: rgba(var(--c-primary-rgb), 0.1);
    border-color: rgba(var(--c-primary-rgb), 0.5);
    color: $night-accent;
  }
}

.home-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.home-body {
  max-width: 1080px;
  margin: 0 auto;
  padding: $spacing-xl $spacing-xxl 80px;
  display: flex;
  flex-direction: column;
  gap: $spacing-lg;
}

.card {
  background: $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  padding: $spacing-xl $spacing-xl $spacing-xl + 4px;
  box-shadow: $shadow-sm;

  h2 {
    font-size: $font-size-md;
    margin-bottom: $spacing-md;
    color: $text-primary;
  }
}

.history-head {
  display: flex;
  align-items: baseline;
  gap: $spacing-md;
  flex-wrap: wrap;
  margin-bottom: $spacing-md;

  h2 {
    margin-bottom: 0;
  }
}

.history-stats {
  font-size: $font-size-sm;
  color: $text-secondary;

  .stat {
    margin-left: $spacing-sm;
    padding: 2px 10px;
    border-radius: 999px;

    &.wolf {
      color: #eba3a0;
      background: $wolf-color-light;
    }

    &.good {
      color: var(--c-primary);
      background: var(--c-primary-light);
    }

    &.draw {
      color: $text-secondary;
      background: rgba(255, 255, 255, 0.08);
    }
  }
}

// 开新一局：区块间用大留白分层，避免所有元素挤成一团
.create-card h2 {
  margin-bottom: 22px;
}

.create-card .block + .block {
  margin-top: 28px;
}

.section-label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: $text-secondary;
  font-size: $font-size-base;
  font-weight: 600;
  margin: 0 0 10px;

  &::before {
    content: '';
    width: 3px;
    height: 13px;
    border-radius: 2px;
    background: linear-gradient(180deg, $night-accent, var(--c-primary));
  }
}

.role-hint {
  color: $text-tertiary;
  font-size: $font-size-xs;
}

.role-select {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.role-chip {
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: $bg-input;
  cursor: pointer;
  font-size: $font-size-sm;
  transition: background $transition-fast, border-color $transition-fast;

  &:hover {
    background: $bg-hover;
  }

  &.active {
    background: var(--c-primary-light);
    border-color: var(--c-primary);
    color: $night-accent;
    font-weight: 600;
  }

  &.wolf.active {
    color: $wolf-color;
  }

  &.god.active {
    color: $god-color;
  }

  &.civil.active {
    color: #8fbf8f;
  }
}

.board-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.board-chip {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
  min-width: 190px;
  padding: 13px 16px;
  border-radius: $radius-md;
  border: 1px solid transparent;
  background: $bg-input;
  cursor: pointer;
  text-align: left;
  transition: background $transition-fast, border-color $transition-fast, box-shadow $transition-fast;

  &:hover {
    background: $bg-hover;
  }

  &.active {
    background: var(--c-primary-light);
    border-color: var(--c-primary);
    box-shadow: 0 0 12px rgba(var(--c-primary-rgb), 0.18);
  }

  &.custom {
    border: 1px dashed var(--c-border);
    background: transparent;

    &.active {
      border-style: solid;
      border-color: var(--c-primary);
      background: var(--c-primary-light);
    }
  }
}

.board-name {
  font-size: $font-size-base;
  font-weight: 600;
  color: $text-primary;
}

.board-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.role-badge {
  font-size: $font-size-xs;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 4px;

  &.wolf {
    color: $wolf-color;
    background: $wolf-color-light;
  }

  &.god {
    color: $god-color;
    background: $god-color-light;
  }

  &.civil {
    color: #8fbf8f;
    background: rgba(143, 191, 143, 0.14);
  }
}

.board-sub {
  font-size: $font-size-xs;
  color: $text-secondary;
  display: flex;
  align-items: center;
  gap: 6px;

  .dot {
    width: 3px;
    height: 3px;
    border-radius: 50%;
    background: $text-tertiary;
  }

  em {
    font-style: normal;

    &.city {
      color: $night-accent;
    }

    &.side {
      color: $god-color;
    }
  }
}

.sheriff-row {
  display: flex;
  align-items: center;
  gap: $spacing-md;
  padding: 12px 16px;
  border-radius: $radius-md;
  border: 1px solid $border-color;
  background: rgba(255, 255, 255, 0.02);
  cursor: pointer;
  transition: background $transition-fast, border-color $transition-fast;

  &:hover {
    background: rgba(255, 255, 255, 0.05);
  }

  &.on,
  &:has(.switch.on) {
    border-color: rgba(var(--c-primary-rgb), 0.55);
  }
}

.sheriff-icon {
  font-size: 22px;
}

.sheriff-text {
  flex: 1;
  min-width: 0;
}

.sheriff-title {
  font-size: $font-size-base;
  font-weight: 600;
  color: $text-primary;
  display: flex;
  align-items: center;
  gap: 8px;
}

.sheriff-desc {
  font-size: $font-size-xs;
  color: $text-tertiary;
  margin-top: 4px;
}

.sheriff-state {
  flex-shrink: 0;
  font-size: $font-size-xs;
  color: $text-secondary;
}

.swallow-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
  padding: 0 4px;
}

.swallow-label {
  font-size: $font-size-sm;
  color: $text-secondary;
}

.swallow-chip {
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid $border-color;
  background: transparent;
  color: $text-secondary;
  font-size: $font-size-xs;
  cursor: pointer;
  transition: all $transition-fast;

  &:hover {
    border-color: rgba(var(--c-primary-rgb), 0.5);
  }

  &.active {
    border-color: var(--c-primary);
    color: var(--c-primary);
    background: rgba(var(--c-primary-rgb), 0.1);
  }
}

.swallow-desc {
  font-size: $font-size-xs;
  color: $text-tertiary;
}

.switch {
  display: inline-flex;
  align-items: center;
  width: 34px;
  height: 18px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
  padding: 2px;
  transition: background $transition-fast;

  i {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: $text-secondary;
    transition: transform $transition-fast, background $transition-fast;
  }

  &.on {
    background: rgba(var(--c-primary-rgb), 0.55);

    i {
      transform: translateX(16px);
      background: $night-accent;
    }
  }
}

.persona-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.persona-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: $bg-input;
  cursor: pointer;
  font-size: $font-size-sm;
  transition: background $transition-fast, border-color $transition-fast;

  &:hover {
    background: $bg-hover;
  }

  &.active {
    background: var(--c-primary-light);
    border-color: var(--c-primary);
  }
}

.persona-check {
  color: var(--c-primary);
  font-size: $font-size-xs;
}

.empty-guide {
  display: flex;
  align-items: center;
  gap: $spacing-md;
  padding: $spacing-md $spacing-lg;
  border: 1px dashed var(--c-border);
  border-radius: $radius-md;
  background: rgba(255, 255, 255, 0.02);
}

.empty-moon {
  font-size: 28px;
}

.empty-title {
  font-size: $font-size-base;
  color: $text-primary;
}

.empty-desc {
  font-size: $font-size-sm;
  color: $text-secondary;
  margin: 2px 0 0;
}

.empty-guide .btn {
  margin-left: auto;
  flex-shrink: 0;
}

.mode-row {
  display: flex;
  align-items: center;
  gap: $spacing-md;
  flex-wrap: wrap;
}

.mode-seg {
  display: flex;
  border: 1px solid var(--c-border);
  border-radius: $radius-md;
  overflow: hidden;
  flex-shrink: 0;

  button {
    padding: 7px 16px;
    font-size: $font-size-sm;
    color: $text-secondary;
    background: transparent;
    cursor: pointer;
    transition: background $transition-fast, color $transition-fast;

    &:hover {
      background: $bg-hover;
    }

    &.active {
      background: var(--c-primary-light);
      color: $night-accent;
      font-weight: 600;
    }
  }
}

.mode-desc {
  font-size: $font-size-xs;
  color: $text-tertiary;
}

.create-bar {
  display: flex;
  align-items: center;
  gap: $spacing-md;
  padding-top: 22px;
  border-top: 1px solid $border-light;
}

.create-btn {
  padding: 10px 42px;
  font-size: $font-size-md;
}

.create-hint {
  font-size: $font-size-sm;
  color: $text-tertiary;
}

.mode-empty {
  color: $text-tertiary;
  font-size: $font-size-sm;
  text-align: center;
  padding: $spacing-lg;
}

.game-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: $spacing-sm $spacing-sm;
  margin: 0 (-$spacing-sm);
  border-radius: $radius-md;
  border-bottom: 1px solid $border-light;
  transition: background $transition-fast;

  &:hover {
    background: rgba(255, 255, 255, 0.03);
  }

  &:last-child {
    border-bottom: none;
  }

  .btn {
    flex-shrink: 0;
  }
}

.game-info {
  flex: 1;
  min-width: 0;
}

.game-title {
  font-size: $font-size-base;
  display: flex;
  align-items: center;
  gap: 8px;
}

.winner-badge {
  font-size: $font-size-xs;
  padding: 2px 8px;
  border-radius: 999px;
  flex-shrink: 0;

  &.wolf {
    color: $wolf-color;
    background: $wolf-color-light;
  }

  &.good {
    color: $night-accent;
    background: var(--c-primary-light);
  }

  &.draw {
    color: $text-secondary;
    background: rgba(255, 255, 255, 0.08);
  }
}

.game-sub {
  font-size: $font-size-xs;
  color: $text-tertiary;
}

.game-players {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.mini-player {
  font-size: $font-size-xs;
  color: $text-secondary;

  &.dead {
    opacity: 0.4;
    text-decoration: line-through;
  }
}
</style>
