<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, artifactUrl, fmtDuration, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import EvidenceGallery from '../components/EvidenceGallery.vue'
import GraphView from '../components/GraphView.vue'
import JsonBlock from '../components/JsonBlock.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Diagnosis, EventRow, Evidence, EvidenceGraph, Run, StepRow } from '../types'

const route = useRoute()
const router = useRouter()
const runId = String(route.params.id)

const run = ref<Run | null>(null)
const steps = ref<StepRow[]>([])
const evidence = ref<Evidence[]>([])
const events = ref<EventRow[]>([])
const graph = ref<EvidenceGraph | null>(null)
const diagnoses = ref<Diagnosis[]>([])
const loading = ref(true)
const error = ref('')
const tab = ref<'steps' | 'evidence' | 'assertions' | 'graph' | 'diagnosis' | 'events'>('steps')
const busy = ref('')
const probes = ref<{ probe_id: string; params: Record<string, unknown>; why: string; score: number }[]>([])

let timer: number | undefined

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
    error.value = ''
  } catch (e) {
    if (!silent) error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

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
    notify('已创建新的重试 Run')
    router.push(`/runs/${r.run_id}`)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

function screenshotOf(stepId: string): string | null {
  const step = report.value?.steps?.find((s) => s.step_id === stepId)
  const art = step?.artifacts?.screenshot
  return art?.artifact_id ? artifactUrl(art.artifact_id) : null
}

function obsPairs(obs: Record<string, unknown> | undefined): [string, string][] {
  if (!obs) return []
  const keys = ['url', 'title', 'present', 'count', 'text', 'visible', 'status', 'filled', 'clicked', 'pressed', 'waited_ms', 'error', 'timed_out']
  return keys
    .filter((k) => obs[k] !== undefined && obs[k] !== '' && obs[k] !== null)
    .map((k) => [k, typeof obs[k] === 'object' ? JSON.stringify(obs[k]) : String(obs[k]).slice(0, 160)])
}

onMounted(async () => {
  await load()
  loadGraph()
  loadProbes()
  timer = window.setInterval(() => {
    if (running.value) load(true)
  }, 2200)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div>
    <AsyncBlock :loading="loading" :error="error">
      <div v-if="run">
        <div class="page-head">
          <div>
            <div class="flex">
              <h1>Run 详情</h1>
              <StatusBadge :value="run.test_verdict" />
              <StatusBadge :value="run.execution_status" />
              <span v-if="run.observer_mode" class="badge info">{{ run.observer_mode }}</span>
              <span v-if="running" class="badge warn"><span class="spinner" style="width: 10px; height: 10px; border-width: 1.5px" /> 执行中</span>
            </div>
            <div class="sub mono">{{ run.run_id }} · task {{ run.task_id }} · attempt {{ run.attempt }}</div>
          </div>
          <div class="actions">
            <button @click="router.push('/runs')">返回</button>
            <button :disabled="busy === 'graph'" @click="buildGraph">生成证据图</button>
            <button :disabled="busy === 'diag' || run.test_verdict === 'PASSED'" @click="diagnose">诊断</button>
            <button :disabled="running || busy === 'retry'" @click="retry">重试</button>
          </div>
        </div>

        <div class="grid c4" style="margin-bottom: 14px">
          <div class="metric">
            <div class="label">断言通过率</div>
            <div class="value" :style="passRate === 100 ? 'color:var(--pass)' : 'color:var(--fail)'">{{ passRate }}%</div>
            <div class="hint">{{ report?.summary?.passed ?? 0 }} / {{ report?.summary?.total ?? 0 }} 通过</div>
          </div>
          <div class="metric">
            <div class="label">步骤</div>
            <div class="value">{{ steps.length }}</div>
            <div class="hint">失败 {{ failedSteps }} 步</div>
          </div>
          <div class="metric">
            <div class="label">耗时</div>
            <div class="value">{{ fmtDuration(report?.duration_ms) }}</div>
            <div class="hint">{{ fmtTime(run.created_at) }}</div>
          </div>
          <div class="metric">
            <div class="label">证据</div>
            <div class="value">{{ evidence.length }}</div>
            <div class="hint">截图 / DOM / ARIA / 状态</div>
          </div>
        </div>

        <div v-if="report?.errors?.length" class="card" style="border-color:#fecdca; margin-bottom: 14px">
          <div class="card-body" style="background:var(--fail-weak); border-radius:8px">
            <div class="strong" style="color:var(--fail)">执行错误</div>
            <div v-for="e in report.errors" :key="e" class="mono small" style="margin-top: 4px; color:#7a271a">{{ e }}</div>
          </div>
        </div>

        <div class="tabs">
          <button :class="{ active: tab === 'steps' }" @click="tab = 'steps'">步骤轨迹 ({{ steps.length }})</button>
          <button :class="{ active: tab === 'assertions' }" @click="tab = 'assertions'">断言 ({{ report?.assertion_outcomes?.length ?? 0 }})</button>
          <button :class="{ active: tab === 'evidence' }" @click="tab = 'evidence'">证据 ({{ evidence.length }})</button>
          <button :class="{ active: tab === 'graph' }" @click="tab = 'graph'">证据图</button>
          <button :class="{ active: tab === 'diagnosis' }" @click="tab = 'diagnosis'">诊断 ({{ diagnoses.length }})</button>
          <button :class="{ active: tab === 'events' }" @click="tab = 'events'">事件 ({{ events.length }})</button>
        </div>

        <!-- steps -->
        <div v-if="tab === 'steps'" class="split">
          <div class="card">
            <div class="card-head"><h2>执行轨迹</h2><div class="actions"><span class="small muted">浏览器真实操作 + 观测</span></div></div>
            <div class="card-body">
              <div class="timeline">
                <div v-for="(s, i) in steps" :key="s.step_id + s.index" class="timeline-item">
                  <div class="timeline-dot" :class="s.status === 'PASSED' ? 'pass' : s.status === 'FAILED' ? 'fail' : ''">
                    {{ s.status === 'PASSED' ? '✓' : s.status === 'FAILED' ? '!' : i + 1 }}
                  </div>
                  <div>
                    <div class="flex wrap">
                      <span class="strong small">{{ s.step_id }}</span>
                      <span class="badge neutral">{{ s.action }}</span>
                      <StatusBadge :value="s.status" />
                      <span class="small muted">{{ fmtDuration(s.duration_ms) }}</span>
                    </div>
                    <div v-if="s.error" class="small mono" style="color: var(--fail); margin-top: 3px">{{ s.error }}</div>
                    <div class="chip-row" style="margin-top: 5px">
                      <span v-for="[k, v] in obsPairs(s.observation)" :key="k" class="chip">
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
            <div class="card-head"><h2>步骤截图</h2></div>
            <div class="card-body">
              <div class="flex-col">
                <div v-for="s in report?.steps ?? []" :key="'shot-' + s.step_id">
                  <div v-if="screenshotOf(s.step_id)" style="margin-bottom: 8px">
                    <div class="small muted">{{ s.step_id }} · {{ s.action }}</div>
                    <a :href="screenshotOf(s.step_id)!" target="_blank">
                      <img :src="screenshotOf(s.step_id)!" style="width: 100%; border: 1px solid var(--border); border-radius: 6px" />
                    </a>
                  </div>
                </div>
                <div v-if="!report?.steps?.length" class="empty">暂无截图</div>
              </div>
            </div>
          </div>
        </div>

        <!-- assertions -->
        <div v-if="tab === 'assertions'" class="card">
          <div class="card-head"><h2>断言判定</h2><div class="actions"><span class="small muted">基于 Oracle 与确定性规则，不由 Agent 判定</span></div></div>
          <div class="card-body tight scroll-x">
            <table>
              <thead><tr><th>断言</th><th>状态</th><th>必需</th><th>Oracle</th><th>判定依据</th></tr></thead>
              <tbody>
                <tr v-for="a in report?.assertion_outcomes ?? []" :key="a.assertion_id">
                  <td class="mono small">{{ a.assertion_id }}</td>
                  <td><StatusBadge :value="a.status" /></td>
                  <td class="small">{{ a.required ? '是' : '否' }}</td>
                  <td class="small">{{ a.oracle_available ? '已加载' : '不可用' }}</td>
                  <td class="small mono">{{ a.reason }}</td>
                </tr>
                <tr v-if="!report?.assertion_outcomes?.length"><td colspan="5"><div class="empty">无断言结果</div></td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- evidence -->
        <div v-if="tab === 'evidence'" class="card">
          <div class="card-head"><h2>证据</h2><div class="actions"><span class="small muted">不可变 · SHA-256 完整性</span></div></div>
          <div class="card-body">
            <EvidenceGallery :items="evidence" />
          </div>
        </div>

        <!-- graph -->
        <div v-if="tab === 'graph'" class="card">
          <div class="card-head">
            <h2>Evidence Graph</h2>
            <div class="actions">
              <span class="small muted">{{ graph?.stats?.nodes ?? 0 }} 节点 / {{ graph?.stats?.edges ?? 0 }} 边</span>
              <button class="sm" @click="buildGraph">重建</button>
            </div>
          </div>
          <div class="card-body">
            <GraphView :graph="graph" />
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
                <router-link :to="`/diagnoses/${d.diagnosis_id}`" class="small">详情 →</router-link>
              </div>
            </div>
            <div class="card-body">
              <div class="kv">
                <dt>症状</dt><dd class="mono small">{{ d.symptom }}</dd>
                <dt>引擎</dt><dd>{{ d.engine }}</dd>
                <dt>根因数量</dt><dd>{{ d.root_causes.length }}</dd>
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
                </div>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-head">
              <h2>探针建议（Active Diagnosis）</h2>
              <div class="actions"><button class="sm" @click="loadProbes">刷新建议</button></div>
            </div>
            <div class="card-body tight scroll-x">
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
            <div class="card-head"><h2>事件流（只追加）</h2><div class="actions"><span class="small muted">序号在实例内唯一 · 幂等键去重</span></div></div>
            <div class="card-body tight scroll-x">
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
  </div>
</template>
