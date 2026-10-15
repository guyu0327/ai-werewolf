<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ActionBar from '@/components/ActionBar.vue'
import ModalShell from '@/components/common/ModalShell.vue'
import RoleCard from '@/components/RoleCard.vue'
import SeatTable from '@/components/SeatTable.vue'
import SpeechStream from '@/components/SpeechStream.vue'
import { llmResume } from '@/api/game'
import { useGameStore } from '@/stores/game'
import { showToast } from '@/composables/toast'
import { setBgmTrack } from '@/composables/useBgm'

const route = useRoute()
const router = useRouter()
const store = useGameStore()

const selectedSeat = ref<number | null>(null)

const gid = computed(() => route.params.id as string)

onMounted(() => {
  store.loadAndConnect(gid.value)
})

// 背景音乐按昼夜切曲：夜晚阶段用 night 曲，其余（白天/大厅/结算）用 day 曲；离开对局页停止
watch(
  () => (store.state ? (store.state.phase.startsWith('night') ? 'night' : 'day') : null),
  (t) => setBgmTrack(t),
  { immediate: true },
)

const players = computed(() => store.state?.players ?? [])
const gameEnded = computed(() => store.state?.phase === 'game_over')
const isSpectator = computed(() => store.state?.human_seat == null)

// 当前发言的座位（用于牌桌高亮）：取流式中的发言条目
const activeSpeaker = computed(() => {
  const item = store.feed.find((f) => f.streaming)
  return item?.seat ?? null
})

// 死亡高亮：本场出过局的座位集合由玩家 alive 字段表达
const selectableSeats = computed(() => (store.pending && store.pending.allowed.length ? store.pending.allowed : null))

function onSelect(seat: number) {
  selectedSeat.value = seat
}

async function onSubmit(payload: {
  kind: string
  text?: string
  target?: number
  use?: boolean
  shoot?: boolean
  run?: boolean
  destroy?: boolean
  direction?: string
}) {
  try {
    await store.submit(payload)
    selectedSeat.value = null
  } catch (e) {
    showToast((e as Error).message)
  }
}

async function onStartOrResume() {
  try {
    if (store.state && store.state.day === 0) await store.start()
    else await store.resume()
    store.active = true
  } catch (e) {
    showToast((e as Error).message)
  }
}

const canExplode = computed(() => {
  const s = store.state
  if (!s || gameEnded.value) return false
  // 白天发言随时可爆；吞警徽规则下警长竞选发言阶段也可警上自爆
  const phaseOk = s.phase === 'day_speech' || (s.phase === 'sheriff_election' && s.badge_swallow !== 'none')
  return phaseOk && store.me?.role === 'werewolf' && !!store.me.alive
})

const explodeTip = computed(() =>
  store.state?.phase === 'sheriff_election'
    ? '警上自爆：翻牌亮狼立即出局（无遗言），竞选终止，按吞警徽规则结算'
    : '狼人技能：翻牌亮明身份立即出局（无遗言），白天立即结束进入黑夜',
)

async function onExplode() {
  try {
    await store.explode()
  } catch (e) {
    showToast((e as Error).message)
  }
}

// LLM 失败暂停：弹窗展示具体报错；用户关掉弹窗后底栏兜底入口仍可重试/终止
const pauseModalDismissed = ref(false)
watch(
  () => store.paused,
  (p) => {
    if (p) pauseModalDismissed.value = false
  },
)

async function onPause(action: 'retry' | 'abort') {
  if (!store.state) return
  try {
    await llmResume(store.state.id, action)
  } catch (e) {
    showToast((e as Error).message)
  }
}

function backHome() {
  store.disconnect()
  router.push('/')
}

function openReview() {
  store.disconnect()
  router.push({ path: '/', query: { review: gid.value } })
}

const showGodToggle = computed(() => !isSpectator.value && (gameEnded.value || (store.me != null && !store.me.alive)))

// 夜晚等待指示：夜晚阶段且没轮到真人操作时显示计时，避免长时间无事件被误认为卡死
const showNightWait = computed(() => !!store.state?.phase.startsWith('night') && !store.pending && store.active && !gameEnded.value)
const waitSecs = ref(0)
let waitTimer: number | null = null

watch(showNightWait, (v) => {
  if (v) {
    waitSecs.value = 0
    waitTimer = window.setInterval(() => waitSecs.value++, 1000)
  } else if (waitTimer != null) {
    window.clearInterval(waitTimer)
    waitTimer = null
  }
})

onBeforeUnmount(() => {
  if (waitTimer != null) window.clearInterval(waitTimer)
  setBgmTrack(null)
})
</script>

