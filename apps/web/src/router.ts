import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { title: '登录', crumb: '登录', public: true } },
  { path: '/', name: 'dashboard', component: () => import('./views/DashboardView.vue'), meta: { title: '总览', crumb: '总览' } },
  { path: '/intents', name: 'intents', component: () => import('./views/IntentsView.vue'), meta: { title: '测试需求', crumb: '测试需求' } },
  { path: '/testcases', name: 'testcases', component: () => import('./views/TestCasesView.vue'), meta: { title: '测试资产', crumb: '测试资产' } },
  { path: '/testcases/:id', name: 'testcase-detail', component: () => import('./views/TestCaseDetailView.vue'), meta: { title: '用例详情', crumb: '测试资产 / 用例详情' } },
  { path: '/oracles', name: 'oracles', component: () => import('./views/OraclesView.vue'), meta: { title: 'Oracle 契约', crumb: '测试资产 / Oracle 契约' } },
  { path: '/oracles/:id', name: 'oracle-detail', component: () => import('./views/OracleDetailView.vue'), meta: { title: 'Oracle 详情', crumb: '测试资产 / Oracle 契约 / 详情' } },
  { path: '/runs', name: 'runs', component: () => import('./views/RunsView.vue'), meta: { title: '执行中心', crumb: '执行中心' } },
  { path: '/runs/:id', name: 'run-detail', component: () => import('./views/RunDetailView.vue'), meta: { title: 'Run 详情', crumb: '执行中心 / Run 详情' } },
  { path: '/explorer', name: 'explorer', component: () => import('./views/ExplorerView.vue'), meta: { title: 'Agent Studio', crumb: 'Agent Studio · 探索与编译' } },
  { path: '/models', name: 'models', component: () => import('./views/ModelsView.vue'), meta: { title: '模型运行中心', crumb: '模型运行中心' } },
  { path: '/diagnoses', name: 'diagnoses', component: () => import('./views/DiagnosesView.vue'), meta: { title: '诊断中心', crumb: '诊断中心' } },
  { path: '/diagnoses/:id', name: 'diagnosis-detail', component: () => import('./views/DiagnosisDetailView.vue'), meta: { title: '诊断详情', crumb: '诊断中心 / 诊断详情' } },
  { path: '/repairs', name: 'repairs', component: () => import('./views/RepairsView.vue'), meta: { title: '修复中心', crumb: '修复中心' } },
  { path: '/benchmark', name: 'benchmark', component: () => import('./views/BenchmarkView.vue'), meta: { title: 'Benchmark', crumb: 'Benchmark' } },
  { path: '/system', name: 'system', component: () => import('./views/SystemView.vue'), meta: { title: '系统与运行环境', crumb: '系统与运行环境' } },
  { path: '/monitoring', name: 'monitoring', component: () => import('./views/MonitoringView.vue'), meta: { title: '运行监控与告警', crumb: '运行 / 运行监控与告警' } },
  { path: '/users', name: 'users', component: () => import('./views/UsersView.vue'), meta: { title: '用户与角色', crumb: '运行 / 用户与角色', adminOnly: true } },
  { path: '/workspaces', name: 'workspaces', component: () => import('./views/WorkspacesView.vue'), meta: { title: '工作区管理', crumb: '运行 / 工作区管理', adminOnly: true } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = String(to.meta.title ?? 'TraceGuard')
  document.title = `${title} · TraceGuard`
})

/** 认证状态懒加载（避免循环依赖：auth.ts 引 api.ts，api.ts 动态引 auth.ts）。 */
let authReady: Promise<typeof import('./auth')> | null = null
async function ensureAuthLoaded() {
  const mod = await import('./auth')
  if (!mod.authState.loaded && !authReady) {
    authReady = (async () => {
      try {
        await mod.fetchAuthStatus()
        if (mod.authState.status?.auth_enabled) {
          await mod.loadMe()
        }
      } catch {
        /* 未登录 */
      }
      return mod
    })()
  }
  await authReady
  return mod
}

router.beforeEach(async (to) => {
  const mod = await ensureAuthLoaded()
  const { authState } = mod
  const authEnabled = authState.status?.auth_enabled ?? true
  // 认证开启：未登录访问业务页 → 跳登录（记录回跳地址）
  if (authEnabled && !to.meta.public && !authState.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  // 已登录访问登录页 → 直接进入工作台
  if (to.name === 'login' && (authState.user || !authEnabled)) {
    return { path: '/' }
  }
  // 管理页：非 platform-admin 引导回工作台（服务端仍是权威判定者）
  if (to.meta.adminOnly && !authState.user?.roles?.includes('platform-admin')) {
    return { path: '/' }
  }
  return true
})
