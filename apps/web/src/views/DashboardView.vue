<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtDuration, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Diagnosis, Run, TestCaseSummary } from '../types'

const router = useRouter()
const runs = ref<Run[]>([])
const cases = ref<TestCaseSummary[]>([])
const diagnoses = ref<Diagnosis[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
let timer: number | undefined

async function load() {
  try {
    const [r, c, d] = await Promise.all([
      api.get<Run[]>('/runs?limit=60&workspace_id=default'),
      api.get<TestCaseSummary[]>('/testcases?workspace_id=default'),
      api.get<Diagnosis[]>('/diagnoses?limit=20'),
    ])
    runs.value = r
    cases.value = c
    diagnoses.value = d
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

const finished = computed(() => runs.value.filter((r) => r.test_verdict !== 'NOT_EVALUATED'))
const passed = computed(() => finished.value.filter((r) => r.test_verdict === 'PASSED').length)
const failed = computed(() => finished.value.filter((r) => r.test_verdict === 'FAILED').length)
const passRate = computed(() => (finished.value.length ? Math.round((passed.value / finished.value.length) * 100) : 0))
const running = computed(() => runs.value.filter((r) => ['QUEUED', 'PREPARING', 'RUNNING'].includes(r.execution_status)).length)
const evidenceCount = computed(() => runs.value.reduce((n, r) => n + (r.report?.evidence_ids?.length ?? 0), 0))

const recent = computed(() => runs.value.slice(0, 8))
const approvedCases = computed(() => cases.value.filter((c) => c.review_status === 'APPROVED'))

async function execute(caseId: string) {
  busy.value = caseId
  try {
    const res = await api.post<{ run_id: string }>(`/testcases/${caseId}/execute`, { sync: false })
    notify('已入队执行，正在跳转…')
    router.push(`/runs/${res.run_id}`)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

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
        <h1>质量总览</h1>
        <div class="sub">真实浏览器执行 · 证据可追溯 · 诊断与修复闭环</div>
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
        <MetricCard label="通过率" :value="passRate + '%'" :hint="`${passed} 通过 / ${finished.length} 有结论`" :tone="passRate >= 80 ? 'pass' : 'warn'" />
        <MetricCard label="失败 Run" :value="failed" hint="需诊断与分流" :tone="failed ? 'fail' : 'pass'" />
        <MetricCard label="执行中" :value="running" :hint="`已审核用例 ${approvedCases.length} 个`" />
        <MetricCard label="采集证据" :value="evidenceCount" hint="截图 / DOM / ARIA / 状态查询" />
      </div>

      <div class="split">
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
                <tr v-for="r in recent" :key="r.run_id" style="cursor: pointer" @click="router.push(`/runs/${r.run_id}`)">
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
                <tr v-if="recent.length === 0">
                  <td colspan="6"><div class="empty">还没有执行记录，点击右上角执行用例</div></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="flex-col">
          <div class="card">
            <div class="card-head"><h2>结论分布</h2></div>
            <div class="card-body">
              <div class="bar" style="height: 10px; margin-bottom: 10px">
                <span class="p" :style="`width:${(passed / Math.max(finished.length, 1)) * 100}%`" />
                <span class="f" :style="`width:${(failed / Math.max(finished.length, 1)) * 100}%`" />
              </div>
              <div class="flex" style="justify-content: space-between">
                <span class="small"><span class="dot ok" />PASSED {{ passed }}</span>
                <span class="small"><span class="dot bad" />FAILED {{ failed }}</span>
              </div>
              <div class="hr" />
              <div class="kv">
                <dt>诊断记录</dt><dd>{{ diagnoses.length }}</dd>
                <dt>最近结论</dt><dd>{{ diagnoses[0]?.outcome ?? '—' }}</dd>
                <dt>处置分流</dt><dd>{{ diagnoses[0]?.disposition ?? '—' }}</dd>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-head">
              <h2>用例资产</h2>
              <div class="actions"><router-link to="/testcases" class="small">管理 →</router-link></div>
            </div>
            <div class="card-body flex-col">
              <div v-for="c in cases.slice(0, 5)" :key="c.testcase_id" class="flex" style="justify-content: space-between">
                <div style="min-width: 0">
                  <div class="truncate small strong">{{ c.title || c.testcase_id }}</div>
                  <div class="muted" style="font-size: 11px">{{ c.step_count }} 步 · {{ c.assertion_count }} 断言 · v{{ c.latest_version }}</div>
                </div>
                <button class="sm" :disabled="c.review_status !== 'APPROVED' || busy === c.testcase_id" @click="execute(c.testcase_id)">
                  执行
                </button>
              </div>
              <div v-if="cases.length === 0" class="empty">暂无用例</div>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>
  </div>
</template>