<template>
  <div class="game-view">
    <header class="game-header view-header" :class="{ night: store.state?.phase.startsWith('night') }">
      <button class="btn" @click="backHome">← 大厅</button>
      <div class="header-mid">
        <span class="phase-label" :class="{ night: store.state?.phase.startsWith('night') }">
          {{ store.state ? `第 ${store.state.day} 天 · ${store.phaseLabel}` : '连接中…' }}
          <em v-if="isSpectator" class="spec-tag">上帝观战</em>
        </span>
        <span v-if="!store.connected" class="conn-warn">（重连中）</span>
      </div>
      <div class="header-right">
        <button v-if="showGodToggle" class="btn" :class="{ primary: store.godView }" @click="store.toggleGodView()">
          {{ store.godView ? '👁 上帝视角：开' : '👁 上帝视角：关' }}
        </button>
        <button v-if="gameEnded" class="btn" @click="openReview">完整复盘</button>
      </div>
    </header>

    <div v-if="!store.state" class="loading">连接游戏中……</div>

    <div v-else class="game-body">
      <!-- 牌桌区 -->
      <div class="table-area" :class="{ night: store.state?.phase.startsWith('night') }">
        <SeatTable
          :players="players"
          :my-seat="store.state.human_seat"
          :active-seat="activeSpeaker"
          :selectable-seats="selectableSeats"
          :selected-seat="selectedSeat"
          :phase="store.state.phase"
          :sheriff-seat="store.state.sheriff_seat"
          @select="onSelect"
        >
          <template #center>
            <div class="center-phase">{{ store.phaseLabel }}</div>
            <div class="center-day">第 {{ store.state.day }} 天</div>
            <div v-if="store.state.board.win_condition === 'city'" class="center-board">屠城局</div>
            <div v-else class="center-board">屠边局</div>
          </template>
        </SeatTable>

        <div class="lobby-bar" v-if="store.state.phase === 'lobby'">
          <button class="btn primary" @click="onStartOrResume">开始游戏</button>
        </div>
        <div class="lobby-bar" v-else-if="!store.active && !gameEnded">
          <button class="btn primary" @click="onStartOrResume">继续游戏</button>
        </div>

        <div class="night-wait" v-if="showNightWait">
          <div class="pill">
            <span class="moon">🌙</span>
            <span>天黑请闭眼，夜晚行动进行中</span>
            <span class="secs">{{ waitSecs }}s</span>
          </div>
        </div>

        <div class="paused-bar" v-if="store.paused && pauseModalDismissed">
          <div class="pill pause-pill">
            <span class="pause-msg">{{ store.paused.message }}</span>
            <button class="btn danger" @click="onPause('abort')">终止对局</button>
            <button class="btn primary" @click="onPause('retry')">重试</button>
          </div>
        </div>
      </div>

      <!-- 右侧：身份牌 + 消息流 + 行动条 -->
      <div class="side-area">
        <div v-if="isSpectator" class="god-badge">👁 上帝视角 · 身份 / 夜聊 / 思考全程可见</div>
        <RoleCard
          v-else-if="store.me"
          :seat="store.me.seat"
          :name="store.me.name"
          :avatar="store.me.avatar"
          :alive="store.me.alive"
          :role="store.me.role"
        />
        <div class="stream-wrap">
          <SpeechStream :feed="store.feed" :players="players" :my-seat="store.state.human_seat" />
        </div>
        <ActionBar
          v-if="!isSpectator"
          :pending="store.pending"
          :note="store.pendingNote"
          :players="players"
          :my-seat="store.state.human_seat ?? 0"
          :can-explode="canExplode"
          :explode-tip="explodeTip"
          v-model:selected-seat="selectedSeat"
          @submit="onSubmit"
          @explode="onExplode"
        />
      </div>
    </div>

    <!-- LLM 失败暂停弹窗：展示具体报错，等用户重试或终止 -->
    <ModalShell
      v-if="store.paused && !pauseModalDismissed"
      title="模型调用失败，对局已暂停"
      width="520px"
      height="auto"
      @close="pauseModalDismissed = true"
    >
      <div class="pause-body">
        <p class="pause-error">{{ store.paused.message }}</p>
        <p class="pause-hint">检查模型配置或网络后可重试，出错的行动会重新执行；终止则本局按故障结束。</p>
        <div class="pause-actions">
          <button class="btn danger" @click="onPause('abort')">终止对局</button>
          <button class="btn primary" @click="onPause('retry')">重试</button>
        </div>
      </div>
    </ModalShell>
  </div>
</template>

