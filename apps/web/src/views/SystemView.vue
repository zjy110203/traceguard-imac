<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, fmtBytes } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import { notify, notifyError } from '../toast'
import type { BrowserHealth, SystemHealth } from '../types'

const health = ref<SystemHealth | null>(null)
const browser = ref<BrowserHealth | null>(null)
const config = ref<Record<string, unknown> | null>(null)
const metrics = ref<Record<string, number>>({})
const artifacts = ref<{ artifact_id: string; media_type: string; size: number; original_name: string }[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')

async function load(probe = false) {
  loading.value = !health.value
  try {
    const [h, b, c, m, a] = await Promise.all([
      api.get<SystemHealth>('/system/health'),
      api.get<BrowserHealth>(`/system/browser?probe=${probe}`),
      api.get<Record<string, unknown>>('/system/config'),
      api.get<Record<string, number>>('/system/metrics'),
      api.get<{ artifact_id: string; media_type: string; size: number; original_name: string }[]>('/artifacts?limit=20'),
    ])
    health.value = h
    browser.value = b
    config.value = c
    metrics.value = m
    artifacts.value = a
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function probeBrowser() {
  busy.value = 'browser'
  try {
    await load(true)
    notify(browser.value?.available ? `浏览器可用：${browser.value.channel} ${browser.value.version ?? ''}` : '浏览器不可用')
  } finally {
    busy.value = ''
  }
}

async function seed() {
  busy.value = 'seed'
  try {
    const r = await api.post<{ created: number; skipped: number }>('/system/seed')
    notify(`种子完成：新增 ${r.created}，跳过 ${r.skipped}`)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

onMounted(() => load())
</script>

<template>
  <div>
    <PageHeader eyebrow="Runtime / System" title="系统与运行环境"
      subtitle="数据库、浏览器、Worker、产物存储的真实状态（不是配置文件的自我描述）。浏览器状态需主动自检，配置成功不等于连通成功。">
      <template #actions>
        <button :disabled="busy === 'browser'" @click="probeBrowser">真实启动浏览器自检</button>
        <button :disabled="busy === 'seed'" @click="seed">写入演示资产</button>
        <button @click="load()">刷新</button>
      </template>
    </PageHeader>

    <AsyncBlock :loading="loading" :error="error">
      <div class="metric-grid m5" style="margin-bottom: 14px">
        <MetricCard label="数据库" :value="health?.database.ok ? 'OK' : 'FAIL'" :hint="`${health?.database.dialect || '—'}${health?.database.error ? ' · ' + health.database.error : ''}`" :tone="health?.database.ok ? 'pass' : 'fail'" icon="shield" />
        <MetricCard label="Worker" :value="health?.worker?.inline_running ? 'RUNNING' : 'IDLE'" :hint="health?.worker?.id || '—'" :tone="health?.worker?.inline_running ? 'pass' : 'neutral'" icon="activity" />
        <MetricCard label="浏览器" :value="browser?.available ? 'READY' : 'N/A'" :hint="`${browser?.channel || '—'} ${browser?.version || ''}`" :tone="browser?.available ? 'pass' : 'warn'" icon="gauge" />
        <MetricCard label="最近产物" :value="artifacts.length" hint="真实落盘文件" icon="layers" />
      </div>

      <div class="split">
        <div class="flex-col">
          <div class="card">
            <div class="card-head"><h2>运行配置</h2></div>
            <div class="card-body"><pre class="json">{{ JSON.stringify(config, null, 2) }}</pre></div>
          </div>
          <div class="card">
            <div class="card-head"><h2>数据统计</h2></div>
            <div class="card-body">
              <div class="grid c3">
                <div v-for="(v, k) in metrics" :key="k" class="metric" style="box-shadow: none">
                  <div class="label">{{ k }}</div>
                  <template v-if="k === 'verdicts'">
                    <div class="chip-row" style="margin-top: 6px">
                      <span v-for="(n, key) in (v as unknown as Record<string, number>)" :key="key" class="chip">{{ key }} {{ n }}</span>
                    </div>
                  </template>
                  <div v-else class="value" style="font-size: 20px">{{ v }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="flex-col">
          <div class="card">
            <div class="card-head"><h2>浏览器自检结果</h2></div>
            <div class="card-body"><pre class="json">{{ JSON.stringify(browser, null, 2) }}</pre></div>
          </div>
          <div class="card">
            <div class="card-head"><h2>最近产物</h2></div>
            <div class="card-body tight scroll-x">
              <table>
                <thead><tr><th>名称</th><th>类型</th><th>大小</th></tr></thead>
                <tbody>
                  <tr v-for="a in artifacts" :key="a.artifact_id">
                    <td class="mono small truncate" style="max-width: 240px">{{ a.original_name || a.artifact_id }}</td>
                    <td class="small">{{ a.media_type }}</td>
                    <td class="small muted">{{ fmtBytes(a.size) }}</td>
                  </tr>
                  <tr v-if="artifacts.length === 0"><td colspan="3"><div class="empty">暂无产物</div></td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>
  </div>
</template>
