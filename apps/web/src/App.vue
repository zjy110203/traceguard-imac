<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from './api'
import ToastHost from './components/ToastHost.vue'
import type { SystemHealth } from './types'

const route = useRoute()
const health = ref<SystemHealth | null>(null)
const healthError = ref('')
const counts = ref<Record<string, number>>({})
let timer: number | undefined

async function load() {
  try {
    const h = await api.get<SystemHealth>('/system/health')
    health.value = h
    counts.value = h.counts ?? {}
    healthError.value = ''
  } catch (e) {
    healthError.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 15000)
})
onUnmounted(() => window.clearInterval(timer))

const crumb = computed(() => String(route.meta.crumb ?? ''))
const env = computed(() => health.value?.environment ?? 'local')
const dbOk = computed(() => health.value?.database?.ok ?? false)
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">TG</div>
        <div>
          <div class="brand-name">TraceGuard</div>
          <div class="brand-sub">Quality Intelligence</div>
        </div>
      </div>
      <nav class="nav">
        <div class="nav-group">
          <div class="nav-title">工作台</div>
          <router-link to="/"><span class="ico">◈</span>总览</router-link>
          <router-link to="/intents"><span class="ico">◎</span>测试需求<span v-if="counts.intents" class="count">{{ counts.intents }}</span></router-link>
          <router-link to="/testcases"><span class="ico">▤</span>测试资产<span v-if="counts.testcases" class="count">{{ counts.testcases }}</span></router-link>
          <router-link to="/runs"><span class="ico">▷</span>执行中心<span v-if="counts.runs" class="count">{{ counts.runs }}</span></router-link>
        </div>
        <div class="nav-group">
          <div class="nav-title">质量闭环</div>
          <router-link to="/diagnoses"><span class="ico">◐</span>诊断中心</router-link>
          <router-link to="/repairs"><span class="ico">⟲</span>修复中心</router-link>
          <router-link to="/benchmark"><span class="ico">▩</span>Benchmark</router-link>
        </div>
        <div class="nav-group">
          <div class="nav-title">运行</div>
          <router-link to="/system"><span class="ico">⚙</span>系统与浏览器</router-link>
        </div>
      </nav>
      <div class="sidebar-foot">
        <div><span class="dot" :class="dbOk ? 'ok' : 'bad'" />DB {{ dbOk ? 'connected' : 'unavailable' }}</div>
        <div style="margin-top: 4px">worker · {{ health?.worker?.inline_running ? 'inline running' : 'idle' }}</div>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="crumb">{{ crumb }}</div>
        <div class="spacer" />
        <span v-if="healthError" class="badge fail">后端不可达</span>
        <span class="env-pill">env: {{ env }}</span>
        <span class="env-pill">Playwright · 真实浏览器</span>
      </header>
      <div class="content">
        <router-view />
      </div>
    </div>
    <ToastHost />
  </div>
</template>
