<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtDuration, fmtTime } from '../api'
import { currentWorkspaceId, wsPath } from '../workspace'
import AsyncBlock from '../components/AsyncBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Run, TestCaseDetail, TestCaseVersion } from '../types'

interface OracleOption {
  oracle_id: string
  version: number
  assertion_id: string
  approved: boolean
  expected_behavior: string
}

interface ReplaySample {
  run_id: string
  execution_status: string
  test_verdict: string
  failure_class?: string
  browser_version?: string
}

interface StabilityReplay {
  replay_id: string
  testcase_version_ref: string
  status: string
  samples_recorded?: number
  samples_expected?: number
  records?: ReplaySample[]
  conclusion?: string | null
  classification?: { classification?: string; pass_count?: number; fail_count?: number } | null
  promotable?: boolean
  oracle_complete?: boolean
  oracle_problems?: string[]
  oracle_versions?: Record<string, string>
  browser_versions?: string[]
  blocked_reason?: string
  created_at?: string
  completed_at?: string | null
  requires_human_review?: boolean
}

interface GatePreview {
  satisfied: boolean
  problems: string[]
  evidence?: { replay_id?: string } | null
}

const route = useRoute()
const router = useRouter()
const id = String(route.params.id)
const detail = ref<TestCaseDetail | null>(null)
const versions = ref<(TestCaseVersion & { immutable: boolean })[]>([])
const runs = ref<Run[]>([])
const oracles = ref<OracleOption[]>([])
const bindingDraft = ref<Record<string, string>>({})
const loading = ref(true)
const error = ref('')
const busy = ref('')
const activeVersion = ref<number | null>(null)
// RUN-013 WP3：稳定性回放状态面板（状态/进度/样本/结论/Oracle/审核条件）
const replay = ref<StabilityReplay | null>(null)
const replayGate = ref<GatePreview | null>(null)
const replayLoading = ref(false)
let replayTimer: number | undefined

const replayCounts = computed(() => {
  const records = replay.value?.records ?? []
  return {
    passed: records.filter((r) => r.test_verdict === 'PASSED').length,
    failed: records.filter((r) => r.test_verdict === 'FAILED').length,
    pending: records.filter(
      (r) => !['COMPLETED', 'FAILED', 'TIMED_OUT', 'CANCELLED'].includes(r.execution_status),
    ).length,
  }
})

async function loadReplay(): Promise<void> {
  const v = current.value
  if (!v) {
    replay.value = null
    replayGate.value = null
    return
  }
  if (replayTimer !== undefined) {
    window.clearTimeout(replayTimer)
    replayTimer = undefined
  }
  replayLoading.value = true
  try {
    const rows = await api.get<{ replay_id: string; testcase_ref: string }[]>(
      `/stability-replays?testcase_id=${encodeURIComponent(id)}&limit=20`,
    )
    const mine = rows.find((r) => r.testcase_ref === `${id}@${v.version}`)
    if (!mine) {
      replay.value = null
      replayGate.value = null
      return
    }
    replay.value = await api.get<StabilityReplay>(
      `/stability-replays/${mine.replay_id}?refresh=true`,
    )
    replayGate.value = await api
      .get<GatePreview>(`/testcases/${id}/versions/${v.version}/stability-gate`)
      .catch(() => null)
    // 回放运行中：每 2 秒吸收一次真实 Run 状态（服务端 refresh 后再判定）。
    if (replay.value?.status === 'RUNNING') {
      replayTimer = window.setTimeout(() => void loadReplay(), 2000)
    }
  } catch {
    // 回放是辅助信息，不阻断页面主体；错误由审核条件显示为"未满足"。
    replay.value = null
    replayGate.value = null
  } finally {
    replayLoading.value = false
  }
}

const current = computed<TestCaseVersion | null>(() => {
  const latest = detail.value?.latest ?? null
  if (!activeVersion.value) return latest
  const found = versions.value.find((v) => v.version === activeVersion.value)
  if (found) return found
  // 列表里没有该版本时回退到服务端返回的最新版本（结构一致：断言/步骤在顶层）
  return latest
})

