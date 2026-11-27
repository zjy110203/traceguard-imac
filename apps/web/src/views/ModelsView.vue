<script setup lang="ts">
/** 模型运行中心：全部数据来自 GET /api/v1/system/llm/summary，只读、脱敏。 */
import { computed, onMounted, ref } from 'vue'
import { Activity, CircleAlert, Clock3, Cpu, Database, Info, RotateCcw, Wallet } from '@lucide/vue'
import { endpoints, fmtNumber, fmtPercent, fmtTime, fmtTokens, fmtUsd } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import BarChart from '../components/BarChart.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import TrendChart from '../components/TrendChart.vue'
import type { LLMUsageSummary } from '../types'
import { notifyError } from '../toast'

const data = ref<LLMUsageSummary | null>(null)
const loading = ref(true)
const error = ref('')

async function load() {
  try {
    data.value = await endpoints.llmSummary()
    error.value = ''
  } catch (e) {
    // RUN-014 WP3：全局模型运行摘要仅 platform-admin（system.observe）可读
    const status = (e as { status?: number })?.status
    error.value =
      status === 403
        ? '需要 platform-admin 的 system.observe 权限才能查看全局模型运行摘要'
        : e instanceof Error
          ? e.message
          : String(e)
    notifyError(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

const hasData = computed(() => !!data.value?.data_available)
const days = computed(() => data.value?.daily_series ?? [])
const trendSeries = computed(() => [
  { label: '调用次数', color: 'var(--brand)', values: days.value.map((d) => d.calls) },
  { label: '失败请求', color: 'var(--fail)', values: days.value.map((d) => d.errors) },
])
const byAgent = computed(() => Object.entries(data.value?.by_agent ?? {}).map(([label, v]) => ({ label, value: v.calls, hint: `${fmtTokens(v.input_tokens)} in` })))
const byModel = computed(() => Object.entries(data.value?.by_model ?? {}).map(([label, v]) => ({
  label: label.includes('pro') ? `${label} · Pro` : label.includes('flash') ? `${label} · Flash` : label,
  value: v.calls,
  color: label.includes('pro') ? 'var(--incon)' : 'var(--brand)',
})))
const budgetPct = computed(() => {
  const d = data.value
  if (!d?.budget_usd || d.estimated_cost_usd === null) return null
  return Math.min(1, d.estimated_cost_usd / d.budget_usd)
})
const successRate = computed(() => {
  const d = data.value
  return d && d.calls ? (d.calls - d.errors) / d.calls : null
})
</script>

<template>
  <div>
    <PageHeader
      eyebrow="Observability / Model Gateway"
      title="模型运行中心"
      subtitle="真实 LLM 调用、Token、成本与决策来源监控。成本为应用侧按牌价估算，不是 DeepSeek 平台账单。"
    >
      <template #actions>
        <button class="sm" type="button" @click="load">刷新</button>
      </template>
    </PageHeader>

    <AsyncBlock :loading="loading" :error="error" :empty="!hasData" empty-title="暂无模型调用数据"
      empty-hint="当前没有任何 LLM 调用记录。配置 DeepSeek 并运行探索/诊断后，这里将显示真实统计。">
      <template v-if="data">
        <section class="config-strip panel">
          <div class="config-item">
            <span class="config-label">Provider</span>
            <strong class="mono">{{ data.provider }}</strong>
            <span class="small muted">配置状态</span>
          </div>
          <div class="config-item">
            <span class="config-label">默认模型</span>
            <strong class="mono">{{ data.default_model }}</strong>
            <span class="small muted">{{ data.configured ? '已配置' : '未配置 · 规则模式' }}</span>
          </div>
          <div class="config-item">
            <span class="config-label">推理模型</span>
            <strong class="mono">{{ data.reasoning_model }}</strong>
            <span class="small muted">复杂任务升级</span>
          </div>
          <div class="config-item">
            <span class="config-label">连通性</span>
            <strong>{{ data.connected === 'unknown' ? '未探测' : data.connected }}</strong>
            <span class="small muted">只读摘要不触发外部请求</span>
          </div>
          <div class="config-item">
            <span class="config-label">统计日期</span>
            <strong class="mono">{{ data.date }}</strong>
            <span class="small muted">更新于 {{ fmtTime(data.generated_at) }}</span>
          </div>
        </section>

        <div class="metric-grid m5">
          <MetricCard label="今日调用" :value="fmtNumber(data.calls)" hint="按 UTC 自然日" tone="info" icon="activity" />
          <MetricCard label="失败请求" :value="fmtNumber(data.errors)" :hint="`成功率 ${fmtPercent(successRate)}`" :tone="data.errors ? 'fail' : 'pass'" icon="alert" />
          <MetricCard label="Input Tokens" :value="fmtTokens(data.input_tokens)" hint="今日累计输入" icon="layers" />
          <MetricCard label="Output Tokens" :value="fmtTokens(data.output_tokens)" hint="今日累计输出" icon="activity" />
          <MetricCard label="估算成本" :value="fmtUsd(data.estimated_cost_usd)" :hint="`预算 ${fmtUsd(data.budget_usd)} · 应用侧估算`" :tone="data.budget_exceeded ? 'fail' : 'warn'" icon="shield" />
        </div>

        <div v-if="budgetPct !== null" class="panel budget-panel">
          <div class="panel-head"><h2>应用侧预算使用率</h2><span class="small muted">{{ (budgetPct * 100).toFixed(1) }}% · {{ fmtUsd(data.estimated_cost_usd) }} / {{ fmtUsd(data.budget_usd) }}</span></div>
          <div class="panel-body">
            <div class="progress-track"><span :style="{ width: `${budgetPct * 100}%`, background: data.budget_exceeded ? 'var(--fail)' : 'var(--brand)' }" /></div>
            <p class="small muted"><Info :size="12" /> 预算是应用侧目标，不是平台硬性封顶；超出后新的非必要调用会被跳过并转规则兜底。</p>
          </div>
        </div>

        <div class="grid c2 mt">
          <section class="panel">
            <div class="panel-head"><h2>每日调用趋势</h2><span class="small muted">真实时间分桶 · n={{ days.length }} 天</span></div>
            <div class="panel-body"><TrendChart :series="trendSeries" :labels="days.map((d) => d.date || '')" :height="180" /></div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>决策来源</h2><span class="small muted">来自探索/诊断文档</span></div>
            <div class="panel-body decision-grid">
              <div><span class="small muted">LLM 成功调用</span><strong>{{ data.decisions.llm_calls_recorded }}</strong></div>
              <div><span class="small muted">规则兜底次数</span><strong class="warn-text">{{ data.decisions.fallback_calls }}</strong></div>
              <div><span class="small muted">规则规划探索</span><strong>{{ data.decisions.rule_explorations }}</strong></div>
              <div><span class="small muted">诊断 LLM 顾问已调用 / 跳过</span><strong>{{ data.decisions.diagnosis_advisor_called }} / {{ data.decisions.diagnosis_advisor_skipped }}</strong></div>
              <p class="small muted full"><CircleAlert :size="12" /> {{ data.decisions.note }}</p>
            </div>
          </section>
        </div>

        <div class="grid c2 mt">
          <section class="panel">
            <div class="panel-head"><h2>按 Agent 分布</h2><span class="small muted">今日调用</span></div>
            <div class="panel-body"><BarChart :items="byAgent" /></div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>按模型分布</h2><span class="small muted">Flash / Pro 路由</span></div>
            <div class="panel-body"><BarChart :items="byModel" /></div>
          </section>
        </div>

        <div class="grid c2 mt">
          <section class="panel">
            <div class="panel-head"><h2>延迟与重试</h2><span class="small muted">样本 n={{ data.latency_sample }}</span></div>
            <div class="panel-body kv">
              <div class="kvt">平均延迟</div><div class="kvd mono">{{ data.avg_latency_ms }} ms</div>
              <div class="kvt">P95 延迟</div><div class="kvd mono">{{ data.p95_latency_ms }} ms</div>
              <div class="kvt">重试次数</div><div class="kvd mono">{{ data.retries }}</div>
              <div class="kvt">Prompt 版本</div><div class="kvd mono">{{ Object.keys(data.prompt_versions).join(', ') || '—' }}</div>
            </div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>最近调用记录</h2><span class="small muted">仅安全元数据</span></div>
            <div class="panel-body tight scroll-x" tabindex="0">
              <table>
                <thead><tr><th>时间</th><th>Agent</th><th>模型</th><th>状态</th><th>延迟</th><th>Tokens</th></tr></thead>
                <tbody>
                  <tr v-for="(c, i) in data.recent_calls" :key="i">
                    <td class="small mono nowrap">{{ fmtTime(c.ts) }}</td>
                    <td class="small">{{ c.agent }}</td>
                    <td class="small mono">{{ c.model }}</td>
                    <td class="small"><span :class="c.request_status === 'ok' ? 'ok-text' : 'fail-text'">{{ c.request_status }}</span><span v-if="c.error_kind" class="muted"> · {{ c.error_kind }}</span></td>
                    <td class="small mono">{{ c.latency_ms }}ms</td>
                    <td class="small mono">{{ fmtTokens(c.input_tokens) }}/{{ fmtTokens(c.output_tokens) }}</td>
                  </tr>
                  <tr v-if="!data.recent_calls.length"><td colspan="6"><div class="empty small">暂无调用记录</div></td></tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <p class="data-note small muted"><Wallet :size="12" /> {{ data.note }}</p>
      </template>
    </AsyncBlock>
  </div>
</template>
