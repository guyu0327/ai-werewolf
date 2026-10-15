import { reactive, watch } from 'vue'
import { apiUrl } from '@/api/http'

export type BgmTrack = 'lobby' | 'day' | 'night'

const KEY_ENABLED = 'ww:bgm-enabled'
const KEY_VOLUME = 'ww:bgm-volume'
const DEFAULT_VOLUME = 0.18

function clampVolume(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : DEFAULT_VOLUME
}

export const bgm = reactive({
  enabled: localStorage.getItem(KEY_ENABLED) !== '0',
  volume: clampVolume(localStorage.getItem(KEY_VOLUME) ?? DEFAULT_VOLUME),
  playing: false,
  track: null as BgmTrack | null,
})

const players: Record<BgmTrack, HTMLAudioElement> = {
  lobby: new Audio(apiUrl('/audio/bgm-lobby.mp3')),
  day: new Audio(apiUrl('/audio/bgm-day.mp3')),
  night: new Audio(apiUrl('/audio/bgm-night.mp3')),
}

for (const a of Object.values(players)) {
  a.loop = true
  a.preload = 'auto'
}

// 切曲时记住进度，回到同一曲从中断处继续（loop 播完自然回卷）
const positions: Record<BgmTrack, number> = { lobby: 0, day: 0, night: 0 }

let ducked = false

function syncVolume() {
  for (const a of Object.values(players)) a.volume = ducked ? 0 : bgm.volume
}

function stopAll() {
  for (const [track, a] of Object.entries(players) as [BgmTrack, HTMLAudioElement][]) {
    positions[track] = a.currentTime
    a.pause()
  }
  bgm.playing = false
}

export function setBgmTrack(track: BgmTrack | null) {
  bgm.track = track
  if (!track || !bgm.enabled) {
    stopAll()
    return
  }
  const other = players[track === 'day' ? 'night' : 'day']
  positions[track === 'day' ? 'night' : 'day'] = other.currentTime
  other.pause()
  syncVolume()
  const next = players[track]
  next.currentTime = positions[track] || 0
  void next
    .play()
    .then(() => {
      bgm.playing = true
    })
    .catch(() => {
      bgm.playing = false
    })
}

// 麦克风录音期间压到 0，避免音乐被采进语音识别
export function setBgmDuck(v: boolean) {
  ducked = v
  syncVolume()
}

watch(
  () => bgm.enabled,
  (v) => {
    localStorage.setItem(KEY_ENABLED, v ? '1' : '0')
    setBgmTrack(bgm.track)
  },
)

watch(
  () => bgm.volume,
  (v) => {
    localStorage.setItem(KEY_VOLUME, String(clampVolume(v)))
    syncVolume()
  },
)
