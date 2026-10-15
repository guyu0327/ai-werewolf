/**
 * 后端 API 地址与本地访问令牌。
 * 浏览器 / Vite 开发环境为空（同源，走代理）；
 * Electron 桌面壳通过 preload 注入 window.werewolf（跨域直连本地后端，需携带令牌）。
 */
const injected: { apiBase?: string; token?: string } = (window as any).werewolf ?? {}

export const API_BASE: string = injected.apiBase ?? ''
export const API_TOKEN: string = injected.token ?? ''

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

export function apiHeaders(extra?: Record<string, string>): Record<string, string> {
  return API_TOKEN ? { 'X-AT-Token': API_TOKEN, ...extra } : { ...extra }
}

export async function request<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(apiUrl(path), {
    ...options,
    headers: apiHeaders({
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers as Record<string, string> | undefined),
    }),
  })
  if (!res.ok) {
    let message = `请求失败 (${res.status})`
    try {
      const data = await res.json()
      if (typeof data?.message === 'string') message = data.message
      if (typeof data?.detail === 'string') message = data.detail
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(message)
  }
  return res.json()
}

export const get = <T = any>(path: string) => request<T>(path)

export const post = <T = any>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const put = <T = any>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })

export const del = <T = any>(path: string) => request<T>(path, { method: 'DELETE' })
