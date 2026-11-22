<script setup lang="ts">
/** 质量指挥中心：全部指标来自真实 API，不硬编码业务数字。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtDuration, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import Sparkline from '../components/Sparkline.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Diagnosis, Run, TestCaseSummary } from '../types'

const router = useRouter()
const runs = ref<Run[]>([])
const cases = ref<TestCaseSummary[]>([])
const diagnoses = ref<Diagnosis[]>([])
const repairs = ref<Record<string, unknown>[]>([])
const explorations = ref<Record<string, unknown>[]>([])
const queue = ref<{ by_status: Record<string, number>; total: number; worker: { id: string; inline_running: boolean } } | null>(null)
const loading = ref(true)
const error = ref('')
const busy = ref('')
let timer: number | undefined

async function load() {
  try {
    const [r, c, d, p, ex, q] = await Promise.all([
      api.get<Run[]>('/runs?limit=120&workspace_id=default'),
      api.get<TestCaseSummary[]>('/testcases?workspace_id=default'),
      api.get<Diagnosis[]>('/diagnoses?limit=50&workspace_id=default'),
      api.get<Record<string, unknown>[]>('/repairs?workspace_id=default'),
      api.get<Record<string, unknown>[]>('/explorations?workspace_id=default'),
      api.get<{ by_status: Record<string, number>; total: number; worker: { id: string; inline_running: boolean } }>('/queue/stats'),
    ])
    runs.value = r
    cases.value = c
    diagnoses.value = d
    repairs.value = p
    explorations.value = ex
    queue.value = q
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

// ---- 结论统计（口径：test_verdict 非空即"有结论"；NOT_EVALUATED 单列不计入通过率分母） ----
const finished = computed(() => runs.value.filter((r) => r.test_verdict && r.test_verdict !== 'NOT_EVALUATED'))
const passed = computed(() => finished.value.filter((r) => r.test_verdict === 'PASSED').length)
const failed = computed(() => finished.value.filter((r) => r.test_verdict === 'FAILED').length)
const inconclusive = computed(() => finished.value.filter((r) => r.test_verdict === 'INCONCLUSIVE').length)
const notEvaluated = computed(() => runs.value.filter((r) => !r.test_verdict || r.test_verdict === 'NOT_EVALUATED').length)
const passRate = computed(() => (finished.value.length ? Math.round((passed.value / finished.value.length) * 100) : 0))

const running = computed(() => runs.value.filter((r) => ['QUEUED', 'PREPARING', 'RUNNING'].includes(r.execution_status)))
const approvedCases = computed(() => cases.value.filter((c) => c.review_status === 'APPROVED'))

// ---- 趋势（最近 20 个有结论 Run，按创建时间正序） ----
const trendRuns = computed(() =>
  finished.value
    .slice()
    .sort((a, b) => Number(a.created_at) - Number(b.created_at))
    .slice(-20),
)
const trendSeries = computed(() => {
  let p = 0
  let f = 0
  const pv: number[] = []
  const fv: number[] = []
  for (const r of trendRuns.value) {
    if (r.test_verdict === 'PASSED') p++
    else f++
    pv.push(p)
    fv.push(f)
  }
  return [
    { label: 'PASSED（累计）', color: '#12b76a', values: pv },
    { label: 'FAILED（累计）', color: '#f04438', values: fv },
  ]
})

// ---- 失败类型分布（来自真实诊断记录的 failure_domain 聚合） ----
const failureDomains = computed(() => {
  const counts: Record<string, number> = {}
  for (const d of diagnoses.value) {
    for (const h of d.hypotheses ?? []) {
      const dom = String(h.failure_domain ?? 'UNKNOWN')
      counts[dom] = (counts[dom] ?? 0) + 1
    }
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  return { counts, total }
})

// ---- 待办：失败 Run 中尚未诊断的 + 待审批修复 ----
const diagnosedRunIds = computed(() => new Set(diagnoses.value.map((d) => d.run_id)))
const pendingDiagnosis = computed(() =>
  runs.value.filter((r) => r.test_verdict === 'FAILED' && !diagnosedRunIds.value.has(r.run_id)),
)
const pendingRepairApproval = computed(() =>
  repairs.value.filter((r) => r.status === 'PENDING_APPROVAL' || r.status === 'VALIDATED'),
)
const recentErrors = computed(() =>
  runs.value
    .filter((r) => (r.report?.errors?.length ?? 0) > 0)
    .slice(0, 4),
)

const dist = computed(() => {
  const total = Math.max(runs.value.length, 1)
  const seg = (n: number) => (n / total) * 100
  return [
    { key: 'PASSED', n: passed.value, w: seg(passed.value), c: '#12b76a' },
    { key: 'FAILED', n: failed.value, w: seg(failed.value), c: '#f04438' },
    { key: 'INCONCLUSIVE', n: inconclusive.value, w: seg(inconclusive.value), c: '#7a5af8' },
    { key: 'NOT_EVALUATED', n: notEvaluated.value, w: seg(notEvaluated.value), c: '#98a2b3' },
  ]
})

async function executeAll() {
  busy.value = 'all'
  try {
    for (const c of approvedCases.value) {
      await api.post(`/testcases/${c.testcase_id}/execute`, { sync: false })
    }
    notify(`已入队 ${approvedCases.value.length} 个已审核用例`)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function seed() {
  busy.value = 'seed'
  try {
    const r = await api.post<{ created: number; skipped: number }>('/system/seed')
    notify(`种子完成：新增 ${r.created}，跳过 ${r.skipped}`)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 5000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>质量指挥中心</h1>
        <div class="sub">
          全部指标来自真实执行数据 · 通过率口径：PASSED /（PASSED+FAILED+INCONCLUSIVE），NOT_EVALUATED 不计入
        </div>
      </div>
      <div class="actions">
        <button :disabled="busy === 'seed'" @click="seed">初始化演示资产</button>
        <button class="primary" :disabled="busy === 'all' || approvedCases.length === 0" @click="executeAll">
          执行全部已审核用例（{{ approvedCases.length }}）
        </button>
      </div>
    </div>

    <AsyncBlock :loading="loading" :error="error">
      <div class="grid c4" style="margin-bottom: 14px">
        <div class="metric">
          <div class="label">通过率</div>
          <div class="value" :style="passRate >= 80 ? 'color:var(--pass)' : passRate >= 50 ? 'color:var(--warn)' : 'color:var(--fail)'">
            {{ finished.length ? passRate + '%' : '—' }}
          </div>
          <div class="hint">{{ passed }} 通过 / {{ finished.length }} 有结论</div>
        </div>
        <div class="metric">
          <div class="label">失败 Run</div>
          <div class="value" :style="failed ? 'color:var(--fail)' : 'color:var(--pass)'">{{ failed }}</div>
          <div class="hint">待诊断 {{ pendingDiagnosis.length }} 个</div>
        </div>
        <div class="metric">
          <div class="label">执行中 / 队列</div>
          <div class="value">{{ running.length }}</div>
          <div class="hint">
            队列总计 {{ queue?.total ?? 0 }} · Worker {{ queue?.worker?.inline_running ? '运行中' : '空闲' }}
          </div>
        </div>
        <div class="metric">
          <div class="label">证据与探索</div>
          <div class="value">{{ explorations.length }}</div>
          <div class="hint">探索记录 · 待审批修复 {{ pendingRepairApproval.length }}</div>
        </div>
      </div>

      <div class="split" style="margin-bottom: 14px">
        <div class="card">
          <div class="card-head">
            <h2>结论趋势</h2>
            <div class="actions"><span class="small muted">最近 {{ trendRuns.length }} 个有结论 Run</span></div>
          </div>
          <div class="card-body">
            <Sparkline :series="trendSeries" :height="140" />
          </div>
        </div>

        <div class="card">
          <div class="card-head"><h2>四态结论分布</h2></div>
          <div class="card-body">
            <div class="bar" style="height: 12px; margin-bottom: 12px">
              <span v-for="d in dist" :key="d.key" :style="`width:${d.w}%;background:${d.c}`" />
            </div>
            <div class="legend">
              <span v-for="d in dist" :key="d.key">
                <span class="swatch" :style="`background:${d.c}`" />{{ d.key }} {{ d.n }}
              </span>
            </div>
            <div class="hr" />
            <div class="kv">
              <dt>诊断记录</dt><dd>{{ diagnoses.length }}（CONFIRMED 需确定性规则与可信证据）</dd>
              <dt>INCONCLUSIVE</dt><dd>{{ inconclusive }} —— 证据不足不算通过</dd>
              <dt>NOT_EVALUATED</dt><dd>{{ notEvaluated }} —— 未完成评估，单独列示</dd>
            </div>
          </div>
        </div>
      </div>

      <div class="split">
        <div class="flex-col">
          <div class="card">
            <div class="card-head">
              <h2>失败类型分布</h2>
              <div class="actions"><span class="small muted">按诊断 failure_domain 聚合（{{ failureDomains.total }} 假设）</span></div>
            </div>
            <div class="card-body">
              <template v-if="failureDomains.total">
                <div v-for="(n, dom) in failureDomains.counts" :key="dom" class="flex" style="margin-bottom: 6px">
                  <span class="small" style="width: 130px">{{ dom }}</span>
                  <div class="bar" style="flex: 1">
                    <span :style="`width:${(Number(n) / failureDomains.total) * 100}%;background:#2f5bea`" />
                  </div>
                  <span class="small muted" style="width: 24px; text-align: right">{{ n }}</span>
                </div>
              </template>
              <div v-else class="empty">暂无诊断记录，无失败类型可聚合</div>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><h2>待办</h2></div>
            <div class="card-body">
              <div class="todo-item">
                <span class="num" :style="pendingDiagnosis.length ? 'color:var(--fail)' : 'color:var(--text-3)'">{{ pendingDiagnosis.length }}</span>
                <div style="flex: 1">
                  <div class="strong small">失败 Run 待诊断</div>
                  <div class="muted" style="font-size: 11.5px">失败但尚无诊断记录</div>
                </div>
                <router-link v-if="pendingDiagnosis.length" to="/diagnoses" class="small">去处理 →</router-link>
              </div>
              <div class="todo-item">
                <span class="num" :style="pendingRepairApproval.length ? 'color:var(--warn)' : 'color:var(--text-3)'">{{ pendingRepairApproval.length }}</span>
                <div style="flex: 1">
                  <div class="strong small">修复提案待审批</div>
                  <div class="muted" style="font-size: 11.5px">沙箱验证通过，等待具名人工审批</div>
                </div>
                <router-link v-if="pendingRepairApproval.length" to="/repairs" class="small">去审批 →</router-link>
              </div>
            </div>
          </div>
        </div>

        <div class="flex-col">
          <div class="card">
            <div class="card-head">
              <h2>最近执行</h2>
              <div class="actions"><router-link to="/runs" class="small">查看全部 →</router-link></div>
            </div>
            <div class="card-body tight scroll-x">
              <table>
                <thead>
                  <tr><th>用例</th><th>结论</th><th>执行</th><th>模式</th><th>耗时</th><th>时间</th></tr>
                </thead>
                <tbody>
                  <tr v-for="r in runs.slice(0, 8)" :key="r.run_id" class="row-click" @click="router.push(`/runs/${r.run_id}`)">
                    <td>
                      <div class="strong">{{ r.report?.testcase_id || r.testcase_version_id }}</div>
                      <div class="mono muted" style="font-size: 10.5px">{{ r.run_id.slice(-12) }}</div>
                    </td>
                    <td><StatusBadge :value="r.test_verdict" /></td>
                    <td><StatusBadge :value="r.execution_status" /></td>
                    <td class="small">{{ r.observer_mode || '—' }}</td>
                    <td class="small nowrap">{{ fmtDuration(r.report?.duration_ms) }}</td>
                    <td class="small muted nowrap">{{ fmtTime(r.created_at) }}</td>
                  </tr>
                  <tr v-if="runs.length === 0">
                    <td colspan="6"><div class="empty">还没有执行记录，点击右上角执行用例</div></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><h2>最近异常</h2></div>
            <div class="card-body">
              <template v-if="recentErrors.length">
                <div v-for="r in recentErrors" :key="r.run_id" style="margin-bottom: 8px">
                  <router-link :to="`/runs/${r.run_id}`" class="mono small">{{ r.run_id.slice(-12) }}</router-link>
                  <div v-for="e in r.report?.errors?.slice(0, 2)" :key="e" class="small mono truncate" style="color: var(--fail); max-width: 480px">
                    {{ e }}
                  </div>
                </div>
              </template>
              <div v-else class="empty">近期没有执行异常</div>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>
  </div>
</template>
