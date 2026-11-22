<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import StatusBadge from '../components/StatusBadge.vue'
import type { Diagnosis, Disposition } from '../types'

const router = useRouter()
const items = ref<Diagnosis[]>([])
const dispositions = ref<Disposition[]>([])
const loading = ref(true)
const error = ref('')
const outcome = ref('')

const filtered = computed(() => items.value.filter((d) => !outcome.value || d.outcome === outcome.value))

async function load() {
  loading.value = true
  try {
    const [d, p] = await Promise.all([
      api.get<Diagnosis[]>('/diagnoses?limit=200'),
      api.get<Disposition[]>('/dispositions?limit=200'),
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
    <div class="page-head">
      <div>
        <h1>诊断中心</h1>
        <div class="sub">确定性规则 + 可信证据裁决。没有足够证据就是 INCONCLUSIVE，不会被包装成 PASSED。</div>
      </div>
      <div class="actions">
        <select v-model="outcome" style="width: 150px">
          <option value="">全部结论</option>
          <option value="CONFIRMED">CONFIRMED</option>
          <option value="PROBABLE">PROBABLE</option>
          <option value="INCONCLUSIVE">INCONCLUSIVE</option>
        </select>
        <button @click="load">刷新</button>
      </div>
    </div>

    <div class="grid c3" style="margin-bottom: 14px">
      <div class="metric"><div class="label">诊断总数</div><div class="value">{{ items.length }}</div></div>
      <div class="metric"><div class="label">已确认根因</div><div class="value" style="color: var(--fail)">{{ items.filter((d) => d.outcome === 'CONFIRMED').length }}</div></div>
      <div class="metric"><div class="label">证据不足</div><div class="value" style="color: var(--warn)">{{ items.filter((d) => d.outcome === 'INCONCLUSIVE').length }}</div></div>
    </div>

    <div class="card">
      <div class="card-head"><h2>诊断记录</h2><div class="actions"><span class="small muted">{{ filtered.length }} 条</span></div></div>
      <div class="card-body tight scroll-x">
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
