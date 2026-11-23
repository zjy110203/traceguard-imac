import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('./views/DashboardView.vue'), meta: { title: '总览', crumb: '总览' } },
  { path: '/intents', name: 'intents', component: () => import('./views/IntentsView.vue'), meta: { title: '测试需求', crumb: '测试需求' } },
  { path: '/testcases', name: 'testcases', component: () => import('./views/TestCasesView.vue'), meta: { title: '测试资产', crumb: '测试资产' } },
  { path: '/testcases/:id', name: 'testcase-detail', component: () => import('./views/TestCaseDetailView.vue'), meta: { title: '用例详情', crumb: '测试资产 / 用例详情' } },
  { path: '/oracles', name: 'oracles', component: () => import('./views/OraclesView.vue'), meta: { title: 'Oracle 契约', crumb: '测试资产 / Oracle 契约' } },
  { path: '/runs', name: 'runs', component: () => import('./views/RunsView.vue'), meta: { title: '执行中心', crumb: '执行中心' } },
  { path: '/runs/:id', name: 'run-detail', component: () => import('./views/RunDetailView.vue'), meta: { title: 'Run 详情', crumb: '执行中心 / Run 详情' } },
  { path: '/explorer', name: 'explorer', component: () => import('./views/ExplorerView.vue'), meta: { title: 'Agent Studio', crumb: 'Agent Studio · 探索与编译' } },
  { path: '/models', name: 'models', component: () => import('./views/ModelsView.vue'), meta: { title: '模型运行中心', crumb: '模型运行中心' } },
  { path: '/diagnoses', name: 'diagnoses', component: () => import('./views/DiagnosesView.vue'), meta: { title: '诊断中心', crumb: '诊断中心' } },
  { path: '/diagnoses/:id', name: 'diagnosis-detail', component: () => import('./views/DiagnosisDetailView.vue'), meta: { title: '诊断详情', crumb: '诊断中心 / 诊断详情' } },
  { path: '/repairs', name: 'repairs', component: () => import('./views/RepairsView.vue'), meta: { title: '修复中心', crumb: '修复中心' } },
  { path: '/benchmark', name: 'benchmark', component: () => import('./views/BenchmarkView.vue'), meta: { title: 'Benchmark', crumb: 'Benchmark' } },
  { path: '/system', name: 'system', component: () => import('./views/SystemView.vue'), meta: { title: '系统与运行环境', crumb: '系统与运行环境' } },
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
