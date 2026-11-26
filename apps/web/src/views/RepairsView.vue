<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api'
import { currentWorkspaceId, wsPath } from '../workspace'
import AsyncBlock from '../components/AsyncBlock.vue'
import CodeDiff from '../components/CodeDiff.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import ModalShell from '../components/ModalShell.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Repair, TestCaseSummary, TestCaseVersion } from '../types'

const items = ref<Repair[]>([])
const cases = ref<TestCaseSummary[]>([])
const versions = ref<(TestCaseVersion & { immutable: boolean })[]>([])
const selected = ref<Repair | null>(null)
const loading = ref(true)
const error = ref('')
const busy = ref('')
const showCreate = ref(false)
const includeBench = ref(false)
const reviewer = ref('role:qa-lead')

const stepOptions = ref<{ index: number; action: string; target: string }[]>([])
const baseDoc = ref<TestCaseVersion | null>(null)
const pendingAction = ref<{ path: string; body?: Record<string, unknown>; label: string; danger: boolean; message: string } | null>(null)

const form = ref({
  testcase_id: '',
  base_version: 1,
  step_index: 0,
  strategy: 'test_id',
  value: '',
  reason: '',
})

async function load() {
  loading.value = true
  try {
    const [r, c] = await Promise.all([
      api.get<Repair[]>(includeBench.value ? '/repairs' : wsPath('/repairs')),
      api.get<TestCaseSummary[]>(wsPath('/testcases')),
    ])
    items.value = r
    cases.value = c
    const keep = selected.value?.proposal_id
    if (keep) await selectById(keep)
    else if (r.length) await selectById(r[0].proposal_id)
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function onCaseChange() {
  const c = cases.value.find((x) => x.testcase_id === form.value.testcase_id)
  form.value.base_version = c?.latest_version ?? 1
  try {
    versions.value = await api.get(`/testcases/${form.value.testcase_id}/versions`)
    await loadSteps()
  } catch (e) {
    versions.value = []
    stepOptions.value = []
    notifyError(e)
  }
}

async function loadSteps() {
  stepOptions.value = []
  if (!form.value.testcase_id) return
  try {
    const doc = await api.get<TestCaseVersion>(
      `/testcases/${form.value.testcase_id}/versions/${form.value.base_version}`,
    )
    stepOptions.value = (doc.steps || []).map((s, i) => ({
      index: i,
      action: s.action,
      target: s.target ? `${s.target.strategy}:${s.target.value}` : s.value || '—',
    }))
    const clickable = stepOptions.value.find((s) => ['click', 'fill', 'select', 'press'].includes(s.action))
    form.value.step_index = clickable ? clickable.index : 0
  } catch {
    /* 历史版本不可读时保留手填 */
  }
}

async function selectById(proposalId: string) {
  try {
    selected.value = await api.get<Repair>(`/repairs/${proposalId}`)
  } catch {
    selected.value = items.value.find((x) => x.proposal_id === proposalId) ?? null
  }
  baseDoc.value = null
  if (selected.value) {
    try {
      baseDoc.value = await api.get<TestCaseVersion>(
        `/testcases/${selected.value.testcase_id}/versions/${selected.value.base_version}`,
      )
    } catch { /* 历史版本不可读时只展示结构化 diff */ }
  }
}

async function select(r: Repair) {
  await selectById(r.proposal_id)
}

async function propose() {
  busy.value = 'create'
  try {
    const change: Record<string, unknown> = {
      path: 'steps[].target',
      step_index: form.value.step_index,
      value: { strategy: form.value.strategy, value: form.value.value },
      reason: form.value.reason || '定位器修复',
    }
    const r = await api.post<Repair>('/repairs', {
      testcase_id: form.value.testcase_id,
      base_version: form.value.base_version,
      changes: [change],
      reason: form.value.reason || '定位器修复',
    })
    notify('提案已创建：' + r.status)
    showCreate.value = false
    await load()
    await selectById(r.proposal_id)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function llmSuggest() {
  if (!form.value.testcase_id) {
    notify('请先选择测试用例', true)
    return
  }
  busy.value = 'llm'
  try {
    const r = await api.post<{ called: boolean; fallback: boolean; reason?: string; changes: Record<string, unknown>[]; warnings: string[] }>(
      '/repairs/llm-suggest',
      {
        testcase_id: form.value.testcase_id,
        base_version: form.value.base_version,
        propose: false,
      },
    )
    if (!r.called && r.reason === 'not_configured') {
      notify('LLM 未配置（缺凭据），无法生成建议；可手动填写修复提案', true)
      return
    }
    if (!r.called && r.reason === 'disabled') {
      notify('修复规划器已禁用，无法生成 LLM 建议', true)
      return
    }
    const first = (r.changes || [])[0] as { step_index?: number; value?: { strategy?: string; value?: string }; reason?: string } | undefined
    if (first) {
      if (typeof first.step_index === 'number') form.value.step_index = first.step_index
      if (first.value?.strategy) form.value.strategy = first.value.strategy
      if (first.value?.value) form.value.value = String(first.value.value)
      if (first.reason) form.value.reason = first.reason
      showCreate.value = true
      notify(`LLM 建议${r.fallback ? '（规则兜底）' : ''}：已填入首个候选，仍需人工确认后创建提案`)
    } else {
      notify('LLM 未给出可用候选，请手动填写', true)
    }
    if (r.warnings?.length) notify(r.warnings[0], true)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function action(path: string, body?: Record<string, unknown>, label = '') {
  if (!selected.value) return
  busy.value = path
  try {
    const r = await api.post<Repair>(`/repairs/${selected.value.proposal_id}/${path}`, body)
    notify((label || path) + '：' + r.status)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

/** 回归验证：真实创建独立 Run 并由 Worker 执行；结果只认服务端状态。 */
async function runRegression() {
  if (!selected.value) return
  busy.value = 'regression-run'
  try {
    const r = await api.post<Repair>(`/repairs/${selected.value.proposal_id}/regression-run`)
    selected.value = r
    notify('回归验证：' + (r.regression_status || 'UNKNOWN') + (r.regression_verdict ? ` / ${r.regression_verdict}` : ''))
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

const canApprove = computed(() => selected.value?.status === 'PENDING_APPROVAL')
const canPromote = computed(() => selected.value?.status === 'APPROVED')
const canRollback = computed(() => selected.value?.status === 'PROMOTED')
const canRunRegression = computed(() => {
  const r = selected.value
  if (!r || r.status !== 'PROMOTED') return false
  return !['PASSED', 'FAILED', 'INCONCLUSIVE', 'BLOCKED'].includes(r.regression_status || 'NOT_STARTED')
})
const regressionRunning = computed(() =>
  ['QUEUED', 'RUNNING'].includes(selected.value?.regression_status || ''),
)
const regressionTerminal = computed(() =>
  ['PASSED', 'FAILED', 'INCONCLUSIVE', 'BLOCKED'].includes(selected.value?.regression_status || ''),
)
// RUN-017 WP3：只有服务端返回 regression_retry_allowed=true 时才显示"重新验证"入口。
// 前端不自行推断原因码/次数/版本是否满足条件。
const canRetryRegression = computed(
  () => selected.value?.regression_retry_allowed === true,
)
const retryNote = ref('')
const regressionAttempts = computed(() => selected.value?.regression_attempts || [])
const remainingAttempts = computed(() => {
  const r = selected.value
  if (!r) return 0
  return Math.max(0, (r.regression_max_attempts || 2) - (r.regression_attempt_no || 1))
})

/** 人工授权的新 Attempt：二次确认 + 原因码（服务端判定）+ 说明。 */
function askRetryRegression() {
  const r = selected.value
  if (!r || !canRetryRegression.value) return
  ask(
    'regression-retry',
    {
      operator: reviewer.value,
      confirm: true,
      reason_code: r.regression_retry_reason_code || '',
      note: retryNote.value,
      idempotency_key: `retry-${r.proposal_id}-${r.regression_attempt_no || 1}`,
    },
    '重新验证（新 Attempt）',
    true,
  )
}

/** 回归持续时间（秒）：只有真实时间戳才算，不用"已进行多久"冒充结果。 */
const regressionDuration = computed(() => {
  const r = selected.value
  if (!r?.regression_started_at) return ''
  const start = Date.parse(r.regression_started_at)
  const end = r.regression_finished_at ? Date.parse(r.regression_finished_at) : Date.now()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return ''
  return `${Math.round((end - start) / 1000)}s`
})

/** 候选预览：仅根据后端 diff 字段叠加真实基线版本，不冒充已晋升版本。 */
const candidatePreview = computed(() => {
  const base = baseDoc.value
  const r = selected.value
  if (!base || !r) return null
  const copy: any = JSON.parse(JSON.stringify(base))
  for (const ch of r.diff || []) {
    const idx = Number(ch.step_index)
    const step = Number.isInteger(idx) ? copy.steps?.[idx] : undefined
    if (!step) continue
    if (String(ch.path || '').includes('target')) step.target = ch.to
    else step.value = ch.to
  }
  return copy
})

/**
 * 生命周期每一步都必须来自服务端真实状态（RUN-014 WP1）。
 * 旧实现把 Promotion 与 Regression 用同一个 `promoted` 条件判断，
 * 且把任何非空 promotion_record（含 VERSION_CONFLICT）当作晋升成功 —— 都会误报完成。
 */
const lifecycle = computed(() => {
  const r = selected.value
  const validationPassed =
    !!r &&
    !['DRAFT', 'VALIDATION_FAILED'].includes(r.status) &&
    r.assertion_preservation === 'PASS' &&
    r.oracle_integrity === 'PASS' &&
    r.diff_allowed === true
  // 晋升成功 = 服务端给出有效新版本号，且提案确实进入 PROMOTED；
  // VERSION_CONFLICT / PROMOTION_RESULT_INVALID / PROMOTION_FAILED 一律不算。
  const promotedOk =
    !!r &&
    Number(r.promoted_version || 0) > 0 &&
    ['PROMOTED', 'ROLLED_BACK'].includes(r.status) &&
    (r.promotion_record?.check === 'CAS_OK' || r.promotion_record?.result === 'CAS_OK')
  const promotionFailed =
    !!r &&
    (r.status === 'PROMOTION_FAILED' ||
      ['VERSION_CONFLICT', 'PROMOTION_RESULT_INVALID'].includes(r.promotion_record?.result))
  const regressionPassed = r?.regression_status === 'PASSED'
  const regressionFailed = ['FAILED', 'INCONCLUSIVE'].includes(r?.regression_status || '')
  const rollbackDone = Number(r?.rollback_record?.restored_version || 0) > 0
  return [
    { label: 'Proposal', state: r ? 'done' : 'todo' },
    {
      label: 'Rule Validation',
      state: validationPassed ? 'done' : r?.status === 'VALIDATION_FAILED' ? 'fail' : 'todo',
    },
    { label: 'Sandbox Run', state: r?.sandbox_status === 'PASSED' ? 'done' : r?.sandbox_status === 'FAILED' ? 'fail' : 'todo' },
    {
      label: 'Human Approval',
      state: r?.approval_ref || ['APPROVED', 'PROMOTING', 'PROMOTED', 'ROLLED_BACK'].includes(r?.status || '') ? 'done' : 'todo',
    },
    {
      label: 'Promotion',
      state: promotedOk ? 'done' : promotionFailed ? 'fail' : r?.status === 'PROMOTING' ? 'current' : 'todo',
    },
    {
      label: 'Regression',
      state: regressionPassed
        ? 'done'
        : regressionFailed
          ? 'fail'
          : regressionRunning.value
            ? 'current'
            : 'todo',
    },
    { label: 'Rollback', state: rollbackDone ? 'done' : 'todo' },
  ]
})

function ask(path: string, body: Record<string, unknown> | undefined, label: string, danger = false) {
  pendingAction.value = {
    path, body, label, danger,
    message: danger
      ? `${label} 会改变正式资产状态，全部权限与 CAS 校验由后端执行。请确认操作对象 ${selected.value?.proposal_id || ''}。`
      : `确认执行「${label}」？后端将校验当前状态与权限。`,
  }
}
async function confirmPending() {
  const p = pendingAction.value
  pendingAction.value = null
  if (p) await action(p.path, p.body, p.label)
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader eyebrow="Self-Healing Center" title="修复中心"
      subtitle="Oracle-Protected Self-Healing：只改定位/等待/数据引用，断言与 Oracle 不可触碰；沙箱必须真实再执行。审批与晋升由后端鉴权，前端不绕过。">
      <template #actions>
        <label class="flex small" style="gap: 5px; margin: 0">
          <input v-model="includeBench" type="checkbox" style="width: auto" @change="load" /> 包含 Benchmark
        </label>
        <button @click="load">刷新</button>
        <button class="primary" @click="showCreate = true">新建修复提案</button>
      </template>
    </PageHeader>

    <div class="split">
      <div class="card">
        <div class="card-head"><h2>提案</h2><div class="actions"><span class="small muted">{{ items.length }}</span></div></div>
        <div class="card-body tight scroll-x">
          <AsyncBlock :loading="loading" :error="error" :empty="items.length === 0" empty-title="暂无修复提案">
            <table>
              <thead><tr><th>提案</th><th>用例</th><th>状态</th><th>沙箱</th><th>回归</th><th>创建</th></tr></thead>
              <tbody>
                <tr v-for="r in items" :key="r.proposal_id" style="cursor: pointer" :style="selected?.proposal_id === r.proposal_id ? 'background: var(--brand-weak)' : ''" @click="select(r)">
                  <td class="mono small">{{ r.proposal_id.slice(-10) }}</td>
                  <td class="small">{{ r.testcase_id }}<div class="muted" style="font-size: 10.5px">base v{{ r.base_version }}<template v-if="r.promoted_version"> → v{{ r.promoted_version }}</template></div></td>
                  <td><StatusBadge :value="r.status" /></td>
                  <td><StatusBadge :value="r.sandbox_status" /></td>
                  <td><StatusBadge :value="r.regression_status || 'NOT_STARTED'" /></td>
                  <td class="small muted nowrap">{{ fmtTime(r.created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </AsyncBlock>
        </div>
      </div>

      <div v-if="selected" class="flex-col">
        <div class="card">
          <div class="card-head">
            <h2>生命周期</h2>
            <div class="actions"><StatusBadge :value="selected.status" /><StatusBadge :value="selected.sandbox_status" /></div>
          </div>
          <div class="card-body">
            <div class="lifecycle">
              <template v-for="(s, i) in lifecycle" :key="s.label">
                <span class="stage" :class="s.state">{{ s.label }}</span>
                <span v-if="i < lifecycle.length - 1" class="arrow">→</span>
              </template>
            </div>
            <div class="hr" />
            <div class="kv">
              <dt>用例</dt><dd class="mono small">{{ selected.testcase_id }} · base v{{ selected.base_version }}</dd>
              <dt>断言保持</dt><dd><StatusBadge :value="selected.assertion_preservation" /></dd>
              <dt>Oracle 完整</dt><dd><StatusBadge :value="selected.oracle_integrity" /></dd>
              <dt>Diff 白名单</dt>
              <dd>
                <span v-if="selected.diff_allowed === false" class="badge fail">FAIL</span>
                <span v-else-if="selected.diff_allowed" class="badge pass">PASS</span>
                <span v-else class="muted">—</span>
              </dd>
              <dt>原因</dt><dd class="small">{{ selected.reason || '—' }}</dd>
              <dt>沙箱 Run</dt>
              <dd class="mono small">
                <router-link v-if="selected.sandbox_run_id" :to="`/runs/${selected.sandbox_run_id}`">{{ selected.sandbox_run_id }}</router-link>
                <span v-else>—</span>
              </dd>
            </div>
            <div class="hr" />
            <div class="section-title">变更 Diff（代码审查视图）</div>
            <CodeDiff
              v-if="baseDoc"
              :before="baseDoc"
              :after="candidatePreview || baseDoc"
              label="基线版本 → 修复候选（按后端 diff 字段预览）"
            />
            <div v-else>
              <div class="small muted" style="margin-bottom: 4px">结构化 Diff（基线版本暂不可读）</div>
              <pre class="json" style="max-height: 180px">{{ JSON.stringify(selected.diff || [], null, 2) }}</pre>
            </div>
            <div v-for="d in selected.diff || []" :key="`${d.path}-${d.step_index}`" class="change-reason small">
              <span class="mono">steps[{{ d.step_index }}].{{ d.path }}</span> · {{ d.reason || '未提供理由' }}
            </div>
            <div v-if="selected.failure_reason" class="small fail-text mt">{{ selected.failure_reason }}</div>
            <div class="hr" />
            <div class="flex wrap">
              <button class="sm" :disabled="busy === 'validate' || ['PROMOTED','PROMOTING','ROLLED_BACK','REJECTED','PENDING_APPROVAL','APPROVED'].includes(selected.status)" @click="action('validate', undefined, '规则校验')">规则校验</button>
              <button class="sm" :disabled="busy === 'sandbox-run' || selected.status !== 'VALIDATED'" @click="action('sandbox-run', undefined, '沙箱验证')">沙箱真实执行</button>
              <button class="sm" :disabled="busy === 'submit' || selected.status !== 'VALIDATED' || selected.sandbox_status !== 'PASSED'" @click="action('submit', undefined, '提交审批')">提交审批</button>
              <button class="sm primary" :disabled="busy === 'approve' || !canApprove" @click="ask('approve', { reviewer, note: '控制台审批' }, '审批', true)">批准</button>
              <button class="sm danger" :disabled="busy === 'reject' || !canApprove" @click="ask('reject', { reviewer, note: '控制台驳回' }, '驳回', true)">驳回</button>
              <button class="sm primary" :disabled="busy === 'promote' || !canPromote" @click="ask('promote', { idempotency_key: 'promo-' + selected.proposal_id }, '晋升正式版本', true)">晋升正式版本</button>
              <button class="sm" :disabled="busy === 'regression-run' || !canRunRegression" @click="runRegression">运行回归验证</button>
              <button class="sm danger" :disabled="busy === 'rollback' || !canRollback" @click="ask('rollback', { operator: reviewer, reason: '控制台回滚' }, '回滚', true)">回滚</button>
            </div>
            <div class="hr" />
            <label>审批人 / 操作者</label>
            <input v-model="reviewer" />
            <p class="small muted" style="margin-top: 6px">Agent 不能自批：审批人不得包含 agent/llm 字样。</p>
          </div>
        </div>

        <div v-if="selected.promotion_record && Object.keys(selected.promotion_record).length" class="card">
          <div class="card-head"><h2>晋升记录</h2></div>
          <div class="card-body"><pre class="json">{{ JSON.stringify(selected.promotion_record, null, 2) }}</pre></div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>回归验证</h2>
            <div class="actions">
              <StatusBadge :value="selected.regression_status || 'NOT_STARTED'" />
              <StatusBadge v-if="selected.regression_verdict" :value="selected.regression_verdict" />
            </div>
          </div>
          <div class="card-body">
            <div class="kv">
              <dt>回归 Run</dt>
              <dd class="mono small">
                <router-link v-if="selected.regression_run_id" :to="`/runs/${selected.regression_run_id}`">{{ selected.regression_run_id }}</router-link>
                <span v-else class="muted">尚未创建（晋升后才可回归）</span>
              </dd>
              <dt>回归版本</dt>
              <dd class="mono small">{{ selected.regression_testcase_version_id || '—' }}</dd>
              <dt>Verdict</dt>
              <dd><StatusBadge v-if="selected.regression_verdict" :value="selected.regression_verdict" /><span v-else class="muted">—</span></dd>
              <dt>耗时</dt><dd class="small">{{ regressionDuration || '—' }}</dd>
              <dt>证据</dt>
              <dd class="small">
                <router-link v-if="selected.regression_run_id" :to="`/runs/${selected.regression_run_id}`">
                  {{ (selected.regression_evidence_ids || []).length }} 条（在 Run 详情回读）
                </router-link>
                <span v-else class="muted">—</span>
              </dd>
              <dt>Oracle</dt>
              <dd class="small">
                <span v-if="!(selected.regression_oracle_refs || []).length" class="muted">—</span>
                <span v-for="o in selected.regression_oracle_refs || []" :key="o.oracle_id + (o.oracle_version || '')" class="mono" style="margin-right: 8px">
                  {{ o.oracle_id }}@v{{ o.oracle_version ?? '?' }}
                  <span :class="o.status === 'OK' ? 'pass-text' : 'fail-text'">{{ o.status }}</span>
                </span>
              </dd>
              <dt>失败原因</dt><dd class="small fail-text">{{ selected.regression_failure_reason || '—' }}</dd>
              <dt>结论时效</dt>
              <dd class="small">
                <span v-if="selected.regression_stale" class="badge fail">STALE（正式版本已变更）</span>
                <span v-else class="muted">—</span>
              </dd>
              <dt>回滚</dt>
              <dd class="small">
                <span v-if="canRollback">可回滚（需要 repair.rollback 权限，人工确认）</span>
                <span v-else-if="selected.rollback_record?.restored_version" class="pass-text">已回滚到 v{{ selected.rollback_record.restored_version }}</span>
                <span v-else class="muted">仅 PROMOTED 状态可回滚</span>
              </dd>
            </div>
            <p v-if="regressionRunning" class="small muted">回归 Run 执行中：状态来自服务端，刷新页面不会伪造完成。</p>
            <p v-else-if="selected.status === 'PROMOTED' && (selected.regression_status || 'NOT_STARTED') === 'NOT_STARTED'" class="small muted">
              晋升已生效，但回归尚未执行：不得视为已验证。
            </p>

            <div class="hr" />
            <div class="kv">
              <dt>Attempt</dt>
              <dd class="small">
                第 {{ selected.regression_attempt_no || 1 }} 次 / 最多 {{ selected.regression_max_attempts || 2 }} 次
                <span v-if="remainingAttempts === 0" class="muted">（已用完）</span>
              </dd>
              <dt>重试资格</dt>
              <dd class="small">
                <span v-if="canRetryRegression" class="pass-text">允许人工授权重试（{{ selected.regression_retry_reason_code }}）</span>
                <span v-else-if="selected.regression_status === 'INCONCLUSIVE'" class="muted">
                  不可重试：{{ selected.regression_retry_block_reason || '未满足条件' }}
                </span>
                <span v-else class="muted">—</span>
              </dd>
              <dt>血缘</dt>
              <dd class="small mono">
                <span v-if="selected.regression_parent_run_id">
                  上一 Attempt：<router-link :to="`/runs/${selected.regression_parent_run_id}`">{{ selected.regression_parent_run_id }}</router-link>
                </span>
                <span v-else class="muted">首次验证（无父 Run）</span>
              </dd>
            </div>

            <div v-if="regressionAttempts.length">
              <div class="small muted" style="margin: 8px 0 4px">Attempt 历史（旧结果只读，不被覆盖）</div>
              <table class="table">
                <thead>
                  <tr><th>#</th><th>Run</th><th>状态</th><th>原因码</th><th>授权人</th><th>完成时间</th></tr>
                </thead>
                <tbody>
                  <tr v-for="a in regressionAttempts" :key="a.run_id">
                    <td>{{ a.attempt }}</td>
                    <td class="mono small"><router-link :to="`/runs/${a.run_id}`">{{ a.run_id }}</router-link></td>
                    <td><StatusBadge :value="a.status" /></td>
                    <td class="small">{{ a.reason_code || '—' }}</td>
                    <td class="small">{{ a.authorized_by || '—' }}</td>
                    <td class="small">{{ a.authorized_at ? fmtTime(a.authorized_at) : '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="canRetryRegression" class="hr" />
            <div v-if="canRetryRegression">
              <label>重试说明（写入授权审计）</label>
              <input v-model="retryNote" placeholder="例如：采集失败导致证据缺失，环境已自检恢复" />
              <div class="flex wrap" style="margin-top: 8px">
                <button class="sm primary" :disabled="busy === 'regression-retry'" @click="askRetryRegression">
                  重新验证（人工授权 · 新 Attempt）
                </button>
                <span class="small muted" style="align-self: center">
                  原因码 {{ selected.regression_retry_reason_code }} 由服务端判定；旧 Run / 旧结论永远保留。
                </span>
              </div>
            </div>

            <div v-if="(selected.regression_history || []).length" class="hr" />
            <pre v-if="(selected.regression_history || []).length" class="json" style="max-height: 180px">{{ JSON.stringify(selected.regression_history, null, 2) }}</pre>
          </div>
        </div>

        <div v-if="selected.rollback_record" class="card">
          <div class="card-head"><h2>回滚记录</h2></div>
          <div class="card-body"><pre class="json">{{ JSON.stringify(selected.rollback_record, null, 2) }}</pre></div>
        </div>
      </div>
      <div v-else class="card"><div class="card-body"><div class="empty">选择左侧提案查看生命周期</div></div></div>
    </div>

    <ConfirmDialog
      :open="!!pendingAction"
      :title="pendingAction?.label || '确认操作'"
      :message="pendingAction?.message || ''"
      :confirm-label="pendingAction?.label || '确认'"
      :danger="!!pendingAction?.danger"
      @confirm="confirmPending"
      @cancel="pendingAction = null"
    />

    <ModalShell v-if="showCreate" title="新建修复提案" @close="showCreate = false">
      <div>
        <label>目标用例</label>
        <select v-model="form.testcase_id" @change="onCaseChange">
          <option value="">选择用例</option>
          <option v-for="c in cases" :key="c.testcase_id" :value="c.testcase_id">{{ c.title }}（v{{ c.latest_version }}）</option>
        </select>
      </div>
      <div>
        <label>基线版本</label>
        <input v-model.number="form.base_version" type="number" min="1" />
      </div>
      <div>
        <label>目标步骤</label>
        <select v-if="stepOptions.length" v-model.number="form.step_index">
          <option v-for="s in stepOptions" :key="s.index" :value="s.index">
            #{{ s.index }} · {{ s.action }} · {{ s.target }}
          </option>
        </select>
        <input v-else v-model.number="form.step_index" type="number" min="0" />
      </div>
      <div class="grid c2">
        <div>
          <label>新定位策略</label>
          <select v-model="form.strategy">
            <option value="test_id">test_id</option>
            <option value="role">role</option>
            <option value="label">label</option>
            <option value="placeholder">placeholder</option>
            <option value="css">css</option>
            <option value="xpath">xpath</option>
          </select>
        </div>
        <div>
          <label>新定位值</label>
          <input v-model="form.value" placeholder="例如 create-submit" />
        </div>
      </div>
      <div>
        <label>修复原因</label>
        <input v-model="form.reason" placeholder="例如：按钮增加 aria-label，改用 label 定位" />
      </div>
      <p class="small muted">仅允许修改步骤定位/值/等待/数据引用；任何涉及断言、Oracle、业务语义的变更会被直接拒绝。</p>
      <template #footer>
        <button @click="showCreate = false">取消</button>
        <button :disabled="busy === 'llm' || !form.testcase_id" @click="llmSuggest">LLM 建议</button>
        <button class="primary" :disabled="busy === 'create' || !form.testcase_id || !form.value" @click="propose">创建提案</button>
      </template>
    </ModalShell>
  </div>
</template>
