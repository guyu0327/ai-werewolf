<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import MarkdownIt from 'markdown-it'
import ModalShell from '@/components/common/ModalShell.vue'
import Avatar from '@/components/common/Avatar.vue'
import SheriffBadge from '@/components/common/SheriffBadge.vue'
import SpeechStream from '@/components/SpeechStream.vue'
import { fetchReview, fetchSummaryStatus, generateSummary } from '@/api/game'
import { fetchSettings, type ModelPreset } from '@/api/settings'
import { mapEvent, ROLE_LABELS, type FeedItem } from '@/utils/feed'
import { showToast } from '@/composables/toast'

const props = defineProps<{ gid: string; initialSection?: 'stream' | 'summary' }>()
const emit = defineEmits<{ close: [] }>()

const game = ref<Record<string, any> | null>(null)
const feed = ref<FeedItem[]>([])
const selectedSeat = ref<number | null>(null)
const presets = ref<ModelPreset[]>([])
const presetId = ref('')
// 总结与事件流互斥展开：点头部展开并把另一个收起；再点展开中的那个则切换到另一个
const expanded = ref<'stream' | 'summary'>(props.initialSection === 'summary' ? 'summary' : 'stream')

function toggleSection(s: 'stream' | 'summary') {
  expanded.value = expanded.value === s ? (s === 'summary' ? 'stream' : 'summary') : s
}
const md = new MarkdownIt({ html: false, breaks: true })
const summaryHtml = computed(() => (game.value?.summary ? md.render(game.value.summary) : ''))
// 生成在服务端后台任务执行：切页/最小化不中断，本页轮询状态展示进度
const running = computed(() => game.value?.summary_status === 'running')
let pollTimer: number | null = null

function stopPoll() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollSummary() {
  try {
    const r = await fetchSummaryStatus(props.gid)
    if (!game.value) return
    game.value.summary = r.summary
    game.value.summary_model = r.summary_model
    game.value.summary_status = r.status
    game.value.summary_error = r.error
    if (r.status !== 'running') stopPoll()
  } catch {
    /* 单次轮询失败忽略，下个周期重试 */
  }
}

function startPoll() {
  if (pollTimer === null) pollTimer = window.setInterval(pollSummary, 3000)
}

function toggleSeat(seat: number) {
  selectedSeat.value = selectedSeat.value === seat ? null : seat
}

async function genSummary() {
  if (!game.value || !presetId.value) return
  expanded.value = 'summary'
  try {
    await generateSummary(props.gid, presetId.value)
    game.value.summary_status = 'running'
    game.value.summary_error = ''
    startPoll()
  } catch (e) {
    showToast((e as Error).message)
  }
}

const selectedName = computed(() => players.value.find((p) => p.seat === selectedSeat.value)?.name ?? '')

const players = computed(() =>
  ((game.value?.players ?? []) as Record<string, any>[]).map((p) => ({
    seat: p.seat,
    name: p.name,
    avatar: p.avatar,
    is_human: p.is_human,
    alive: p.alive,
    role: p.role,
    role_label: p.role ? ROLE_LABELS[p.role] ?? p.role : null,
    role_revealed: true,
    vote_lost: Boolean(p.vote_lost),
  })),
)

onMounted(async () => {
  try {
    const settings = await fetchSettings().catch(() => null)
    presets.value = settings?.presets ?? []
    presetId.value = presets.value[0]?.id ?? ''
  } catch {
    /* 预设加载失败不阻塞复盘 */
  }
  try {
    game.value = await fetchReview(props.gid)
    const events = (game.value?.events ?? []) as Record<string, any>[]
    for (const e of events) {
      const item = mapEvent(e)
      if (item) {
        if (item.kind === 'speech') {
          const p = players.value.find((x) => x.seat === e.actor)
          if (p) {
            item.name = p.name
            item.avatar = p.avatar
          }
        }
        feed.value.push(item)
      }
    }
  } catch (e) {
    showToast((e as Error).message)
  }
  if (game.value?.summary_status === 'running') startPoll()
})

