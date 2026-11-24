<script setup lang="ts">
/** RUN-004-FE AppShell：统一导航、面包屑、健康/模型状态、全局搜索、主题与移动抽屉。
 * RUN-010 WP1：用户会话区（当前用户/角色、修改密码、退出）、认证 401 全局跳转、管理菜单按角色显示。
 */
import { computed, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowRight, Bot, ChevronRight, CircleCheck, CircleX, Command, Cpu, Database,
  FileCode2, GitCompare, KeyRound, LayoutDashboard, ListChecks, LoaderCircle, LogOut, Menu, Moon,
  PanelLeftClose, PanelLeftOpen, PlayCircle, Search, Settings2, ShieldCheck,
  Stethoscope, Sun, UserRound, Users, Wrench, X,
} from '@lucide/vue'
import { api, endpoints } from './api'
import {
  authState, can, changePassword, onUnauthorized, roleLabel,
} from './auth'
import ToastHost from './components/ToastHost.vue'
import { notify, notifyError } from './toast'
import type { LLMUsageSummary, SystemHealth } from './types'

interface NavItem { label: string; to: string; icon: Component; count?: number; adminOnly?: boolean }
interface NavGroup { title: string; items: NavItem[] }

const route = useRoute()
const router = useRouter()
const health = ref<SystemHealth | null>(null)
const healthError = ref('')
const model = ref<LLMUsageSummary | null>(null)
const pendingRepairs = ref(0)
const queued = ref(0)
const collapsed = ref(localStorage.getItem('tg-sidebar-collapsed') === '1')
const mobileOpen = ref(false)
const searchOpen = ref(false)
const query = ref('')
const searching = ref(false)
const results = ref<{ label: string; sub: string; to: string }[]>([])
const theme = ref<'light' | 'dark'>(localStorage.getItem('tg-theme') === 'dark' ? 'dark' : 'light')
const userMenuOpen = ref(false)
const passwordDialogOpen = ref(false)
const oldPassword = ref('')
const newPassword = ref('')
const newPassword2 = ref('')
const passwordError = ref('')
const changingPassword = ref(false)
let timer: number | undefined

async function load() {
  const jobs = await Promise.allSettled([
    endpoints.systemHealth(),
    endpoints.llmSummary(),
    api.get<Record<string, unknown>[]>('/repairs?workspace_id=default'),
    endpoints.queueStats(),
  ])
  if (jobs[0].status === 'fulfilled') {
    health.value = jobs[0].value
    healthError.value = ''
  } else {
    healthError.value = jobs[0].reason instanceof Error ? jobs[0].reason.message : String(jobs[0].reason)
  }
  if (jobs[1].status === 'fulfilled') model.value = jobs[1].value
  if (jobs[2].status === 'fulfilled') {
    pendingRepairs.value = jobs[2].value.filter((r) => ['PENDING', 'PENDING_APPROVAL'].includes(String(r.status))).length
  }
  if (jobs[3].status === 'fulfilled') {
    const by = jobs[3].value.by_status ?? {}
    queued.value = (by.QUEUED || 0) + (by.RUNNING || 0) + (by.PREPARING || 0) + (by.FINALIZING || 0)
  }
}