<style scoped lang="scss">
.game-view {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.game-header {
  display: flex;
  align-items: center;
  gap: $spacing-md;
  padding: $spacing-sm $spacing-lg;
  border-bottom: 1px solid $border-color;
  background: $bg-sidebar;
  flex-shrink: 0;
  transition: background $transition-normal;

  &.night {
    background: linear-gradient(180deg, #1c1733, $bg-sidebar);
  }
}

.header-mid {
  flex: 1;
  text-align: center;
}

.phase-label {
  font-size: $font-size-base;
  color: $night-accent;

  &.night {
    color: #9f8fd8;
  }
}

.conn-warn {
  color: $text-tertiary;
  font-size: $font-size-xs;
  margin-left: 6px;
}

.spec-tag {
  font-style: normal;
  font-size: $font-size-xs;
  color: #aac8ec;
  background: rgba(122, 165, 216, 0.14);
  border-radius: 999px;
  padding: 1px 8px;
  margin-left: 8px;
  vertical-align: 1px;
}

.header-right {
  display: flex;
  gap: 8px;
}

.loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: $text-tertiary;
}

.game-body {
  flex: 1;
  display: flex;
  min-height: 0;
}

.table-area {
  flex: 1.15;
  position: relative;
  padding: $spacing-md;
  min-width: 0;
  transition: background $transition-normal;

  &.night {
    background: radial-gradient(ellipse 70% 60% at 50% 42%, #1b1631 0%, $night-bg 100%);
  }
}

.center-phase {
  font-size: $font-size-lg;
  color: $night-accent;
  font-weight: 700;
  letter-spacing: 2px;
  text-shadow: 0 0 16px rgba(var(--c-primary-rgb), 0.35);
}

.center-day {
  font-size: $font-size-sm;
  color: $text-secondary;
}

.center-board {
  font-size: $font-size-xs;
  color: $text-tertiary;
}

.lobby-bar {
  position: absolute;
  bottom: 18px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;

  .btn {
    padding: 10px 42px;
    font-size: $font-size-md;
  }
}

.night-wait {
  position: absolute;
  bottom: 18px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  pointer-events: none;

  .pill {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 20px;
    border-radius: 999px;
    border: 1px solid rgba(122, 165, 216, 0.35);
    background: rgba(20, 16, 38, 0.78);
    color: #b9aee4;
    font-size: $font-size-sm;
    backdrop-filter: blur(4px);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
  }

  .moon {
    animation: moon-breathe 2.4s ease-in-out infinite;
  }

  .secs {
    color: $text-tertiary;
    font-variant-numeric: tabular-nums;
    min-width: 30px;
    text-align: right;
  }
}

@keyframes moon-breathe {
  0%,
  100% {
    opacity: 0.5;
    transform: scale(1);
  }
  50% {
    opacity: 1;
    transform: scale(1.15);
  }
}

.paused-bar {
  position: absolute;
  bottom: 18px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  pointer-events: auto;

  .pause-pill {
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: 92%;
    padding: 9px 16px;
    border-radius: 999px;
    border: 1px solid rgba(216, 122, 122, 0.4);
    background: rgba(38, 20, 20, 0.82);
    color: #eec9c9;
    font-size: $font-size-sm;
    backdrop-filter: blur(4px);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
  }

  .pause-msg {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.pause-body {
  padding: $spacing-lg $spacing-xl;
  display: flex;
  flex-direction: column;
  gap: $spacing-md;
}

.pause-error {
  margin: 0;
  padding: $spacing-md;
  border-radius: $radius-md;
  border: 1px solid rgba(216, 122, 122, 0.35);
  background: rgba(216, 122, 122, 0.08);
  color: #eec9c9;
  font-size: $font-size-sm;
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}

.pause-hint {
  margin: 0;
  color: $text-tertiary;
  font-size: $font-size-xs;
}

.pause-actions {
  display: flex;
  justify-content: flex-end;
  gap: $spacing-sm;
}

.side-area {
  width: 44%;
  max-width: 560px;
  min-width: 380px;
  display: flex;
  flex-direction: column;
  gap: $spacing-sm;
  padding: $spacing-md;
  border-left: 1px solid $border-color;
  background: $bg-sidebar;
  min-height: 0;
}

.god-badge {
  padding: 8px 12px;
  border-radius: $radius-md;
  border: 1px solid rgba(122, 165, 216, 0.4);
  background: linear-gradient(135deg, rgba(122, 165, 216, 0.14), transparent 65%), $bg-panel;
  color: #aac8ec;
  font-size: $font-size-sm;
  font-weight: 600;
  text-align: center;
}

.stream-wrap {
  flex: 1;
  min-height: 0;
  background: $bg-content;
  border: 1px solid $border-color;
  border-radius: $radius-md;
  overflow: hidden;
}
</style>
