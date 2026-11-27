<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtDuration, fmtTime } from '../api'
import { wsPath } from '../workspace'
import AsyncBlock from '../components/AsyncBlock.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import type { Run } from '../types'

const router = useRouter()
const route = useRoute()
const runs = ref<Run[]>([])
const loading = ref(true)
const error = ref('')
const verdict = ref(String(route.query.verdict ?? ''))
const includeBench = ref(false)
const status = ref('')
let timer: number | undefined
watch(() => route.query.verdict, (v) => { verdict.value = String(v ?? '') })

const filtered = computed(() =>
  runs.value.filter((r) => (!verdict.value || r.test_verdict === verdict.value) && (!status.value || r.execution_status === status.value)),
)
const active = computed(() => runs.value.some((r) => ['QUEUED', 'PREPARING', 'RUNNING', 'FINALIZING'].includes(r.execution_status)))

async function load() {
  try {
    runs.value = await api.get<Run[]>(includeBench.value ? '/runs?limit=200' : wsPath('/runs', { limit: 200 }))
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(() => {
    if (active.value) load()
  }, 2500)
})
onUnmounted(() => window.clearInterval(timer))

function verdictCount(v: string) {
  return runs.value.filter((r) => r.test_verdict === v).length
}
</script>

<template>
  <div>
    <PageHeader eyebrow="Execution" title="执行中心"
      subtitle="每次执行是一个 Run；重试产生新 Run，历史不被覆盖。模式区分 playwright 与 http-fallback。">
      <template #actions>
        <span v-if="active" class="badge warn"><span class="spinner" style="width: 10px; height: 10px; border-width: 1.5px" /> 有执行中的 Run</span>
        <button @click="load">刷新</button>
      </template>
    </PageHeader>

    <div class="metric-grid m5" style="margin-bottom: 14px">
      <MetricCard label="总 Run" :value="runs.length" hint="当前可见范围" icon="activity" />
      <MetricCard label="PASSED" :value="verdictCount('PASSED')" tone="pass" icon="shield" />
      <MetricCard label="FAILED" :value="verdictCount('FAILED')" tone="fail" icon="alert" />
      <MetricCard label="INCONCLUSIVE" :value="verdictCount('INCONCLUSIVE')" tone="incon" icon="help" />
      <MetricCard label="NOT_EVALUATED" :value="verdictCount('NOT_EVALUATED')" hint="不计入通过率分母" icon="layers" />
    </div>

    <div class="card">
      <div class="card-head">
        <div class="filter-bar">
          <select id="runs-verdict" v-model="verdict" aria-label="按测试结论筛选">
            <option value="">全部结论</option>
            <option value="PASSED">PASSED</option>
            <option value="FAILED">FAILED</option>
            <option value="INCONCLUSIVE">INCONCLUSIVE</option>
            <option value="NOT_EVALUATED">NOT_EVALUATED</option>
          </select>
          <label class="filter-check">
            <input id="runs-include-bench" v-model="includeBench" type="checkbox" style="width: auto" aria-label="包含 Benchmark 运行（跨全部已授权 Workspace 聚合）" @change="load" />
            包含 Benchmark 运行（跨全部已授权 Workspace 聚合）
          </label>
          <select id="runs-status" v-model="status" aria-label="按执行状态筛选">
            <option value="">全部执行状态</option>
            <option value="QUEUED">QUEUED</option>
            <option value="RUNNING">RUNNING</option>
            <option value="COMPLETED">COMPLETED</option>
            <option value="FAILED">FAILED</option>
            <option value="CANCELLED">CANCELLED</option>
          </select>
        </div>
        <div class="actions"><span class="small muted">{{ filtered.length }} 条</span></div>
      </div>
      <div class="card-body tight scroll-x" tabindex="0">
        <AsyncBlock :loading="loading" :error="error" :empty="filtered.length === 0" empty-title="暂无执行记录">
          <table>
            <thead><tr><th>Run</th><th>用例 / 版本</th><th>结论</th><th>执行状态</th><th>模式</th><th>步骤</th><th>耗时</th><th>证据</th><th>时间</th></tr></thead>
            <tbody>
              <tr v-for="r in filtered" :key="r.run_id" style="cursor: pointer" @click="router.push(`/runs/${r.run_id}`)">
                <td class="mono small">{{ r.run_id.slice(-12) }}</td>
                <td>
                  <div class="small strong">{{ r.report?.testcase_id || r.testcase_version_id.split('@')[0] }}</div>
                  <div class="muted" style="font-size: 10.5px">attempt {{ r.attempt }} · v{{ r.report?.testcase_version ?? r.testcase_version_id.split('@')[1] }}</div>
                </td>
                <td><StatusBadge :value="r.test_verdict" /></td>
                <td><StatusBadge :value="r.execution_status" /></td>
                <td class="small">{{ r.observer_mode || '—' }}</td>
                <td class="small">{{ r.report?.steps?.length ?? '—' }}</td>
                <td class="small nowrap">{{ fmtDuration(r.report?.duration_ms) }}</td>
                <td class="small">{{ r.report?.evidence_ids?.length ?? 0 }}</td>
                <td class="small muted nowrap">{{ fmtTime(r.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </AsyncBlock>
      </div>
    </div>
  </div>
</template>
