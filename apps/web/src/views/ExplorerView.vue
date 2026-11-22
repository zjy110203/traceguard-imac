<script setup lang="ts">
/** Agent 探索与编译中心：Explorer 轨迹 / ToolGateway 审计 / Compiler 候选。
 *  诚实标注：当前规划器为规则实现（rule-based-v1），不是 LLM。 */
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import JsonBlock from '../components/JsonBlock.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'

interface ExplorationRow {
  exploration_id: string
  intent_id: string
  status: string
  action_count: number
  stop_reason: string
  compiled?: { testcase_id: string; version: number; warnings: string[]; compiler_version: string; prompt_version: string; model_version: string } | null
  created_at: string
}

interface ActionItem {
  action: string
  params: Record<string, unknown>
  result?: string
  reason?: string
  gateway: string
  error?: string
}

interface ExplorationDetail extends ExplorationRow {
  workspace_id: string
  error: string
  trace: {
    exploration_id: string
    agent_version?: string
    prompt_version?: string
    tool_version?: string
    browser_version?: string
    application_version?: string
    stop_reason?: string
    action_sequence: ActionItem[]
    page_state_refs: string[]
  }
  gateway_audit: Record<string, unknown>[]
}

interface IntentRow {
  intent_id: string
  title?: string
  description?: string
  review_status?: string
}

const rows = ref<ExplorationRow[]>([])
const intents = ref<IntentRow[]>([])
const loading = ref(true)
const error = ref('')
const selected = ref<ExplorationDetail | null>(null)
const detailLoading = ref(false)
const busy = ref('')

