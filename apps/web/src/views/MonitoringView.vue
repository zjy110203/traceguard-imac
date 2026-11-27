<script setup lang="ts">
/** RUN-018 WP8：产品运行监控与告警（Monitoring）。
 *
 * 数据全部来自真实后端 /api/v1/monitoring/*，无 Mock；
 * 支持手动刷新与自动刷新；告警支持 OPEN → ACKNOWLEDGED → RESOLVED 状态流转；
 * 阈值为 provisional（非 SLA），页面显式标注。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, endpoints, fmtBytes, fmtNumber, fmtPercent, fmtTime, type AlertRecord } from '../api'
import { authState, can } from '../auth'
import { currentWorkspaceId } from '../workspace'
import AsyncBlock from '../components/AsyncBlock.vue'
import MetricCard from '../components/MetricCard.vue'
import ModalShell from '../components/ModalShell.vue'
import PageHeader from '../components/PageHeader.vue'
import Sparkline from '../components/Sparkline.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'

interface WorkerRow {
  worker_id: string
  mode: string
  status: string
  online: boolean
  hostname: string
  pid: number
  last_heartbeat_at: number
  heartbeat_age_s: number
  current_run_id: string | null
  lease_remaining_s: number
  claims_total: number
  takeovers_total: number
  failures_total: number
}
interface OverdueRun {
  run_id: string
  task_id: string
  execution_status: string
  waited_s: number
  worker_id: string | null
}
interface RecentError {
  method: string
  path: string
  status: number
  duration_ms: number
  at: number
}
interface RecentFailure {
  run_id: string
  execution_status: string
  test_verdict: string
  error: string
  ended_at: number | null
}
interface Snapshot {
  generated_at: number
  thresholds: Record<string, unknown>
  thresholds_status: { status: string; disclaimer: string }
  api: {
    health: string
    requests_total: number
    error_count: number
    error_rate: number
    response_time_ms: { p50: number | null; p95: number | null; avg: number | null }
    recent_errors: RecentError[]
    database_ok: boolean
    since_iso: string
  }
  worker: {
    workers: WorkerRow[]
    total: number
    online: number
    offline: number
    thresholds: Record<string, unknown>
  }
  queue: {
    by_status: Record<string, number>
    queued: number
    running: number
    oldest_queued_wait_s: number
    timed_out_total: number
    overdue_running: OverdueRun[]
    overdue_threshold_s: number
    trend: { samples: { at: number; queued: number; running: number }[]; backlog_direction: string }
    thresholds: Record<string, unknown>
    scope?: { mode: string; global: boolean }
  }
  run: {
    by_verdict: Record<string, number>
    by_status: Record<string, number>
    last_24h: {
      finished: number
      by_verdict: Record<string, number>
      evaluated: number
      success_rate: number | null
      duration_ms: { p50: number | null; p95: number | null }
      failure_types: Record<string, number>
    }
    recent_failures: RecentFailure[]
  }
  artifact: {
    registered: number
    total_bytes: number
    missing_files: { artifact_id: string }[]
    hash_mismatch: { artifact_id: string; expected: string; actual: string }[]
    orphan_files: unknown[]
    orphan_rows: unknown[]
    gc: { last_run_at: string | null; total_records: number; errors: number; quarantine_dirs: number }
    hash_check: { checked: number; limit: number; truncated: boolean }
    thresholds: Record<string, unknown>
  }
  database: {
    ok: boolean
    error: string
    dialect: string
    migration: { latest_version?: number; applied?: number; error?: string }
    pool: Record<string, unknown>
    failures: { read_failures: number; write_failures: number; last_error: string }
  }
  alerts: {
    summary: { open: number; acknowledged: number; created_this_eval: string[] }
  }
  scope: { mode: string; global: boolean; authorized_workspaces: string[] | null }
}

const snap = ref<Snapshot | null>(null)
const alerts = ref<AlertRecord[]>([])
const history = ref<AlertRecord[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const autoRefresh = ref(true)
const showHistory = ref(false)
const resolving = ref<AlertRecord | null>(null)
const resolveReason = ref('')
let timer: number | null = null

// 告警确认/解决在服务端映射到 system.configure；认证关闭的开发环境保持可操作。
const canManageAlerts = computed(
  () => authState.status?.auth_enabled === false || can('system.configure'),
)

/** RUN-019-R2：默认统计当前 Workspace；全局汇总必须显式勾选并在页面标注范围。 */
const globalScope = ref(false)
const activeAlerts = computed(() =>
  alerts.value.filter((a) => a.status === 'OPEN' || a.status === 'ACKNOWLEDGED'),
)
const scopeLabel = computed(() => {
  const mode = snap.value?.scope?.mode
  if (mode === 'workspace') return `当前 Workspace（${snap.value?.scope?.workspace_id}）`
  if (mode === 'global') return '全局汇总（全部 Workspace）'
  if (mode === 'authorized_workspaces') return '全部已授权 Workspace 聚合'
  return '未声明（服务端未返回 scope）'
})
const trendSeries = computed(() => {
  const samples = snap.value?.queue.trend.samples ?? []
  return [
    { label: 'QUEUED', color: '#d97706', values: samples.map((s) => s.queued) },
    { label: 'RUNNING', color: '#2563eb', values: samples.map((s) => s.running) },
  ]
})

