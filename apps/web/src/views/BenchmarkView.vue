<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { BenchmarkExperiment } from '../types'

const items = ref<BenchmarkExperiment[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const selected = ref<BenchmarkExperiment | null>(null)

async function load() {
  loading.value = true
  try {
    items.value = await api.get<BenchmarkExperiment[]>('/benchmark/experiments?limit=50')
    if (!selected.value && items.value.length) selected.value = items.value[0]
    else if (selected.value) selected.value = items.value.find((x) => x.experiment_id === selected.value!.experiment_id) ?? null
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function run(kind: string) {
  busy.value = kind
  try {
    const r = await api.post<BenchmarkExperiment>('/benchmark/experiments', { kind })
    notify('实验完成：' + r.experiment_id)
    await load()
    selected.value = items.value.find((x) => x.experiment_id === r.experiment_id) ?? r
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

const grouped = computed(() => {
  const out: Record<string, Record<string, { value: number; n?: number | null; d?: number | null; ci?: string }>> = {}
  for (const m of selected.value?.metrics ?? []) {
    ;(out[m.metric] ||= {})[m.baseline] = {
      value: m.value,
      n: m.numerator,
      d: m.denominator,
      ci: m.ci_low != null && m.ci_high != null ? `${(m.ci_low * 100).toFixed(1)}–${(m.ci_high * 100).toFixed(1)}%` : undefined,
    }
  }
  return out
})
const baselines = computed(() => Array.from(new Set((selected.value?.metrics ?? []).map((m) => m.baseline))))

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>UI-FaultBench</h1>
        <div class="sub">真实执行产出的对照实验：固定数据集、GT 隔离、分子/分母可核、LLM 基线缺失时明确标记 BLOCKED。</div>
      </div>
      <div class="actions">
        <button :disabled="!!busy" @click="run('A')">运行实验 A · 用例生成</button>
        <button :disabled="!!busy" @click="run('B')">运行实验 B · 故障诊断</button>
        <button :disabled="!!busy" @click="run('C')">运行实验 C · 测试自愈</button>
        <button @click="load">刷新</button>
      </div>
    </div>

    <div class="bench-grid">
      <div class="card">
        <div class="card-head"><h2>实验</h2><div class="actions"><span class="small muted">{{ items.length }}</span></div></div>
        <div class="card-body tight scroll-x">
          <AsyncBlock :loading="loading" :error="error" :empty="items.length === 0" empty-title="暂无实验" empty-hint="选择上方按钮运行真实实验">
            <table>
              <thead><tr><th>实验</th><th>类型</th><th>状态</th><th>指标</th><th>时间</th></tr></thead>
              <tbody>
                <tr v-for="e in items" :key="e.experiment_id" style="cursor: pointer" :style="selected?.experiment_id === e.experiment_id ? 'background: var(--brand-weak)' : ''" @click="selected = e">
                  <td>
                    <div class="small strong" style="max-width: 200px" :title="e.name">{{ e.name }}</div>
                    <div class="mono muted" style="font-size: 10.5px">{{ e.experiment_id.slice(-10) }}</div>
                  </td>
                  <td><span class="badge neutral">{{ e.kind }}</span></td>
                  <td><StatusBadge :value="e.status" /></td>
                  <td class="small">{{ e.metrics.length }}</td>
                  <td class="small muted nowrap">{{ fmtTime(e.started_at) }}</td>
                </tr>
              </tbody>
            </table>
          </AsyncBlock>
        </div>
      </div>

      <div v-if="selected" class="flex-col">
        <div class="card">
          <div class="card-head">
            <h2>{{ selected.name }}</h2>
            <div class="actions"><StatusBadge :value="selected.status" /><span class="badge neutral">{{ selected.dataset }}</span></div>
          </div>
          <div class="card-body">
            <div class="kv">
              <dt>数据集</dt><dd class="mono small">{{ selected.dataset }}</dd>
              <dt>配置</dt><dd class="mono small">{{ JSON.stringify(selected.config) }}</dd>
              <dt>说明</dt><dd class="small">{{ selected.notes || '—' }}</dd>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><h2>指标（分子 / 分母）</h2></div>
          <div class="card-body tight scroll-x">
            <table style="min-width: 680px">
              <thead>
                <tr><th style="width: 190px">指标</th><th v-for="b in baselines" :key="b">{{ b }}</th></tr>
              </thead>
              <tbody>
                <tr v-for="(bl, metric) in grouped" :key="metric">
                  <td class="small strong">{{ metric }}</td>
                  <td v-for="b in baselines" :key="b + metric" class="small">
                    <template v-if="bl[b]">
                      <div class="strong">{{ (bl[b].value * 100).toFixed(1) }}%</div>
                      <div class="muted" style="font-size: 10.5px">
                        {{ bl[b].n ?? '—' }}/{{ bl[b].d ?? '—' }}<span v-if="bl[b].ci"> · CI {{ bl[b].ci }}</span>
                      </div>
                    </template>
                    <span v-else class="muted">—</span>
                  </td>
                </tr>
                <tr v-if="Object.keys(grouped).length === 0"><td :colspan="baselines.length + 1"><div class="empty">无指标</div></td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="selected.blocked_baselines?.length" class="card">
          <div class="card-head"><h2>阻塞基线（不伪造结果）</h2></div>
          <div class="card-body">
            <div v-for="b in selected.blocked_baselines" :key="b.name" class="flex" style="margin-bottom: 6px">
              <span class="badge warn">BLOCKED</span>
              <span class="small strong">{{ b.name }}</span>
              <span class="small muted">{{ b.reason }}</span>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="card"><div class="card-body"><div class="empty">选择实验查看指标</div></div></div>
    </div>
  </div>
</template>
