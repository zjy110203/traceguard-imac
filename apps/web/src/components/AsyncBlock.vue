<script setup lang="ts">
defineProps<{
  loading?: boolean
  error?: string | null
  empty?: boolean
  emptyTitle?: string
  emptyHint?: string
  loadingText?: string
}>()
</script>

<template>
  <div v-if="loading" class="state"><span class="spinner" /> <span style="margin-left: 8px">{{ loadingText || '加载中…' }}</span></div>
  <div v-else-if="error" class="state error">{{ error }}</div>
  <div v-else-if="empty" class="empty">
    <div class="title">{{ emptyTitle || '暂无数据' }}</div>
    <div v-if="emptyHint" class="small">{{ emptyHint }}</div>
    <slot name="empty-action" />
  </div>
  <slot v-else />
</template>
