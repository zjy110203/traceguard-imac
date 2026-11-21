<script setup lang="ts">
import { ref, onMounted } from 'vue'

const health = ref<Record<string, unknown>>({ status: 'loading' })
const apiBase = '/api'

async function loadHealth() {
  try {
    const res = await fetch(`${apiBase}/health`)
    health.value = await res.json()
  } catch (e) {
    health.value = { status: 'unreachable', detail: String(e) }
  }
}
onMounted(loadHealth)
</script>

<template>
  <main class="shell">
    <header>
      <h1>TraceGuard</h1>
      <span class="badge">架构基线 v1.1</span>
    </header>
    <section class="card">
      <h2>平台状态</h2>
      <pre>{{ health }}</pre>
    </section>
    <section class="card">
      <h2>工作台</h2>
      <p>测试需求 / 测试资产 / 执行中心 / 证据中心 / 诊断中心 / 修复中心 / Benchmark 将在后续阶段接入。</p>
      <p class="muted">当前为阶段 0 工程骨架：前后端可启动、健康检查联通、契约与基础 CI 已就位。</p>
    </section>
  </main>
</template>

<style scoped>
.shell { max-width: 880px; margin: 3rem auto; padding: 0 1rem; font-family: system-ui, sans-serif; color: #1f2937; }
header { display: flex; align-items: center; gap: 0.75rem; }
h1 { font-size: 1.8rem; }
.badge { background: #e0e7ff; color: #3730a3; padding: 0.2rem 0.5rem; border-radius: 6px; font-size: 0.8rem; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 1.2rem; margin-top: 1rem; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
.card h2 { margin-top: 0; font-size: 1.1rem; }
pre { background: #f9fafb; padding: 0.8rem; border-radius: 6px; overflow: auto; }
.muted { color: #6b7280; font-size: 0.9rem; }
</style>
