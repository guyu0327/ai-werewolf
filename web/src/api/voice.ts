import { API_BASE, API_TOKEN, get } from './http'

export interface VoiceUsage {
  used: number
  limit: number
}

export function voiceWsUrl(): string {
  const base = API_BASE || `${location.protocol}//${location.host}`
  const ws = base.replace(/^http/, 'ws')
  return API_TOKEN ? `${ws}/ws/voice?token=${encodeURIComponent(API_TOKEN)}` : `${ws}/ws/voice`
}

export function fetchVoiceUsage(): Promise<VoiceUsage> {
  return get('/api/voice/usage')
}
