/** RUN-010 WP1：前端认证状态与权限辅助。
 *
 * 原则：
 * - 用户身份只来自服务端 /auth/me，前端本地状态仅做展示与路由引导；
 * - 权限判定最终权威是服务端；前端 can() 仅用于 UI 呈现（隐藏按钮/菜单）；
 * - 401 统一处理：清空本地状态 → 跳转登录页（携带回跳地址）。
 */

import { computed, reactive } from 'vue'
import { api, ApiError } from './api'

export interface AuthUser {
  user_id: string
  username: string
  roles: string[]
  actor_id: string
  workspaces?: string[]
}

export interface AuthStatus {
  auth_enabled: boolean
  csrf_required: boolean
}

/** 角色 → 动作映射（与 packages/persistence auth.PERMISSIONS 同构，仅展示用）。 */
export const ROLE_PERMISSIONS: Record<string, string[]> = {
  'platform-admin': [
    'read', 'testcase.create', 'testcase.review', 'oracle.create', 'oracle.bind',
    'run.execute', 'diagnosis.operate', 'probe.execute', 'repair.submit',
    'repair.approve', 'repair.promote', 'repair.rollback', 'system.configure', 'user.manage',
  ],
  'qa-lead': [
    'read', 'testcase.create', 'testcase.review', 'oracle.create', 'oracle.bind',
    'run.execute', 'diagnosis.operate', 'probe.execute', 'repair.submit', 'repair.approve',
  ],
  'qa-engineer': [
    'read', 'testcase.create', 'run.execute', 'diagnosis.operate', 'probe.execute', 'repair.submit',
  ],
  viewer: ['read'],
}

export const ROLE_LABELS: Record<string, string> = {
  'platform-admin': '平台管理员',
  'qa-lead': 'QA 负责人',
  'qa-engineer': 'QA 工程师',
  viewer: '只读用户',
}

const state = reactive({
  user: null as AuthUser | null,
  status: null as AuthStatus | null,
  csrfToken: sessionStorage.getItem('tg-csrf') ?? '',
  loaded: false,
})

export const authState = state

export const isAuthenticated = computed(() => !!state.user)
export const primaryRole = computed(() => state.user?.roles[0] ?? '')
export const roleLabel = computed(() => ROLE_LABELS[primaryRole.value] ?? primaryRole.value)

/** UI 权限判定（服务端仍是最终权威）。 */
export function can(action: string): boolean {
  const roles = state.user?.roles ?? []
  return roles.some((r) => ROLE_PERMISSIONS[r]?.includes(action))
}

/** 审核/审批主体默认值始终来自服务端 /auth/me；开发环境未登录时才退化为具名本地主体。 */
export function currentActor(): string {
  return state.user?.actor_id || 'human:admin'
}

export function isViewerOnly(): boolean {
  const roles = state.user?.roles ?? []
  return roles.length > 0 && roles.every((r) => ROLE_PERMISSIONS[r]?.every((a) => a === 'read'))
}

export function setCsrfToken(token: string): void {
  state.csrfToken = token
  if (token) sessionStorage.setItem('tg-csrf', token)
  else sessionStorage.removeItem('tg-csrf')
}

export class AuthRequiredError extends Error {
  constructor() {
    super('AUTH_REQUIRED')
  }
}

/** 全局 401 处理器：由 api.ts 触发，App/Router 注册。 */
type UnauthorizedHandler = (redirectAfterLogin?: string) => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function onUnauthorized(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler
}

export function fireUnauthorized(): void {
  unauthorizedHandler?.()
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const s = await api.get<AuthStatus>('/auth/status')
  state.status = s
  return s
}

export async function loadMe(): Promise<AuthUser | null> {
  try {
    state.user = await api.get<AuthUser>('/auth/me')
  } catch (e) {
    state.user = null
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
      setCsrfToken('')
      throw new AuthRequiredError()
    }
    throw e
  } finally {
    state.loaded = true
  }
  return state.user
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const body = await api.post<{ csrf_token: string; username: string; roles: string[] }>(
    '/auth/login',
    { username, password },
  )
  setCsrfToken(body.csrf_token)
  await loadMe()
  return state.user as AuthUser
}

export async function logout(): Promise<void> {
  try {
    await api.post('/auth/logout', {})
  } finally {
    state.user = null
    setCsrfToken('')
  }
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await api.post('/auth/password', { old_password: oldPassword, new_password: newPassword })
  // 密码变更后服务端撤销全部会话：清空本地状态并要求重新登录。
  state.user = null
  setCsrfToken('')
}

export async function fetchPermissionMatrix(): Promise<Record<string, string[]>> {
  const res = await api.get<{ matrix: Record<string, string[]> }>('/auth/permissions')
  return res.matrix
}
