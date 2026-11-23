<script setup lang="ts">
/** Agent Studio：探索轨迹 / ToolGateway 审计 / 编译候选。所有模型信息来自真实 API。 */
import { computed, onMounted, ref } from 'vue'
import { Bot, ChevronRight, CircleAlert, Cpu, FileCode2, GitBranch, Play, RefreshCw, Search, ShieldCheck } from '@lucide/vue'
import { api, fmtDuration, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import JsonBlock from '../components/JsonBlock.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { ExplorationAction, ExplorationDetail } from '../types'

interface ExplorationRow {
  exploration_id: string
  intent_id: string
  status: string
  action_count: number
  stop_reason: string
  engine?: string
  compiled?: { testcase_id: string; version: number; warnings: string[]; compiler_version: string; prompt_version: string; model_version: string } | null
  created_at: string
}
interface IntentRow { intent_id: string; title?: string; description?: string; review_status?: string }

type Source = 'llm' | 'rule' | 'fallback'

const rows = ref<ExplorationRow[]>([])
const intents = ref<IntentRow[]>([])
const loading = ref(true)
const error = ref('')
const selected = ref<ExplorationDetail | null>(null)
const detailLoading = ref(false)
const busy = ref('')
const filter = ref('')
const statusFilter = ref('ALL')
const chosenIntent = ref('')
const maxSteps = ref(8)
const activeStep = ref(0)

async function load() {
  try {
    const [e, i] = await Promise.all([
      api.get<ExplorationRow[]>('/explorations?workspace_id=default'),
      api.get<IntentRow[]>('/intents?workspace_id=default'),
    ])
    rows.value = e
    intents.value = i
    if (!chosenIntent.value && i.length) chosenIntent.value = i[0].intent_id
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function open(row: ExplorationRow) {
  detailLoading.value = true
  activeStep.value = 0
  try {
    selected.value = await api.get<ExplorationDetail>(`/explorations/${row.exploration_id}`)
  } catch (e) {
    notifyError(e)
  } finally {
    detailLoading.value = false
  }
}

function intentTitle(intentId: string): string {
  return intents.value.find((x) => x.intent_id === intentId)?.title || intentId
}

async function explore() {
  if (!chosenIntent.value) return
  busy.value = 'explore'
  try {
    const doc = await api.post<{ exploration_id: string }>(`/intents/${chosenIntent.value}/explore?max_steps=${maxSteps.value}`)
    notify('探索完成：' + doc.exploration_id.slice(-10))
    await load()
    const row = rows.value.find((r) => r.exploration_id === doc.exploration_id)
    if (row) await open(row)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function compile(explorationId: string) {
  busy.value = 'compile'
  try {
    const doc = await api.post<ExplorationDetail>(`/explorations/${explorationId}/compile`)
    notify('已生成候选用例（PENDING，需人工审核）')
    await load()
    selected.value = doc
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

const filtered = computed(() => rows.value.filter((r) => {
  const q = filter.value.trim().toLowerCase()
  return (statusFilter.value === 'ALL' || r.status === statusFilter.value)
    && (!q || r.exploration_id.toLowerCase().includes(q) || intentTitle(r.intent_id).toLowerCase().includes(q))
}))
const actions = computed<ExplorationAction[]>(() => selected.value?.trace?.action_sequence ?? [])
const currentStep = computed(() => actions.value[activeStep.value] ?? null)
const engine = computed(() => selected.value?.engine || selected.value?.trace?.agent_version || '未记录')
const promptVersion = computed(() => selected.value?.trace?.prompt_version || '—')
const stats = computed(() => selected.value?.llm_stats ?? null)
const llmEnabled = computed(() => String(engine.value).startsWith('llm:'))
const fallbackCount = computed(() => stats.value?.fallback_calls ?? 0)
const llmCallCount = computed(() => stats.value?.llm_calls ?? 0)

function sourceOf(action: ExplorationAction | null): Source {
  if (!action) return 'rule'
  const reason = String(action.reason || '')
  if (reason.startsWith('fallback(rule)') || reason.includes('fallback(rule)')) return 'fallback'
  if (llmEnabled.value && llmCallCount.value > 0 && String(promptVersion.value).startsWith('deepseek:')) return 'llm'
  if (llmEnabled.value && fallbackCount.value > 0 && llmCallCount.value === 0) return 'fallback'
  return 'rule'
}
const sourceLabel: Record<Source, string> = { llm: 'LLM 决策', rule: '规则决策', fallback: '规则兜底' }
const statusTone = (s: string) => (s === 'COMPILED' ? 'pass' : s === 'FAILED' ? 'fail' : s === 'RUNNING' ? 'warn' : 'neutral')
const stepTone = (a: ExplorationAction) => a.gateway === 'denied' || a.error ? 'fail' : a.gateway === 'allowed' ? 'pass' : 'neutral'
const started = computed(() => selected.value?.trace?.started_at || selected.value?.created_at)
const elapsed = computed(() => {
  if (!started.value || !selected.value?.compiled) return null
  const ms = new Date(selected.value.created_at).getTime() - new Date(started.value).getTime()
  return Number.isFinite(ms) && ms >= 0 ? fmtDuration(ms) : null
})

onMounted(load)
</script>

<template>
  <div>
    <PageHeader eyebrow="Agent Studio" title="探索与编译中心"
      subtitle="真实浏览器探索 → ToolGateway 权限检查 → 规则编译器候选。模型信息以 API 返回的 engine / llm_stats 为准，不用 engine 标记代替每次决策的真实性。">
      <template #actions>
        <button class="sm" type="button" :disabled="loading" @click="load"><RefreshCw :size="13" /> 刷新</button>
        <button class="sm primary" type="button" :disabled="busy === 'explore' || !chosenIntent" @click="explore">
          <Play :size="13" /> {{ busy === 'explore' ? '探索中…' : '发起探索' }}
        </button>
      </template>
      <template #meta>
        <div class="toolbar mt">
          <select v-model="chosenIntent" aria-label="选择测试需求" style="max-width: 320px">
            <option value="" disabled>选择测试需求…</option>
            <option v-for="it in intents" :key="it.intent_id" :value="it.intent_id">{{ it.title || it.intent_id }}</option>
          </select>
          <select v-model.number="maxSteps" aria-label="最大步数" style="width: 110px">
            <option :value="4">4 步</option><option :value="8">8 步</option><option :value="12">12 步</option>
          </select>
          <span class="small muted">最多 {{ maxSteps }} 步 · 全部需求可选</span>
        </div>
      </template>
    </PageHeader>

    <AsyncBlock :loading="loading" :error="error">
      <div class="metric-grid m5">
        <MetricCard label="探索记录" :value="rows.length" hint="当前 Workspace" icon="activity" />
        <MetricCard label="LLM 引擎" :value="llmEnabled ? 'DEEPSEEK' : 'RULE'" :hint="engine" :tone="llmEnabled ? 'info' : 'neutral'" icon="layers" />
        <MetricCard label="LLM 调用" :value="llmCallCount" hint="本次探索真实计数" :tone="llmCallCount ? 'pass' : 'neutral'" icon="activity" />
        <MetricCard label="规则兜底" :value="fallbackCount" hint="LLM 失败/预算熔断后" :tone="fallbackCount ? 'warn' : 'neutral'" icon="alert" />
        <MetricCard label="编译候选" :value="selected?.compiled ? 1 : 0" hint="PENDING · 需人工审核" :tone="selected?.compiled ? 'pass' : 'neutral'" icon="shield" />
      </div>

      <div class="agent-studio mt">
        <!-- 左：任务列表 -->
        <section class="panel">
          <div class="panel-head">
            <h2>探索任务 <span class="muted small">({{ filtered.length }})</span></h2>
          </div>
          <div class="panel-body" style="display: grid; gap: 10px">
            <div class="filter-bar">
              <div class="grow"><input v-model="filter" placeholder="搜索探索 ID / 需求" aria-label="搜索探索" /></div>
              <select v-model="statusFilter" aria-label="按状态筛选" style="width: 118px">
                <option value="ALL">全部状态</option><option value="COMPLETED">COMPLETED</option>
                <option value="COMPILED">COMPILED</option><option value="FAILED">FAILED</option>
              </select>
            </div>
            <div class="explore-list">
              <button v-for="r in filtered" :key="r.exploration_id" type="button" class="explore-row"
                :class="{ selected: selected?.exploration_id === r.exploration_id }" @click="open(r)">
                <div class="flex" style="justify-content: space-between">
                  <span class="small strong truncate">{{ intentTitle(r.intent_id) }}</span>
                  <StatusBadge :value="r.status" />
                </div>
                <div class="small mono muted truncate">{{ r.exploration_id }}</div>
                <div class="flex wrap small muted">
                  <span>{{ r.action_count }} 动作</span>
                  <span v-if="r.engine" class="source-tag" :class="String(r.engine).startsWith('llm:') ? 'llm' : 'rule'">{{ r.engine }}</span>
                  <span v-if="r.compiled" class="source-tag">已编译 v{{ r.compiled.version }}</span>
                  <span class="right">{{ fmtTime(r.created_at) }}</span>
                </div>
              </button>
              <div v-if="!filtered.length" class="empty small">暂无探索记录。选择需求并点击「发起探索」。</div>
            </div>
          </div>
        </section>

        <!-- 中：执行轨迹 -->
        <section class="panel">
          <div class="panel-head">
            <h2>Agent 执行轨迹</h2>
            <span class="small muted">{{ actions.length }} 步</span>
          </div>
          <div class="panel-body" style="max-height: 720px; overflow: auto">
            <div v-if="detailLoading" class="state"><span class="spinner" /></div>
            <template v-else-if="selected">
              <div class="run-meta">
                <div><span class="config-label">Engine</span><strong class="mono">{{ engine }}</strong></div>
                <div><span class="config-label">Prompt</span><strong class="mono truncate" :title="promptVersion">{{ promptVersion }}</strong></div>
                <div><span class="config-label">LLM / 兜底</span><strong class="mono">{{ llmCallCount }} / {{ fallbackCount }}</strong></div>
                <div><span class="config-label">停止原因</span><strong class="mono truncate" :title="selected.stop_reason || selected.trace?.stop_reason">{{ selected.stop_reason || selected.trace?.stop_reason || '—' }}</strong></div>
                <div><span class="config-label">耗时</span><strong class="mono">{{ elapsed || '未记录结束时间' }}</strong></div>
              </div>
              <div v-if="selected.error" class="alert-line"><CircleAlert :size="13" /> {{ selected.error }}</div>
              <div v-if="selected.llm_stats?.last_error" class="alert-line warn"><CircleAlert :size="13" /> last_error: {{ selected.llm_stats.last_error }}</div>

              <div class="timeline mt">
                <div v-for="(a, i) in actions" :key="i" class="timeline-item">
                  <div class="timeline-dot" :class="stepTone(a)">{{ i + 1 }}</div>
                  <div class="timeline-body action-item" :class="{ selected: activeStep === i }" @click="activeStep = i">
                    <div class="act-head">
                      <span class="source-tag" :class="sourceOf(a)">{{ sourceLabel[sourceOf(a)] }}</span>
                      <strong class="mono">{{ a.action }}</strong>
                      <span class="badge" :class="a.gateway === 'allowed' ? 'pass' : 'fail'">{{ a.gateway || '未记录' }}</span>
                    </div>
                    <div class="small muted mt" style="margin-top: 4px">{{ a.reason || '—' }}</div>
                    <div v-if="a.error" class="small fail-text mono">{{ a.error }}</div>
                  </div>
                </div>
                <div v-if="!actions.length" class="empty small">轨迹为空：本次探索没有记录动作。</div>
              </div>

              <div class="detail-section mt">
                <div class="sec-title">ToolGateway 审计（{{ selected.gateway_audit?.length || 0 }} 条）</div>
                <JsonBlock :data="selected.gateway_audit ?? []" label="Gateway audit" />
              </div>
            </template>
            <div v-else class="empty">
              <div class="empty-icon"><GitBranch :size="20" /></div>
              <div class="title">选择一次探索</div>
              <div class="small">从左侧列表选择记录，查看真实执行轨迹与模型决策来源。</div>
            </div>
          </div>
        </section>

        <!-- 右：步骤详情 + 编译产物 -->
        <section class="panel">
          <div class="panel-head">
            <h2>步骤详情与编译</h2>
            <button v-if="selected && actions.length" class="sm primary right" type="button"
              :disabled="busy === 'compile'" @click="compile(selected.exploration_id)">
              {{ busy === 'compile' ? '编译中…' : selected.compiled ? '重新编译' : '编译候选用例' }}
            </button>
          </div>
          <div class="panel-body" style="max-height: 720px; overflow: auto">
            <template v-if="currentStep">
              <div class="section-title">步骤 {{ activeStep + 1 }} · {{ currentStep.action }}</div>
              <div class="kv">
                <dt>决策来源</dt><dd><span class="source-tag" :class="sourceOf(currentStep)">{{ sourceLabel[sourceOf(currentStep)] }}</span></dd>
                <dt>参数</dt><dd><code class="code-inline">{{ JSON.stringify(currentStep.params || {}) }}</code></dd>
                <dt>Reason</dt><dd class="mono">{{ currentStep.reason || '—' }}</dd>
                <dt>结果</dt><dd class="mono">{{ currentStep.result || '—' }}</dd>
                <dt>Gateway</dt><dd><StatusBadge :value="currentStep.gateway === 'denied' ? 'DENIED' : currentStep.gateway === 'allowed' ? 'ALLOWED' : 'NOT_EVALUATED'" /></dd>
                <dt>错误</dt><dd class="mono fail-text">{{ currentStep.error || '—' }}</dd>
                <dt>页面状态</dt><dd class="mono">{{ (currentStep.page_state_refs || []).join(', ') || selected?.trace?.page_state_refs?.[activeStep] || '—' }}</dd>
                <dt>关联证据</dt><dd class="mono">{{ (currentStep.evidence_refs || []).join(', ') || '—' }}</dd>
              </div>
            </template>
            <div v-else class="empty small">点击中间轨迹中的步骤查看详情。</div>

            <div class="detail-section mt" v-if="selected?.compiled">
              <div class="sec-title">编译产物（规则编译器）</div>
              <div class="kv">
                <dt>候选用例</dt><dd><router-link :to="`/testcases/${selected.compiled.testcase_id}`" class="mono">{{ selected.compiled.testcase_id }}</router-link> · PENDING</dd>
                <dt>Compiler</dt><dd class="mono">{{ selected.compiled.compiler_version }}</dd>
                <dt>Model Version</dt><dd class="mono">{{ selected.compiled.model_version }}</dd>
                <dt>Prompt</dt><dd class="mono">{{ selected.compiled.prompt_version }}</dd>
              </div>
              <div v-if="selected.compiled.warnings?.length" class="warning-box mt">
                <div class="small strong">编译 Warning</div>
                <div v-for="w in selected.compiled.warnings" :key="w" class="small mono">· {{ w }}</div>
              </div>
            </div>
            <div v-else-if="selected" class="empty small">尚未编译。编译产物是 PENDING 候选，必须经人工审核才能成为正式用例。</div>

            <div class="detail-section mt">
              <div class="sec-title">模型信息边界</div>
              <p class="small muted">
                <Cpu :size="12" /> engine 标记规划器配置，不代表每个动作都由 LLM 决策。
                只有 <b>llm_calls &gt; 0</b> 且 prompt 版本来自 deepseek 的动作标记为 LLM 决策；
                fallback(rule) 或 llm_calls=0 的动作标记为规则兜底。Compiler 保持规则实现。
              </p>
            </div>
          </div>
        </section>
      </div>
    </AsyncBlock>
  </div>
</template>