onUnmounted(stopPoll)

function roleClass(role: string | null): string {
  if (role === 'werewolf') return 'wolf'
  if (role === 'villager') return 'villager'
  return 'god'
}

function winnerText(): string {
  const w = game.value?.winner
  if (w === 'wolf') return '🐺 狼人阵营获胜'
  if (w === 'good') return '🎉 好人阵营获胜'
  if (w === 'error') return '🛑 系统异常'
  if ((game.value?.end_reason ?? '').includes('中断')) return '⏹ 已中断'
  return '🤝 和局'
}

function winnerClass(): string {
  const w = game.value?.winner
  if (w === 'wolf') return 'wolf'
  if (w === 'good') return 'good'
  return 'draw'
}
</script>

<template>
  <ModalShell title="完整复盘" width="min(960px, calc(100vw - 48px))" @close="emit('close')">
    <template #head-extra>
      <span v-if="game" class="winner" :class="winnerClass()">{{ winnerText() }} · {{ game.end_reason }}</span>
    </template>

    <div class="review">
      <div v-if="!game" class="loading">加载中……</div>

      <div v-else class="review-body">
        <!-- 身份总览 -->
        <section class="roles-card">
          <div class="roles-bar">
            <div class="roles-title">{{ game.board.name }} · {{ game.day }} 天</div>
            <span v-if="selectedSeat === null" class="roles-hint">点击玩家，高亮事件流中与其相关的内容</span>
            <button v-else class="filter-chip" @click="selectedSeat = null">
              {{ selectedSeat }}号 {{ selectedName }} · 取消筛选 ✕
            </button>
          </div>
          <div class="roles-grid">
            <div
              v-for="p in players"
              :key="p.seat"
              class="role-cell"
              :class="[roleClass(p.role), { dead: !p.alive, selected: selectedSeat === p.seat, dim: selectedSeat !== null && selectedSeat !== p.seat }]"
              @click="toggleSeat(p.seat)"
            >
              <Avatar :name="p.name" :avatar="p.avatar" :size="30" />
              <div class="role-cell-info">
                <div class="role-cell-name">
                  {{ p.seat }}号 {{ p.name }}<span v-if="p.is_human" class="you">（你）</span>
                  <span v-if="p.seat === game.sheriff_seat" class="sheriff-badge" title="警长">
                    <SheriffBadge :size="13" />警长
                  </span>
                </div>
                <div class="role-cell-role">{{ p.role_label }}</div>
              </div>
            </div>
          </div>
        </section>

        <!-- AI 复盘分析（与事件流互斥，可点开/点关切换） -->
        <section class="summary-card" :class="{ open: expanded === 'summary' }">
          <div class="card-head" @click="toggleSection('summary')">
            <span class="card-chevron">{{ expanded === 'summary' ? '▾' : '▸' }}</span>
            <div class="summary-title">🧠 AI 复盘分析</div>
            <span v-if="running" class="head-hint">后台生成中 · 可离开本页</span>
            <span v-else-if="expanded !== 'summary' && presets.length" class="head-hint">
              {{ game.summary ? '点击查看' : '未生成 · 可点击右侧按钮生成' }}
            </span>
            <div v-if="presets.length" class="summary-actions" @click.stop>
              <select v-model="presetId" class="preset-select" :disabled="running">
                <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}（{{ p.model }}）</option>
              </select>
              <button class="btn primary" :disabled="running || !presetId" @click="genSummary">
                {{ running ? '后台生成中……' : game.summary ? '重新生成' : '生成分析' }}
              </button>
            </div>
          </div>
          <div v-if="expanded === 'summary'" class="summary-scroll">
            <div v-if="running" class="summary-loading">正在后台通读整局事件并生成分析，关闭弹窗不会中断，通常需要几十秒……</div>
            <div v-else-if="game.summary_error" class="summary-error">{{ game.summary_error }}</div>
            <div v-else-if="summaryHtml" class="summary-body md" v-html="summaryHtml"></div>
            <div v-else class="summary-empty">
              {{ presets.length ? '选择一个模型，让它以上帝视角通读整局，分析双方胜负关键。' : '尚无可用模型预设，请先在设置中添加。' }}
            </div>
            <div v-if="!running && game.summary && game.summary_model" class="summary-meta">由 {{ game.summary_model }} 生成</div>
          </div>
        </section>

        <!-- 全量事件流（与总结互斥） -->
        <section class="stream-card" :class="{ open: expanded === 'stream' }">
          <div class="card-head" @click="toggleSection('stream')">
            <span class="card-chevron">{{ expanded === 'stream' ? '▾' : '▸' }}</span>
            <div class="summary-title">📜 完整复盘流程</div>
            <span class="head-hint">{{ feed.length }} 条事件{{ expanded === 'stream' ? '' : ' · 点击展开' }}</span>
          </div>
          <SpeechStream
            v-if="expanded === 'stream'"
            :feed="feed"
            :players="players"
            :highlight-seat="selectedSeat"
            :my-seat="players.find((p) => p.is_human)?.seat ?? null"
          />
        </section>
      </div>
    </div>
  </ModalShell>
