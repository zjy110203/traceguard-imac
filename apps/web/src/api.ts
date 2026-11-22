/** 统一 API 客户端：真实后端调用，不做任何 Mock。 */

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
