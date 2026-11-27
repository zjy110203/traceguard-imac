<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  endpoints,
  fmtTime,
  type WorkspaceMember,
  type WorkspaceRecord,
} from '../api'
import { authState, can } from '../auth'
import { currentWorkspaceId, switchWorkspace } from '../workspace'
import AsyncBlock from '../components/AsyncBlock.vue'
import ModalShell from '../components/ModalShell.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'

const items = ref<WorkspaceRecord[]>([])
const loading = ref(true)
const error = ref('')
const selectedId = ref('')
const members = ref<WorkspaceMember[]>([])
const membersError = ref('')
const showCreate = ref(false)
const busy = ref('')
const form = ref({ name: '', description: '' })
const editForm = ref({ name: '', description: '' })

const allowed = (action: string): boolean =>
  authState.status?.auth_enabled === false || can(action)

const selected = () => items.value.find((w) => w.workspace_id === selectedId.value)

async function load() {
  loading.value = true
  try {
    items.value = await endpoints.workspaces()
    error.value = ''
    if (!selectedId.value || !items.value.some((w) => w.workspace_id === selectedId.value)) {
      selectedId.value = items.value[0]?.workspace_id ?? ''
    }
    await loadDetail()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function loadDetail() {
  members.value = []
  membersError.value = ''
  const ws = selected()
  if (!ws) return
  editForm.value = { name: ws.name, description: ws.description || '' }
  try {
    members.value = (await endpoints.workspaceMembers(ws.workspace_id)).members
  } catch (e) {
    membersError.value = e instanceof Error ? e.message : String(e)
  }
}

watch(selectedId, loadDetail)

async function create() {
  if (!form.value.name.trim()) {
    notify('工作区名称必填', true)
    return
  }
  busy.value = 'create'
  try {
    const created = await endpoints.createWorkspace({
      name: form.value.name.trim(),
      description: form.value.description,
    })
    notify(`工作区 ${created.name} 已创建（创建者由服务端记录）`)
    showCreate.value = false
    form.value = { name: '', description: '' }
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function save() {
  if (!selectedId.value) return
  busy.value = 'save'
  try {
    await endpoints.updateWorkspace(selectedId.value, editForm.value)
    notify('工作区信息已更新')
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function activate() {
  if (!selectedId.value) return
  const ok = await switchWorkspace(selectedId.value)
  notify(ok ? '已切换当前工作区，页面数据已刷新' : '工作区切换失败', !ok)
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader eyebrow="Workspace / Management" title="工作区管理"
      subtitle="创建、查看与编辑工作区；成员范围来自服务端用户授权，默认工作区受保护。切换工作区会重新挂载所有业务视图。">
      <template #actions>
        <button @click="load">刷新</button>
        <button v-if="allowed('system.configure')" class="primary" @click="showCreate = true">新建工作区</button>
      </template>
    </PageHeader>

    <div class="ws-layout">
      <div class="card">
        <div class="card-body tight scroll-x" tabindex="0">
          <AsyncBlock :loading="loading" :error="error" :empty="items.length === 0" empty-title="暂无工作区">
            <table>
              <thead><tr><th>名称</th><th>ID</th><th>当前</th><th>创建者</th></tr></thead>
              <tbody>
                <tr v-for="w in items" :key="w.workspace_id" :class="{ active: w.workspace_id === selectedId }"
                    @click="selectedId = w.workspace_id" style="cursor: pointer">
                  <td>{{ w.name }}</td>
                  <td class="mono small">{{ w.workspace_id }}</td>
                  <td><StatusBadge v-if="w.workspace_id === currentWorkspaceId" value="ACTIVE" /></td>
                  <td class="small muted">{{ w.created_by || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </AsyncBlock>
        </div>
      </div>

      <div class="card" v-if="selected()">
        <div class="card-body">
          <h3>{{ selected()?.name }}</h3>
          <p class="small muted mono">{{ selected()?.workspace_id }}</p>
          <div class="grid c2">
            <div>
              <label for="ws-name">名称</label>
              <input id="ws-name" v-model="editForm.name" :disabled="selectedId === 'default' || !allowed('system.configure')" />
            </div>
            <div>
              <label for="ws-desc">描述</label>
              <input id="ws-desc" v-model="editForm.description" :disabled="!allowed('system.configure')" />
            </div>
          </div>
          <p v-if="selectedId === 'default'" class="small muted">默认工作区受保护：名称与内容不可修改。</p>
          <div class="toolbar" style="margin-top: 12px">
            <button :disabled="!allowed('system.configure') || busy === 'save'" @click="save">保存信息</button>
            <button :disabled="selectedId === currentWorkspaceId" @click="activate">切换到此工作区</button>
          </div>

          <h4 style="margin-top: 18px">成员与授权范围</h4>
          <p v-if="membersError" class="small" role="alert">{{ membersError }}</p>
          <p v-else-if="!members.length" class="small muted">暂无成员（或当前角色无权读取成员）。</p>
          <table v-else>
            <thead><tr><th>用户</th><th>角色</th><th>范围</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="m in members" :key="m.user_id">
                <td>{{ m.username }}</td>
                <td class="small">{{ m.roles.join(', ') }}</td>
                <td class="small">{{ m.scope_mode === 'all' ? '全部工作区' : '显式授权' }}</td>
                <td><StatusBadge :value="m.active ? 'ACTIVE' : 'DISABLED'" /></td>
              </tr>
            </tbody>
          </table>
          <p class="small muted">成员授权请在「用户与角色」页修改；服务端会在下一请求按新范围判定权限。</p>
        </div>
      </div>
    </div>

    <ModalShell v-if="showCreate" title="新建工作区" width="520px" @close="showCreate = false">
      <div>
        <label for="new-ws-name">名称</label>
        <input id="new-ws-name" v-model="form.name" placeholder="例如：发布验证" />
      </div>
      <div>
        <label for="new-ws-desc">描述</label>
        <input id="new-ws-desc" v-model="form.description" />
      </div>
      <p class="small muted">创建者主体由服务端 Principal 写入并记入审计链，请求体中的创建者声明无效。</p>
      <template #footer>
        <button @click="showCreate = false">取消</button>
        <button class="primary" :disabled="busy === 'create'" @click="create">创建</button>
      </template>
    </ModalShell>
  </div>
</template>

<style scoped>
.ws-layout { display: grid; grid-template-columns: minmax(320px, 0.9fr) minmax(360px, 1.1fr); gap: 16px; }
@media (max-width: 900px) { .ws-layout { grid-template-columns: 1fr; } }
tr.active { background: var(--bg-subtle, rgba(0, 0, 0, 0.04)); }
</style>
