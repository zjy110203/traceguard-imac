<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, endpoints, fmtTime } from '../api'
import { currentWorkspaceId, wsPath } from '../workspace'
import { authState, can } from '../auth'
import AsyncBlock from '../components/AsyncBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import ModalShell from '../components/ModalShell.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Intent, TestCaseSummary } from '../types'

const items = ref<Intent[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const showCreate = ref(false)
const keyword = ref('')
// 全局搜索把意图 ID 带到这里；没有 /intents/:id 详情路由，必须在列表页真实过滤。
const route = useRoute()
const focusId = ref(typeof route.query.id === 'string' ? route.query.id : '')
watch(
  () => route.query.id,
  (v) => { focusId.value = typeof v === 'string' ? v : '' },
)
const form = ref({ title: '', description: '', owner: 'qa-team', candidate_assertions: '' })
const editing = ref<string | null>(null)
const editForm = ref({ title: '', description: '', owner: '', candidate_assertions: '' })
/** 候选用例链接来自真实 /testcases 列表，不再用页面内本地状态伪装持久结果。 */
const compiledByIntent = ref<Record<string, string>>({})

/** 按钮可见性与服务端权限矩阵同构；服务端仍独立校验每次操作。 */
const allowed = (action: string): boolean =>
  authState.status?.auth_enabled === false || can(action)

async function explore(intentId: string) {
  busy.value = intentId
  try {
    const exp = await api.post<{ exploration_id: string; status: string; action_count: number }>(
      `/intents/${intentId}/explore?max_steps=8`,
    )
    notify(`探索完成（${exp.action_count} 个动作），正在编译候选用例…`)
    const compiled = await api.post<{ compiled: { testcase_id: string; warnings: string[] } }>(
      `/explorations/${exp.exploration_id}/compile`,
    )
    await load()
    notify(
      compiled.compiled.warnings.length
        ? `候选用例已生成（${compiled.compiled.warnings.length} 条待确认警告）`
        : '候选用例已生成，待人工审核',
    )
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

const filtered = computed(() => {
  const kw = focusId.value || keyword.value
  return items.value.filter((i) => {
    if (!kw) return true
    const k = kw.toLowerCase()
    return (
      i.intent_id.toLowerCase() === k ||
      (i.title + i.description + i.intent_id).toLowerCase().includes(k)
    )
  })
})

async function load() {
  loading.value = true
  try {
    const [intents, cases] = await Promise.all([
      api.get<Intent[]>(wsPath('/intents')),
      api.get<TestCaseSummary[]>(wsPath('/testcases')),
    ])
    items.value = intents
    const map: Record<string, string> = {}
    for (const c of cases) {
      const key = c.intent_ref
      if (!key) continue
      const current = map[key]
      if (!current || c.latest_version >= (cases.find((x) => x.testcase_id === current)?.latest_version ?? 0)) {
        map[key] = c.testcase_id
      }
    }
    compiledByIntent.value = map
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.value.title || !form.value.description) {
    notify('标题与描述必填', true)
    return
  }
  busy.value = 'create'
  try {
    await api.post('/intents', {
      workspace_id: currentWorkspaceId.value,
      title: form.value.title,
      description: form.value.description,
      owner: form.value.owner,
      candidate_assertions: form.value.candidate_assertions
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    })
    notify('需求已创建')
    showCreate.value = false
    form.value = { title: '', description: '', owner: 'qa-team', candidate_assertions: '' }
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function setStatus(id: string, status: string) {
  busy.value = id
  try {
    if (status === 'REVIEW') await endpoints.submitIntent(id)
    else if (status === 'APPROVED') await endpoints.approveIntent(id)
    else await api.post(`/intents/${id}/status`, { status })
    notify('状态已更新为 ' + status)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

function startEdit(i: Intent): void {
  editing.value = i.intent_id
  editForm.value = {
    title: i.title,
    description: i.description,
    owner: i.owner,
    candidate_assertions: (i.candidate_assertions || []).join('\n'),
  }
}

async function saveEdit(): Promise<void> {
  if (!editing.value) return
  busy.value = 'edit'
  try {
    await endpoints.updateIntent(editing.value, {
      title: editForm.value.title,
      description: editForm.value.description,
      owner: editForm.value.owner,
      candidate_assertions: editForm.value.candidate_assertions
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    })
    notify('需求已保存；版本递增并回到 DRAFT，需重新提交评审')
    editing.value = null
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
    <PageHeader eyebrow="Catalog / Intents" title="测试需求"
      subtitle="TestIntent 是测试资产的上游输入：需求 → 探索/编译 → 候选用例 → 人工审核。">
      <template #actions>
        <button @click="load">刷新</button>
        <button class="primary" :disabled="!allowed('testcase.create')" @click="showCreate = true">新建需求</button>
      </template>
    </PageHeader>

    <div class="toolbar" style="margin-bottom: 12px">
      <input id="intents-search" v-model="keyword" aria-label="搜索需求" placeholder="搜索需求" style="max-width: 260px" />
      <span class="small muted">{{ filtered.length }} 条需求</span>
    </div>

    <AsyncBlock :loading="loading" :error="error" :empty="filtered.length === 0" empty-title="暂无测试需求">
      <div class="grid c2">
        <div v-for="i in filtered" :key="i.intent_id" class="card">
          <div class="card-head">
            <h2>{{ i.title }}</h2>
            <div class="actions"><StatusBadge :value="i.status" /></div>
          </div>
          <div class="card-body">
            <p class="small" style="color: var(--text-2)">{{ i.description }}</p>
            <div class="chip-row" style="margin-top: 10px">
              <span class="chip">{{ i.intent_id }}</span>
              <span class="chip">owner: {{ i.owner }}</span>
              <span v-for="r in i.requirement_refs" :key="r" class="chip">{{ r }}</span>
            </div>
            <div v-if="i.candidate_assertions?.length" style="margin-top: 10px">
              <div class="small muted">候选断言</div>
              <ul class="small" style="margin: 4px 0 0 16px; padding: 0">
                <li v-for="a in i.candidate_assertions" :key="a">{{ a }}</li>
              </ul>
            </div>
            <div class="hr" />
            <div class="flex">
              <span class="small muted">{{ fmtTime(i.created_at) }}</span>
              <div class="right flex">
                <button class="sm ghost" :disabled="!!busy || !allowed('testcase.create')" @click="startEdit(i)">编辑</button>
                <button v-if="i.status === 'DRAFT'" class="sm" :disabled="busy === i.intent_id || !allowed('testcase.create')" @click="setStatus(i.intent_id, 'REVIEW')">提交评审</button>
                <button v-if="i.status === 'REVIEW'" class="sm primary" :disabled="busy === i.intent_id || !allowed('testcase.review')" @click="setStatus(i.intent_id, 'APPROVED')">通过</button>
                <router-link
                  v-if="compiledByIntent[i.intent_id]"
                  :to="`/testcases/${compiledByIntent[i.intent_id]}`"
                  class="sm"
                >查看候选用例</router-link>
                <button class="sm" :disabled="busy === i.intent_id || !allowed('testcase.create')" @click="explore(i.intent_id)">探索并生成候选</button>
                <button v-if="i.status !== 'DEPRECATED'" class="sm ghost" :disabled="busy === i.intent_id || !allowed('testcase.create')" @click="setStatus(i.intent_id, 'DEPRECATED')">废弃</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>

    <ModalShell v-if="editing" title="编辑测试需求" @close="editing = null">
      <div>
        <label for="edit-intent-title">标题</label>
        <input id="edit-intent-title" v-model="editForm.title" />
      </div>
      <div>
        <label for="edit-intent-desc">描述</label>
        <textarea id="edit-intent-desc" v-model="editForm.description" rows="4" />
      </div>
      <div>
        <label for="edit-intent-owner">Owner</label>
        <input id="edit-intent-owner" v-model="editForm.owner" />
      </div>
      <div>
        <label for="edit-intent-assertions">候选断言（每行一条）</label>
        <textarea id="edit-intent-assertions" v-model="editForm.candidate_assertions" rows="3" />
      </div>
      <p class="small muted">原始需求来源保留；若该需求已关联测试用例，编辑会被服务端拒绝。</p>
      <template #footer>
        <button @click="editing = null">取消</button>
        <button class="primary" :disabled="busy === 'edit' || !allowed('testcase.create')" @click="saveEdit">保存</button>
      </template>
    </ModalShell>

    <ModalShell v-if="showCreate" title="新建测试需求" @close="showCreate = false">
      <div>
        <label for="intent-title">标题</label>
        <input id="intent-title" v-model="form.title" placeholder="例如：创建项目后数据必须真实持久化" />
      </div>
      <div>
        <label for="intent-desc">描述</label>
        <textarea id="intent-desc" v-model="form.description" rows="4" placeholder="业务背景、用户操作路径、预期结果" />
      </div>
      <div>
        <label for="intent-owner">Owner</label>
        <input id="intent-owner" v-model="form.owner" />
      </div>
      <div>
        <label for="intent-assertions">候选断言（每行一条，可留空）</label>
        <textarea id="intent-assertions" v-model="form.candidate_assertions" rows="3" />
      </div>
      <template #footer>
        <button @click="showCreate = false">取消</button>
        <button class="primary" :disabled="busy === 'create' || !allowed('testcase.create')" @click="create">创建</button>
      </template>
    </ModalShell>
  </div>
</template>
