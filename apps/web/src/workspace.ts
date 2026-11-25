/** RUN-012 WP2：统一 Workspace 上下文（轻量共享模块，不引入新状态库）。
 *
 * 原则：
 * - 可用空间列表**只从服务端** `/api/v1/workspaces` + `/auth/me` 获得；
 * - localStorage 只保存用户偏好，不授予任何权限；每次加载/切换都重新向服务端验证；
 * - 选择失效（角色变更、空间删除、手改 localStorage）时自动回落到合法空间；
 * - 切换时 generation 递增，AppShell 用 `:key` 强制视图重挂载，杜绝旧空间数据闪现。
 */

import { computed, reactive } from 'vue'
import { api, qs } from './api'
import { authState } from './auth'
import type { Workspace } from './types'

const PREF_KEY = 'tg-workspace'

const state = reactive({
  available: [] as Workspace[],
  currentId: '' as string,
  loading: false,
  initialized: false,
  error: '' as string,
  generation: 0,
})

export const workspaceState = state
export const currentWorkspaceId = computed(() => state.currentId)
export const availableWorkspaces = computed(() => state.available)
export const workspaceGeneration = computed(() => state.generation)
export const hasWorkspace = computed(() => !!state.currentId)

function readPreference(): string {
  try {
    return localStorage.getItem(PREF_KEY) || ''
  } catch {
    return ''
  }
}

function savePreference(id: string): void {
  try {
    if (id) localStorage.setItem(PREF_KEY, id)
    else localStorage.removeItem(PREF_KEY)
  } catch {
    /* 隐私模式下 localStorage 不可用：偏好不持久化，不影响服务端授权 */
  }
}

/** 重新拉取服务端可见空间；返回当前合法选择（无可用空间时为空串）。 */
export async function loadWorkspaces(): Promise<string> {
  state.loading = true
  state.error = ''
  try {
    const [rows, status] = await Promise.all([
      api.get<Workspace[]>('/workspaces'),
      authState.status
        ? Promise.resolve(authState.status)
        : api.get<{ auth_enabled: boolean; csrf_required: boolean }>('/auth/status'),
    ])
    if (status.auth_enabled && !authState.user) {
      authState.user = await api.get<NonNullable<typeof authState.user>>('/auth/me')
    }
    // 服务端 `/workspaces` 已按 Principal 过滤；前端不得自行推断或扩大授权范围。
    // /auth/me 只用于确认会话有效（认证关闭的开发模式跳过）。
    const permitted = rows.filter((w) => !!w.workspace_id)
    state.available = permitted.sort((a, b) => a.workspace_id.localeCompare(b.workspace_id))
    const ids = new Set(state.available.map((w) => w.workspace_id))
    const preferred = readPreference()
    const next = preferred && ids.has(preferred) ? preferred : (state.available[0]?.workspace_id ?? '')
    if (next !== state.currentId) {
      state.currentId = next
      state.generation += 1
    }
    savePreference(next)
    state.initialized = true
    return next
  } catch (e) {
    state.error = e instanceof Error ? e.message : String(e)
    state.currentId = ''
    state.initialized = true
    return ''
  } finally {
    state.loading = false
  }
}

/** 切换空间：先向服务端重新验证授权，成功后才切换并清理所有视图缓存。 */
export async function switchWorkspace(id: string): Promise<boolean> {
  if (!id || id === state.currentId) return false
  await loadWorkspaces()
  if (!state.available.some((w) => w.workspace_id === id)) return false
  state.currentId = id
  savePreference(id)
  state.generation += 1
  return true
}

export function resetWorkspaceContext(): void {
  state.available = []
  state.currentId = ''
  state.initialized = false
  state.error = ''
  state.generation += 1
  savePreference('')
}

/** 业务列表/写入参数：始终使用当前合法空间；未选择时给出不可能命中真实数据的哨兵值。 */
export function workspaceParams(extra: Record<string, string | number | boolean | undefined | null> = {}): string {
  return qs({ workspace_id: state.currentId || '__unselected__', ...extra })
}

/** 带 query 的实体无关端点（列表接口），避免视图继续手写 `?workspace_id=...`。 */
export function wsPath(path: string, extra: Record<string, string | number | boolean | undefined | null> = {}): string {
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}${workspaceParams(extra).replace(/^\?/, '')}`
}