const groups = computed<NavGroup[]>(() => [
  {
    title: '工作台',
    items: [
      { label: '质量总览', to: '/', icon: LayoutDashboard },
      { label: '测试需求', to: '/intents', icon: ListChecks, count: health.value?.counts?.intents },
      { label: '测试资产', to: '/testcases', icon: FileCode2, count: health.value?.counts?.testcases },
      { label: 'Oracle 契约', to: '/oracles', icon: ShieldCheck },
      { label: '执行中心', to: '/runs', icon: PlayCircle, count: health.value?.counts?.runs },
      { label: 'Agent Studio', to: '/explorer', icon: Bot },
      { label: '模型运行', to: '/models', icon: Cpu },
    ],
  },
  {
    title: '质量闭环',
    items: [
      { label: '诊断中心', to: '/diagnoses', icon: Stethoscope },
      { label: '修复中心', to: '/repairs', icon: Wrench, count: pendingRepairs.value },
      { label: 'Benchmark', to: '/benchmark', icon: GitCompare },
    ],
  },
  {
    title: '运行',
    items: [
      { label: '系统与浏览器', to: '/system', icon: Settings2 },
      // platform-admin 专属菜单（路由守卫与服务端双重校验）
      ...(can('user.manage') ? [{ label: '用户与角色', to: '/users', icon: Users, adminOnly: true } satisfies NavItem] : []),
    ],
  },
])

const currentUser = computed(() => authState.user)
const displayName = computed(() => currentUser.value?.username ?? '')
const authEnabled = computed(() => authState.status?.auth_enabled ?? true)

function openPasswordDialog(): void {
  userMenuOpen.value = false
  oldPassword.value = ''
  newPassword.value = ''
  newPassword2.value = ''
  passwordError.value = ''
  passwordDialogOpen.value = true
}

async function submitPasswordChange(): Promise<void> {
  passwordError.value = ''
  if (newPassword.value.length < 8) { passwordError.value = '新密码至少 8 位'; return }
  if (newPassword.value !== newPassword2.value) { passwordError.value = '两次输入的新密码不一致'; return }
  changingPassword.value = true
  try {
    await changePassword(oldPassword.value, newPassword.value)
    passwordDialogOpen.value = false
    notify('密码已修改，请重新登录')
    await router.replace({ name: 'login', query: { redirect: route.fullPath } })
  } catch (e) {
    passwordError.value = e instanceof Error ? e.message : String(e)
    notifyError(passwordError.value)
  } finally {
    changingPassword.value = false
  }
}

async function doLogout(): Promise<void> {
  userMenuOpen.value = false
  try {
    const { logout } = await import('./auth')
    await logout()
  } catch { /* 会话可能已失效 */ }
  await router.replace({ name: 'login' })
}

function handleClickOutside(e: MouseEvent): void {
  const target = e.target as HTMLElement
  if (userMenuOpen.value && !target.closest('.user-menu-anchor')) userMenuOpen.value = false
}

const crumb = computed(() => String(route.meta.crumb ?? ''))
const env = computed(() => health.value?.environment ?? 'local')
const dbOk = computed(() => health.value?.database?.ok ?? false)
const modelText = computed(() => {
  if (!model.value) return '模型状态未知'
  if (!model.value.configured) return '模型未配置 · 规则模式'
  return `${model.value.default_model} · 已配置`
})
const modelOk = computed(() => !!model.value?.configured)
const isActive = (to: string) => to === '/' ? route.path === '/' : route.path.startsWith(to)
function toggleCollapsed() {
  collapsed.value = !collapsed.value
  localStorage.setItem('tg-sidebar-collapsed', collapsed.value ? '1' : '0')
}
function toggleTheme() {
  theme.value = theme.value === 'light' ? 'dark' : 'light'
  document.documentElement.dataset.theme = theme.value
  localStorage.setItem('tg-theme', theme.value)
}

