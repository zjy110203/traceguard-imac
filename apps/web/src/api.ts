/** 统一 API 客户端：真实后端调用，不做任何 Mock。 */

import type { BrowserHealth, LLMUsageSummary, SystemHealth } from './types'

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let code = 'HTTP_' + res.status
  let message = `请求失败（HTTP ${res.status}）`
  try {
    const body = await res.json()
    const detail = body?.detail ?? body?.error ?? body
    if (typeof detail === 'string') {
      message = detail
    } else if (detail && typeof detail === 'object') {
      code = String(detail.code ?? code)
      message = String(detail.message ?? detail.detail ?? message)
      if (Array.isArray(detail)) message = detail.map((d: any) => d.msg).join('; ')
    }
  } catch {
    /* 非 JSON 错误体 */
  }
  return new ApiError(res.status, code, message)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const sp = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  })
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export function artifactUrl(artifactId: string): string {
  return `${BASE}/artifacts/${artifactId}/download`
}

export function evidenceDownloadUrl(evidenceId: string): string {
  return `${BASE}/evidence/${evidenceId}/download`
}

export function fmtTime(ts: number | string | null | undefined): string {
  if (!ts) return '—'
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  if (Number.isNaN(d.getTime())) return String(ts)
  return d.toLocaleString('zh-CN', { hour12: false })
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms === undefined || ms === null) return '—'
  if (ms < 1000) return `${ms} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}

export function fmtBytes(n: number | undefined): string {
  if (!n) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** 语义化端点：避免在视图中散落字符串路径。 */
export const endpoints = {
  systemHealth: () => api.get<SystemHealth>('/system/health'),
  browserHealth: (probe = false) => api.get<BrowserHealth>(`/system/browser?probe=${probe}`),
  llmSummary: () => api.get<LLMUsageSummary>('/system/llm/summary'),
  queueStats: () => api.get<{ by_status: Record<string, number>; total: number; worker: Record<string, unknown> }>('/queue/stats'),
}

export function fmtNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString('zh-CN')
}

export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

export function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return '未知'
  return `$${n < 0.01 ? n.toFixed(5) : n.toFixed(3)}`
}

export function fmtPercent(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—'
  return `${(n * 100).toFixed(digits)}%`
}
