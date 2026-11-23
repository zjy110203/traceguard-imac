<script setup lang="ts">
/** 质量指挥中心：所有指标与趋势均从真实 API 数据计算，按真实时间分桶。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Activity, CircleAlert, Clock3, Play, RefreshCw, ShieldCheck, Users } from '@lucide/vue'
import { api, fmtDuration, fmtPercent, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import BarChart from '../components/BarChart.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import DonutChart from '../components/DonutChart.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TrendChart from '../components/TrendChart.vue'
import { notify, notifyError } from '../toast'
import type { Diagnosis, Run, TestCaseSummary } from '../types'

const router = useRouter()
const runs = ref<Run[]>([])
const cases = ref<TestCaseSummary[]>([])
const diagnoses = ref<Diagnosis[]>([])
const repairs = ref<Record<string, unknown>[]>([])
const queue = ref<{ by_status: Record<string, number>; total: number; worker: { id: string; inline_running: boolean } } | null>(null)
const health = ref<{ status?: string; database?: { ok: boolean; dialect: string; error?: string }; worker?: { id: string; inline_running: boolean }; counts?: Record<string, number> } | null>(null)
const loading = ref(true)
const error = ref('')
const busy = ref('')
const confirmAll = ref(false)
const lastUpdated = ref<number | null>(null)
let timer: number | undefined

async function load() {
  try {
    const [r, c, d, p, q, h] = await Promise.all([
      api.get<Run[]>('/runs?limit=200&workspace_id=default'),
      api.get<TestCaseSummary[]>('/testcases?workspace_id=default'),
      api.get<Diagnosis[]>('/diagnoses?limit=100&workspace_id=default'),
      api.get<Record<string, unknown>[]>('/repairs?workspace_id=default'),
      api.get<{ by_status: Record<string, number>; total: number; worker: { id: string; inline_running: boolean } }>('/queue/stats'),
      api.get<{ status?: string; database?: { ok: boolean; dialect: string }; worker?: { id: string; inline_running: boolean } }>('/system/health'),
    ])
    runs.value = r; cases.value = c; diagnoses.value = d; repairs.value = p; queue.value = q; health.value = h
    lastUpdated.value = Date.now()
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

const finished = computed(() => runs.value.filter((r) => r.test_verdict && r.test_verdict !== 'NOT_EVALUATED'))
const passed = computed(() => finished.value.filter((r) => r.test_verdict === 'PASSED').length)
const failed = computed(() => finished.value.filter((r) => r.test_verdict === 'FAILED').length)
const inconclusive = computed(() => finished.value.filter((r) => r.test_verdict === 'INCONCLUSIVE').length)
const notEvaluated = computed(() => runs.value.length - finished.value.length)
const passRate = computed(() => (finished.value.length ? passed.value / finished.value.length : null))
const runningNow = computed(() => runs.value.filter((r) => ['QUEUED', 'PREPARING', 'RUNNING', 'FINALIZING'].includes(r.execution_status)))
const approvedCases = computed(() => cases.value.filter((c) => c.review_status === 'APPROVED'))

/** 近 7 个 UTC 自然日，按 Run.created_at 真实分桶；空桶保留，样本量可见。 */
const daily = computed(() => {
  const today = new Date()
  const buckets = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - 6 + i))
    const key = d.toISOString().slice(0, 10)
    return { key, label: key.slice(5), PASSED: 0, FAILED: 0, INCONCLUSIVE: 0, n: 0 }
  })
  for (const r of finished.value) {
    const key = new Date(r.created_at * 1000).toISOString().slice(0, 10)
    const bucket = buckets.find((b) => b.key === key)
    if (!bucket || !r.test_verdict) continue
    if (r.test_verdict === 'PASSED') bucket.PASSED += 1
    else if (r.test_verdict === 'FAILED') bucket.FAILED += 1
    else if (r.test_verdict === 'INCONCLUSIVE') bucket.INCONCLUSIVE += 1
    bucket.n += 1
  }
  return buckets
})
const trendSeries = computed(() => [
  { label: 'PASSED', color: 'var(--pass)', values: daily.value.map((d) => d.PASSED) },
  { label: 'FAILED', color: 'var(--fail)', values: daily.value.map((d) => d.FAILED) },
  { label: 'INCONCLUSIVE', color: 'var(--incon)', values: daily.value.map((d) => d.INCONCLUSIVE) },
])
const segments = computed(() => [
  { label: 'PASSED', value: passed.value, color: 'var(--pass)' },
  { label: 'FAILED', value: failed.value, color: 'var(--fail)' },
  { label: 'INCONCLUSIVE', value: inconclusive.value, color: 'var(--incon)' },
  { label: 'NOT_EVALUATED', value: notEvaluated.value, color: 'var(--border-strong)' },
])
const failureDomains = computed(() => {
  const counts: Record<string, number> = {}
  for (const d of diagnoses.value) for (const h of d.hypotheses ?? []) {
    const key = String(h.failure_domain || 'UNKNOWN')
    counts[key] = (counts[key] || 0) + 1
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([label, value]) => ({ label, value, hint: `n=${value}` }))
})
const diagnosedRunIds = computed(() => new Set(diagnoses.value.map((d) => d.run_id)))
const pendingDiagnosis = computed(() => runs.value.filter((r) => r.test_verdict === 'FAILED' && !diagnosedRunIds.value.has(r.run_id)))
const pendingRepairApproval = computed(() => repairs.value.filter((r) => ['PENDING_APPROVAL', 'VALIDATED'].includes(String(r.status))))
const todoCount = computed(() => pendingDiagnosis.value.length + pendingRepairApproval.value.length)
const latestEvents = computed(() => runs.value.slice().sort((a, b) => b.created_at - a.created_at).slice(0, 5))
const recentErrors = computed(() => runs.value.filter((r) => (r.report?.errors?.length ?? 0) > 0).slice(0, 3))

