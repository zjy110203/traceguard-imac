<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api'
import { currentActor } from '../auth'
import { currentWorkspaceId, wsPath } from '../workspace'
import AsyncBlock from '../components/AsyncBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import ModalShell from '../components/ModalShell.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'

interface OracleRow {
  oracle_id: string
  version: number
  assertion_id: string
  approved: boolean
  approved_by: string
  expected_behavior: string
  created_at: string
}

const items = ref<OracleRow[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const showCreate = ref(false)
const keyword = ref('')
const form = ref({
  oracle_id: '',
  assertion_id: '',
  expected_behavior: '',
  requirement_ref: '',
  approved_by: '',
  rule: '{"type":"presence","test_id":"banner-success"}',
  approve: true,
})

const filtered = computed(() =>
  items.value.filter(
    (o) => !keyword.value || (o.oracle_id + o.expected_behavior).toLowerCase().includes(keyword.value.toLowerCase()),
  ),
)

async function load() {
  loading.value = true
  try {
    items.value = await api.get<OracleRow[]>(wsPath('/oracles'))
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.value.assertion_id || !form.value.expected_behavior || !form.value.requirement_ref) {
    notify('断言 ID / 预期行为 / 需求引用必填', true)
    return
  }
  busy.value = 'create'
  try {
    let rule: unknown = {}
    try {
      rule = JSON.parse(form.value.rule)
    } catch {
      notify('evaluation_rule JSON 解析失败', true)
      return
    }
    await api.post('/oracles', {
      workspace_id: currentWorkspaceId.value,
      oracle_id: form.value.oracle_id || undefined,
      assertion_id: form.value.assertion_id,
      expected_behavior: form.value.expected_behavior,
      requirement_ref: form.value.requirement_ref,
      evaluation_rule: rule,
      approved: form.value.approve,
      approved_by: form.value.approved_by || currentActor(),
      approval_note: 'Oracle 管理页创建',
    })
    notify('Oracle 已创建' + (form.value.approve ? '（已审批）' : '（未审批，不参与判定）'))
    showCreate.value = false
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader eyebrow="Catalog / Oracle Contract" title="Oracle 契约"
      subtitle="Oracle 是判定的唯一依据：未审批的 Oracle 不参与判定，Agent 不能自批。每个 Oracle 都关联 Assertion，具备独立版本与可追溯来源。">
      <template #actions>
        <button @click="load">刷新</button>
        <button class="primary" @click="showCreate = true">新建 Oracle</button>
      </template>
    </PageHeader>
    <div class="oracle-primer panel">
      <span><b>是什么</b>：独立、可追溯的判定契约</span>
      <span><b>关联</b>：Assertion ID</span>
      <span><b>版本</b>：审批后参与正式判定</span>
      <span><b>来源</b>：需求 / 规则 / 人工</span>
      <span><b>状态</b>：APPROVED 可用，PENDING 不参与判定</span>
    </div>

    <div class="toolbar" style="margin-bottom: 12px">
      <input id="oracle-search" v-model="keyword" aria-label="搜索 Oracle 或预期行为" placeholder="搜索 Oracle / 预期行为" style="max-width: 280px" />
      <span class="small muted">{{ filtered.length }} 条</span>
    </div>

    <div class="card">
      <div class="card-body tight scroll-x" tabindex="0">
        <AsyncBlock :loading="loading" :error="error" :empty="filtered.length === 0" empty-title="暂无 Oracle" empty-hint="新建后绑定到用例断言">
          <table>
            <thead><tr><th>Oracle</th><th>断言</th><th>预期行为</th><th>审批</th><th>版本</th><th>时间</th></tr></thead>
            <tbody>
              <tr v-for="o in filtered" :key="o.oracle_id + '@' + o.version">
                <td class="mono small">
                  <router-link :to="`/oracles/${encodeURIComponent(o.oracle_id)}`">{{ o.oracle_id }}</router-link>
                </td>
                <td class="mono small">{{ o.assertion_id }}</td>
                <td class="small">{{ o.expected_behavior }}</td>
                <td>
                  <StatusBadge :value="o.approved ? 'APPROVED' : 'PENDING'" />
                  <div class="muted" style="font-size: 10.5px">{{ o.approved_by || '—' }}</div>
                </td>
                <td class="small">v{{ o.version }}</td>
                <td class="small muted nowrap">{{ fmtTime(o.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </AsyncBlock>
      </div>
    </div>

    <ModalShell v-if="showCreate" title="新建 Oracle（版本化）" width="680px" @close="showCreate = false">
      <div class="grid c2">
        <div>
          <label for="oracle-id">Oracle ID（留空自动生成）</label>
          <input id="oracle-id" v-model="form.oracle_id" placeholder="oracle_xxx" />
        </div>
        <div>
          <label for="oracle-assertion">关联断言 ID</label>
          <input id="oracle-assertion" v-model="form.assertion_id" placeholder="a_ui_banner" />
        </div>
      </div>
      <div>
        <label for="oracle-expected">预期行为（人类可读，必须可追溯到需求）</label>
        <input id="oracle-expected" v-model="form.expected_behavior" placeholder="创建成功后必须出现成功提示" />
      </div>
      <div>
        <label for="oracle-reqref">需求引用</label>
        <input id="oracle-reqref" v-model="form.requirement_ref" placeholder="REQ-PROJECT-CREATE#UI-1" />
      </div>
      <div>
        <label for="oracle-rule">判定规则（确定性规则，JSON）</label>
        <textarea id="oracle-rule" v-model="form.rule" rows="4" class="mono" style="font-size: 12px" />
      </div>
      <div class="grid c2">
        <div>
          <label for="oracle-approver">审批人（必须具名人类角色）</label>
          <input id="oracle-approver" v-model="form.approved_by" />
        </div>
        <div>
          <label>创建时审批</label>
          <label class="flex small" style="gap: 6px; margin: 6px 0 0">
            <input id="oracle-approve-now" v-model="form.approve" type="checkbox" style="width: auto" />
            同步标记为已审批
          </label>
        </div>
      </div>
      <p class="small muted">未审批的 Oracle 会让断言返回 NOT_EVALUATED —— 这是契约要求，不是系统故障。</p>
      <template #footer>
        <button @click="showCreate = false">取消</button>
        <button class="primary" :disabled="busy === 'create'" @click="create">创建</button>
      </template>
    </ModalShell>
  </div>
</template>