</template>

<style scoped lang="scss">
.review {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.winner {
  font-size: $font-size-sm;
  padding: 3px 10px;
  border-radius: 999px;

  &.wolf {
    color: #eba3a0;
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

.loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: $text-tertiary;
}

.review-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: $spacing-lg;
  gap: $spacing-md;
  max-width: 900px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

.roles-card {
  background: $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  padding: $spacing-md $spacing-lg;
}

.summary-card {
  background: linear-gradient(135deg, rgba(var(--c-primary-rgb), 0.06), transparent 55%), $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  padding: $spacing-md $spacing-lg;
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;

  &.open {
    flex: 1 1 0;
    min-height: 0;
  }
}

.card-head {
  display: flex;
  align-items: center;
  gap: $spacing-sm;
  min-height: 28px;
  cursor: pointer;
  user-select: none;
}

.card-chevron {
  width: 12px;
  flex: 0 0 auto;
  color: $text-tertiary;
  font-size: $font-size-sm;
}

.head-hint {
  font-size: $font-size-xs;
  color: $text-tertiary;
}

.summary-title {
  font-size: $font-size-base;
  font-weight: 600;
  color: $night-accent;
  white-space: nowrap;
}

.summary-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.preset-select {
  max-width: 260px;
}

.summary-loading {
  color: $night-accent;
  font-size: $font-size-sm;
  padding: $spacing-sm 0;
  animation: summary-pulse 1.6s ease-in-out infinite;
}

@keyframes summary-pulse {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}

.summary-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: $spacing-sm;
  margin-top: $spacing-sm;
  padding-right: 4px;
}

.summary-body {
  flex: 0 0 auto;
  word-break: break-word;
  line-height: 1.75;
  font-size: $font-size-sm;
  color: $text-primary;

  & :deep(h1) {
    font-size: 1.15em;
    margin: 14px 0 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid $border-light;
    color: var(--c-primary);
  }

  & :deep(h2) {
    font-size: 1.08em;
    margin: 12px 0 6px;
    color: var(--c-primary);
  }

  & :deep(h3) {
    font-size: 1em;
    margin: 10px 0 6px;
  }

  & :deep(p) {
    margin: 6px 0;
  }

  & :deep(ul) {
    list-style: disc;
    padding-left: 1.4em;
    margin: 6px 0;
  }

  & :deep(ol) {
    list-style: decimal;
    padding-left: 1.4em;
    margin: 6px 0;
  }

  & :deep(li) {
    margin: 3px 0;
  }

  & :deep(strong) {
    color: $text-white;
  }

  & :deep(hr) {
    border: none;
    border-top: 1px solid $border-color;
    margin: 10px 0;
  }

  & :deep(blockquote) {
    margin: 6px 0;
    padding: 4px 10px;
    border-left: 3px solid rgba(var(--c-primary-rgb), 0.5);
    background: rgba(var(--c-primary-rgb), 0.06);
    color: $text-secondary;
  }

  & :deep(code) {
    background: rgba(255, 255, 255, 0.08);
    border-radius: 4px;
    padding: 1px 5px;
    font-size: 0.9em;
  }

  & :deep(table) {
    border-collapse: collapse;
    margin: 8px 0;
  }

  & :deep(th),
  & :deep(td) {
    border: 1px solid $border-color;
    padding: 4px 8px;
  }
}

.summary-empty {
  color: $text-tertiary;
  font-size: $font-size-sm;
  padding: $spacing-xs 0;
}

.summary-error {
  color: #eba3a0;
  font-size: $font-size-sm;
  padding: $spacing-xs 0;
}

.summary-meta {
  align-self: flex-end;
  font-size: $font-size-xs;
  color: $text-tertiary;
}

.roles-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: $spacing-md;
  margin-bottom: $spacing-sm;
  min-height: 26px;
}

