<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtTime } from '../api'
import { currentWorkspaceId, wsPath } from '../workspace'
import AsyncBlock from '../components/AsyncBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import ModalShell from '../components/ModalShell.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Intent, TestCaseSummary } from '../types'

const router = useRouter()
const items = ref<TestCaseSummary[]>([])
const intents = ref<Intent[]>([])
const loading = ref(true)
const error = ref('')
const filter = ref('')
const keyword = ref('')
const busy = ref('')
const showCreate = ref(false)
/** 被测目标地址来自服务端配置（TG_TEAMFLOW_BASE_URL），不再前端硬编码端口。 */
const targetBaseUrl = ref('')

const form = ref({
  title: '新建项目并校验持久化',
  intent_ref: '',
  steps: `[
  {"step_id": "s1", "action": "navigate", "value": "/projects", "description": "打开项目列表"},
  {"step_id": "s2", "action": "click", "target": {"strategy": "test_id", "value": "nav-new"}, "description": "新建"},
  {"step_id": "s3", "action": "fill", "target": {"strategy": "test_id", "value": "project-name"}, "value": "UI 创建", "description": "填写名称"},
  {"step_id": "s4", "action": "click", "target": {"strategy": "test_id", "value": "create-submit"}, "description": "提交"},
  {"step_id": "s5", "action": "assert", "target": {"strategy": "test_id", "value": "banner-success"}, "value": "banner-success", "description": "读取成功提示"}
]`,
  assertions: `[
  {"assertion_id": "a_ui", "description": "成功提示可见", "kind": "ui", "required": true, "expected": {"test_id": "banner-success"}, "oracle_id": "oracle_tf_ui_banner"}
]`,
})

const filtered = computed(() =>
  items.value.filter(
    (i) =>
      (!filter.value || i.review_status === filter.value) &&
      (!keyword.value || (i.title + i.testcase_id).toLowerCase().includes(keyword.value.toLowerCase())),
  ),
)

