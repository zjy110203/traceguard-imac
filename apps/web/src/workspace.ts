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

/** RUN-013 WP2：加载/切换序号，后发请求覆盖先发请求，杜绝快速切换的响应回填。 */
let loadSeq = 0
let switchSeq = 0

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
  const seq = ++loadSeq
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
    // 后发请求优先：较早的响应可能携带旧身份的授权列表，直接丢弃。
    if (seq !== loadSeq) return state.currentId
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
    if (seq !== loadSeq) return state.currentId
    state.error = e instanceof Error ? e.message : String(e)
    if (state.currentId) state.generation += 1
    state.currentId = ''
    state.initialized = true
    return ''
  } finally {
    if (seq === loadSeq) state.loading = false
  }
}

/** 切换空间：先向服务端重新验证授权，成功后才切换并清理所有视图缓存。 */
export async function switchWorkspace(id: string): Promise<boolean> {
  if (!id || id === state.currentId) return false
  const seq = ++switchSeq
  await loadWorkspaces()
  // 期间又发生了新的切换：本次结果作废，避免旧选择回填。
  if (seq !== switchSeq) return false
  if (!state.available.some((w) => w.workspace_id === id)) return false
  state.currentId = id
  savePreference(id)
  state.generation += 1
  return true
}

export function resetWorkspaceContext(): void {
  // 使所有在途加载/切换失效：身份变化或登出后不得回填上一身份的数据。
  loadSeq += 1
  switchSeq += 1
  state.available = []
  state.currentId = ''
  state.initialized = false
  state.error = ''
  state.loading = false
  state.generation += 1
  savePreference('')
}

/** 业务列表/写入参数：始终使用当前合法空间；未选择时给出不可能命中真实数据的哨兵值。 */
export function workspaceParams(extra: Record<string, string | number | boolean | undefined | null> = {}): string {
  // 调用方不得通过 extra 覆盖 workspace_id（防止参数覆盖绕过当前上下文）。
  const rest: Record<string, string | number | boolean | undefined | null> = { ...extra }
  delete rest.workspace_id
  return qs({ workspace_id: state.currentId || '__unselected__', ...rest })
}

/** 带 query 的实体无关端点（列表接口），避免视图继续手写 `?workspace_id=...`。 */
export function wsPath(path: string, extra: Record<string, string | number | boolean | undefined | null> = {}): string {
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}${workspaceParams(extra).replace(/^\?/, '')}`
}