.roles-title {
  font-size: $font-size-sm;
  color: $text-secondary;
}

.roles-hint {
  font-size: $font-size-xs;
  color: $text-tertiary;
}

.filter-chip {
  font-size: $font-size-xs;
  color: var(--c-primary);
  background: rgba(var(--c-primary-rgb), 0.12);
  border: 1px solid rgba(var(--c-primary-rgb), 0.45);
  border-radius: 999px;
  padding: 2px 10px;
  cursor: pointer;

  &:hover {
    background: rgba(var(--c-primary-rgb), 0.2);
  }
}

.roles-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px;
}

.role-cell {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: $radius-md;
  border: 1px solid $border-color;
  background: $bg-input;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, opacity 0.15s;

  &:hover {
    border-color: rgba(var(--c-primary-rgb), 0.55);
  }

  &.wolf {
    background: $wolf-color-light;
    border-color: rgba(208, 122, 114, 0.4);
  }

  &.god {
    background: $god-color-light;
    border-color: rgba(122, 165, 216, 0.35);
  }

  &.dead {
    opacity: 0.5;
    filter: grayscale(0.6);
  }

  &.selected {
    border-color: var(--c-primary);
    box-shadow: 0 0 12px rgba(var(--c-primary-rgb), 0.45);
    opacity: 1;
    filter: none;
  }

  &.dim {
    opacity: 0.35;
    filter: grayscale(0.6);
  }
}

.role-cell-info {
  line-height: 1.3;
  min-width: 0;
}

.role-cell-name {
  font-size: $font-size-sm;
}

.you {
  color: var(--c-primary);
  font-size: $font-size-xs;
}

.sheriff-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: 4px;
  font-size: 10px;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 999px;
  font-weight: 600;
  color: #f0d48a;
  background: linear-gradient(135deg, rgba(226, 190, 118, 0.3), rgba(226, 190, 118, 0.12));
  border: 1px solid rgba(226, 190, 118, 0.55);
  box-shadow: 0 0 8px rgba(226, 190, 118, 0.25);
  white-space: nowrap;
}

.role-cell-role {
  font-size: $font-size-xs;
  color: $text-secondary;
}

.stream-card {
  display: flex;
  flex-direction: column;
  background: $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  overflow: hidden;

  &.open {
    flex: 1 1 0;
    min-height: 0;

    :deep(.speech-stream) {
      flex: 1;
      min-height: 0;
    }
  }

  &:not(.open) {
    flex: 0 0 auto;
  }

  .card-head {
    padding: $spacing-sm $spacing-lg;
  }
}
</style>