async function load() {
  loading.value = true
  try {
    const [t, i, cfg] = await Promise.all([
      api.get<TestCaseSummary[]>(wsPath('/testcases')),
      api.get<Intent[]>(wsPath('/intents')),
      api.get<{ teamflow_base_url?: string }>('/system/config').catch(() => ({}) as { teamflow_base_url?: string }),
    ])
    items.value = t
    intents.value = i
    targetBaseUrl.value = cfg.teamflow_base_url || ''
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function execute(id: string) {
  busy.value = id
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

async function review(id: string, version: number, decision: string) {
  busy.value = id
  try {
    await api.post(`/testcases/${id}/versions/${version}/review`, {
      decision,
      reviewer: 'role:qa-lead',
      note: '控制台审核',
    })
    notify(decision === 'APPROVED' ? '已审核通过' : '已拒绝')
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function create() {
  busy.value = 'create'
  try {
    let steps: unknown
    let assertions: unknown
    try {
      steps = JSON.parse(form.value.steps)
      assertions = JSON.parse(form.value.assertions)
    } catch {
      notify('步骤/断言 JSON 解析失败', true)
      return
    }
    // base_url 省略时由服务端按 TG_TEAMFLOW_BASE_URL 解析；前端只在明确知道配置时写入。
    const runConfig: Record<string, unknown> = { fail_fast: false, trace: true }
    if (targetBaseUrl.value) runConfig.base_url = targetBaseUrl.value
    await api.post('/testcases', {
      workspace_id: currentWorkspaceId.value,
      title: form.value.title,
      intent_ref: form.value.intent_ref || null,
      steps,
      assertions,
      run_config: runConfig,
      cleanup_policy: 'RESET_DATA',
    })
    notify('用例已创建（待审核）')
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
    <PageHeader eyebrow="Catalog / Test Assets" title="测试资产"
      subtitle="TestIntent → TestCase 版本 / 审核 → 正式执行。只有 APPROVED 版本可执行。">
      <template #actions>
        <button @click="load">刷新</button>
        <button class="primary" @click="showCreate = true">新建用例</button>
      </template>
    </PageHeader>

    <div class="card">
      <div class="card-head">
        <div class="toolbar">
          <input id="tc-search" v-model="keyword" aria-label="搜索用例标题或 ID" placeholder="搜索用例标题 / ID" style="width: 240px" />
          <select id="tc-status-filter" v-model="filter" aria-label="按审核状态筛选">
            <option value="">全部状态</option>
            <option value="APPROVED">已审核</option>
            <option value="PENDING">待审核</option>
            <option value="REJECTED">已拒绝</option>
          </select>
        </div>
        <div class="actions"><span class="small muted">{{ filtered.length }} 个用例</span></div>
      </div>
      <div class="card-body tight scroll-x">
        <AsyncBlock :loading="loading" :error="error" :empty="filtered.length === 0" empty-title="暂无用例" empty-hint="可新建用例或初始化演示资产">
          <table>
            <thead>
              <tr><th>用例</th><th>来源</th><th>版本</th><th>结构</th><th>状态</th><th>更新时间</th><th style="width: 210px">操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in filtered" :key="c.testcase_id">
                <td>
                  <router-link :to="`/testcases/${c.testcase_id}`" class="link-quiet strong">{{ c.title || c.testcase_id }}</router-link>
                  <div class="mono muted" style="font-size: 10.5px">{{ c.testcase_id }}</div>
                </td>
                <td class="small">{{ c.compile_source }}<div class="muted" style="font-size: 10.5px">{{ c.intent_ref || '—' }}</div></td>
                <td class="small">v{{ c.latest_version }}</td>
                <td class="small nowrap">{{ c.step_count }} 步 / {{ c.assertion_count }} 断言</td>
                <td><StatusBadge :value="c.review_status" /></td>
                <td class="small muted nowrap">{{ fmtTime(c.updated_at) }}</td>
                <td>
                  <div class="flex">
                    <button class="sm primary" :disabled="c.review_status !== 'APPROVED' || busy === c.testcase_id" @click="execute(c.testcase_id)">执行</button>
                    <button v-if="c.review_status === 'PENDING'" class="sm" :disabled="busy === c.testcase_id" @click="review(c.testcase_id, c.latest_version, 'APPROVED')">通过</button>
                    <button v-if="c.review_status === 'PENDING'" class="sm danger" :disabled="busy === c.testcase_id" @click="review(c.testcase_id, c.latest_version, 'REJECTED')">拒绝</button>
                    <router-link :to="`/testcases/${c.testcase_id}`" class="small">详情</router-link>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </AsyncBlock>
      </div>
    </div>

    <ModalShell v-if="showCreate" title="新建测试用例" width="760px" @close="showCreate = false">
      <div>
        <label for="tc-title">用例标题</label>
        <input id="tc-title" v-model="form.title" />
      </div>
      <div>
        <label for="tc-intent-ref">关联需求（可选）</label>
        <select id="tc-intent-ref" v-model="form.intent_ref">
          <option value="">不关联</option>
          <option v-for="i in intents" :key="i.intent_id" :value="i.intent_id">{{ i.title }}</option>
        </select>
      </div>
      <div>
        <label for="tc-steps">步骤（JSON 数组，支持 navigate/click/fill/select/press/assert/wait/api）</label>
        <textarea id="tc-steps" v-model="form.steps" rows="10" class="mono" style="font-size: 12px" />
      </div>
      <div>
        <label for="tc-assertions">断言（JSON 数组，oracle_id 必须是已审批 Oracle）</label>
        <textarea id="tc-assertions" v-model="form.assertions" rows="5" class="mono" style="font-size: 12px" />
      </div>
      <template #footer>
        <button @click="showCreate = false">取消</button>
        <button class="primary" :disabled="busy === 'create'" @click="create">创建（待审核）</button>
      </template>
    </ModalShell>
  </div>
</template>
