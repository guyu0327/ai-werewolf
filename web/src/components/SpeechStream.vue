<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Avatar from '@/components/common/Avatar.vue'
import type { FeedItem } from '@/utils/feed'
import type { PublicPlayer } from '@/api/game'

const props = defineProps<{
  feed: FeedItem[]
  players: PublicPlayer[]
  highlightSeat?: number | null
  mySeat?: number | null
}>()

const scroller = ref<HTMLElement>()
const stickToBottom = ref(true)

function onScroll() {
  const el = scroller.value
  if (el) stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

async function scrollToBottom() {
  if (!stickToBottom.value) return // 用户已上翻查看历史，不打断
  await nextTick()
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

// 高频 delta（并行思考/流式发言）下强制滚动会逐条 reflow 巨型列表，节流到 100ms 并保证尾部补一次
let lastScrollAt = 0
let scrollTimer: number | null = null

function scheduleScroll() {
  if (!stickToBottom.value) return
  const now = performance.now()
  const gap = now - lastScrollAt
  if (gap >= 100) {
    lastScrollAt = now
    void nextTick().then(scrollToBottom)
  } else if (scrollTimer == null) {
    scrollTimer = window.setTimeout(() => {
      scrollTimer = null
      lastScrollAt = performance.now()
      void nextTick().then(scrollToBottom)
    }, 100 - gap)
  }
}

onBeforeUnmount(() => {
  if (scrollTimer != null) clearTimeout(scrollTimer)
})

watch(() => props.feed[props.feed.length - 1]?.id, scheduleScroll)

// 流式发言过程中也持续吸底
watch(
  () => props.feed.reduce((a, f) => a + f.text.length, 0),
  scheduleScroll,
)

function playerOf(seat?: number): PublicPlayer | undefined {
  if (seat == null) return undefined
  return props.players.find((p) => p.seat === seat)
}

// 发言里的座位指代（如「11号」）切成可读徽章：带头像与名字，防止 1 号把 11 号误读成自己。
// 模板每个渲染周期对每条发言重新调用，加缓存避免长对局里每个 delta 都重切全部历史发言
type Seg = string | { seat: number }
const seatRe = /(\d{1,2})号/g
const tokenCache = new Map<string, Seg[]>()

function tokenize(text: string): Seg[] {
  const hit = tokenCache.get(text)
  if (hit) return hit
  const segs: Seg[] = []
  let last = 0
  for (const m of text.matchAll(seatRe)) {
    const seat = Number(m[1])
    if (!props.players.some((p) => p.seat === seat)) continue
    const idx = m.index ?? 0
    if (idx > last) segs.push(text.slice(last, idx))
    segs.push({ seat })
    last = idx + m[0].length
  }
  if (last < text.length) segs.push(text.slice(last))
  if (tokenCache.size >= 1000) tokenCache.clear()
  tokenCache.set(text, segs)
  return segs
}

// 复盘聚光灯：选中某玩家后，与其无关的条目压暗。
// 「相关」按事件结构判定（行动者/目标/死者/投票双方），不做正文文本匹配。
const highlight = computed(() => (props.highlightSeat == null ? null : { n: props.highlightSeat }))

function dimmed(item: FeedItem): boolean {
  const h = highlight.value
  if (!h) return false
  return !(item.related ?? []).includes(h.n)
}

function tagClass(tag?: string): string {
  if (!tag) return ''
  if (tag.includes('狼')) return 'wolf'
  if (tag === '遗言') return 'grey'
  return 'gold'
}

// 身份配色：与牌桌角色牌同色系；role 为 null（玩家视角未亮牌）返回空串保持默认色
function roleClass(role: string | null): string {
  if (role === 'werewolf') return 'wolf'
  if (role === 'villager') return 'villager'
  return role ? 'god' : ''
}

// 投票行的人名配色：身份可见时按身份牌色；真人玩家恒用黄色高亮（自己始终知道自己的身份）
function nameClass(p?: PublicPlayer): Record<string, boolean> {
  const cls: Record<string, boolean> = {}
  const rc = p ? roleClass(p.role ?? null) : ''
  if (rc) cls[rc] = true
  if (p?.is_human) cls.me = true
  return cls
}

function tailText(text: string, n = 140): string {
  return text.length > n ? '…' + text.slice(-n) : text
}
</script>

<template>
  <div ref="scroller" class="speech-stream" @scroll="onScroll">
    <template v-for="item in feed" :key="item.id">
      <!-- 主持人台词 -->
      <div v-if="item.kind === 'host'" class="host-line" :class="{ dim: dimmed(item) }">{{ item.text }}</div>

      <!-- 发言气泡 -->
      <div v-else-if="item.kind === 'speech'" class="speech-row" :class="{ dim: dimmed(item) }">
        <Avatar :name="playerOf(item.seat)?.name ?? item.name ?? '?'" :avatar="playerOf(item.seat)?.avatar ?? item.avatar" :size="32" />
        <div class="speech-body">
          <div class="speech-meta">
            <span class="speech-name" :class="nameClass(playerOf(item.seat))">{{ playerOf(item.seat)?.name ?? item.name ?? `${item.seat}号` }}</span>
            <span class="speech-seat">{{ item.seat }}号</span>
            <span v-if="item.tag" class="tag" :class="tagClass(item.tag)">{{ item.tag }}</span>
          </div>
          <div class="bubble" :class="{ typing: item.streaming }">
            <template v-for="(seg, i) in tokenize(item.text)" :key="i">
              <template v-if="typeof seg === 'string'">{{ seg }}</template>
              <span v-else class="mention" :class="[roleClass(playerOf(seg.seat)?.role ?? null), { me: playerOf(seg.seat)?.is_human }]">
                <Avatar :name="playerOf(seg.seat)?.name ?? '?'" :avatar="playerOf(seg.seat)?.avatar" :size="14" />
                {{ seg.seat }}号<template v-if="playerOf(seg.seat)?.is_human"> 玩家</template><template v-else> {{ playerOf(seg.seat)?.name ?? '' }}</template>
              </span>
            </template>
            <span v-if="item.streaming" class="cursor">▍</span>
          </div>
        </div>
      </div>

      <!-- 投票过程（逐个亮票） -->
      <div v-else-if="item.kind === 'voteLive'" class="vote-live" :class="{ done: !item.streaming, dim: dimmed(item) }">
        <span class="vote-icon">🗳</span>
        <template v-if="item.streaming">
          <span><b>{{ item.seat }}号</b> <span class="vote-name" :class="nameClass(playerOf(item.seat))">{{ item.name ?? '' }}</span> 正在投票</span>
          <span class="dots"><i /></span>
        </template>
        <template v-else>
          <div class="vote-lines">
            <!-- 一行内书写：Vue 会吞掉元素间含换行的空白，拆行会丢空格 -->
            <span class="vote-main"><b>{{ item.seat }}号</b> <span class="vote-name" :class="nameClass(playerOf(item.seat))">{{ item.name ?? '' }}</span> <template v-if="item.target === 0">弃票</template><template v-else>投给 <b class="vote-target">{{ item.target }}号</b> <span class="vote-name" :class="nameClass(playerOf(item.target))">{{ playerOf(item.target)?.name ?? '' }}</span></template></span>
            <span v-if="item.reason" class="vote-reason">{{ item.reason }}</span>
          </div>
        </template>
      </div>

      <!-- 思考过程（上帝视角可见） -->
      <div v-else-if="item.kind === 'thinking'" class="thinking-card" :class="{ live: item.streaming, dim: dimmed(item) }">
        <div class="thinking-head">
          <span class="thinking-seat">{{ item.seat }}</span>
          {{ playerOf(item.seat)?.name ?? `${item.seat}号` }}
          <span v-if="playerOf(item.seat)?.role_label" class="thinking-role" :class="roleClass(playerOf(item.seat)?.role ?? null)">{{ playerOf(item.seat)?.role_label }}</span>
          <span v-if="item.streaming" class="thinking-status">正在思考<span class="dots"><i /></span></span>
          <span v-else class="thinking-status">的思考过程</span>
        </div>
        <!-- 流式思考框固定高度底端对齐：出字只在框内裁切，列表不随高度抖动；
             玩家模式收不到思考内容（text 恒空），不渲染正文框，占位卡只留一行 -->
        <div v-if="item.streaming && item.text" class="thinking-body"><span class="thinking-live-tail">{{ tailText(item.text) }}</span></div>
        <details v-else-if="item.text" class="thinking-fold">
          <summary>展开思考（{{ item.text.length }} 字）</summary>
          <div class="thinking-body">{{ item.text }}</div>
        </details>
      </div>

      <!-- 私密信息（狼队频道 / 验人 / 女巫） -->
      <div v-else-if="item.kind === 'private'" class="private-row" :class="{ dim: dimmed(item) }">
        <span class="tag" :class="tagClass(item.tag)">{{ item.tag }}</span>
        <span class="private-text">{{ playerOf(item.seat)?.name ? `${playerOf(item.seat)!.name}：` : '' }}{{ item.text }}</span>
      </div>

      <!-- 夜晚结算 -->
      <div v-else-if="item.kind === 'night'" class="night-card" :class="{ dim: dimmed(item) }">{{ item.text }}</div>

      <!-- 猎人开枪 -->
      <div v-else-if="item.kind === 'hunter'" class="hunter-card" :class="{ dim: dimmed(item) }">
        <Avatar :name="playerOf(item.seat)?.name ?? '?'" :avatar="playerOf(item.seat)?.avatar" :size="26" />
        <span>{{ playerOf(item.seat)?.name ?? `${item.seat}号` }}（{{ item.seat }}号） {{ item.text }}</span>
      </div>

      <!-- 投票结果 -->
      <div v-else-if="item.kind === 'vote'" class="vote-card" :class="{ dim: dimmed(item) }">
        <div class="vote-title">投票结果</div>
        <div v-for="(line, i) in item.text.split('\n')" :key="i" class="vote-line">{{ line }}</div>
      </div>

      <!-- 游戏结束 -->
      <div v-else-if="item.kind === 'gameover'" class="gameover-card" :class="{ dim: dimmed(item) }">{{ item.text }}</div>

      <!-- 系统提示 -->
      <div v-else class="system-line" :class="{ dim: dimmed(item) }">{{ item.text }}</div>
    </template>
  </div>
</template>

<style scoped lang="scss">
.speech-stream {
  height: 100%;
  overflow-y: auto;
  padding: $spacing-md $spacing-lg;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dim {
  opacity: 0.16;
  filter: grayscale(0.5);
  transition: opacity 0.15s;
}

.host-line {
  text-align: center;
  color: $night-accent;
  font-size: $font-size-sm;
  opacity: 0.85;
  margin: 4px 0;
}

.speech-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.speech-body {
  max-width: 78%;
}

.speech-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
  font-size: $font-size-xs;
}

.speech-name {
  color: $text-secondary;
  font-size: $font-size-sm;

  &.wolf {
    color: #eba3a0;
  }

  &.god {
    color: #aac8ec;
  }

  &.villager {
    color: #a8c8a8;
  }

  // .me 放最后：真人玩家恒用黄色，压过身份色（自己始终知道自己的身份）
  &.me {
    color: #ffff00;
  }
}

.speech-seat {
  color: $text-tertiary;
}

.tag {
  font-size: 10px;
  border-radius: 4px;
  padding: 0 5px;
  line-height: 16px;
  flex-shrink: 0;
  white-space: nowrap;

  &.wolf {
    color: #eba3a0;
    background: rgba(208, 122, 114, 0.28);
  }

  &.gold {
    color: $night-accent;
    background: rgba(var(--c-primary-rgb), 0.16);
  }

  &.grey {
    color: $text-secondary;
    background: rgba(255, 255, 255, 0.08);
  }
}

.bubble {
  background: $bg-bubble-other;
  border: 1px solid $border-light;
  border-top-left-radius: 2px;
  border-radius: $radius-md;
  padding: 8px 12px;
  line-height: 1.6;
  font-size: $font-size-base;
  white-space: pre-wrap;
  word-break: break-word;
}

.cursor {
  animation: blink 0.8s infinite;
  color: var(--c-primary);
}

.mention {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 0 6px 0 3px;
  margin: 0 1px;
  border-radius: 999px;
  background: rgba(122, 165, 216, 0.16);
  border: 1px solid rgba(122, 165, 216, 0.4);
  color: #aac8ec;
  font-size: 0.92em;
  line-height: 19px;
  vertical-align: middle;
  white-space: nowrap;

  // 被提及者的身份可见时，徽章跟随其身份配色
  &.wolf {
    background: rgba(208, 122, 114, 0.16);
    border-color: rgba(208, 122, 114, 0.5);
    color: #eba3a0;
  }

  &.villager {
    background: rgba(143, 191, 143, 0.14);
    border-color: rgba(143, 191, 143, 0.4);
    color: #a8c8a8;
  }

  &.me {
    background: rgba(255, 255, 0, 0.2);
    border-color: rgba(255, 255, 0, 0.7);
    color: #ffff00;
    box-shadow: 0 0 8px rgba(255, 255, 0, 0.25);
  }
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

.vote-live {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 12px;
  border-radius: $radius-md;
  border: 1px dashed rgba(var(--c-primary-rgb), 0.35);
  background: rgba(var(--c-primary-rgb), 0.05);
  font-size: $font-size-sm;
  color: $text-secondary;
  align-self: center;
  width: 86%;

  b {
    color: $text-primary;
  }

  &.done {
    border-style: solid;
    border-color: $border-color;
    background: $bg-panel;
  }
}

.vote-icon {
  flex-shrink: 0;
}

.vote-lines {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.vote-main {
  color: $text-secondary;
}

.vote-target {
  color: $night-accent;
}

// 身份可见时（上帝观战/死后视角/翻牌），投票行里的名字按身份牌配色；座位号保持原色。
// .me 放最后：真人玩家恒用黄色高亮，压过身份色
.vote-name {
  &.wolf {
    color: #eba3a0;
  }

  &.god {
    color: #aac8ec;
  }

  &.villager {
    color: #a8c8a8;
  }

  &.me {
    color: #ffff00;
  }
}

.vote-reason {
  color: $text-tertiary;
  font-size: $font-size-xs;
  line-height: 1.6;
  word-break: break-word;
}

.dots {
  display: inline-flex;
  gap: 2px;
  align-items: baseline;

  i {
    width: 3px;
    height: 3px;
    border-radius: 50%;
    background: currentColor;
    animation: dot-blink 1.2s infinite;

    &::before,
    &::after {
      content: '';
    }
  }
}

@keyframes dot-blink {
  0%,
  100% {
    opacity: 0.2;
  }

  50% {
    opacity: 1;
  }
}

.thinking-card {
  border: 1px solid rgba(122, 165, 216, 0.25);
  background: rgba(122, 165, 216, 0.06);
  border-radius: $radius-md;
  padding: 8px 12px;
  font-size: $font-size-sm;
  align-self: center;
  width: 86%;
  transition: border-color $transition-fast, box-shadow $transition-fast;

  &.live {
    border-color: rgba(122, 165, 216, 0.55);
    box-shadow: 0 0 14px rgba(122, 165, 216, 0.12);
  }
}

.thinking-head {
  color: #aac8ec;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.thinking-seat {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 999px;
  background: rgba(122, 165, 216, 0.18);
  border: 1px solid rgba(122, 165, 216, 0.4);
  font-size: 10px;
  font-weight: 700;
  color: #aac8ec;
  flex-shrink: 0;
}

.thinking-role {
  font-size: 10px;
  border-radius: 4px;
  padding: 0 5px;
  line-height: 16px;
  font-weight: 400;

  &.wolf {
    color: #eba3a0;
    background: $wolf-color-light;
  }

  &.god {
    color: #aac8ec;
    background: $god-color-light;
  }

  &.villager {
    color: #a8c8a8;
    background: rgba(143, 191, 143, 0.14);
  }
}

.thinking-status {
  font-weight: 400;
  color: $text-secondary;
  margin-left: 4px;
  font-size: $font-size-xs;
}

.thinking-body {
  margin-top: 6px;
  color: $text-secondary;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: $font-size-xs;

  &.pending {
    color: $text-tertiary;
    font-style: italic;
  }
}

// 流式中固定 3 行高、底端对齐：新字把旧字往上顶出框外，框高全程不变
.thinking-card.live .thinking-body {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  height: 4.8em;
  overflow: hidden;
}

.thinking-live-tail {
  white-space: pre-wrap;
  word-break: break-word;
}

.thinking-fold {
  margin-top: 4px;

  summary {
    cursor: pointer;
    color: #aac8ec;
    font-size: $font-size-xs;
    user-select: none;

    &:hover {
      color: $text-primary;
    }
  }
}

.private-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  background: rgba(var(--c-primary-rgb), 0.06);
  border: 1px dashed rgba(var(--c-primary-rgb), 0.35);
  border-radius: $radius-md;
  padding: 6px 10px;
  font-size: $font-size-sm;
  color: #d8cba4;
}

.private-text {
  flex: 1;
  min-width: 0;
}

.night-card {
  text-align: center;
  background: rgba(16, 13, 26, 0.65);
  border: 1px solid rgba(122, 165, 216, 0.18);
  border-radius: $radius-md;
  padding: 10px;
  color: #c9c2dd;
  font-size: $font-size-base;
}

.hunter-card {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(208, 122, 114, 0.12);
  border: 1px solid rgba(208, 122, 114, 0.4);
  border-radius: $radius-md;
  padding: 8px 12px;
  color: #eba3a0;
}

.vote-card {
  background: $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-md;
  padding: 10px 14px;
  align-self: center;
  width: 86%;
}

.vote-title {
  font-size: $font-size-xs;
  color: $text-tertiary;
  margin-bottom: 4px;
}

.vote-line {
  font-size: $font-size-sm;
  line-height: 1.7;
  color: $text-primary;
  white-space: pre-wrap;
}

.gameover-card {
  align-self: center;
  text-align: center;
  background: linear-gradient(135deg, rgba(var(--c-primary-rgb), 0.14), rgba(122, 165, 216, 0.12));
  border: 1px solid rgba(var(--c-primary-rgb), 0.4);
  border-radius: $radius-lg;
  padding: 14px 20px;
  font-size: $font-size-md;
  width: 90%;
  box-shadow: 0 0 24px rgba(var(--c-primary-rgb), 0.12);
}

.system-line {
  text-align: center;
  color: #d09a9a;
  font-size: $font-size-sm;
}
</style>