async function executeAll() {
  confirmAll.value = false
  busy.value = 'all'
  try {
    for (const c of approvedCases.value) await api.post(`/testcases/${c.testcase_id}/execute`, { sync: false })
    notify(`已入队 ${approvedCases.value.length} 个已审核用例`)
    await load()
  } catch (e) { notifyError(e) } finally { busy.value = '' }
}
function drill(verdict: string) { void router.push({ path: '/runs', query: { verdict } }) }

onMounted(() => { load(); timer = window.setInterval(load, 10000) })
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div>
    <PageHeader eyebrow="Quality Command Center" title="质量指挥中心"
      subtitle="第一屏回答：质量状况、执行中任务、失败项、待诊断、待审批修复、系统状态。口径与样本量随图表一起展示。">
      <template #actions>
        <button class="sm" type="button" @click="load"><RefreshCw :size="13" /> 刷新</button>
        <button class="sm primary" type="button" :disabled="busy === 'all' || !approvedCases.length" @click="confirmAll = true">
          <Play :size="13" /> 执行全部已审核用例（{{ approvedCases.length }}）
        </button>
      </template>
      <template #meta>
        <div class="small muted mt">数据每 10 秒静默刷新 · 上次更新 {{ fmtTime(lastUpdated ? lastUpdated / 1000 : null) }} · 后端状态来自真实 API</div>
      </template>
    </PageHeader>

    <AsyncBlock :loading="loading" :error="error">
      <div class="metric-grid m5">
        <MetricCard label="总 Run 数" :value="runs.length" :hint="`${approvedCases.length} 个已审核用例`" to="/runs" icon="activity" />
        <MetricCard label="通过率" :value="fmtPercent(passRate)" :hint="`口径 PASSED / (PASSED+FAILED+INCONCLUSIVE) · n=${finished.length}`" tone="pass" icon="shield" />
        <MetricCard label="FAILED" :value="failed" hint="点击查看失败执行" tone="fail" icon="alert" to="/runs?verdict=FAILED" />
        <MetricCard label="INCONCLUSIVE" :value="inconclusive" hint="不等于 PASSED" tone="incon" icon="help" to="/runs?verdict=INCONCLUSIVE" />
        <MetricCard label="待处理任务" :value="todoCount" :hint="`${pendingDiagnosis.length} 待诊断 · ${pendingRepairApproval.length} 待审批`" tone="warn" icon="gauge" to="/repairs" />
      </div>

      <div class="grid c2 mt">
        <section class="panel">
          <div class="panel-head"><h2>测试结论趋势</h2><span class="small muted">近 7 个 UTC 自然日 · 真实时间分桶</span></div>
          <div class="panel-body">
            <TrendChart :series="trendSeries" :labels="daily.map((d) => d.label)" :height="200" @select="drill(['PASSED','FAILED','INCONCLUSIVE'][$event] || '')" />
            <div class="sample-strip">
              <span v-for="d in daily" :key="d.key" class="small muted"><b class="mono">{{ d.label }}</b> n={{ d.n }}</span>
            </div>
            <p class="small muted mt">每桶按 Run 创建时间归类；空桶保留为 0，不把累计计数包装成通过率趋势。</p>
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>四态分布</h2><span class="small muted">n={{ runs.length }}</span></div>
          <div class="panel-body"><DonutChart :segments="segments" center-label="总 Run" @select="drill" /></div>
        </section>
      </div>

      <div class="grid c2 mt">
        <section class="panel">
          <div class="panel-head"><h2>失败类型分布</h2><span class="small muted">来自诊断假设 · n={{ diagnoses.reduce((s, d) => s + (d.hypotheses?.length || 0), 0) }}</span></div>
          <div class="panel-body"><BarChart :items="failureDomains" @select="() => $router.push('/diagnoses')" /></div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>执行队列与 Worker</h2><span class="small muted">实时轮询</span></div>
          <div class="panel-body">
            <div class="status-cards">
              <div><span class="small muted">QUEUED</span><strong>{{ queue?.by_status?.QUEUED || 0 }}</strong></div>
              <div><span class="small muted">RUNNING</span><strong class="warn-text">{{ queue?.by_status?.RUNNING || 0 }}</strong></div>
              <div><span class="small muted">执行中 Run</span><strong>{{ runningNow.length }}</strong></div>
              <div><span class="small muted">执行模式</span><strong class="small">{{ queue?.worker?.inline_running ? '内嵌 Worker' : '独立 Worker' }}</strong></div>
            </div>
            <div class="small muted mt">API Worker ID：<span class="mono">{{ queue?.worker?.id || '—' }}</span></div>
            <div class="small muted mt">
              数据库
              <span class="badge" :class="health?.database?.ok ? 'pass' : 'fail'">{{ health?.database?.ok ? '正常' : '异常' }}</span>
              <span class="mono">{{ health?.database?.dialect || '—' }}</span>
              · 服务
              <span class="badge" :class="health?.status === 'ok' ? 'pass' : 'fail'">{{ health?.status || 'unknown' }}</span>
            </div>
            <div v-if="health?.database?.ok === false" class="small fail-text mt">数据库错误：{{ health?.database?.error || '未知' }}</div>
            <p class="small muted mt">「独立 Worker」表示 API 未启用内嵌执行器，Run 由外部 Worker 进程消费队列。</p>
          </div>
        </section>
      </div>

      <div class="grid c3 mt">
        <section class="panel">
          <div class="panel-head"><h2>最近执行</h2><router-link class="small" to="/runs">全部 →</router-link></div>
          <div class="panel-body tight scroll-x">
            <table><thead><tr><th>Run</th><th>结论</th><th>时间</th></tr></thead><tbody>
              <tr v-for="r in latestEvents" :key="r.run_id" class="row-click" @click="$router.push(`/runs/${r.run_id}`)">
                <td class="mono small">{{ r.run_id.slice(-12) }}</td>
                <td><StatusBadge :value="r.test_verdict" /></td>
                <td class="small muted nowrap">{{ fmtTime(r.created_at) }}</td>
              </tr>
              <tr v-if="!latestEvents.length"><td colspan="3"><div class="empty small">暂无执行记录</div></td></tr>
            </tbody></table>
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>待办</h2><span class="small muted">{{ todoCount }} 项</span></div>
          <div class="panel-body">
            <div class="todo-item"><span class="num warn-text">{{ pendingDiagnosis.length }}</span><div><b>待诊断</b><div class="small muted">FAILED 且尚无诊断记录</div></div><button class="sm right" @click="$router.push('/diagnoses')">查看</button></div>
            <div class="todo-item"><span class="num warn-text">{{ pendingRepairApproval.length }}</span><div><b>待审批修复</b><div class="small muted">PENDING_APPROVAL / VALIDATED</div></div><button class="sm right" @click="$router.push('/repairs')">查看</button></div>
            <div class="todo-item"><span class="num">{{ runs.filter((r) => r.execution_status === 'COMPLETED' && !r.report).length }}</span><div><b>报告缺失</b><div class="small muted">COMPLETED 但无 report</div></div></div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>最近重要事件</h2><span class="small muted">执行错误</span></div>
          <div class="panel-body">
            <div v-for="r in recentErrors" :key="r.run_id" class="todo-item">
              <Activity :size="14" class="warn-text" />
              <div class="truncate"><b class="mono small">{{ r.run_id.slice(-10) }}</b><div class="small muted truncate">{{ r.report?.errors?.[0] || r.error || '未知错误' }}</div></div>
            </div>
            <div v-if="!recentErrors.length" class="empty small">最近没有执行错误</div>
          </div>
        </section>
      </div>
    </AsyncBlock>

    <ConfirmDialog :open="confirmAll" title="执行全部已审核用例" danger
      :message="`将向队列提交 ${approvedCases.length} 个已审核用例。每个执行都会产生新的 Run，不会覆盖历史。`"
      confirm-label="确认入队" @confirm="executeAll" @cancel="confirmAll = false" />
  </div>
</template>
