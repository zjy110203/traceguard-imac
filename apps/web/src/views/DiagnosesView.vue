<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtTime } from '../api'
import { wsPath } from '../workspace'
import AsyncBlock from '../components/AsyncBlock.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import type { Diagnosis, Disposition } from '../types'

const router = useRouter()
const items = ref<Diagnosis[]>([])
const dispositions = ref<Disposition[]>([])
const loading = ref(true)
const error = ref('')
const outcome = ref('')
const includeBench = ref(false)

const filtered = computed(() => items.value.filter((d) => !outcome.value || d.outcome === outcome.value))

async function load() {
  loading.value = true
  try {
    const [d, p] = await Promise.all([
      api.get<Diagnosis[]>(includeBench.value ? '/diagnoses?limit=200' : wsPath('/diagnoses', { limit: 200 })),
      api.get<Disposition[]>(wsPath('/dispositions', { limit: 200 })),
    ])
    items.value = d
    dispositions.value = p
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function dispOf(id: string) {
  return dispositions.value.find((p) => p.diagnosis_id === id)
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader eyebrow="Intelligence / Diagnosis" title="诊断中心"
      subtitle="确定性规则 + 可信证据裁决。没有足够证据就是 INCONCLUSIVE，不会被包装成 PASSED；LLM 候选不会被确认。">
      <template #actions>
        <label class="flex small" style="gap: 5px; margin: 0">
          <input id="diag-include-bench" v-model="includeBench" type="checkbox" style="width: auto" aria-label="包含 Benchmark 诊断" @change="load" /> 包含 Benchmark
        </label>
        <select id="diag-outcome" v-model="outcome" aria-label="按诊断结论筛选" style="width: 150px">
          <option value="">全部结论</option>
          <option value="CONFIRMED">CONFIRMED</option>
          <option value="PROBABLE">PROBABLE</option>
          <option value="INCONCLUSIVE">INCONCLUSIVE</option>
        </select>
        <button @click="load">刷新</button>
      </template>
    </PageHeader>

    <div class="metric-grid m5" style="margin-bottom: 14px">
      <MetricCard label="诊断总数" :value="items.length" icon="activity" />
      <MetricCard label="已确认根因" :value="items.filter((d) => d.outcome === 'CONFIRMED').length" hint="仅确定性规则可确认" tone="pass" icon="shield" />
      <MetricCard label="证据不足" :value="items.filter((d) => d.outcome === 'INCONCLUSIVE').length" tone="incon" icon="help" />
      <MetricCard label="可能根因" :value="items.filter((d) => d.outcome === 'PROBABLE').length" tone="warn" icon="alert" />
    </div>

    <div class="card">
      <div class="card-head"><h2>诊断记录</h2><div class="actions"><span class="small muted">{{ filtered.length }} 条</span></div></div>
      <div class="card-body tight scroll-x" tabindex="0">
        <AsyncBlock :loading="loading" :error="error" :empty="filtered.length === 0" empty-title="暂无诊断" empty-hint="在 Run 详情中执行诊断">
          <table>
            <thead><tr><th>诊断</th><th>用例</th><th>结论</th><th>处置</th><th>根因</th><th>Flaky</th><th>时间</th></tr></thead>
            <tbody>
              <tr v-for="d in filtered" :key="d.diagnosis_id" style="cursor: pointer" @click="router.push(`/diagnoses/${d.diagnosis_id}`)">
                <td class="mono small">{{ d.diagnosis_id.slice(-12) }}</td>
                <td class="small strong">{{ d.testcase_id || d.run_id.slice(-10) }}</td>
                <td><StatusBadge :value="d.outcome" /></td>
                <td><StatusBadge :value="dispOf(d.diagnosis_id)?.review_status ?? 'PENDING'" />
                  <div class="small muted">{{ dispOf(d.diagnosis_id)?.kind }}</div>
                </td>
                <td class="small">{{ d.root_causes.length }}</td>
                <td class="small">{{ d.flaky_characteristic ? '是' : '否' }}</td>
                <td class="small muted nowrap">{{ fmtTime(d.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </AsyncBlock>
      </div>
    </div>
  </div>
</template>
