<script setup lang="ts">
/** RUN-004-FE AppShell：统一导航、面包屑、健康/模型状态、全局搜索、主题与移动抽屉。 */
import { computed, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useRoute } from 'vue-router'
import {
  ArrowRight, Bot, ChevronRight, CircleCheck, CircleX, Command, Cpu, Database,
  FileCode2, GitCompare, LayoutDashboard, ListChecks, LoaderCircle, Menu, Moon,
  PanelLeftClose, PanelLeftOpen, PlayCircle, Search, Settings2, ShieldCheck,
  Stethoscope, Sun, Wrench, X,
} from '@lucide/vue'
import { api, endpoints } from './api'
import ToastHost from './components/ToastHost.vue'
import type { LLMUsageSummary, SystemHealth } from './types'

interface NavItem { label: string; to: string; icon: Component; count?: number }
interface NavGroup { title: string; items: NavItem[] }

const route = useRoute()
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
    items: [{ label: '系统与浏览器', to: '/system', icon: Settings2 }],
  },
])

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
})
onUnmounted(() => {
  window.clearInterval(timer)
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div class="app" :class="{ 'shell-collapsed': collapsed }">
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
  </div>
</template>
