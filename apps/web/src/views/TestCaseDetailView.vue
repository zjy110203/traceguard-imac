<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtDuration, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Run, TestCaseDetail, TestCaseVersion } from '../types'

const route = useRoute()
const router = useRouter()
const id = String(route.params.id)
const detail = ref<TestCaseDetail | null>(null)
const versions = ref<(TestCaseVersion & { immutable: boolean })[]>([])
const runs = ref<Run[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const activeVersion = ref<number | null>(null)

const current = computed<TestCaseVersion | null>(() => {
  if (!activeVersion.value) return detail.value?.latest ?? null
  return versions.value.find((v) => v.version === activeVersion.value) ?? detail.value?.latest ?? null
})

async function load() {
  loading.value = true
  try {
    const [d, v, r] = await Promise.all([
      api.get<TestCaseDetail>(`/testcases/${id}`),
      api.get<(TestCaseVersion & { immutable: boolean })[]>(`/testcases/${id}/versions`),
      api.get<Run[]>('/runs?limit=200'),
    ])
    detail.value = d
    versions.value = v
    runs.value = r.filter((x) => x.testcase_version_id.startsWith(id))
    if (!activeVersion.value) activeVersion.value = d.latest_version
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function execute() {
  busy.value = 'exec'
  try {
    const r = await api.post<{ run_id: string }>(`/testcases/${id}/execute`, { sync: false })
    notify('已入队执行')
    router.push(`/runs/${r.run_id}`)
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function review(decision: string) {
  const v = current.value
  if (!v) return
  busy.value = 'review'
  try {
    await api.post(`/testcases/${id}/versions/${v.version}/review`, {
      decision,
      reviewer: 'role:qa-lead',
      note: '控制台审核',
    })
    notify('审核完成：' + decision)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

function targetText(t: any): string {
  if (!t) return '—'
  return `${t.strategy}:${t.value}`
}

onMounted(load)
</script>

<template>
  <div>
    <AsyncBlock :loading="loading" :error="error">
      <div v-if="detail">
        <div class="page-head">
          <div>
            <h1>{{ detail.title }}</h1>
            <div class="sub mono">{{ detail.testcase_id }} · 来源 {{ detail.compile_source }} · 关联需求 {{ detail.intent_ref || '—' }}</div>
          </div>
          <div class="actions">
            <button @click="router.push('/testcases')">返回列表</button>
            <button class="primary" :disabled="detail.review_status !== 'APPROVED' || busy === 'exec'" @click="execute">执行当前版本</button>
          </div>
        </div>

        <div class="split">
          <div class="flex-col">
            <div class="card">
              <div class="card-head">
                <h2>版本</h2>
                <div class="actions">
                  <span class="small muted">不可变版本，审核状态可流转</span>
                </div>
              </div>
              <div class="card-body tight scroll-x">
                <table>
                  <thead><tr><th>版本</th><th>状态</th><th>来源</th><th>时间</th><th></th></tr></thead>
                  <tbody>
                    <tr v-for="v in versions" :key="v.version + '-' + v.created_at">
                      <td class="strong">v{{ v.version }} <span v-if="v.version === detail.latest_version" class="badge info">latest</span></td>
                      <td><StatusBadge :value="v.review_status" /></td>
                      <td class="small">{{ v.compile_source }}</td>
                      <td class="small muted nowrap">{{ fmtTime(v.created_at) }}</td>
                      <td><button class="sm ghost" @click="activeVersion = v.version">查看</button></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div v-if="current" class="card">
              <div class="card-head">
                <h2>步骤轨迹（v{{ current.version }}）</h2>
                <div class="actions">
                  <span class="small muted">cleanup: {{ current.cleanup_policy }}</span>
                </div>
              </div>
              <div class="card-body tight scroll-x">
                <table>
                  <thead><tr><th style="width: 44px">#</th><th>动作</th><th>定位 / 值</th><th>说明</th></tr></thead>
                  <tbody>
                    <tr v-for="(s, i) in current.steps" :key="s.step_id">
                      <td class="muted small">{{ i + 1 }}</td>
                      <td><span class="badge neutral">{{ s.action }}</span></td>
                      <td class="mono small">
                        <div v-if="s.target">{{ targetText(s.target) }}</div>
                        <div v-if="s.value" style="color: var(--text-2)">value: {{ s.value }}</div>
                        <span v-if="!s.target && !s.value">—</span>
                      </td>
                      <td class="small muted">{{ s.description || '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div v-if="current" class="card">
              <div class="card-head"><h2>断言与 Oracle 绑定</h2></div>
              <div class="card-body tight scroll-x">
                <table>
                  <thead><tr><th>断言</th><th>类型</th><th>必需</th><th>期望</th><th>Oracle</th></tr></thead>
                  <tbody>
                    <tr v-for="a in current.assertions" :key="a.assertion_id">
                      <td>
                        <div class="strong small">{{ a.description }}</div>
                        <div class="mono muted" style="font-size: 10.5px">{{ a.assertion_id }}</div>
                      </td>
                      <td><span class="badge neutral">{{ a.kind }}</span></td>
                      <td class="small">{{ a.required ? '是' : '否' }}</td>
                      <td class="mono small">{{ JSON.stringify(a.expected) }}</td>
                      <td class="mono small">{{ a.oracle_id || '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>审核</h2></div>
              <div class="card-body">
                <div class="kv">
                  <dt>当前状态</dt><dd><StatusBadge :value="detail.review_status" /></dd>
                  <dt>最新版本</dt><dd>v{{ detail.latest_version }}</dd>
                  <dt>运行配置</dt><dd class="mono small">{{ JSON.stringify(current?.run_config ?? {}) }}</dd>
                </div>
                <div class="hr" />
                <p class="small muted">审核人必须是具名人类角色（Agent 不能自审）。审核通过后才允许正式执行。</p>
                <div class="flex" style="margin-top: 10px">
                  <button class="sm primary" :disabled="busy === 'review'" @click="review('APPROVED')">审核通过</button>
                  <button class="sm danger" :disabled="busy === 'review'" @click="review('REJECTED')">拒绝</button>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-head">
                <h2>执行历史</h2>
                <div class="actions"><button class="sm ghost" @click="load">刷新</button></div>
              </div>
              <div class="card-body tight">
                <table>
                  <thead><tr><th>Run</th><th>结论</th><th>耗时</th></tr></thead>
                  <tbody>
                    <tr v-for="r in runs.slice(0, 12)" :key="r.run_id" style="cursor: pointer" @click="router.push(`/runs/${r.run_id}`)">
                      <td class="mono small">{{ r.run_id.slice(-10) }}</td>
                      <td><StatusBadge :value="r.test_verdict" /></td>
                      <td class="small muted">{{ fmtDuration(r.report?.duration_ms) }}</td>
                    </tr>
                    <tr v-if="runs.length === 0"><td colspan="3"><div class="empty">尚未执行</div></td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>
  </div>
</template>
