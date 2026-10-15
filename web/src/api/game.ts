import { apiUrl, del, get, post } from './http'
import { apiHeaders } from './http'

export interface PublicPlayer {
  seat: number
  name: string
  avatar: string
  is_human: boolean
  alive: boolean
  role: string | null
  role_label: string | null
  role_revealed: boolean
  vote_lost: boolean
}

export interface BoardView {
  id: string
  name: string
  win_condition: 'city' | 'side'
  roles: string[]
  max_days: number
}

export interface GameState {
  id: string
  created_at: string
  board: BoardView
  day: number
  phase: string
  phase_label: string
  winner: 'wolf' | 'good' | 'draw' | 'error' | null
  end_reason: string
  players: PublicPlayer[]
  human_seat: number | null
  mode: 'normal' | 'god'
  sheriff_enabled: boolean
  sheriff_seat: number | null
  badge_swallow: 'none' | 'single' | 'double'
}

export interface PendingInfo {
  kind: string
  allowed: number[]
}

export interface GameMeta {
  id: string
  created_at: string
  board_name: string
  day: number
  phase: string
  mode: 'normal' | 'god'
  winner: string | null
  end_reason: string
  players: { seat: number; name: string; avatar: string; is_human: boolean; alive: boolean }[]
}

export interface BoardInfo {
  id: string
  name: string
  win_condition: 'city' | 'side'
  roles: string[]
  max_days: number
}

export const fetchBoards = () => get<BoardInfo[]>('/api/games/boards')

export const createGame = (body: {
  board_id?: string
  board?: { name: string; roles: string[]; win_condition: string; max_days: number }
  persona_ids: string[]
  human_name: string
  human_avatar: string
  mode?: 'normal' | 'god'
  human_role?: string
  sheriff?: boolean
  badge_swallow?: 'none' | 'single' | 'double'
}) => post<GameState>('/api/games', body)

export const listGames = () => get<GameMeta[]>('/api/games')

export const fetchGame = (id: string) =>
  get<{ state: GameState; active: boolean; pending: PendingInfo | null; paused: { message: string } | null }>(`/api/games/${id}`)

export const startGame = (id: string) => post(`/api/games/${id}/start`)

export const resumeGame = (id: string) => post(`/api/games/${id}/resume`)

export const abortGame = (id: string) => post(`/api/games/${id}/abort`)

export const llmResume = (id: string, action: 'retry' | 'abort') => post(`/api/games/${id}/llm-resume`, { action })

export const explodeGame = (id: string) => post(`/api/games/${id}/explode`)

export const submitAction = (id: string, payload: {
  kind: string
  text?: string
  target?: number
  use?: boolean
  shoot?: boolean
  run?: boolean
  destroy?: boolean
  direction?: string
}) => post(`/api/games/${id}/action`, payload)

export const deleteGame = (id: string) => del(`/api/games/${id}`)

export const fetchReview = (id: string) => get<Record<string, any>>(`/api/games/${id}/review`)

export const generateSummary = (id: string, presetId: string) =>
  post<{ started: boolean }>(`/api/games/${id}/summary`, { preset_id: presetId })

export const fetchSummaryStatus = (id: string) =>
  get<{ status: 'running' | 'idle'; error: string; summary: string; summary_model: string }>(`/api/games/${id}/summary/status`)

export function streamUrl(id: string, view: string): string {
  return apiUrl(`/api/games/${id}/stream?view=${view}`)
}

export { apiHeaders }
