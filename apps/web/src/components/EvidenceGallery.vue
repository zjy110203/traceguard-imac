<script setup lang="ts">
import { computed, ref } from 'vue'
import { artifactUrl } from '../api'
import type { Evidence } from '../types'

const props = defineProps<{ items: Evidence[] }>()
const filter = ref<'all' | 'screenshot' | 'dom' | 'other'>('all')

const filtered = computed(() => {
  if (filter.value === 'all') return props.items
  return props.items.filter((e) => {
    const mt = String((e.content?.artifact as any)?.media_type ?? '')
    if (filter.value === 'screenshot') return mt.startsWith('image/')
    if (filter.value === 'dom') return mt.includes('html')
    return !mt.startsWith('image/') && !mt.includes('html')
  })
})

function artOf(e: Evidence): { id?: string; name?: string; media?: string; size?: number } {
  const a = (e.content?.artifact ?? {}) as any
  return { id: a.artifact_id, name: a.original_name, media: a.media_type, size: a.size }
}
function isImage(e: Evidence) {
  return String(artOf(e).media ?? '').startsWith('image/')
}
</script>

<template>
  <div>
    <div class="toolbar" style="margin-bottom: 10px">
      <button class="sm" :class="{ primary: filter === 'all' }" @click="filter = 'all'">全部 {{ items.length }}</button>
      <button class="sm" :class="{ primary: filter === 'screenshot' }" @click="filter = 'screenshot'">截图</button>
      <button class="sm" :class="{ primary: filter === 'dom' }" @click="filter = 'dom'">DOM</button>
      <button class="sm" :class="{ primary: filter === 'other' }" @click="filter = 'other'">其他</button>
    </div>
    <div class="evidence-grid">
      <div v-for="e in filtered" :key="e.evidence_id" class="evidence-card">
        <div class="thumb">
          <a v-if="isImage(e) && artOf(e).id" :href="artifactUrl(artOf(e).id!)" target="_blank" style="width: 100%; height: 100%">
            <img :src="artifactUrl(artOf(e).id!)" :alt="artOf(e).name" loading="lazy" />
          </a>
          <span v-else>{{ artOf(e).media || e.source_type }}</span>
        </div>
        <div class="meta">
          <div class="name" :title="artOf(e).name">{{ artOf(e).name || e.evidence_id }}</div>
          <div class="muted flex" style="justify-content: space-between">
            <span>{{ e.kind }} · {{ e.source_type }}</span>
            <a v-if="artOf(e).id" :href="artifactUrl(artOf(e).id!)" target="_blank">打开</a>
          </div>
          <div class="muted" style="font-family: var(--mono); font-size: 10px">{{ e.sha256.slice(0, 12) }}… · {{ e.source_trust }}</div>
        </div>
      </div>
    </div>
    <div v-if="filtered.length === 0" class="empty">该分类下暂无证据</div>
  </div>
</template>
