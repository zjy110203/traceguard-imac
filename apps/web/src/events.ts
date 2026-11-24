/** 实时事件订阅：SSE 优先，失败自动降级为轮询。
 *
 * 设计边界（与后端契约一致）：
 * - SSE 只是"实时提示"通道，业务状态仍以 API 重新拉取的结果为准；
 * - 事件投递为 at-least-once，前端按 event_id 去重，避免重复触发界面动作；
 * - 断线后浏览器会自动带 Last-Event-ID 重连，本模块在其失败时切换到轮询。
 */

export interface StreamEvent {
  event_id: string
  event_type: string
  sequence: number
  producer_instance_id: string
  workspace_id: string
  context_refs: Record<string, string>
  payload: Record<string, unknown>
  recorded_at: string
}

export type StreamMode = 'connecting' | 'live' | 'polling' | 'closed'

const EVENT_TYPES = [
  'run.started',
  'run.completed',
  'run.failed',
  'step.completed',
  'assertion.evaluated',
]

export interface SubscribeOptions {
  runId?: string
  workspaceId?: string
  onEvent: (event: StreamEvent) => void
  onMode?: (mode: StreamMode) => void
  pollIntervalMs?: number
}

export function subscribeEvents(opts: SubscribeOptions): () => void {
  const pollIntervalMs = opts.pollIntervalMs ?? 2500
  const seen = new Set<string>()
  let mode: StreamMode = 'connecting'
  let closed = false
  let es: EventSource | null = null
  let pollTimer: number | undefined
  let errorCount = 0

  const setMode = (next: StreamMode) => {
    if (mode === next) return
    mode = next
    opts.onMode?.(next)
  }

  const accept = (raw: unknown) => {
    const ev = raw as StreamEvent
    if (!ev || !ev.event_id || seen.has(ev.event_id)) return
    seen.add(ev.event_id)
    opts.onEvent(ev)
  }

  const params = new URLSearchParams()
  if (opts.runId) params.set('run_id', opts.runId)
  if (opts.workspaceId) params.set('workspace_id', opts.workspaceId)
  params.set('heartbeat_seconds', '15')

  const startPolling = () => {
    if (closed || pollTimer !== undefined) return
    setMode('polling')
    const tick = async () => {
      try {
        const sp = new URLSearchParams({ limit: '50' })
        if (opts.runId) sp.set('run_id', opts.runId)
        if (opts.workspaceId) sp.set('workspace_id', opts.workspaceId)
        const res = await fetch(`/api/v1/events?${sp.toString()}`)
        if (!res.ok) return
        const rows = (await res.json()) as StreamEvent[]
        // 后端按时间倒序返回；按时间正序处理，保持因果可读
        rows.sort((a, b) => a.recorded_at.localeCompare(b.recorded_at))
        rows.forEach(accept)
      } catch {
        /* 轮询失败保持现状，下个周期重试 */
      }
    }
    void tick()
    pollTimer = window.setInterval(tick, pollIntervalMs)
  }

  const stopPolling = () => {
    if (pollTimer !== undefined) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
  }

  const startStream = () => {
    if (closed) return
    try {
      es = new EventSource(`/events/stream?${params.toString()}`)
    } catch {
      startPolling()
      return
    }
    es.onopen = () => {
      errorCount = 0
      stopPolling()
      setMode('live')
    }
    const handler = (e: MessageEvent) => {
      try {
        accept(JSON.parse(e.data))
      } catch {
        /* 忽略无法解析的帧 */
      }
    }
    EVENT_TYPES.forEach((t) => es?.addEventListener(t, handler as EventListener))
    es.addEventListener('error', () => {
      if (closed) return
      errorCount += 1
      // 连续失败说明 SSE 通道不可用（代理/网络），降级为轮询但保留重连
      if (errorCount >= 2) {
        // startPolling 自身幂等：已在轮询时不重建计时器，避免连续 error 把回调永久推迟。
        startPolling()
      } else {
        setMode('connecting')
      }
    })
  }

  startStream()

  return () => {
    closed = true
    stopPolling()
    es?.close()
    es = null
    setMode('closed')
  }
}