const staticSearch = [
  { label: '质量总览', sub: 'Dashboard', to: '/' },
  { label: 'Agent Studio 探索与编译', sub: '/explorer', to: '/explorer' },
  { label: '模型运行中心', sub: '/models', to: '/models' },
  { label: '执行中心', sub: '/runs', to: '/runs' },
  { label: '诊断中心', sub: '/diagnoses', to: '/diagnoses' },
  { label: '修复中心', sub: '/repairs', to: '/repairs' },
  { label: 'Benchmark 实验对照', sub: '/benchmark', to: '/benchmark' },
]
async function runSearch() {
  const q = query.value.trim().toLowerCase()
  results.value = staticSearch.filter((s) => !q || s.label.toLowerCase().includes(q) || s.sub.includes(q))
  if (!q) return
  searching.value = true
  const jobs = await Promise.allSettled([
    api.get<Record<string, unknown>[]>('/runs?limit=100'),
    api.get<Record<string, unknown>[]>('/testcases?workspace_id=default'),
    api.get<Record<string, unknown>[]>('/intents?workspace_id=default'),
    api.get<Record<string, unknown>[]>('/diagnoses?limit=100&workspace_id=default'),
  ])
  const labels = ['run_id', 'testcase_id', 'intent_id', 'diagnosis_id']
  // 只有存在详情路由的实体才可下钻；Intents 无详情路由，改为列表页带 id 过滤参数。
  const routes = ['/runs/', '/testcases/', '/intents?id=', '/diagnoses/']
  jobs.forEach((job, idx) => {
    if (job.status !== 'fulfilled') return
    const idKey = labels[idx]
    const routePrefix = routes[idx]
    for (const row of job.value.slice(0, 40)) {
      const id = String(row[idKey] ?? '')
      const text = JSON.stringify(row).toLowerCase()
      if (!text.includes(q)) continue
      results.value.push({
        label: String(row.title || row.name || row.symptom || id).slice(0, 80),
        sub: id,
        to: `${routePrefix}${id}`,
      })
    }
  })
  results.value = results.value.slice(0, 12)
  searching.value = false
}
function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    searchOpen.value = true
  }
  if (e.key === 'Escape') {
    searchOpen.value = false
    mobileOpen.value = false
  }
}
function goto(to: string) {
  searchOpen.value = false
  mobileOpen.value = false
  query.value = ''
  void route
  void to
}
watch(() => route.fullPath, () => { mobileOpen.value = false; searchOpen.value = false })

onMounted(() => {
  document.documentElement.dataset.theme = theme.value
  load()
  timer = window.setInterval(load, 20000)
  window.addEventListener('keydown', onKey)
  window.addEventListener('click', handleClickOutside)
  // RUN-010：全局 401 → 跳转登录页并记录回跳地址
  onUnauthorized(() => {
    const current = route.fullPath
    if (route.name !== 'login') {
      void router.replace({ name: 'login', query: current && current !== '/' ? { redirect: current } : {} })
    }
  })
})
onUnmounted(() => {
  window.clearInterval(timer)
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('click', handleClickOutside)
})
</script>

