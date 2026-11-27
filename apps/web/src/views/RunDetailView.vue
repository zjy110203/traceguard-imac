<script setup lang="ts">
/** Run 详情：步骤轨迹 + 右侧详情面板（截图/网络/Console/观察），
 *  证据跳转、诊断、探针、事件流。全部数据来自真实 API。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, artifactUrl, fmtDuration, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import EvidenceGallery from '../components/EvidenceGallery.vue'
import GraphView from '../components/GraphView.vue'
import JsonBlock from '../components/JsonBlock.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { subscribeEvents, type StreamMode } from '../events'
import { notify, notifyError } from '../toast'
import type { Diagnosis, EventRow, Evidence, EvidenceGraph, GraphNode, Run, StepRow } from '../types'

const route = useRoute()
const router = useRouter()
const runId = String(route.params.id)

const run = ref<Run | null>(null)
const steps = ref<StepRow[]>([])
const evidence = ref<Evidence[]>([])
const events = ref<EventRow[]>([])
const graph = ref<EvidenceGraph | null>(null)
const graphNode = ref<GraphNode | null>(null)
const diagnoses = ref<Diagnosis[]>([])
const loading = ref(true)
const error = ref('')
const tab = ref<'steps' | 'evidence' | 'assertions' | 'graph' | 'diagnosis' | 'events'>('steps')
const busy = ref('')
const probes = ref<{ probe_id: string; params: Record<string, unknown>; why: string; score: number }[]>([])
const selectedStepId = ref<string | null>(null)
const zoomShot = ref<string | null>(null)
let timer: number | undefined
let unsubscribe: (() => void) | undefined
const streamMode = ref<StreamMode>('connecting')
const liveEvents = ref(0)

const streamLabel = computed(() => {
  if (streamMode.value === 'live') return '实时推送'
  if (streamMode.value === 'polling') return '轮询降级'
  if (streamMode.value === 'connecting') return '连接中'
  return '已断开'
})

const report = computed(() => run.value?.report ?? null)
const running = computed(() => !!run.value && ['QUEUED', 'PREPARING', 'RUNNING', 'FINALIZING'].includes(run.value.execution_status))
const failedSteps = computed(() => steps.value.filter((s) => s.status === 'FAILED').length)
const passRate = computed(() => {
  const s = report.value?.summary
  if (!s || !s.total) return 0
  return Math.round((s.passed / s.total) * 100)
})

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const [r, st, ev, evt, dg] = await Promise.all([
      api.get<Run>(`/runs/${runId}`),
      api.get<StepRow[]>(`/runs/${runId}/steps`),
      api.get<Evidence[]>(`/runs/${runId}/evidence`),
      api.get<EventRow[]>(`/runs/${runId}/events?limit=200`),
      api.get<Diagnosis[]>(`/diagnoses?run_id=${runId}`),
    ])
    run.value = r
    steps.value = st
    evidence.value = ev
    events.value = evt
    diagnoses.value = dg
    if (!selectedStepId.value) {
      const failedFirst = st.find((s) => s.status === 'FAILED')
      selectedStepId.value = failedFirst?.step_id ?? st[0]?.step_id ?? null
    }
    error.value = ''
  } catch (e) {
    if (!silent) error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

// ---------- 步骤详情数据（来自真实证据与报告） ----------
const selectedStep = computed(() => steps.value.find((s) => s.step_id === selectedStepId.value) ?? null)
const selectedReportStep = computed(() => report.value?.steps?.find((s) => s.step_id === selectedStepId.value) ?? null)
const selectedScreenshot = computed(() => {
  const art = selectedReportStep.value?.artifacts?.screenshot
  return art?.artifact_id ? artifactUrl(art.artifact_id) : null
})

interface NetEntry { url: string; method?: string; status: number | null; failure?: string | null; resource_type?: string; duration_ms?: number | null }
interface ConsoleEntry { type: string; text: string; location?: Record<string, unknown> }

const stepSignals = computed(() => {
  const evd = evidence.value.find((e) => e.source_ref === `step_signals:${selectedStepId.value}`)
  const sig = evd?.content?.signals ?? {}
  return {
    network: (sig.network ?? []) as NetEntry[],
    consoleErrors: (sig.console_errors ?? []) as ConsoleEntry[],
    pageErrors: (sig.page_errors ?? []) as { message?: string }[],
    httpErrors: (sig.http_errors ?? []) as Record<string, unknown>[],
  }
})

const stepOtherEvidence = computed(() => {
  const sid = selectedStepId.value
  if (!sid) return []
  return evidence.value.filter(
    (e) => e.source_ref?.includes(sid) && e.source_ref !== `step_signals:${sid}`,
  )
})

function obsPairs(obs: Record<string, unknown> | undefined): [string, string][] {
  if (!obs) return []
  const keys = ['url', 'title', 'present', 'count', 'text', 'visible', 'status', 'filled', 'clicked', 'pressed', 'waited_ms', 'error', 'timed_out']
  return keys
    .filter((k) => obs[k] !== undefined && obs[k] !== '' && obs[k] !== null)
    .map((k) => [k, typeof obs[k] === 'object' ? JSON.stringify(obs[k]) : String(obs[k]).slice(0, 160)])
}

function selectStep(id: string, target: 'steps' | 'evidence' = 'steps') {
  selectedStepId.value = id
  tab.value = target
}

// ---------- 原有动作 ----------
async function diagnose() {
  busy.value = 'diag'
  try {
    const d = await api.post<Diagnosis>(`/runs/${runId}/diagnose`)
    notify('诊断完成：' + d.outcome)
    await load(true)
    tab.value = 'diagnosis'
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function buildGraph() {
  busy.value = 'graph'
  try {
    graph.value = await api.post<EvidenceGraph>(`/runs/${runId}/graph`)
    notify('证据图已生成')
    tab.value = 'graph'
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function loadGraph() {
  if (graph.value) return
  try {
    graph.value = await api.get<EvidenceGraph>(`/runs/${runId}/graph`)
  } catch {
    /* 图尚未生成 */
  }
}