async function load() {
  loading.value = true
  try {
    const [d, v, r, o] = await Promise.all([
      api.get<TestCaseDetail>(`/testcases/${id}`),
      api.get<(TestCaseVersion & { immutable: boolean })[]>(`/testcases/${id}/versions`),
      api.get<Run[]>('/runs?limit=200'),
      api.get<OracleOption[]>(wsPath('/oracles')).catch(() => [] as OracleOption[]),
    ])
    detail.value = d
    // 版本列表接口返回 {testcase_id, version, review_status, immutable, doc:{...}}，
    // 断言/步骤在 doc 内；不展开的话断言表格与 Oracle 绑定控件全部不渲染。
    versions.value = v.map((row) => {
      const doc = (row as unknown as { doc?: Partial<TestCaseVersion> }).doc ?? {}
      return { ...doc, ...row } as TestCaseVersion & { immutable: boolean }
    })
    runs.value = r.filter((x) => String(x.testcase_version_id).split('@')[0] === id)
    oracles.value = o
    if (!activeVersion.value) activeVersion.value = d.latest_version
    initBindingDraft()
    await loadReplay()
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function initBindingDraft() {
  const cur = current.value
  if (!cur) return
  const draft: Record<string, string> = {}
  for (const a of cur.assertions || []) {
    draft[a.assertion_id] = a.oracle_id || ''
  }
  bindingDraft.value = draft
}

function oracleOptionsFor(assertionId: string): OracleOption[] {
  return oracles.value.filter((o) => o.assertion_id === assertionId)
}

async function saveBindings() {
  const cur = current.value
  if (!cur) return
  const bindings: Record<string, { oracle_id: string }> = {}
  for (const a of cur.assertions || []) {
    const chosen = bindingDraft.value[a.assertion_id]
    if (chosen) bindings[a.assertion_id] = { oracle_id: chosen }
  }
  if (!Object.keys(bindings).length) {
    notify('请先为必需断言选择 Oracle', true)
    return
  }
  busy.value = 'bind'
  try {
    await api.post(`/testcases/${id}/versions/${cur.version}/oracle-bindings`, {
      bindings,
      actor: 'role:qa-lead',
    })
    notify('Oracle 绑定已保存')
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function execute() {
  busy.value = 'exec'
  try {
    const r = await api.post<{ run_id: string }>(`/testcases/${id}/execute`, { sync: false })
    notify('已入队执行')
    router.push(`/runs/${r.run_id}`)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function startStabilityReplay() {
  const v = current.value
  if (!v) return
  busy.value = 'replay'
  try {
    const r = await api.post<{ replay_id: string; status: string }>(
      `/testcases/${id}/versions/${v.version}/stability-replay?repeats=3`,
    )
    notify(`稳定性回放已启动：${r.replay_id}`)
    await loadReplay()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function review(decision: string) {
  const v = current.value
  if (!v) return
  busy.value = 'review'
  try {
    await api.post(`/testcases/${id}/versions/${v.version}/review`, {
      decision,
      reviewer: 'role:qa-lead',
      note: '控制台审核',
    })
    notify('审核完成：' + decision)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

function targetText(t: any): string {
  if (!t) return '—'
  return `${t.strategy}:${t.value}`
}

onMounted(load)
onUnmounted(() => {
  if (replayTimer !== undefined) window.clearTimeout(replayTimer)
})
</script>

<template>
  <div>
    <AsyncBlock :loading="loading" :error="error">
      <div v-if="detail">
        <PageHeader eyebrow="Catalog / Test Case" :title="detail.title"
          :subtitle="`${detail.testcase_id} · 来源 ${detail.compile_source} · 关联需求 ${detail.intent_ref || '—'}`">
          <template #actions>
            <button @click="router.push('/testcases')">返回列表</button>
            <button class="primary" :disabled="detail.review_status !== 'APPROVED' || busy === 'exec'" @click="execute">执行当前版本</button>
          </template>
          <template #meta>
            <div class="flex wrap mt">
              <StatusBadge :value="detail.review_status" />
              <span class="badge neutral">最新版本 v{{ detail.latest_version }}</span>
              <span class="badge neutral">{{ detail.step_count }} 步 / {{ detail.assertion_count }} 断言</span>
            </div>
          </template>
        </PageHeader>

        <div class="split">
          <div class="flex-col">
            <div class="card">
              <div class="card-head">
                <h2>版本</h2>
                <div class="actions">
                  <span class="small muted">不可变版本，审核状态可流转</span>
                </div>
              </div>
              <div class="card-body tight scroll-x">
                <table>
                  <thead><tr><th>版本</th><th>状态</th><th>来源</th><th>时间</th><th></th></tr></thead>
                  <tbody>
                    <tr v-for="v in versions" :key="v.version + '-' + v.created_at">
                      <td class="strong">v{{ v.version }} <span v-if="v.version === detail.latest_version" class="badge info">latest</span></td>
                      <td><StatusBadge :value="v.review_status" /></td>
                      <td class="small">{{ v.compile_source }}</td>
                      <td class="small muted nowrap">{{ fmtTime(v.created_at) }}</td>
                      <td><button class="sm ghost" @click="activeVersion = v.version">查看</button></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div v-if="current" class="card">
              <div class="card-head">
                <h2>步骤轨迹（v{{ current.version }}）</h2>
                <div class="actions">
                  <span class="small muted">cleanup: {{ current.cleanup_policy }}</span>
                </div>
              </div>
              <div class="card-body tight scroll-x">
                <table>
                  <thead><tr><th style="width: 44px">#</th><th>动作</th><th>定位 / 值</th><th>说明</th></tr></thead>
                  <tbody>
                    <tr v-for="(s, i) in current.steps" :key="s.step_id">
                      <td class="muted small">{{ i + 1 }}</td>
                      <td><span class="badge neutral">{{ s.action }}</span></td>
                      <td class="mono small">
                        <div v-if="s.target">{{ targetText(s.target) }}</div>
                        <div v-if="s.value" style="color: var(--text-2)">value: {{ s.value }}</div>
                        <span v-if="!s.target && !s.value">—</span>
                      </td>
                      <td class="small muted">{{ s.description || '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div v-if="current" class="card">
              <div class="card-head">
                <h2>断言与 Oracle 绑定</h2>
                <div class="actions">
                  <button class="sm primary"
                    :disabled="busy === 'bind' || current.review_status === 'APPROVED'"
                    @click="saveBindings">保存绑定</button>
                </div>
              </div>
              <div class="card-body tight scroll-x">
                <table>
                  <thead><tr><th>断言</th><th>类型</th><th>必需</th><th>期望</th><th>Oracle</th></tr></thead>
                  <tbody>
                    <tr v-for="a in current.assertions" :key="a.assertion_id">
                      <td>
                        <div class="strong small">{{ a.description }}</div>
                        <div class="mono muted" style="font-size: 10.5px">{{ a.assertion_id }}</div>
                      </td>
                      <td><span class="badge neutral">{{ a.kind }}</span></td>
                      <td class="small">{{ a.required ? '是' : '否' }}</td>
                      <td class="mono small">{{ JSON.stringify(a.expected) }}</td>
                      <td>
                        <select v-if="current.review_status !== 'APPROVED'"
                          v-model="bindingDraft[a.assertion_id]" class="sm" style="max-width: 220px">
                          <option value="">未绑定</option>
                          <option v-for="o in oracleOptionsFor(a.assertion_id)" :key="o.oracle_id + '@' + o.version"
                            :value="o.oracle_id">
                            {{ o.oracle_id }}@v{{ o.version }}{{ o.approved ? '' : '（未审批）' }}
                          </option>
                        </select>
                        <span v-else class="mono small">{{ a.oracle_id || '—' }}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <p v-if="current.review_status !== 'APPROVED'" class="small muted" style="margin-top: 8px">
                  审核通过前必须为必需断言绑定<b>已审批</b> Oracle（门禁：ORACLE_BINDING_INCOMPLETE）。已 APPROVED 的版本内容不可变，需新建版本再改绑定。
                </p>
              </div>
            </div>
          </div>

          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>审核</h2></div>
              <div class="card-body">
                <div class="kv">
                  <dt>当前状态</dt><dd><StatusBadge :value="detail.review_status" /></dd>
                  <dt>最新版本</dt><dd>v{{ detail.latest_version }}</dd>
                  <dt>运行配置</dt><dd class="mono small">{{ JSON.stringify(current?.run_config ?? {}) }}</dd>
                </div>
                <div class="hr" />
                <p class="small muted">审核人必须是具名人类角色（Agent 不能自审）。审核通过后才允许正式执行。</p>
                <div class="flex" style="margin-top: 10px">
                  <button class="sm primary" :disabled="busy === 'review'" @click="review('APPROVED')">审核通过</button>
                  <button class="sm danger" :disabled="busy === 'review'" @click="review('REJECTED')">拒绝</button>
                  <button class="sm" :disabled="busy === 'replay'" @click="startStabilityReplay">启动稳定性回放</button>
                </div>
                <p class="small muted" style="margin-top: 6px">
                  编译来源为 compiler/healed 的版本，审核通过前必须有「同版本、STABLE_PASS、Oracle 完整」的稳定性回放记录。
                </p>
              </div>
            </div>

            <div class="card">
              <div class="card-head">
                <h2>稳定性回放</h2>
                <div class="actions">
                  <button class="sm ghost" :disabled="replayLoading" @click="loadReplay">刷新</button>
                </div>
              </div>
              <div class="card-body">
                <p v-if="!replay" class="small muted">
                  当前版本尚无稳定性回放。compiler/healed 候选必须先完成同版本回放，才能通过审核门禁。
                </p>
                <template v-else>
                  <div class="kv">
                    <dt>回放状态</dt>
                    <dd><StatusBadge :value="replay.status" /></dd>
                    <dt>样本进度</dt>
                    <dd>{{ replay.samples_recorded ?? 0 }} / {{ replay.samples_expected ?? 0 }}</dd>
                    <dt>成功 / 失败</dt>
                    <dd>
                      通过 {{ replayCounts.passed }} · 失败 {{ replayCounts.failed }} ·
                      未完成 {{ replayCounts.pending }}
                    </dd>
                    <dt>稳定性结论</dt>
                    <dd>
                      {{ replay.classification?.classification || replay.conclusion || '尚未形成' }}
                    </dd>
                    <dt>可晋升</dt>
                    <dd>{{ replay.promotable ? '是（仍需人工审核）' : '否' }}</dd>
                    <dt>关联 Oracle</dt>
                    <dd class="mono small">
                      {{ Object.keys(replay.oracle_versions || {}).length
                        ? JSON.stringify(replay.oracle_versions)
                        : '—' }}
                    </dd>
                    <dt>Oracle 完整性</dt>
                    <dd>
                      {{ replay.oracle_complete ? '完整' : '不完整' }}
                      <span v-if="replay.oracle_problems?.length" class="small muted">
                        （{{ replay.oracle_problems.join('；') }}）
                      </span>
                    </dd>
                    <dt>浏览器</dt>
                    <dd class="small">{{ (replay.browser_versions || []).join('、') || '—' }}</dd>
                    <dt>报告来源</dt>
                    <dd class="mono small">
                      {{ replay.replay_id }} · {{ fmtTime(replay.created_at) }}
                    </dd>
                    <dt>完成时间</dt>
                    <dd class="small">{{ fmtTime(replay.completed_at) }}</dd>
                    <dt>审核条件</dt>
                    <dd>
                      <span class="badge" :class="replayGate?.satisfied ? 'pass' : 'warn'">
                        {{ replayGate?.satisfied ? '满足' : '未满足' }}
                      </span>
                      <span v-if="replay.blocked_reason" class="small muted">
                        （{{ replay.blocked_reason }}）
                      </span>
                    </dd>
                  </div>
                  <ul
                    v-if="replayGate && !replayGate.satisfied"
                    class="small muted"
                    style="margin: 8px 0 0 18px"
                  >
                    <li v-for="p in replayGate.problems" :key="p">{{ p }}</li>
                  </ul>
                </template>
              </div>
            </div>

            <div class="card">
              <div class="card-head">
                <h2>执行历史</h2>
                <div class="actions"><button class="sm ghost" @click="load">刷新</button></div>
              </div>
              <div class="card-body tight">
                <table>
                  <thead><tr><th>Run</th><th>结论</th><th>耗时</th></tr></thead>
                  <tbody>
                    <tr v-for="r in runs.slice(0, 12)" :key="r.run_id" style="cursor: pointer" @click="router.push(`/runs/${r.run_id}`)">
                      <td class="mono small">{{ r.run_id.slice(-10) }}</td>
                      <td><StatusBadge :value="r.test_verdict" /></td>
                      <td class="small muted">{{ fmtDuration(r.report?.duration_ms) }}</td>
                    </tr>
                    <tr v-if="runs.length === 0"><td colspan="3"><div class="empty">尚未执行</div></td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>
  </div>
</template>