<template>
  <!-- 登录页：全屏独立渲染，不套用工作台壳层 -->
  <router-view v-if="route.name === 'login'" />
  <div v-else class="app" :class="{ 'shell-collapsed': collapsed }">
    <div v-if="mobileOpen" class="mobile-backdrop" @click="mobileOpen = false" />
    <aside class="sidebar" :class="{ open: mobileOpen }" aria-label="主导航">
      <div class="brand">
        <div class="brand-mark"><ShieldCheck :size="16" :stroke-width="2.2" /></div>
        <div v-if="!collapsed" class="brand-text">
          <div class="brand-name">TraceGuard</div>
          <div class="brand-sub">Quality Workbench</div>
        </div>
        <button class="icon-btn mobile-close" aria-label="关闭导航" @click="mobileOpen = false"><X :size="16" /></button>
      </div>
      <nav class="nav">
        <div v-for="g in groups" :key="g.title" class="nav-group">
          <div v-if="!collapsed" class="nav-title">{{ g.title }}</div>
          <router-link
            v-for="item in g.items" :key="item.to" :to="item.to"
            class="nav-item" :class="{ active: isActive(item.to) }"
            :title="collapsed ? item.label : undefined"
            @click="mobileOpen = false"
          >
            <component :is="item.icon" :size="17" :stroke-width="1.8" class="nav-icon" aria-hidden="true" />
            <span v-if="!collapsed" class="nav-label">{{ item.label }}</span>
            <span v-if="!collapsed && item.count" class="count">{{ item.count }}</span>
          </router-link>
        </div>
      </nav>
      <div class="sidebar-foot">
        <div class="foot-line"><Database :size="13" /><span v-if="!collapsed">{{ dbOk ? '数据库已连接' : '数据库不可用' }}</span></div>
        <div v-if="!collapsed" class="foot-line"><LoaderCircle :size="13" :class="{ 'spin-slow': health?.worker?.inline_running }" />{{ health?.worker?.inline_running ? 'Inline Worker 运行中' : 'Worker 空闲' }}</div>
        <div v-if="!collapsed" class="foot-line"><Cpu :size="13" /><span class="truncate">{{ modelText }}</span></div>
        <button v-if="!collapsed" class="collapse-btn" type="button" @click="toggleCollapsed">
          <PanelLeftClose :size="14" /> 收起侧栏
        </button>
        <button v-else class="collapse-btn center" type="button" aria-label="展开侧栏" @click="toggleCollapsed"><PanelLeftOpen :size="15" /></button>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <button class="icon-btn menu-btn" aria-label="打开导航" @click="mobileOpen = true"><Menu :size="18" /></button>
        <div class="breadcrumb" aria-label="面包屑">
          <span class="crumb-root">TraceGuard</span>
          <ChevronRight :size="13" aria-hidden="true" />
          <span class="crumb-current">{{ crumb }}</span>
        </div>
        <div class="topbar-spacer" />
        <button class="search-trigger" type="button" @click="searchOpen = true; query = ''; runSearch()">
          <Search :size="14" /><span>搜索页面、Run、用例…</span><kbd><Command :size="10" />K</kbd>
        </button>
        <span class="env-pill">{{ env }}</span>
        <span class="top-status" :class="healthError || !dbOk ? 'bad' : 'ok'" :title="healthError || '后端健康'">
          <CircleCheck v-if="!healthError && dbOk" :size="13" /><CircleX v-else :size="13" />
          <span class="hide-sm">{{ healthError ? '后端不可达' : '后端正常' }}</span>
        </span>
        <!-- 用户会话区：认证开启时显示当前用户；认证关闭（开发模式）显示提示 -->
        <div v-if="authEnabled && currentUser" class="user-menu-anchor">
          <button class="user-chip" type="button" aria-haspopup="menu" :aria-expanded="userMenuOpen" @click.stop="userMenuOpen = !userMenuOpen">
            <UserRound :size="14" />
            <span class="user-name">{{ displayName }}</span>
            <span class="badge info">{{ roleLabel }}</span>
          </button>
          <div v-if="userMenuOpen" class="user-menu" role="menu" aria-label="用户菜单">
            <div class="user-menu-info">
              <div class="strong">{{ displayName }}</div>
              <div class="small muted mono">{{ currentUser.user_id }}</div>
              <div class="small muted">角色：{{ currentUser.roles.map(r => r).join(' / ') }}</div>
            </div>
            <button class="user-menu-item" type="button" role="menuitem" @click="openPasswordDialog">
              <KeyRound :size="14" /> 修改密码
            </button>
            <button class="user-menu-item danger" type="button" role="menuitem" @click="doLogout">
              <LogOut :size="14" /> 退出登录
            </button>
          </div>
        </div>
        <span v-else-if="authEnabled" class="badge warn" title="未登录">未登录</span>
        <span v-else class="badge warn" title="认证关闭（开发模式）">匿名（开发模式）</span>
        <button class="icon-btn" type="button" :aria-label="theme === 'light' ? '切换深色主题' : '切换浅色主题'" @click="toggleTheme">
          <Sun v-if="theme === 'light'" :size="16" /><Moon v-else :size="16" />
        </button>
      </header>
      <main class="content"><router-view /></main>
    </div>
    <ToastHost />

    <div v-if="searchOpen" class="modal-backdrop search-backdrop" @click.self="searchOpen = false">
      <div class="search-dialog" role="dialog" aria-modal="true" aria-label="全局搜索">
        <div class="search-input-row">
          <Search :size="17" />
          <input v-model="query" autofocus placeholder="输入关键词：页面、Run ID、用例、诊断…" @input="runSearch" />
          <button class="icon-btn" aria-label="关闭搜索" @click="searchOpen = false"><X :size="16" /></button>
        </div>
        <div class="search-results">
          <router-link v-for="r in results" :key="r.to + r.sub" :to="r.to" class="search-result" @click="goto(r.to)">
            <div class="small strong">{{ r.label }}</div>
            <div class="small muted mono">{{ r.sub }}</div>
            <ArrowRight :size="14" class="right" />
          </router-link>
          <div v-if="!results.length && !searching" class="empty small">没有匹配项</div>
          <div v-if="searching" class="small muted" style="padding: 12px">检索真实数据中…</div>
        </div>
        <div class="search-foot small muted">数据源：真实后端列表接口 · Esc 关闭</div>
      </div>
    </div>

    <!-- 修改密码（密码变更后服务端撤销全部会话 → 回登录页） -->
    <div v-if="passwordDialogOpen" class="modal-backdrop" @click.self="passwordDialogOpen = false">
      <div class="modal password-modal" role="dialog" aria-modal="true" aria-label="修改密码">
        <div class="card-head"><h2>修改密码</h2></div>
        <div class="card-body">
          <div class="field">
            <label for="pwd-old">当前密码</label>
            <input id="pwd-old" v-model="oldPassword" type="password" autocomplete="current-password" required />
          </div>
          <div class="field">
            <label for="pwd-new">新密码（至少 8 位）</label>
            <input id="pwd-new" v-model="newPassword" type="password" autocomplete="new-password" required minlength="8" />
          </div>
          <div class="field">
            <label for="pwd-new2">确认新密码</label>
            <input id="pwd-new2" v-model="newPassword2" type="password" autocomplete="new-password" required minlength="8" />
          </div>
          <div v-if="passwordError" class="password-error" role="alert">{{ passwordError }}</div>
          <p class="small muted">密码修改成功后所有会话将被撤销，需要重新登录。</p>
        </div>
        <div class="modal-foot">
          <button class="ghost" type="button" @click="passwordDialogOpen = false">取消</button>
          <button class="primary" type="button" :disabled="changingPassword" @click="submitPasswordChange">
            <LoaderCircle v-if="changingPassword" :size="14" class="spin-slow" /> 确认修改
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.user-menu-anchor { position: relative; }
.user-chip {
  display: inline-flex; align-items: center; gap: 7px; padding: 5px 10px;
  border-radius: 999px; background: var(--neutral-weak); border: 1px solid var(--border);
  cursor: pointer; font-size: 12.5px;
}
.user-chip:hover { background: var(--surface-3); }
.user-name { font-weight: 600; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-menu {
  position: absolute; right: 0; top: calc(100% + 6px); min-width: 230px; z-index: 90;
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  box-shadow: var(--shadow-lg); padding: 6px; display: grid; gap: 2px;
}
.user-menu-info { padding: 10px 12px; border-bottom: 1px solid var(--border); margin-bottom: 4px; display: grid; gap: 3px; }
.user-menu-item {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
  padding: 8px 12px; border: none; background: transparent; border-radius: 8px;
  font-size: 12.5px; cursor: pointer;
}
.user-menu-item:hover { background: var(--neutral-weak); }
.user-menu-item.danger { color: var(--fail); }
.password-modal { width: min(420px, 92vw); }
.password-error {
  background: var(--fail-weak); color: var(--fail); border: 1px solid var(--fail-border);
  border-radius: 8px; padding: 8px 12px; font-size: 12.5px;
}
.field { margin-bottom: 12px; }
</style>