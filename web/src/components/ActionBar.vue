<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { PendingInfo, PublicPlayer } from '@/api/game'
import { fetchVoiceUsage } from '@/api/voice'
import { useVoiceInput } from '@/composables/useVoiceInput'
import { showToast } from '@/composables/toast'
import { setBgmDuck } from '@/composables/useBgm'

const props = withDefaults(
  defineProps<{
    pending: PendingInfo | null
    note?: string
    players: PublicPlayer[]
    mySeat: number
    selectedSeat: number | null
    canExplode?: boolean
    explodeTip?: string
  }>(),
  { note: '', selectedSeat: null, canExplode: false, explodeTip: '' },
)

const emit = defineEmits<{
  submit: [payload: { kind: string; text?: string; target?: number; use?: boolean; shoot?: boolean; run?: boolean; destroy?: boolean; direction?: string }]
  'update:selectedSeat': [seat: number | null]
  explode: []
}>()

const text = ref('')
const explodeArmed = ref(false)

// 语音输入：识别文本填入输入框（不自动发送），再次点击麦克风结束
const voiceUsage = ref<{ used: number; limit: number } | null>(null)
const voiceBase = ref('')
const voice = useVoiceInput(
  (t) => {
    text.value = voiceBase.value + t
  },
  (t) => {
    text.value = voiceBase.value + t
    refreshVoiceUsage()
  },
)
const voiceRecording = computed(() => voice.state.value === 'recording')
const voiceReady = computed(
  () => voice.supported && !voiceRecording.value && (voiceUsage.value == null || voiceUsage.value.used < voiceUsage.value.limit),
)

const micTitle = computed(() => {
  if (!voice.supported) return '当前环境不支持麦克风输入'
  if (voiceUsage.value && voiceUsage.value.used >= voiceUsage.value.limit)
    return `今日语音识别次数已用完（${voiceUsage.value.limit} 次/日）`
  return voiceRecording.value ? '停止录音' : '点击说话，识别文字填入输入框'
})

async function refreshVoiceUsage() {
  try {
    voiceUsage.value = await fetchVoiceUsage()
  } catch {
    /* 用量接口不可用时按钮保持可用，由后端兜底拒绝 */
  }
}

onMounted(refreshVoiceUsage)

async function toggleVoice() {
  if (voice.state.value !== 'idle') {
    voice.stop()
    return
  }
  voiceBase.value = text.value
  try {
    await voice.start()
  } catch (e) {
    showToast((e as Error).message)
  }
}

// 麦克风会话期间（含连接中）压低背景音乐，避免被采进语音识别
watch(voice.state, (s) => setBgmDuck(s !== 'idle'))
onBeforeUnmount(() => setBgmDuck(false))

watch(
  () => props.canExplode,
  (v) => {
    if (!v) explodeArmed.value = false
  },
)

function confirmExplode() {
  if (!explodeArmed.value) {
    explodeArmed.value = true
    return
  }
  explodeArmed.value = false
  emit('explode')
}

watch(
  () => props.pending?.kind,
  () => {
    text.value = ''
    emit('update:selectedSeat', null)
  },
)

const needTarget = computed(() =>
  ['vote', 'wolf_vote', 'seer_check', 'witch_poison', 'hunter', 'guard', 'sheriff_vote', 'badge_pass'].includes(props.pending?.kind ?? ''),
)
const shootMode = computed(() => props.pending?.kind === 'hunter')
const badgeMode = computed(() => props.pending?.kind === 'badge_pass')

function pickSeat() {
  return props.selectedSeat
}

function sendText(kind: string) {
  const t = text.value.trim()
  if (!t) return
  emit('submit', { kind, text: t })
  text.value = ''
}

function submitTarget() {
  const kind = props.pending!.kind
  const target = pickSeat()
  if (kind === 'hunter') {
    if (target == null) return
    emit('submit', { kind, shoot: true, target })
    return
  }
  if (target == null) return
  emit('submit', { kind, target })
}

function submitHunterGiveup() {
  emit('submit', { kind: 'hunter', shoot: false })
}

function submitBadgeDestroy() {
  emit('submit', { kind: 'badge_pass', destroy: true })
}

const actionHint = computed(() => {
  switch (props.pending?.kind) {
    case 'speech':
      return '轮到你发言'
    case 'last_words':
      return '请留下遗言'
    case 'vote':
      return '投票放逐一名玩家'
    case 'wolf_vote':
      return '狼队行动：选择今晚的刀口（可自刀）'
    case 'seer_check':
      return '预言家：选择今晚查验的玩家'
    case 'guard':
      return '守卫：选择今晚守护的玩家（可守自己，不能连守同一人）'
    case 'witch_save':
      return '女巫：是否使用解药？'
    case 'witch_poison':
      return '女巫：是否使用毒药？'
    case 'hunter':
      return '猎人技能：开枪带走一名玩家，或放弃'
    case 'sheriff_run':
      return '警长竞选：是否上警参选？'
    case 'sheriff_vote':
      return '警长竞选：投票支持一名候选人当选警长'
    case 'badge_pass':
      return '警徽处置：移交给一名存活玩家，或撕毁警徽'
    case 'speech_order':
      return '警长：选择首日发言从你的哪一边开始（你最后发言）'
    default:
      return ''
  }
})
</script>

