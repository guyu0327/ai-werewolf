import { defineStore } from 'pinia'
import { apiHeaders } from '@/api/http'
import { explodeGame, fetchGame, resumeGame, startGame, streamUrl, submitAction, type GameState, type PendingInfo } from '@/api/game'
import { sseStream } from '@/api/sse'
import { feedId, mapEvent, PHASE_LABELS, type FeedItem } from '@/utils/feed'

interface SnapshotData {
  state: GameState
  events: Record<string, any>[]
  pending: PendingInfo | null
  paused: { message: string } | null
  active: boolean
}

// 实时流只保留最近 N 条：DOM 与重渲染成本有界，完整历史走复盘弹窗（后端全量事件）
const FEED_CAP = 400

// 流式 delta 合并：上帝观战投票阶段 12 路并行思考逐 token 推送，逐条渲染会打满渲染进程。
// delta 先入缓冲，定时批量落 store（渲染频率钉死在 ~16/s）；任何非 delta 事件先同步冲刷保证顺序
const DELTA_FLUSH_MS = 60
let speechDeltaBuf = ''
const thinkDeltaBuf = new Map<number, string>()
let deltaTimer: number | null = null

export const useGameStore = defineStore('game', {
  state: () => ({
    state: null as GameState | null,
    feed: [] as FeedItem[],
    pending: null as PendingInfo | null,
    pendingNote: '',
    paused: null as { message: string } | null,
    active: false,
    connected: false,
    godView: false,
    controller: null as AbortController | null,
    seenSeqs: new Set<number>(),
    streamingKey: '',
  }),

  getters: {
    me(state): { seat: number; name: string; avatar: string; alive: boolean; role: string | null } | null {
      if (!state.state) return null
      const p = state.state.players.find((x) => x.seat === state.state!.human_seat)
      return p ? { seat: p.seat, name: p.name, avatar: p.avatar, alive: p.alive, role: p.role } : null
    },
    phaseLabel(state): string {
      if (!state.state) return ''
      return PHASE_LABELS[state.state.phase] ?? state.state.phase
    },
  },

  actions: {
    trimFeed() {
      if (this.feed.length > FEED_CAP) this.feed.splice(0, this.feed.length - FEED_CAP)
    },

    dropDeltaBuf() {
      if (deltaTimer != null) {
        clearTimeout(deltaTimer)
        deltaTimer = null
      }
      speechDeltaBuf = ''
      thinkDeltaBuf.clear()
    },

    scheduleDeltaFlush() {
      if (deltaTimer != null) return
      deltaTimer = window.setTimeout(() => {
        deltaTimer = null
        this.flushDeltas()
      }, DELTA_FLUSH_MS)
    },

    flushDeltas() {
      if (deltaTimer != null) {
        clearTimeout(deltaTimer)
        deltaTimer = null
      }
      if (speechDeltaBuf) {
        const item = this.feed.find((f) => f.id === this.streamingKey)
        if (item) item.text += speechDeltaBuf
        speechDeltaBuf = ''
      }
      if (thinkDeltaBuf.size) {
        for (const [seat, text] of thinkDeltaBuf) {
          const t = this.feed.find((f) => f.kind === 'thinking' && f.seat === seat && f.streaming)
          if (t) t.text += text
        }
        thinkDeltaBuf.clear()
      }
    },

    async connect(gid: string) {
      this.disconnect()
      const controller = new AbortController()
      this.controller = controller
      const loop = async () => {
        while (!controller.signal.aborted) {
          try {
            for await (const block of sseStream(streamUrl(gid, this.godView ? 'full' : 'auto'), apiHeaders(), controller.signal)) {
              if (block.event === 'snapshot') this.applySnapshot(JSON.parse(block.data))
              else this.applyEvent(block.event, JSON.parse(block.data))
            }
          } catch {
            /* 连接断开，稍后重连 */
          }
          if (controller.signal.aborted) break
          this.connected = false
          await new Promise((r) => setTimeout(r, 1500))
        }
      }
      loop()
    },

    disconnect() {
      this.controller?.abort()
      this.controller = null
      this.connected = false
      this.pending = null
      this.paused = null
      this.streamingKey = ''
      this.dropDeltaBuf()
    },

    reset() {
      this.disconnect()
      this.state = null
      this.feed = []
      this.seenSeqs = new Set()
      this.godView = false
      this.active = false
    },

    async loadAndConnect(gid: string) {
      this.reset()
      try {
        const res = await fetchGame(gid)
        this.state = res.state
        this.active = res.active
        this.pending = res.pending
        this.paused = res.paused ?? null
      } catch {
        /* 流里会再推快照 */
      }
      this.connect(gid)
    },

    applySnapshot(data: SnapshotData) {
      this.state = data.state
      this.active = data.active
      this.pending = data.pending
      this.paused = data.paused ?? null
      this.connected = true
      this.feed = []
      this.seenSeqs = new Set()
      for (const e of data.events) {
        const item = mapEvent({ ...e })
        if (item) {
          if (e.seq != null) this.seenSeqs.add(e.seq)
          this.feed.push(item)
        }
      }
      this.trimFeed()
    },

    applyEvent(event: string, data: Record<string, any>) {
      // 非 delta 事件先同步冲刷缓冲的文本增量，保证「delta → 收口/替换」的顺序语义
      if (event !== 'speech_delta' && event !== 'thinking_delta') this.flushDeltas()
      switch (event) {
        case 'phase_change': {
          if (this.state) {
            this.state.day = data.day
            this.state.phase = data.phase
            this.state.phase_label = PHASE_LABELS[data.phase] ?? data.phase
          }
          // 阶段切换意味着旧输入已失效（如发言被狼人自爆打断），清掉残留输入框和思考占位
          this.pending = null
          this.pendingNote = ''
          this.feed = this.feed.filter((f) => !(f.kind === 'thinking' && f.streaming))
          return
        }
        case 'speech_start': {
          this.streamingKey = feedId()
          this.feed.push({
            id: this.streamingKey,
            kind: 'speech',
            seat: data.seat,
            name: data.name,
            avatar: data.avatar,
            text: '',
            streaming: true,
          })
          this.trimFeed()
          return
        }
        case 'speech_delta': {
          speechDeltaBuf += data.text
          this.scheduleDeltaFlush()
          return
        }
        case 'await_input': {
          this.pending = { kind: data.kind, allowed: data.allowed ?? [] }
          this.pendingNote = data.note ?? ''
          return
        }
        case 'vote_pending': {
          const waiting = this.feed.find((f) => f.kind === 'voteLive' && f.seat === data.seat && f.streaming)
          if (!waiting) {
            this.feed.push({ id: feedId(), kind: 'voteLive', seat: data.seat, name: data.name, text: '', streaming: true })
            this.trimFeed()
          }
          return
        }
        case 'thinking_start': {
          this.feed.push({ id: feedId(), kind: 'thinking', seat: data.seat, name: data.name, text: '', streaming: true })
          this.trimFeed()
          return
        }
        case 'thinking_delta': {
          thinkDeltaBuf.set(data.seat, (thinkDeltaBuf.get(data.seat) ?? '') + data.text)
          this.scheduleDeltaFlush()
          return
        }
        case 'thinking_end': {
          const done = this.feed.findIndex((f) => f.kind === 'thinking' && f.seat === data.seat && f.streaming)
          if (done >= 0) this.feed.splice(done, 1)
          return
        }
        case 'input_accepted': {
          this.pending = null
          this.pendingNote = ''
          return
        }
        case 'llm_paused': {
          // 持久事件：置位暂停弹窗后继续走通用入流（mapEvent 出系统行，seq 去重）
          this.paused = { message: data.message ?? '' }
          break
        }
        case 'llm_resumed': {
          this.paused = null
          return
        }
        case 'error': {
          this.feed.push({ id: feedId(), kind: 'system', text: data.message ?? '发生错误' })
          this.trimFeed()
          return
        }
        default:
          break
      }
      // 持久化事件（带 seq）：按 seq 去重（快照与实时流可能重叠）
      if (data.seq != null) {
        if (this.seenSeqs.has(data.seq)) {
          if (event === 'speech' || event === 'last_words' || event === 'sheriff_speech') this.finalizeStreaming(data, event)
          return
        }
        this.seenSeqs.add(data.seq)
      }
      // 持久化亮票/思考到达时，替换同座位的流式占位条目
      if (event === 'vote_cast') {
        const i = this.feed.findIndex((f) => f.kind === 'voteLive' && f.seat === data.seat && f.streaming)
        if (i >= 0) this.feed.splice(i, 1)
      }
      if (event === 'thinking') {
        const j = this.feed.findIndex((f) => f.kind === 'thinking' && f.seat === data.seat && f.streaming)
        if (j >= 0) this.feed.splice(j, 1)
      }
      if (event === 'speech' || event === 'last_words' || event === 'sheriff_speech') {
        if (this.finalizeStreaming(data, event)) return
      }
      // 警徽归属即时同步（选举结果 / 移交 / 撕毁），否则要等重连快照才显示
      if (event === 'sheriff_result') {
        if (this.state) this.state.sheriff_seat = data.destroyed ? null : data.sheriff
      } else if (event === 'badge_pass') {
        if (this.state) this.state.sheriff_seat = data.destroyed || data.to == null ? null : data.to
      }
      // 局内伤亡同步到座位（快照间隔期间也能即时置灰/翻牌）
      this.patchPlayersFromEvent(event, data)
      const item = mapEvent({ ...data, type: event })
      if (event === 'speech' && item) {
        const p = this.state?.players.find((x) => x.seat === data.actor)
        if (p) {
          item.name = p.name
          item.avatar = p.avatar
        }
      }
      if (item) {
        this.feed.push(item)
        this.trimFeed()
      }
      if (event === 'game_over' && this.state) {
        this.state.phase = 'game_over'
        this.state.winner = data.winner
        this.state.end_reason = data.reason
        this.paused = null
      }
    },

    /** 把正在流式的发言条目替换为最终内容；返回是否处理了 */
    finalizeStreaming(data: Record<string, any>, event?: string): boolean {
      const item = this.feed.find((f) => f.streaming && f.seat === data.actor)
      if (!item) return false
      item.text = data.text ?? ''
      item.streaming = false
      item.seq = data.seq
      if (event === 'last_words') item.tag = '遗言'
      else if (event === 'sheriff_speech') item.tag = data.tag ?? '竞选宣言'
      this.streamingKey = ''
      return true
    },

    /** 从伤亡类事件就地更新玩家存活/翻牌状态，座位表即时置灰 */
    patchPlayersFromEvent(event: string, data: Record<string, any>) {
      if (!this.state) return
      const bySeat = (seat: number) => this.state!.players.find((x) => x.seat === seat)
      if (event === 'night_settle') {
        for (const s of (data.deaths ?? []) as number[]) {
          const p = bySeat(s)
          if (p) p.alive = false
        }
      } else if (event === 'hunter_shot') {
        const p = bySeat(data.target)
        if (p) p.alive = false
      } else if (event === 'wolf_explode') {
        const p = bySeat(data.seat)
        if (p) {
          p.alive = false
          p.role = 'werewolf'
          p.role_label = '狼人'
          p.role_revealed = true
        }
      } else if (event === 'vote_result' && data.exiled != null) {
        const p = bySeat(data.exiled)
        if (!p) return
        if (data.idiot_survived) {
          if (data.role) p.role_label = data.role
          p.vote_lost = true
        } else {
          p.alive = false
        }
      }
    },

    async start() {
      if (!this.state) return
      await startGame(this.state.id)
      this.active = true
    },

    async resume() {
      if (!this.state) return
      await resumeGame(this.state.id)
      this.active = true
    },

    async submit(payload: { kind: string; text?: string; target?: number; use?: boolean; shoot?: boolean; run?: boolean; destroy?: boolean; direction?: string }) {
      if (!this.state) return
      await submitAction(this.state.id, payload)
    },

    async explode() {
      if (!this.state) return
      await explodeGame(this.state.id)
    },

    toggleGodView() {
      this.godView = !this.godView
      if (this.state) {
        // 切换视角：重连以获取对应视图的快照与事件
        this.connect(this.state.id)
      }
    },
  },
})
