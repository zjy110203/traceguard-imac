<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import ModalShell from '../components/ModalShell.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Intent } from '../types'

const items = ref<Intent[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const showCreate = ref(false)
const keyword = ref('')
const form = ref({ title: '', description: '', owner: 'qa-team', candidate_assertions: '' })
const lastCompiled = ref<{ intent_id: string; testcase_id: string } | null>(null)

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
    lastCompiled.value = { intent_id: intentId, testcase_id: compiled.compiled.testcase_id }
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

const filtered = computed(() =>
  items.value.filter((i) => !keyword.value || (i.title + i.description).toLowerCase().includes(keyword.value.toLowerCase())),
)

async function load() {
  loading.value = true
  try {
    items.value = await api.get<Intent[]>('/intents')
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
    await api.post(`/intents/${id}/status`, { status })
    notify('状态已更新为 ' + status)
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
    <div class="page-head">
      <div>
        <h1>测试需求</h1>
        <div class="sub">TestIntent 是测试资产的上游输入：需求 → 探索/编译 → 候选用例 → 人工审核。</div>
      </div>
      <div class="actions">
        <button @click="load">刷新</button>
        <button class="primary" @click="showCreate = true">新建需求</button>
      </div>
    </div>

    <div class="toolbar" style="margin-bottom: 12px">
      <input v-model="keyword" placeholder="搜索需求" style="max-width: 260px" />
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
                <button v-if="i.status === 'DRAFT'" class="sm" :disabled="busy === i.intent_id" @click="setStatus(i.intent_id, 'REVIEW')">提交评审</button>
                <button v-if="i.status === 'REVIEW'" class="sm primary" :disabled="busy === i.intent_id" @click="setStatus(i.intent_id, 'APPROVED')">通过</button>
                <router-link
                  v-if="lastCompiled && lastCompiled.intent_id === i.intent_id"
                  :to="`/testcases/${lastCompiled.testcase_id}`"
                  class="sm"
                >查看候选用例</router-link>
                <button class="sm" :disabled="busy === i.intent_id" @click="explore(i.intent_id)">探索并生成候选</button>
                <button v-if="i.status !== 'DEPRECATED'" class="sm ghost" :disabled="busy === i.intent_id" @click="setStatus(i.intent_id, 'DEPRECATED')">废弃</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>

    <ModalShell v-if="showCreate" title="新建测试需求" @close="showCreate = false">
      <div>
        <label>标题</label>
        <input v-model="form.title" placeholder="例如：创建项目后数据必须真实持久化" />
      </div>
      <div>
        <label>描述</label>
        <textarea v-model="form.description" rows="4" placeholder="业务背景、用户操作路径、预期结果" />
      </div>
      <div>
        <label>Owner</label>
        <input v-model="form.owner" />
      </div>
      <div>
        <label>候选断言（每行一条，可留空）</label>
        <textarea v-model="form.candidate_assertions" rows="3" />
      </div>
      <template #footer>
        <button @click="showCreate = false">取消</button>
        <button class="primary" :disabled="busy === 'create'" @click="create">创建</button>
      </template>
    </ModalShell>
  </div>
</template>