async function loadProbes() {
  try {
    probes.value = await api.get(`/runs/${runId}/probe-suggestions`)
  } catch (e) {
    notifyError(e)
  }
}

async function runProbe(p: { probe_id: string; params: Record<string, unknown> }) {
  busy.value = 'probe'
  try {
    const res = await api.post<{ status: string }>('/probes/execute', {
      probe_id: p.probe_id,
      params: { ...p.params, run_id: runId },
    })
    notify('探针执行：' + res.status)
    await load(true)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function retry() {
  if (!run.value) return
  busy.value = 'retry'
  try {
    const r = await api.post<{ run_id: string }>(`/tasks/${run.value.task_id}/runs`)
    notify('已创建新的重试 Run（历史保留）')
    router.push(`/runs/${r.run_id}`)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

function statusClass(s: number | null | undefined): string {
  if (s == null) return 'serr'
  if (s < 300) return 's2x'
  if (s < 400) return 's3x'
  if (s < 500) return 's4x'
  return 's5x'
}

onMounted(async () => {
  await load()
  loadGraph()
  loadProbes()
  // SSE 负责低延迟提示；轮询保留为降级与纠偏（SSE 不是状态权威来源）
  unsubscribe = subscribeEvents({
    runId,
    onMode: (m) => (streamMode.value = m),
    onEvent: (ev) => {
      liveEvents.value += 1
      const terminal = ev.event_type === 'run.completed' || ev.event_type === 'run.failed'
      if (running.value || terminal) void load(true)
    },
  })
  timer = window.setInterval(() => {
    if (running.value) load(true)
  }, 6000)
})
onUnmounted(() => {
  window.clearInterval(timer)
  unsubscribe?.()
})
</script>

<template>
  <div>
    <AsyncBlock :loading="loading" :error="error">
      <div v-if="run">
        <PageHeader :eyebrow="`Execution / ${run.observer_mode || 'run'}`" :title="`Run ${run.run_id.slice(-12)}`"
          :subtitle="`task ${run.task_id} · attempt ${run.attempt} · ${fmtTime(run.created_at)}`">
          <template #actions>
            <button @click="router.push('/runs')">返回执行中心</button>
            <button :disabled="busy === 'graph'" @click="buildGraph">生成证据图</button>
            <button :disabled="busy === 'diag' || run.test_verdict === 'PASSED'" @click="diagnose">诊断</button>
            <button :disabled="running || busy === 'retry'" @click="retry">重试</button>
          </template>
          <template #meta>
            <div class="flex wrap mt">
              <StatusBadge :value="run.test_verdict" />
              <StatusBadge :value="run.execution_status" />
              <span v-if="run.observer_mode" class="badge info">{{ run.observer_mode }}</span>
              <span v-if="running" class="badge warn"><span class="spinner" style="width: 10px; height: 10px; border-width: 1.5px" /> 执行中</span>
              <span class="badge" :class="streamMode === 'live' ? 'pass' : streamMode === 'polling' ? 'warn' : 'neutral'"
                :title="`事件通道：${streamLabel}（SSE 仅作实时提示，状态以接口为准）`">{{ streamLabel }}</span>
              <span class="badge neutral">错误 {{ report?.errors?.length ?? 0 }}</span>
            </div>
          </template>
        </PageHeader>

        <div class="metric-grid m5" style="margin-bottom: 14px">
          <MetricCard label="断言通过率" :value="`${passRate}%`" :hint="`${report?.summary?.passed ?? 0} / ${report?.summary?.total ?? 0} 通过`" :tone="passRate === 100 ? 'pass' : 'fail'" icon="shield" />
          <MetricCard label="步骤" :value="steps.length" :hint="`失败 ${failedSteps} 步`" :tone="failedSteps ? 'fail' : 'neutral'" icon="activity" />
          <MetricCard label="耗时" :value="fmtDuration(report?.duration_ms)" :hint="report?.observer_mode || '真实执行'" icon="gauge" />
          <MetricCard label="证据" :value="evidence.length" hint="不可变 · SHA-256 完整性校验" icon="layers" />
          <MetricCard label="Oracle" :value="report?.assertion_outcomes?.some((a) => a.oracle_available) ? 'available' : 'unavailable'" hint="未审批 Oracle 不参与判定" :tone="report?.assertion_outcomes?.some((a) => a.oracle_available) ? 'pass' : 'warn'" icon="shield" />
        </div>

        <div class="context-strip panel">
          <span class="small muted">执行上下文</span>
          <router-link v-if="run.report?.testcase_id" :to="`/testcases/${run.report.testcase_id}`" class="small">用例 {{ run.report.testcase_id }} ↗</router-link>
          <span class="small muted">版本 {{ run.report?.testcase_version ?? '—' }}</span>
          <router-link v-if="run.report?.task_id" to="/runs" class="small">Task {{ run.report.task_id }} ↗</router-link>
          <span class="small muted">Assertion {{ report?.summary?.inconclusive ?? 0 }} INCONCLUSIVE · {{ report?.summary?.not_evaluated ?? 0 }} NOT_EVAL</span>
        </div>

        <div class="context-strip panel" style="margin-top: 8px">
          <span class="small muted">执行归属</span>
          <span class="small">Worker <span class="mono">{{ run.lease_owner || '尚未被领取' }}</span></span>
          <span class="small muted">attempt {{ run.attempt }}</span>
          <span class="small muted">fencing token {{ run.fencing_token }}</span>
          <span class="small muted">队列状态 {{ run.execution_status }}</span>
          <span v-if="run.lease_expires_at && running" class="small muted">租约到期 {{ fmtTime(run.lease_expires_at) }}</span>
          <span v-else-if="run.lease_owner" class="small muted">租约已释放（执行结束）</span>
        </div>

        <div v-if="report?.errors?.length" class="card" style="border-color:var(--fail-border); margin-bottom: 14px">
          <div class="card-body" style="background:var(--fail-weak); border-radius:8px">
            <div class="strong" style="color:var(--fail)">执行错误</div>
            <div v-for="e in report.errors" :key="e" class="mono small" style="margin-top: 4px; color:var(--fail)">{{ e }}</div>
          </div>
        </div>

        <div class="tabs">
          <button :class="{ active: tab === 'steps' }" @click="tab = 'steps'">步骤轨迹 <span class="tabn">{{ steps.length }}</span></button>
          <button :class="{ active: tab === 'assertions' }" @click="tab = 'assertions'">断言 <span class="tabn">{{ report?.assertion_outcomes?.length ?? 0 }}</span></button>
          <button :class="{ active: tab === 'evidence' }" @click="tab = 'evidence'">证据 <span class="tabn">{{ evidence.length }}</span></button>
          <button :class="{ active: tab === 'graph' }" @click="tab = 'graph'">证据图</button>
          <button :class="{ active: tab === 'diagnosis' }" @click="tab = 'diagnosis'">诊断 <span class="tabn">{{ diagnoses.length }}</span></button>
          <button :class="{ active: tab === 'events' }" @click="tab = 'events'">事件 <span class="tabn">{{ events.length }}</span></button>
        </div>

        <!-- steps：主从布局 -->
        <div v-if="tab === 'steps'" class="master-detail">
          <div class="card">
            <div class="card-head">
              <h2>执行轨迹</h2>
              <div class="actions"><span class="small muted">点击步骤查看截图 / 网络 / Console</span></div>
            </div>
            <div class="card-body">
              <div class="timeline">
                <div
                  v-for="(s, i) in steps"
                  :key="s.step_id + s.index"
                  class="timeline-item"
                  :class="{ 'row-click': true, selected: selectedStepId === s.step_id }"
                  style="border-radius: 6px; padding: 8px; margin-left: -8px"
                  @click="selectStep(s.step_id)"
                >
                  <div class="timeline-dot" :class="s.status === 'PASSED' ? 'pass' : s.status === 'FAILED' ? 'fail' : ''">
                    {{ s.status === 'PASSED' ? '✓' : s.status === 'FAILED' ? '!' : i + 1 }}
                  </div>
                  <div>
                    <div class="flex wrap">
                      <span class="strong small">{{ s.step_id }}</span>
                      <span class="badge neutral">{{ s.action }}</span>
                      <StatusBadge :value="s.status" />
                      <span class="small muted">{{ fmtDuration(s.duration_ms) }}</span>
                      <button v-if="s.step_id" class="sm ghost" style="margin-left: auto" @click.stop="selectStep(s.step_id, 'evidence')">证据 ↗</button>
                    </div>
                    <div v-if="s.error" class="small mono" style="color: var(--fail); margin-top: 3px">{{ s.error }}</div>
                    <div class="chip-row" style="margin-top: 5px">
                      <span v-for="[k, v] in obsPairs(s.observation).slice(0, 4)" :key="k" class="chip">
                        <b style="font-weight: 600">{{ k }}</b>: {{ v }}
                      </span>
                    </div>
                  </div>
                </div>
                <div v-if="steps.length === 0" class="empty">暂无步骤记录（Run 可能未执行或已失败在准备阶段）</div>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-head">
              <h2>步骤详情</h2>
              <div class="actions"><span class="mono small muted">{{ selectedStepId?.slice(-12) || '—' }}</span></div>
            </div>
            <div class="card-body" style="max-height: 620px; overflow: auto">
              <div v-if="!selectedStep" class="empty">点击左侧步骤查看详情</div>
              <template v-else>
                <div class="detail-section">
                  <div class="sec-title">截图</div>
                  <div v-if="selectedScreenshot" class="shot-frame" @click="zoomShot = selectedScreenshot">
                    <img :src="selectedScreenshot" :alt="`步骤 ${selectedStep.action} 截图`" loading="lazy" />
                  </div>
                  <div v-else class="empty">该步骤无截图</div>
                </div>

                <div class="detail-section">
                  <div class="sec-title">观察数据</div>
                  <div class="kv">
                    <template v-for="[k, v] in obsPairs(selectedStep.observation)" :key="k">
                      <div class="kvt">{{ k }}</div><div class="kvd mono small">{{ v }}</div>
                    </template>
                    <template v-if="!obsPairs(selectedStep.observation).length">
                      <div class="kvt">—</div><div class="kvd muted">无观察数据</div>
                    </template>
                  </div>
                </div>

                <div class="detail-section">
                  <div class="sec-title">网络请求（{{ stepSignals.network.length }}）</div>
                  <div v-if="stepSignals.network.length">
                    <div v-for="(n, i) in stepSignals.network.slice(-20)" :key="i" class="net-row">
                      <span class="net-status mono" :class="statusClass(n.status)">{{ n.status ?? (n.failure ? 'ERR' : '…') }}</span>
                      <span class="mono truncate" :title="n.url">{{ (n.method ?? 'GET') + ' ' + n.url }}</span>
                      <span class="small muted nowrap">{{ n.resource_type || '' }}</span>
                    </div>
                  </div>
                  <div v-else class="empty">该步骤无网络信号证据</div>
                </div>

                <div class="detail-section">
                  <div class="sec-title">Console 错误（{{ stepSignals.consoleErrors.length }}）· 页面异常（{{ stepSignals.pageErrors.length }}）</div>
                  <div v-if="stepSignals.consoleErrors.length || stepSignals.pageErrors.length">
                    <div v-for="(c, i) in stepSignals.consoleErrors" :key="'c' + i" class="console-row">
                      <span class="ctype mono">{{ c.type }}</span>
                      <span class="mono small">{{ c.text }}</span>
                    </div>
                    <div v-for="(p, i) in stepSignals.pageErrors" :key="'p' + i" class="console-row">
                      <span class="ctype mono error">pageerror</span>
                      <span class="mono small">{{ p.message }}</span>
                    </div>
                  </div>
                  <div v-else class="empty">无 Console 错误（该步骤网络证据中）</div>
                </div>

                <div class="detail-section">
                  <div class="sec-title">关联证据（{{ stepOtherEvidence.length }}）</div>
                  <div v-if="stepOtherEvidence.length">
                    <div v-for="e in stepOtherEvidence" :key="e.evidence_id" class="flex" style="margin-bottom: 4px">
                      <span class="badge neutral">{{ e.source_type }}</span>
                      <span class="mono small truncate">{{ e.source_ref }}</span>
                      <button class="sm ghost" style="margin-left: auto" @click="tab = 'evidence'">查看 ↗</button>
                    </div>
                  </div>
                  <div v-else class="empty">无关联证据</div>
                </div>
              </template>
            </div>
          </div>
        </div>

        <!-- assertions -->
        <div v-if="tab === 'assertions'" class="card">
          <div class="card-head"><h2>断言判定</h2><div class="actions"><span class="small muted">基于 Oracle 与确定性规则，不由 Agent 判定</span></div></div>
          <div class="card-body tight scroll-x" tabindex="0">
            <table>
              <thead><tr><th>断言</th><th>状态</th><th>必需</th><th>Oracle</th><th>判定依据</th><th></th></tr></thead>
              <tbody>
                <tr v-for="a in report?.assertion_outcomes ?? []" :key="a.assertion_id">
                  <td class="mono small">{{ a.assertion_id }}</td>
                  <td><StatusBadge :value="a.status" /></td>
                  <td class="small">{{ a.required ? '是' : '否' }}</td>
                  <td class="small">{{ a.oracle_available ? '已加载' : '不可用' }}</td>
                  <td class="small mono">{{ a.reason }}</td>
                  <td><button class="sm ghost" @click="tab = 'evidence'">关联证据 ↗</button></td>
                </tr>
                <tr v-if="!report?.assertion_outcomes?.length"><td colspan="6"><div class="empty">无断言结果</div></td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- evidence -->
        <div v-if="tab === 'evidence'" class="card">
          <div class="card-head">
            <h2>证据</h2>
            <div class="actions">
              <span class="small muted">不可变 · SHA-256 完整性 · 从证据可返回所属 Run</span>
            </div>
          </div>
          <div class="card-body">
            <EvidenceGallery :items="evidence" />
          </div>
        </div>

        <!-- graph -->
        <div v-if="tab === 'graph'" class="card">
          <div class="card-head">
            <h2>Evidence Graph</h2>
            <div class="actions">
              <span class="small muted">{{ graph?.stats?.nodes ?? 0 }} 节点 / {{ graph?.stats?.edges ?? 0 }} 边 · 悬停高亮邻接，点击查看节点</span>
              <button class="sm" @click="buildGraph">重建</button>
            </div>
          </div>
          <div class="card-body">
            <GraphView :graph="graph" @select="graphNode = $event" />
            <div v-if="graphNode" style="margin-top: 10px">
              <div class="flex" style="margin-bottom: 6px">
                <span class="badge info">{{ graphNode.node_type }}</span>
                <span class="mono small strong">{{ graphNode.node_id }}</span>
                <button class="sm ghost" style="margin-left: auto" @click="graphNode = null">关闭</button>
              </div>
              <div class="kv" style="margin-bottom: 8px">
                <div class="kvt">引用对象</div><div class="kvd mono small">{{ graphNode.ref_id }}</div>
              </div>
              <JsonBlock :data="graphNode.properties" />
            </div>
          </div>
        </div>

        <!-- diagnosis -->
        <div v-if="tab === 'diagnosis'" class="flex-col">
          <div v-if="diagnoses.length === 0" class="card">
            <div class="card-body">
              <div class="empty">
                <div class="title">尚未诊断</div>
                <div class="small">失败 Run 可执行诊断；PASSED 的 Run 不做伪诊断</div>
                <button class="primary" style="margin-top: 10px" :disabled="run.test_verdict === 'PASSED' || busy === 'diag'" @click="diagnose">立即诊断</button>
              </div>
            </div>
          </div>
          <div v-for="d in diagnoses" :key="d.diagnosis_id" class="card">
            <div class="card-head">
              <h2>诊断结论</h2>
              <div class="actions">
                <StatusBadge :value="d.outcome" />
                <StatusBadge :value="d.disposition" />
                <span class="badge neutral">判定引擎：{{ d.engine }}</span>
                <router-link :to="`/diagnoses/${d.diagnosis_id}`" class="small">详情 →</router-link>
              </div>
            </div>
            <div class="card-body">
              <div class="kv">
                <div class="kvt">症状</div><div class="kvd mono small">{{ d.symptom }}</div>
                <div class="kvt">根因数量</div><div class="kvd">{{ d.root_causes.length }}</div>
              </div>
              <div class="hr" />
              <div v-for="h in d.hypotheses" :key="h.hypothesis_id" style="margin-bottom: 10px">
                <div class="flex wrap">
                  <StatusBadge :value="h.status" />
                  <span class="badge neutral">{{ h.failure_domain }}</span>
                  <span class="badge neutral">{{ h.failure_mechanism }}</span>
                  <span v-if="h.flaky_characteristic" class="badge warn">flaky 特征</span>
                </div>
                <div class="small" style="margin-top: 4px">{{ h.description }}</div>
                <div class="chip-row" style="margin-top: 5px">
                  <span v-for="s in h.supporting" :key="s.id" class="chip">{{ s.id.slice(-10) }} · {{ s.trust }}{{ s.confirming ? ' · confirming' : '' }}</span>
                  <span v-for="s in h.contradicting" :key="'x' + s.id" class="chip" style="color: var(--fail)">{{ s.id.slice(-10) }} · contradicting</span>
                </div>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-head">
              <h2>探针建议（Active Diagnosis）</h2>
              <div class="actions"><button class="sm" @click="loadProbes">刷新建议</button></div>
            </div>
            <div class="card-body tight scroll-x" tabindex="0">
              <table>
                <thead><tr><th>探针</th><th>目的</th><th>EIG 评分</th><th></th></tr></thead>
                <tbody>
                  <tr v-for="p in probes" :key="p.probe_id">
                    <td class="mono small">{{ p.probe_id }}</td>
                    <td class="small">{{ p.why }}</td>
                    <td class="small">{{ p.score }}</td>
                    <td><button class="sm" :disabled="busy === 'probe'" @click="runProbe(p)">执行（只读）</button></td>
                  </tr>
                  <tr v-if="probes.length === 0"><td colspan="4"><div class="empty">暂无建议</div></td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <!-- events -->
        <div v-if="tab === 'events'" class="flex-col">
          <div class="card">
            <div class="card-head"><h2>事件流（只追加）</h2><div class="actions"><span class="badge" :class="streamMode === 'live' ? 'pass' : streamMode === 'polling' ? 'warn' : 'neutral'">{{ streamLabel }}</span><span class="small muted">序号在实例内唯一 · 幂等键去重 · SSE 仅作实时提示</span></div></div>
            <div class="card-body tight scroll-x" tabindex="0">
              <table>
                <thead><tr><th>#</th><th>类型</th><th>发生时间</th><th>生产者</th><th>载荷</th></tr></thead>
                <tbody>
                  <tr v-for="e in events" :key="e.event_id">
                    <td class="mono small">{{ e.sequence }}</td>
                    <td><span class="badge neutral">{{ e.event_type }}</span></td>
                    <td class="small muted nowrap">{{ fmtTime(e.occurred_at) }}</td>
                    <td class="mono small">{{ e.producer_instance_id.slice(-14) }}</td>
                    <td class="mono small truncate" style="max-width: 420px">{{ JSON.stringify(e.payload) }}</td>
                  </tr>
                  <tr v-if="events.length === 0"><td colspan="5"><div class="empty">暂无事件</div></td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div v-if="report?.oracle_state" class="card" style="margin-top: 14px">
          <div class="card-head"><h2>Trusted Oracle 独立状态查询</h2><div class="actions"><span class="small muted">不采信页面文本</span></div></div>
          <div class="card-body"><JsonBlock :data="report.oracle_state" /></div>
        </div>
      </div>
    </AsyncBlock>

    <!-- 截图放大 -->
    <div v-if="zoomShot" class="modal-backdrop" @click="zoomShot = null">
      <div style="max-width: 92vw; max-height: 92vh">
        <img :src="zoomShot" style="max-width: 92vw; max-height: 88vh; border-radius: 8px; box-shadow: var(--shadow-lg)" alt="步骤截图（放大）" />
        <div class="small muted" style="text-align: center; margin-top: 8px; color: #fff">点击任意处关闭</div>
      </div>
    </div>
  </div>
</template>