async function load(): Promise<boolean> {
  loading.value = !snap.value
  try {
    const [m, a] = await Promise.all([
      endpoints.monitoringMetrics(
        globalScope.value ? undefined : currentWorkspaceId.value || undefined,
        50,
      ),
      api.get<{ alerts: AlertRecord[] }>('/monitoring/alerts?limit=200'),
    ])
    snap.value = m as unknown as Snapshot
    const rows = a.alerts ?? []
    alerts.value = rows.filter((r) => r.status !== 'RESOLVED')
    history.value = rows.filter((r) => r.status === 'RESOLVED')
    error.value = ''
    return true
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    return false
  } finally {
    loading.value = false
  }
}

async function acknowledge(a: AlertRecord) {
  busy.value = `ack-${a.alert_id}`
  try {
    await endpoints.alertAcknowledge(a.alert_id)
    notify(`已确认告警：${a.title}`)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

function openResolve(a: AlertRecord) {
  resolving.value = a
  resolveReason.value = ''
}

async function confirmResolve() {
  const a = resolving.value
  if (!a) return
  if (!resolveReason.value.trim()) {
    notifyError('请填写解决原因')
    return
  }
  busy.value = `resolve-${a.alert_id}`
  try {
    await endpoints.alertResolve(a.alert_id, resolveReason.value.trim())
    notify(`已解决告警：${a.title}`)
    resolving.value = null
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

function startTimer() {
  stopTimer()
  if (autoRefresh.value) timer = window.setInterval(() => void load(), 30000)
}
function stopTimer() {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}
function toggleAuto() {
  autoRefresh.value = !autoRefresh.value
  startTimer()
}

onMounted(() => {
  void load()
  startTimer()
})
onUnmounted(stopTimer)
</script>

<template>
  <div>
    <PageHeader
      eyebrow="Runtime / Monitoring"
      title="运行监控与告警"
      subtitle="API / Worker / 队列 / Run / Artifact / 数据库的真实运行指标与告警处置。所有告警阈值为 provisional（临时配置，未实测校准），不是 SLA。"
    >
      <template #actions>
        <button :disabled="loading" @click="load()">刷新</button>
        <label class="small muted flex" style="gap: 6px; align-items: center">
          <input v-model="globalScope" type="checkbox" style="width: auto" data-testid="monitoring-global-scope" @change="load()" />
          全局范围（全部已授权 Workspace）
        </label>
        <button :class="{ primary: autoRefresh }" @click="toggleAuto">
          {{ autoRefresh ? '自动刷新开（30s）' : '自动刷新关' }}
        </button>
        <router-link class="button ghost" to="/system">系统与运行环境</router-link>
      </template>
    </PageHeader>

    <AsyncBlock :loading="loading" :error="error">
      <template v-if="snap">
        <div class="small muted" data-testid="monitoring-scope" style="margin-bottom: 10px">
          统计范围：<b>{{ scopeLabel }}</b>
          <span v-if="snap.scope?.authorized_workspaces">（{{ snap.scope.authorized_workspaces.join(', ') }}）</span>
        </div>
        <!-- 概览 -->
        <div class="metric-grid m5" style="margin-bottom: 14px">
          <MetricCard label="API 状态" :value="snap.api.health.toUpperCase()" :hint="`错误率 ${fmtPercent(snap.api.error_rate)} · P95 ${snap.api.response_time_ms.p95 ?? '—'} ms`"
            :tone="snap.api.health === 'ok' ? 'pass' : 'fail'" icon="activity" />
          <MetricCard label="数据库" :value="snap.database.ok ? 'OK' : 'FAIL'"
            :hint="`${snap.database.dialect} · 迁移 v${snap.database.migration.latest_version ?? '—'}`"
            :tone="snap.database.ok ? 'pass' : 'fail'" icon="shield" />
          <MetricCard label="Worker 在线" :value="`${snap.worker.online}/${snap.worker.total}`"
            :hint="`离线 ${snap.worker.offline} · 心跳阈值 ${String(snap.worker.thresholds.worker_offline_seconds ?? '—')}s（provisional）`"
            :tone="snap.worker.online > 0 ? 'pass' : 'warn'" icon="activity" />
          <MetricCard label="队列积压" :value="snap.queue.queued" :hint="`运行中 ${snap.queue.running} · 最老等待 ${Math.round(snap.queue.oldest_queued_wait_s)}s`"
            :tone="snap.queue.queued > Number(snap.queue.thresholds.queue_backlog_count ?? 20) ? 'warn' : 'neutral'" icon="layers"
            to="/runs" />
          <MetricCard label="活跃告警" :value="activeAlerts.length"
            :hint="`OPEN ${snap.alerts.summary.open} · ACK ${snap.alerts.summary.acknowledged}`"
            :tone="activeAlerts.length ? 'fail' : 'pass'" icon="alert" />
          <MetricCard label="产物存储" :value="fmtNumber(snap.artifact.registered)"
            :hint="`${fmtBytes(snap.artifact.total_bytes)} · 缺失 ${snap.artifact.missing_files.length} · 哈希异常 ${snap.artifact.hash_mismatch.length}`"
            :tone="snap.artifact.missing_files.length + snap.artifact.hash_mismatch.length ? 'fail' : 'neutral'" icon="layers" />
        </div>

        <!-- Worker + 队列 -->
        <div class="split">
          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>Worker 运行情况</h2>
                <span class="muted small">在线 = 心跳 {{ String(snap.worker.thresholds.worker_offline_seconds ?? '—') }}s 内（provisional）</span>
              </div>
              <div class="card-body tight scroll-x" tabindex="0">
                <table>
                  <thead>
                    <tr><th>Worker</th><th>状态</th><th>最近心跳</th><th>当前 Run</th><th>租约剩余</th><th>领取/接管/失败</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="w in snap.worker.workers" :key="w.worker_id">
                      <td class="mono small">
                        {{ w.worker_id }}
                        <div class="muted" style="font-size: 11px">{{ w.mode }}</div>
                      </td>
                      <td><StatusBadge :value="w.online ? '在线' : '离线'" /></td>
                      <td class="small">{{ fmtTime(w.last_heartbeat_at) }}
                        <div class="muted" style="font-size: 11px">{{ w.heartbeat_age_s }}s 前</div>
                      </td>
                      <td class="mono small">
                        <router-link v-if="w.current_run_id" :to="`/runs/${w.current_run_id}`">{{ w.current_run_id }}</router-link>
                        <span v-else class="muted">—</span>
                      </td>
                      <td class="small">{{ w.current_run_id ? `${Math.round(w.lease_remaining_s)}s` : '—' }}</td>
                      <td class="small mono">{{ w.claims_total }} / {{ w.takeovers_total }} / {{ w.failures_total }}</td>
                    </tr>
                    <tr v-if="!snap.worker.workers.length">
                      <td colspan="6"><div class="empty">尚无 Worker 心跳（启动 Worker 后自动出现）</div></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div class="card">
              <div class="card-head"><h2>最近错误</h2>
                <router-link class="muted small" to="/runs">执行中心 →</router-link>
              </div>
              <div class="card-body tight scroll-x" tabindex="0">
                <table>
                  <thead><tr><th>来源</th><th>详情</th><th>时间</th></tr></thead>
                  <tbody>
                    <tr v-for="(e, i) in snap.api.recent_errors" :key="'api-' + i">
                      <td><StatusBadge :value="`${e.status}`" /></td>
                      <td class="mono small truncate" style="max-width: 320px">{{ e.method }} {{ e.path }}</td>
                      <td class="small muted">{{ fmtTime(e.at) }}</td>
                    </tr>
                    <tr v-for="f in snap.run.recent_failures" :key="f.run_id">
                      <td><StatusBadge :value="f.execution_status" kind="status" /></td>
                      <td class="small">
                        <router-link :to="`/runs/${f.run_id}`">{{ f.run_id }}</router-link>
                        <div class="muted truncate" style="max-width: 320px">{{ f.error || '—' }}</div>
                      </td>
                      <td class="small muted">{{ fmtTime(f.ended_at) }}</td>
                    </tr>
                    <tr v-if="!snap.api.recent_errors.length && !snap.run.recent_failures.length">
                      <td colspan="3"><div class="empty">最近没有错误</div></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>队列情况</h2>
                <span class="muted small">积压趋势：{{ snap.queue.trend.backlog_direction }}（进程内采样）</span>
              </div>
              <div class="card-body">
                <div class="grid c3" style="box-shadow: none">
                  <div class="metric" style="box-shadow: none"><div class="label">QUEUED</div><div class="value" style="font-size: 20px">{{ snap.queue.queued }}</div></div>
                  <div class="metric" style="box-shadow: none"><div class="label">RUNNING</div><div class="value" style="font-size: 20px">{{ snap.queue.running }}</div></div>
                  <div class="metric" style="box-shadow: none"><div class="label">超时 / 逾期</div><div class="value" style="font-size: 20px">{{ snap.queue.timed_out_total }} / {{ snap.queue.overdue_running.length }}</div></div>
                </div>
                <div v-if="trendSeries[0].values.length > 1" style="margin-top: 10px">
                  <Sparkline :series="trendSeries" :height="110" />
                </div>
                <div v-else class="muted small" style="margin-top: 8px">趋势样本不足（每次刷新最多采样 1 条）</div>
                <div v-if="snap.queue.overdue_running.length" style="margin-top: 10px">
                  <div class="label" style="margin-bottom: 6px">逾期未完成 Run（provisional 上限 {{ Math.round(snap.queue.overdue_threshold_s) }}s）</div>
                  <table>
                    <thead><tr><th>Run</th><th>状态</th><th>已等待</th></tr></thead>
                    <tbody>
                      <tr v-for="o in snap.queue.overdue_running" :key="o.run_id">
                        <td class="mono small"><router-link :to="`/runs/${o.run_id}`">{{ o.run_id }}</router-link></td>
                        <td><StatusBadge :value="o.execution_status" kind="status" /></td>
                        <td class="small">{{ Math.round(o.waited_s) }}s</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-head"><h2>Run 质量（24h）</h2>
                <span class="muted small">成功率 = PASSED / 已评估（PASSED+FAILED+INCONCLUSIVE）</span>
              </div>
              <div class="card-body">
                <div class="chip-row" style="margin-bottom: 8px">
                  <span v-for="(n, k) in snap.run.last_24h.by_verdict" :key="k" class="chip">{{ k }} {{ n }}</span>
                </div>
                <div class="grid c3" style="box-shadow: none">
                  <div class="metric" style="box-shadow: none"><div class="label">24h 成功率</div>
                    <div class="value" style="font-size: 20px">{{ snap.run.last_24h.success_rate === null ? '—' : fmtPercent(snap.run.last_24h.success_rate) }}</div>
                  </div>
                  <div class="metric" style="box-shadow: none"><div class="label">执行时间 P50</div>
                    <div class="value" style="font-size: 20px">{{ snap.run.last_24h.duration_ms.p50 ?? '—' }} ms</div>
                  </div>
                  <div class="metric" style="box-shadow: none"><div class="label">执行时间 P95</div>
                    <div class="value" style="font-size: 20px">{{ snap.run.last_24h.duration_ms.p95 ?? '—' }} ms</div>
                  </div>
                </div>
                <div v-if="Object.keys(snap.run.last_24h.failure_types).length" style="margin-top: 10px">
                  <div class="label" style="margin-bottom: 6px">常见失败类型（24h）</div>
                  <div class="chip-row">
                    <span v-for="(n, k) in snap.run.last_24h.failure_types" :key="k" class="chip">{{ k }} × {{ n }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 告警 -->
        <div class="card" style="margin-top: 14px">
          <div class="card-head">
            <h2>活跃告警（{{ activeAlerts.length }}）</h2>
            <button class="ghost sm" @click="showHistory = !showHistory">
              {{ showHistory ? '隐藏历史告警' : `历史告警（${history.length}）` }}
            </button>
          </div>
          <div class="card-body tight scroll-x" tabindex="0">
            <table>
              <thead>
                <tr><th>级别</th><th>类型</th><th>状态</th><th>描述</th><th>关联</th><th>发生 / 最近</th><th>处理</th></tr>
              </thead>
              <tbody>
                <tr v-for="a in activeAlerts" :key="a.alert_id">
                  <td><StatusBadge :value="a.severity" /></td>
                  <td class="mono small">{{ a.alert_type }}</td>
                  <td><StatusBadge :value="a.status === 'OPEN' ? '待处理' : '已确认'" /></td>
                  <td class="small">
                    {{ a.title }}
                    <div class="muted">{{ a.message }}</div>
                  </td>
                  <td class="small mono">
                    <router-link v-if="a.ref_run_id" :to="`/runs/${a.ref_run_id}`">{{ a.ref_run_id }}</router-link>
                    <span v-else-if="a.ref_worker_id">{{ a.ref_worker_id }}</span>
                    <span v-else class="muted">—</span>
                  </td>
                  <td class="small muted">{{ fmtTime(a.first_seen) }}<br />{{ fmtTime(a.last_seen) }}（×{{ a.occurrences }}）</td>
                  <td class="small" style="white-space: nowrap">
                    <button v-if="a.status === 'OPEN' && canManageAlerts" class="sm" :disabled="busy === `ack-${a.alert_id}`" @click="acknowledge(a)">确认</button>
                    <button v-if="a.status !== 'RESOLVED' && canManageAlerts" class="sm" :disabled="busy === `resolve-${a.alert_id}`" @click="openResolve(a)">解决</button>
                    <div v-if="a.acknowledged_by" class="muted" style="font-size: 11px">
                      ACK：{{ a.acknowledged_by }} @ {{ fmtTime(a.acknowledged_at) }}
                    </div>
                  </td>
                </tr>
                <tr v-if="!activeAlerts.length">
                  <td colspan="7"><div class="empty">当前无活跃告警</div></td>
                </tr>
              </tbody>
            </table>

            <template v-if="showHistory">
              <h3 style="margin: 14px 0 6px">历史告警（已解决）</h3>
              <table>
                <thead>
                  <tr><th>级别</th><th>类型</th><th>描述</th><th>解决原因</th><th>处理主体 / 时间</th></tr>
                </thead>
                <tbody>
                  <tr v-for="a in history" :key="a.alert_id">
                    <td><StatusBadge :value="a.severity" /></td>
                    <td class="mono small">{{ a.alert_type }}</td>
                    <td class="small">{{ a.title }}</td>
                    <td class="small">{{ a.resolution_reason || '—' }}</td>
                    <td class="small muted">{{ a.resolved_by || '—' }} @ {{ fmtTime(a.resolved_at) }}</td>
                  </tr>
                  <tr v-if="!history.length"><td colspan="5"><div class="empty">暂无历史告警</div></td></tr>
                </tbody>
              </table>
            </template>
          </div>
        </div>

        <!-- Artifact / 数据库 / 阈值 -->
        <div class="split" style="margin-top: 14px">
          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>Artifact 存储状态</h2>
                <span class="muted small">哈希校验为有界抽样（最近 {{ snap.artifact.hash_check.limit }} 条）</span>
              </div>
              <div class="card-body">
                <div class="chip-row">
                  <span class="chip">登记 {{ fmtNumber(snap.artifact.registered) }}</span>
                  <span class="chip">占用 {{ fmtBytes(snap.artifact.total_bytes) }}</span>
                  <span class="chip" :class="{ warn: snap.artifact.missing_files.length }">缺失 {{ snap.artifact.missing_files.length }}</span>
                  <span class="chip" :class="{ warn: snap.artifact.hash_mismatch.length }">哈希不一致 {{ snap.artifact.hash_mismatch.length }}</span>
                  <span class="chip">孤儿文件 {{ snap.artifact.orphan_files.length }}</span>
                  <span class="chip">孤儿登记 {{ snap.artifact.orphan_rows.length }}</span>
                </div>
                <div class="small muted" style="margin-top: 10px">
                  GC：{{ snap.artifact.gc.last_run_at ? `最近执行 ${snap.artifact.gc.last_run_at}` : '尚未执行过' }}
                  · 审计记录 {{ snap.artifact.gc.total_records }} 条
                  · error {{ snap.artifact.gc.errors }} 条
                  · 隔离目录 {{ snap.artifact.gc.quarantine_dirs }}
                </div>
                <div v-if="snap.artifact.missing_files.length" class="small" style="margin-top: 8px">
                  <div class="label">缺失文件</div>
                  <div v-for="m in snap.artifact.missing_files.slice(0, 5)" :key="m.artifact_id" class="mono small">{{ m.artifact_id }}</div>
                </div>
                <div v-if="snap.artifact.hash_mismatch.length" class="small" style="margin-top: 8px">
                  <div class="label">哈希不一致</div>
                  <div v-for="m in snap.artifact.hash_mismatch.slice(0, 5)" :key="m.artifact_id" class="mono small">{{ m.artifact_id }}</div>
                </div>
              </div>
            </div>
          </div>
          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>数据库与连接池</h2></div>
              <div class="card-body">
                <div class="chip-row">
                  <span class="chip">连接 {{ snap.database.ok ? 'OK' : 'FAIL' }}</span>
                  <span class="chip">方言 {{ snap.database.dialect }}</span>
                  <span class="chip">迁移 v{{ snap.database.migration.latest_version ?? '—' }}（已应用 {{ snap.database.migration.applied ?? '—' }}）</span>
                </div>
                <pre class="json" tabindex="0" style="margin-top: 10px">{{ JSON.stringify(snap.database.pool, null, 2) }}</pre>
                <div class="small muted" style="margin-top: 8px">
                  监控观测到的读/写失败：{{ snap.database.failures.read_failures }} / {{ snap.database.failures.write_failures }}
                  <template v-if="snap.database.failures.last_error"> · 最近：{{ snap.database.failures.last_error }}</template>
                </div>
              </div>
            </div>
            <div class="card">
              <div class="card-head"><h2>告警阈值（provisional）</h2>
                <span class="muted small">{{ snap.thresholds_status.disclaimer }}</span>
              </div>
              <div class="card-body"><pre class="json" tabindex="0">{{ JSON.stringify(snap.thresholds, null, 2) }}</pre></div>
            </div>
          </div>
        </div>
      </template>
    </AsyncBlock>

    <!-- 解决告警对话框 -->
    <ModalShell v-if="resolving" title="解决告警" width="460px" @close="resolving = null">
      <p class="small">{{ resolving.title }}</p>
      <p class="muted small">{{ resolving.message }}</p>
      <label class="label" for="resolve-reason">解决原因（必填）</label>
      <textarea id="resolve-reason" v-model="resolveReason" rows="3" style="width: 100%"
        placeholder="例如：已扩容 Worker，积压已消化" />
      <template #footer>
        <button @click="resolving = null">取消</button>
        <button class="primary" :disabled="busy === `resolve-${resolving.alert_id}`" @click="confirmResolve">确认解决</button>
      </template>
    </ModalShell>
  </div>
</template>
