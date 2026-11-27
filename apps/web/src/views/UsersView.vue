<script setup lang="ts">
/** RUN-010 WP1：用户管理与角色权限说明（platform-admin）。
 *
 * 前端只做管理入口；服务端 _require_platform_admin 是最终权限判定者。
 */
import { onMounted, ref } from 'vue'
import { CircleCheck, CircleX, LoaderCircle, Plus, RefreshCw, ShieldCheck, UserRound } from '@lucide/vue'
import { api, ApiError, endpoints } from '../api'
import PageHeader from '../components/PageHeader.vue'
import { ROLE_LABELS } from '../auth'
import { notify, notifyError } from '../toast'

interface AuthUserRow {
  user_id: string
  username: string
  roles: string[]
  active: boolean
  workspaces: string[]
}

const users = ref<AuthUserRow[]>([])
const matrix = ref<Record<string, string[]>>({})
const loading = ref(true)
const error = ref('')
const busy = ref('')

const showCreate = ref(false)
const newUsername = ref('')
const newPassword = ref('')
const newRoles = ref<string[]>(['viewer'])
const newWorkspaces = ref('*')
const createError = ref('')

const editing = ref<AuthUserRow | null>(null)
const editRoles = ref<string[]>([])
const editWorkspaces = ref('')

const ALL_ROLES = ['viewer', 'qa-engineer', 'qa-lead', 'platform-admin'] as const
const ACTION_LABELS: Record<string, string> = {
  read: '读取',
  'testcase.create': '创建用例/需求',
  'testcase.review': '用例评审',
  'oracle.create': '创建 Oracle',
  'oracle.bind': '绑定 Oracle',
  'run.execute': '执行 Run',
  'diagnosis.operate': '诊断操作',
  'probe.execute': '执行探针',
  'repair.submit': '提交修复',
  'repair.approve': '审批修复',
  'repair.promote': '晋升（CAS）',
  'repair.rollback': '回滚版本',
  'system.configure': '系统配置',
  'user.manage': '用户管理',
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [u, m] = await Promise.all([
      api.get<AuthUserRow[]>('/auth/users'),
      api.get<{ matrix: Record<string, string[]> }>('/auth/permissions'),
    ])
    users.value = u
    matrix.value = m.matrix
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function createUser(): Promise<void> {
  createError.value = ''
  if (!newUsername.value.trim()) { createError.value = '请输入用户名'; return }
  if (newPassword.value.length < 8) { createError.value = '密码至少 8 位'; return }
  if (!newRoles.value.length) { createError.value = '至少选择一个角色'; return }
  busy.value = 'create'
  try {
    const workspaces = newWorkspaces.value.trim()
      ? newWorkspaces.value.trim().split(/[,\s]+/).filter(Boolean)
      : ['*']
    await api.post('/auth/users', {
      username: newUsername.value.trim(),
      password: newPassword.value,
      roles: newRoles.value,
      workspaces,
    })
    notify(`用户 ${newUsername.value.trim()} 已创建`)
    showCreate.value = false
    newUsername.value = ''
    newPassword.value = ''
    newRoles.value = ['viewer']
    newWorkspaces.value = '*'
    await load()
  } catch (e) {
    createError.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    busy.value = ''
  }
}

async function toggleActive(user: AuthUserRow): Promise<void> {
  busy.value = 'active:' + user.user_id
  try {
    await api.post(`/auth/users/${user.user_id}/active?active=${user.active ? 'false' : 'true'}`, {})
    notify(user.active ? `已停用 ${user.username}（会话全部撤销）` : `已启用 ${user.username}`)
    await load()
  } catch (e) {
    notifyError(e instanceof Error ? e.message : String(e))
  } finally {
    busy.value = ''
  }
}

function startEdit(user: AuthUserRow): void {
  editing.value = user
  editRoles.value = [...user.roles]
  editWorkspaces.value = (user.workspaces || []).join(', ')
}

async function saveEdit(): Promise<void> {
  const user = editing.value
  if (!user) return
  if (!editRoles.value.length) { notify('至少选择一个角色', true); return }
  busy.value = 'edit:' + user.user_id
  try {
    const workspaces = editWorkspaces.value.trim()
      ? editWorkspaces.value.trim().split(/[,\s]+/).filter(Boolean)
      : ['default']
    await endpoints.setUserRoles(user.user_id, editRoles.value)
    await endpoints.setUserWorkspaces(user.user_id, workspaces)
    notify(`已更新 ${user.username} 的角色与授权范围；其现有会话下一次请求即生效`)
    editing.value = null
    await load()
  } catch (e) {
    notifyError(e instanceof Error ? e.message : String(e))
  } finally {
    busy.value = ''
  }
}

const roleLabel = (role: string): string => ROLE_LABELS[role] ?? role
const actionLabel = (action: string): string => ACTION_LABELS[action] ?? action
</script>

<template>
  <div>
    <PageHeader
      eyebrow="运行 · 访问控制"
      title="用户与角色"
      subtitle="管理平台用户、角色与 workspace 数据访问范围。审批、晋升等高风险动作由服务端按角色强制校验，前端展示不构成授权。"
    >
      <template #actions>
        <button class="ghost" type="button" :disabled="loading" @click="load">
          <RefreshCw :size="14" /> 刷新
        </button>
        <button class="primary" type="button" @click="showCreate = true"><Plus :size="14" /> 新建用户</button>
      </template>
    </PageHeader>

    <div v-if="error" class="login-error">{{ error }}</div>

    <div v-if="loading" class="empty"><LoaderCircle :size="18" class="spin-slow" /><div class="title">加载中…</div></div>

    <template v-else>
      <section class="card" style="margin-bottom: 16px">
        <div class="card-head"><h2><UserRound :size="15" /> 用户列表</h2></div>
        <div class="card-body tight">
          <table>
            <thead>
              <tr>
                <th scope="col">用户名</th>
                <th scope="col">角色</th>
                <th scope="col">Workspace 范围</th>
                <th scope="col">状态</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in users" :key="u.user_id">
                <td class="strong">{{ u.username }}</td>
                <td>
                  <span v-for="r in u.roles" :key="r" class="badge info" style="margin-right: 4px">{{ roleLabel(r) }}</span>
                </td>
                <td class="mono">{{ u.workspaces?.join(', ') || 'default' }}</td>
                <td>
                  <span class="badge" :class="u.active ? 'pass' : 'fail'">
                    {{ u.active ? '启用' : '停用' }}
                  </span>
                </td>
                <td>
                  <button class="sm" type="button" style="margin-right: 6px" @click="startEdit(u)">编辑权限</button>
                  <button
                    class="sm" :class="u.active ? 'danger' : 'primary'" type="button"
                    :disabled="busy === 'active:' + u.user_id"
                    @click="toggleActive(u)"
                  >
                    <CircleX v-if="u.active" :size="12" /><CircleCheck v-else :size="12" />
                    {{ u.active ? '停用' : '启用' }}
                  </button>
                </td>
              </tr>
              <tr v-if="!users.length"><td colspan="5" class="empty">暂无用户</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><h2><ShieldCheck :size="15" /> 角色 × 权限矩阵（服务端权威映射的展示）</h2></div>
        <div class="card-body tight">
          <table>
            <thead>
              <tr>
                <th scope="col">动作</th>
                <th v-for="r in ALL_ROLES" :key="r" scope="col">{{ roleLabel(r) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(label, action) in ACTION_LABELS" :key="action">
                <td class="strong">{{ label }}</td>
                <td v-for="r in ALL_ROLES" :key="r" style="text-align: center">
                  <CircleCheck
                    v-if="(matrix[r] ?? []).includes(action)"
                    :size="15" style="color: var(--pass)" aria-label="允许"
                  />
                  <span v-else class="muted" aria-label="拒绝">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <div v-if="editing" class="modal-backdrop" @click.self="editing = null">
      <div class="modal user-create-modal" role="dialog" aria-modal="true" :aria-label="`编辑 ${editing.username} 的权限`">
        <div class="card-head"><h2>编辑 {{ editing.username }} 的权限</h2></div>
        <div class="card-body">
          <div class="field">
            <label>角色</label>
            <div class="role-checks">
              <label v-for="r in ALL_ROLES" :key="r" class="role-check">
                <input v-model="editRoles" type="checkbox" :value="r" /> {{ roleLabel(r) }}
              </label>
            </div>
          </div>
          <div class="field">
            <label for="edit-workspaces">Workspace 范围（逗号分隔；* = 全部）</label>
            <input id="edit-workspaces" v-model="editWorkspaces" type="text" />
          </div>
          <p class="small muted">服务端会拒绝降级最后一个有效平台管理员；角色/范围变更在现有会话的下一请求立即生效。</p>
        </div>
        <div class="modal-foot">
          <button class="ghost" type="button" @click="editing = null">取消</button>
          <button class="primary" type="button" :disabled="busy === 'edit:' + editing.user_id" @click="saveEdit">保存</button>
        </div>
      </div>
    </div>

    <div v-if="showCreate" class="modal-backdrop" @click.self="showCreate = false">
      <div class="modal user-create-modal" role="dialog" aria-modal="true" aria-label="新建用户">
        <div class="card-head"><h2>新建用户</h2></div>
        <div class="card-body">
          <div class="field">
            <label for="new-username">用户名</label>
            <input id="new-username" v-model="newUsername" type="text" autocomplete="off" required />
          </div>
          <div class="field">
            <label for="new-password">密码（至少 8 位）</label>
            <input id="new-password" v-model="newPassword" type="password" autocomplete="new-password" required minlength="8" />
          </div>
          <div class="field">
            <label>角色</label>
            <div class="role-checks">
              <label v-for="r in ALL_ROLES" :key="r" class="role-check">
                <input v-model="newRoles" type="checkbox" :value="r" /> {{ roleLabel(r) }}
              </label>
            </div>
          </div>
          <div class="field">
            <label for="new-workspaces">Workspace 范围（逗号分隔；* = 全部）</label>
            <input id="new-workspaces" v-model="newWorkspaces" type="text" placeholder="*, default, ws-a" />
          </div>
          <div v-if="createError" class="login-error">{{ createError }}</div>
        </div>
        <div class="modal-foot">
          <button class="ghost" type="button" @click="showCreate = false">取消</button>
          <button class="primary" type="button" :disabled="busy === 'create'" @click="createUser">
            <LoaderCircle v-if="busy === 'create'" :size="14" class="spin-slow" /> 创建
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.role-checks { display: flex; flex-wrap: wrap; gap: 10px 16px; }
.role-check { display: flex; gap: 6px; align-items: center; font-weight: 500; }
.user-create-modal { width: min(440px, 92vw); }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 15px; border-top: 1px solid var(--border); }
</style>
