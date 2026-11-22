<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import ModalShell from '../components/ModalShell.vue'
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
const reviewer = ref('role:qa-lead')

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
    const [r, c] = await Promise.all([api.get<Repair[]>('/repairs'), api.get<TestCaseSummary[]>('/testcases')])
    items.value = r
    cases.value = c
    if (selected.value) selected.value = r.find((x) => x.proposal_id === selected.value!.proposal_id) ?? null
    else selected.value = r[0] ?? null
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
  versions.value = await api.get(`/testcases/${form.value.testcase_id}/versions`)
}

async function select(r: Repair) {
  selected.value = r
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
    selected.value = items.value.find((x) => x.proposal_id === r.proposal_id) ?? null
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

const canApprove = computed(() => selected.value?.status === 'PENDING_APPROVAL')
const canPromote = computed(() => selected.value?.status === 'APPROVED')
const canRollback = computed(() => selected.value?.status === 'PROMOTED')

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>修复中心</h1>
        <div class="sub">Oracle-Protected Self-Healing：只改定位/等待/数据引用，断言与 Oracle 不可触碰；沙箱必须真实再执行。</div>
      </div>
      <div class="actions">
        <button @click="load">刷新</button>
        <button class="primary" @click="showCreate = true">新建修复提案</button>
      </div>
    </div>

    <div class="split">
      <div class="card">
        <div class="card-head"><h2>提案</h2><div class="actions"><span class="small muted">{{ items.length }}</span></div></div>
        <div class="card-body tight scroll-x">
          <AsyncBlock :loading="loading" :error="error" :empty="items.length === 0" empty-title="暂无修复提案">
            <table>
              <thead><tr><th>提案</th><th>用例</th><th>状态</th><th>沙箱</th><th>创建</th></tr></thead>
              <tbody>
                <tr v-for="r in items" :key="r.proposal_id" style="cursor: pointer" :style="selected?.proposal_id === r.proposal_id ? 'background: var(--brand-weak)' : ''" @click="select(r)">
                  <td class="mono small">{{ r.proposal_id.slice(-10) }}</td>
                  <td class="small">{{ r.testcase_id }}<div class="muted" style="font-size: 10.5px">v{{ r.base_version }} → v{{ (r.promoted_version ?? r.base_version + 1) }}</div></td>
                  <td><StatusBadge :value="r.status" /></td>
                  <td><StatusBadge :value="r.sandbox_status" /></td>
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
            <div class="kv">
              <dt>用例</dt><dd class="mono small">{{ selected.testcase_id }} · base v{{ selected.base_version }}</dd>
              <dt>断言保持</dt><dd><StatusBadge :value="selected.assertion_preservation" /></dd>
              <dt>Oracle 完整</dt><dd><StatusBadge :value="selected.oracle_integrity" /></dd>
              <dt>Diff 白名单</dt><dd>{{ selected.diff_allowed ? 'PASS' : 'FAIL' }}</dd>
              <dt>原因</dt><dd class="small">{{ selected.reason || '—' }}</dd>
              <dt>沙箱 Run</dt><dd class="mono small">{{ selected.sandbox_run_id || '—' }}</dd>
            </div>
            <div class="hr" />
            <div class="small muted" style="margin-bottom: 4px">Diff</div>
            <pre class="json" style="max-height: 180px">{{ JSON.stringify(selected.diff, null, 2) }}</pre>
            <div v-if="selected.failure_reason" class="small" style="color: var(--fail); margin-top: 6px">{{ selected.failure_reason }}</div>
            <div class="hr" />
            <div class="flex wrap">
              <button class="sm" :disabled="busy === 'validate' || selected.status !== 'DRAFT'" @click="action('validate', undefined, '规则校验')">规则校验</button>
              <button class="sm" :disabled="busy === 'sandbox-run' || selected.status !== 'VALIDATED'" @click="action('sandbox-run', undefined, '沙箱验证')">沙箱真实执行</button>
              <button class="sm" :disabled="busy === 'submit' || selected.status !== 'VALIDATED' || selected.sandbox_status !== 'PASSED'" @click="action('submit', undefined, '提交审批')">提交审批</button>
              <button class="sm primary" :disabled="busy === 'approve' || !canApprove" @click="action('approve', { reviewer, note: '控制台审批' }, '审批')">批准</button>
              <button class="sm danger" :disabled="busy === 'reject' || !canApprove" @click="action('reject', { reviewer, note: '控制台驳回' }, '驳回')">驳回</button>
              <button class="sm primary" :disabled="busy === 'promote' || !canPromote" @click="action('promote', { idempotency_key: 'promo-' + selected.proposal_id }, '晋升')">晋升正式版本</button>
              <button class="sm" :disabled="busy === 'rollback' || !canRollback" @click="action('rollback', { operator: reviewer, reason: '控制台回滚' }, '回滚')">回滚</button>
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
        <div v-if="selected.rollback_record" class="card">
          <div class="card-head"><h2>回滚记录</h2></div>
          <div class="card-body"><pre class="json">{{ JSON.stringify(selected.rollback_record, null, 2) }}</pre></div>
        </div>
      </div>
      <div v-else class="card"><div class="card-body"><div class="empty">选择左侧提案查看生命周期</div></div></div>
    </div>

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
        <label>修改步骤序号（从 0 开始）</label>
        <input v-model.number="form.step_index" type="number" min="0" />
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
        <button class="primary" :disabled="busy === 'create' || !form.testcase_id || !form.value" @click="propose">创建提案</button>
      </template>
    </ModalShell>
  </div>
</template>