<template>
  <div class="action-bar" :class="{ idle: !pending && !canExplode }">
    <template v-if="pending">
      <div class="action-head">
        <span class="action-hint">{{ actionHint }}</span>
        <span v-if="note" class="action-note">{{ note }}</span>
      </div>

      <!-- 发言 / 遗言 -->
      <div v-if="pending.kind === 'speech' || pending.kind === 'last_words'" class="text-row">
        <textarea
          v-model="text"
          class="input action-textarea"
          :placeholder="pending.kind === 'speech' ? '说出你的判断……' : '交代你的身份与判断……'"
          rows="2"
          @keydown.enter.exact.prevent="sendText(pending.kind)"
        ></textarea>
        <button
          class="btn mic-btn"
          :class="{ recording: voiceRecording }"
          :disabled="!voiceReady"
          :title="micTitle"
          @click="toggleVoice"
        >
          {{ voiceRecording ? '⏹' : '🎙' }}
        </button>
        <button class="btn primary" :disabled="!text.trim()" @click="sendText(pending.kind)">发送</button>
      </div>

      <!-- 需要选目标 -->
      <div v-else-if="needTarget" class="target-row">
        <span class="target-tip">{{ selectedSeat != null ? `已选 ${selectedSeat}号` : '请在左侧牌桌点击选择目标' }}</span>
        <button v-if="shootMode" class="btn" @click="submitHunterGiveup">放弃开枪</button>
        <button v-if="badgeMode" class="btn danger" @click="submitBadgeDestroy">撕毁警徽</button>
        <button v-if="pending.kind === 'witch_poison'" class="btn" @click="emit('submit', { kind: 'witch_poison', use: false })">
          不用毒药
        </button>
        <button
          v-if="pending.kind === 'vote' || pending.kind === 'sheriff_vote'"
          class="btn"
          @click="emit('submit', { kind: pending.kind, target: 0 })"
        >
          弃票
        </button>
        <button class="btn primary" :disabled="selectedSeat == null" @click="submitTarget">
          {{ shootMode ? '开枪' : badgeMode ? '移交警徽' : pending.kind === 'witch_poison' ? '使用毒药' : '确认' }}
        </button>
      </div>

      <!-- 上警 / 发言方向 -->
      <div v-else-if="pending.kind === 'sheriff_run'" class="target-row">
        <button class="btn primary" @click="emit('submit', { kind: 'sheriff_run', run: true })">🎖 上警参选</button>
        <button class="btn" @click="emit('submit', { kind: 'sheriff_run', run: false })">不上警</button>
      </div>
      <div v-else-if="pending.kind === 'speech_order'" class="target-row">
        <button class="btn primary" @click="emit('submit', { kind: 'speech_order', direction: 'left' })">从左边开始</button>
        <button class="btn primary" @click="emit('submit', { kind: 'speech_order', direction: 'right' })">从右边开始</button>
      </div>

      <!-- 女巫解药 -->
      <div v-else-if="pending.kind === 'witch_save'" class="target-row">
        <button class="btn primary" @click="emit('submit', { kind: 'witch_save', use: true })">使用解药</button>
        <button class="btn" @click="emit('submit', { kind: 'witch_save', use: false })">不使用</button>
      </div>
    </template>

    <div v-else class="idle-hint">等待其他玩家行动……</div>

    <!-- 自爆：白天发言阶段狼人可用（含 AI 发言中途），独立于 pending -->
    <div v-if="canExplode" class="target-row explode-row">
      <span class="explode-tip">{{ explodeTip || '狼人技能：翻牌亮明身份立即出局（无遗言），白天立即结束进入黑夜' }}</span>
      <button v-if="!explodeArmed" class="btn danger" @click="confirmExplode">自爆</button>
      <template v-else>
        <button class="btn danger" @click="confirmExplode">确认自爆</button>
        <button class="btn" @click="explodeArmed = false">取消</button>
      </template>
    </div>
  </div>
</template>

<style scoped lang="scss">
.action-bar {
  border-top: 2px solid rgba(var(--c-primary-rgb), 0.45);
  background: linear-gradient(180deg, rgba(var(--c-primary-rgb), 0.05), transparent 60%), $bg-panel;
  border-radius: 0 0 $radius-md $radius-md;
  padding: $spacing-md $spacing-lg;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 86px;
  justify-content: center;
  transition: opacity $transition-fast;

  &.idle {
    opacity: 0.55;
    border-top-color: $border-color;
    background: $bg-panel;
  }
}

.action-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.action-hint {
  color: var(--c-primary);
  font-size: $font-size-base;
  font-weight: 600;
}

.action-note {
  color: $text-secondary;
  font-size: $font-size-sm;
}

.text-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.mic-btn {
  font-size: $font-size-md;

  &.recording {
    color: #ff5f5f;
    border-color: rgba(255, 95, 95, 0.55);
    animation: mic-pulse 1.2s ease-in-out infinite;
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
}

@keyframes mic-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(255, 95, 95, 0.35);
  }
  50% {
    box-shadow: 0 0 0 6px rgba(255, 95, 95, 0);
  }
}

.action-textarea {
  resize: none;
  line-height: 1.5;
}

.target-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.target-tip {
  color: $night-accent;
  font-size: $font-size-sm;
  margin-right: auto;
}

.idle-hint {
  text-align: center;
  color: $text-tertiary;
  font-size: $font-size-sm;
}

.explode-row {
  justify-content: flex-end;
  border-top: 1px dashed rgba(255, 80, 80, 0.35);
  padding-top: 8px;
}

.explode-tip {
  color: $text-tertiary;
  font-size: $font-size-sm;
  margin-right: auto;
}
</style>
