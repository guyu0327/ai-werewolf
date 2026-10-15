import { get, put, post } from './http'

export interface ModelPreset {
  id: string
  name: string
  base_url: string
  api_key: string
  model: string
  speech_temperature: number
  decision_temperature: number
  /** 思考力度（reasoning_effort）：发言建议 medium，决策建议 low */
  speech_reasoning_effort: string
  decision_reasoning_effort: string
}

export interface AiPersona {
  id: string
  name: string
  avatar: string
  persona: string
  preset_id: string
}

export interface BoardConfig {
  id: string
  name: string
  win_condition: 'city' | 'side'
  roles: string[]
  max_days: number
}

export interface AppSettings {
  presets: ModelPreset[]
  personas: AiPersona[]
  boards: BoardConfig[]
}

export const fetchSettings = () => get<AppSettings>('/api/settings')

export const saveSettings = (settings: AppSettings) => put<AppSettings>('/api/settings', settings)

export const testPreset = (body: { base_url: string; api_key: string; model: string }) =>
  post<{ ok: boolean; message: string }>('/api/settings/test-preset', body)

export const generatePersona = (body: { preset_id: string; hint?: string }) =>
  post<{ name: string; persona: string }>('/api/settings/generate-persona', body)

export interface PersonaJobStatus {
  status: 'running' | 'done' | 'error' | 'idle'
  total: number
  added: AiPersona[]
  error: string
}

export const startGeneratePersonas = (body: { preset_id: string; count: number }) =>
  post<{ started: boolean; total: number }>('/api/settings/generate-personas', body)

export const fetchPersonaJob = () => get<PersonaJobStatus>('/api/settings/generate-personas/status')