async function load() {
  try {
    const [e, i] = await Promise.all([
      api.get<ExplorationRow[]>('/explorations?workspace_id=default'),
      api.get<IntentRow[]>('/intents?workspace_id=default'),
    ])
    rows.value = e
    intents.value = i
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function open(row: ExplorationRow) {
  detailLoading.value = true
  try {
    selected.value = await api.get<ExplorationDetail>(`/explorations/${row.exploration_id}`)
  } catch (e) {
    notifyError(e)
  } finally {
    detailLoading.value = false
  }
}

function intentTitle(intentId: string): string {
  const it = intents.value.find((x) => x.intent_id === intentId)
  return it?.title || intentId
}

async function explore(intentId: string) {
  busy.value = 'explore:' + intentId
  try {
    const doc = await api.post<{ exploration_id: string }>(`/intents/${intentId}/explore?max_steps=8`)
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
  busy.value = 'compile:' + explorationId
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

const statusTone = (s: string) => (s === 'COMPILED' ? 'pass' : s === 'FAILED' ? 'fail' : s === 'COMPLETED' ? 'info' : 'neutral')

const auditPairs = computed(() => {
  const a = selected.value?.gateway_audit ?? []
  return a.slice(0, 30)
})

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>Agent 探索与编译</h1>
        <div class="sub">
          Explorer 真实浏览器探索 → Compiler 候选用例。
          当前规划器：<b>规则实现（rule-based-v1）</b>，不是 LLM；接入真实模型后此处将展示 model/prompt 版本。
        </div>
      </div>
    </div>

    <AsyncBlock :loading="loading" :error="error">
      <div class="master-detail">
        <div class="card">
          <div class="card-head">
            <h2>探索记录（{{ rows.length }}）</h2>
          </div>
          <div class="card-body tight scroll-x">
            <table>
              <thead>
                <tr><th>探索 ID</th><th>测试需求</th><th>状态</th><th>动作</th><th>停止原因</th><th>编译产物</th><th>时间</th></tr>
              </thead>
              <tbody>
                <tr
                  v-for="r in rows"
                  :key="r.exploration_id"
                  class="row-click"
                  :class="{ selected: selected?.exploration_id === r.exploration_id }"
                  @click="open(r)"
                >
                  <td class="mono small">{{ r.exploration_id.slice(-14) }}</td>
                  <td class="small truncate" style="max-width: 220px">{{ intentTitle(r.intent_id) }}</td>
                  <td><span class="badge" :class="statusTone(r.status)">{{ r.status }}</span></td>
                  <td class="small">{{ r.action_count }}</td>
                  <td class="small mono truncate" style="max-width: 180px">{{ r.stop_reason || '—' }}</td>
                  <td class="small">
                    <template v-if="r.compiled">
                      <router-link :to="`/testcases/${r.compiled.testcase_id}`" @click.stop>
                        v{{ r.compiled.version }} ↗
                      </router-link>
                    </template>
                    <span v-else class="muted">—</span>
                  </td>
                  <td class="small muted nowrap">{{ fmtTime(r.created_at) }}</td>
                </tr>
                <tr v-if="rows.length === 0">
                  <td colspan="7"><div class="empty">暂无探索记录。在下方选择测试需求发起探索。</div></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="flex-col">
          <div class="card">
            <div class="card-head"><h2>发起探索</h2></div>
            <div class="card-body flex-col">
              <div v-for="it in intents.slice(0, 6)" :key="it.intent_id" class="flex" style="justify-content: space-between">
                <div style="min-width: 0">
                  <div class="truncate small strong">{{ it.title || it.intent_id }}</div>
                  <div class="muted" style="font-size: 11px">{{ it.intent_id }}</div>
                </div>
                <button class="sm" :disabled="busy === 'explore:' + it.intent_id" @click="explore(it.intent_id)">
                  {{ busy === 'explore:' + it.intent_id ? '探索中…' : '探索' }}
                </button>
              </div>
              <div v-if="intents.length === 0" class="empty">暂无测试需求，请先到「测试需求」创建</div>
            </div>
          </div>

          <div class="card" v-if="selected">
            <div class="card-head">
              <h2>探索详情</h2>
              <div class="actions">
                <span class="badge" :class="statusTone(selected.status)">{{ selected.status }}</span>
                <button
                  v-if="selected.trace?.action_sequence?.length && !selected.compiled"
                  class="sm primary"
                  :disabled="busy === 'compile:' + selected.exploration_id"
                  @click="compile(selected.exploration_id)"
                >
                  编译候选用例
                </button>
              </div>
            </div>
            <div class="card-body" style="max-height: 560px; overflow: auto">
              <div v-if="detailLoading" class="state"><span class="spinner" /></div>
              <template v-else>
                <div class="detail-section">
                  <div class="sec-title">版本与边界</div>
                  <div class="kv">
                    <dt>Agent 版本</dt><dd class="mono">{{ selected.trace?.agent_version || '—' }}</dd>
                    <dt>Prompt 版本</dt><dd class="mono">{{ selected.trace?.prompt_version || '—' }}（规则实现）</dd>
                    <dt>工具网关</dt><dd class="mono">{{ selected.trace?.tool_version || '—' }}</dd>
                    <dt>停止原因</dt><dd class="mono">{{ selected.trace?.stop_reason || selected.stop_reason || '—' }}</dd>
                  </div>
                  <div v-if="selected.error" class="small mono" style="color: var(--fail); margin-top: 6px">{{ selected.error }}</div>
                </div>

                <div class="detail-section">
                  <div class="sec-title">动作序列（{{ selected.trace?.action_sequence?.length ?? 0 }}）</div>
                  <div v-for="(a, i) in selected.trace?.action_sequence ?? []" :key="i" class="action-item">
                    <div class="act-head">
                      <span class="badge" :class="a.gateway === 'allowed' ? 'pass' : 'fail'">{{ a.gateway }}</span>
                      <span class="mono strong">{{ a.action }}</span>
                      <span v-if="a.params?.test_id" class="code-inline">{{ a.params.test_id }}</span>
                    </div>
                    <div v-if="a.reason" class="small muted" style="margin-top: 3px">理由：{{ a.reason }}</div>
                    <div v-if="a.result" class="small mono muted" style="margin-top: 2px">→ {{ a.result }}</div>
                    <div v-if="a.error" class="small mono" style="margin-top: 2px; color: var(--fail)">✗ {{ a.error }}</div>
                  </div>
                  <div v-if="!selected.trace?.action_sequence?.length" class="empty">轨迹为空</div>
                </div>

                <div class="detail-section">
                  <div class="sec-title">ToolGateway 审计（{{ auditPairs.length }} 条）</div>
                  <JsonBlock :data="selected.gateway_audit" />
                </div>

                <div class="detail-section" v-if="selected.compiled">
                  <div class="sec-title">编译产物</div>
                  <div class="kv">
                    <dt>候选用例</dt>
                    <dd><router-link :to="`/testcases/${selected.compiled.testcase_id}`" class="mono">{{ selected.compiled.testcase_id }}</router-link>（PENDING，需人工审核）</dd>
                    <dt>Compiler</dt><dd class="mono">{{ selected.compiled.compiler_version }}</dd>
                    <dt>Prompt</dt><dd class="mono">{{ selected.compiled.prompt_version }}</dd>
                  </div>
                  <div v-if="selected.compiled.warnings?.length" style="margin-top: 8px">
                    <div class="small" style="color: var(--warn); font-weight: 550">诚实警告（诚实标注生成局限）</div>
                    <div v-for="w in selected.compiled.warnings" :key="w" class="small mono" style="color: var(--warn); margin-top: 2px">· {{ w }}</div>
                  </div>
                </div>
              </template>
            </div>
          </div>
          <div class="card" v-else>
            <div class="card-body"><div class="empty">点击左侧记录查看探索轨迹与审计</div></div>
          </div>
        </div>
      </div>
    </AsyncBlock>
  </div>
</template>
