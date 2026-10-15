import { ref } from 'vue'
import { voiceWsUrl } from '@/api/voice'

export type VoiceState = 'idle' | 'connecting' | 'recording'

const FRAME_BYTES = 1280 // 40ms @ 16kHz L16 单声道
const SAMPLE_RATE = 16000

function floatToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length)
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return out
}

function resample(data: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return data
  const ratio = from / to
  const len = Math.floor(data.length / ratio)
  const out = new Float32Array(len)
  for (let i = 0; i < len; i++) {
    const pos = i * ratio
    const idx = Math.floor(pos)
    const frac = pos - idx
    const a = data[idx] ?? 0
    const b = data[idx + 1] ?? a
    out[i] = a + (b - a) * frac
  }
  return out
}

/**
 * 麦克风 → 后端 /ws/voice 中继 → 讯飞听写。
 * start() 后识别文本经 onPartial 增量上抛（会话内累计全文）；stop() 等最终稿 onFinal。
 * 识别文本只填入输入框，不自动发送。
 */
export function useVoiceInput(onPartial: (text: string) => void, onFinal: (text: string) => void) {
  const state = ref<VoiceState>('idle')
  const supported = typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia

  let ws: WebSocket | null = null
  let stream: MediaStream | null = null
  let ctx: AudioContext | null = null
  let source: MediaStreamAudioSourceNode | null = null
  let processor: ScriptProcessorNode | null = null
  let drainTimer: number | null = null
  let accum = new Uint8Array(0)
  let frames: ArrayBuffer[] = []
  let stopping = false
  let lastText = ''

  function cleanup() {
    if (drainTimer != null) {
      window.clearInterval(drainTimer)
      drainTimer = null
    }
    if (processor) {
      processor.onaudioprocess = null
      processor.disconnect()
      processor = null
    }
    source?.disconnect()
    source = null
    if (ctx) {
      ctx.close().catch(() => {})
      ctx = null
    }
    stream?.getTracks().forEach((t) => t.stop())
    stream = null
    accum = new Uint8Array(0)
    frames = []
  }

  function closeWs() {
    const sock = ws
    ws = null
    if (sock && sock.readyState <= WebSocket.OPEN) {
      sock.onclose = null
      sock.onerror = null
      sock.onmessage = null
      sock.close()
    }
  }

  async function start() {
    if (state.value !== 'idle') return
    if (!supported) throw new Error('当前环境不支持麦克风输入')

    state.value = 'connecting'
    lastText = ''
    stopping = false

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
    } catch {
      state.value = 'idle'
      throw new Error('无法访问麦克风：请检查系统与应用的麦克风权限')
    }

    try {
      ws = new WebSocket(voiceWsUrl())
      ws.binaryType = 'arraybuffer'
    } catch {
      cleanup()
      state.value = 'idle'
      throw new Error('无法建立语音识别连接')
    }

    const opened = new Promise<void>((resolve, reject) => {
      if (!ws) return reject(new Error('连接已关闭'))
      ws.onopen = () => resolve()
      ws.onerror = () => reject(new Error('语音识别连接失败'))
    })

    try {
      await opened
    } catch (e) {
      cleanup()
      closeWs()
      state.value = 'idle'
      throw e
    }

    ctx = new AudioContext({ sampleRate: SAMPLE_RATE })
    source = ctx.createMediaStreamSource(stream)
    processor = ctx.createScriptProcessor(1024, 1, 1)
    processor.onaudioprocess = (e) => {
      if (!ctx) return
      const f32 = resample(e.inputBuffer.getChannelData(0), ctx.sampleRate, SAMPLE_RATE)
      const bytes = new Uint8Array(floatToInt16(f32).buffer)
      const merged = new Uint8Array(accum.length + bytes.length)
      merged.set(accum)
      merged.set(bytes, accum.length)
      let offset = 0
      while (merged.length - offset >= FRAME_BYTES) {
        frames.push(merged.slice(offset, offset + FRAME_BYTES).buffer)
        offset += FRAME_BYTES
      }
      accum = merged.slice(offset)
    }
    source.connect(processor)
    processor.connect(ctx.destination) // ScriptProcessor 需接到输出才会驱动

    ws.onmessage = (ev) => {
      if (typeof ev.data !== 'string') return
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'partial' && typeof msg.text === 'string') {
          lastText = msg.text
          onPartial(lastText)
        } else if (msg.type === 'final') {
          lastText = typeof msg.text === 'string' ? msg.text : lastText
          const text = lastText
          cleanup()
          closeWs()
          state.value = 'idle'
          onFinal(text)
        } else if (msg.type === 'error') {
          cleanup()
          closeWs()
          state.value = 'idle'
          onPartial(lastText) // 已识别的部分保留在输入框
          if (!stopping) console.warn('[voice]', msg.message)
        }
      } catch {
        /* 非 JSON 消息忽略 */
      }
    }

    ws.onclose = () => {
      const wasActive = state.value !== 'idle'
      cleanup()
      ws = null
      state.value = 'idle'
      if (wasActive) onFinal(lastText) // 连接断开按已收文本定稿（含 60s 上限）
    }

    drainTimer = window.setInterval(() => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return
      while (frames.length) {
        const frame = frames.shift()!
        ws.send(frame)
      }
    }, 40)

    state.value = 'recording'
  }

  function stop() {
    if (state.value !== 'recording') return
    stopping = true
    if (drainTimer != null) {
      window.clearInterval(drainTimer)
      drainTimer = null
    }
    if (processor) {
      processor.onaudioprocess = null
      processor.disconnect()
      processor = null
    }
    source?.disconnect()
    source = null
    if (ctx) {
      ctx.close().catch(() => {})
      ctx = null
    }
    stream?.getTracks().forEach((t) => t.stop())
    stream = null

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'stop' }))
      // 等 final / close（onmessage / onclose 收口），期间保持 recording 态显示停止中
    } else {
      cleanup()
      closeWs()
      state.value = 'idle'
      onFinal(lastText)
    }
  }

  return { state, supported, start, stop }
}
