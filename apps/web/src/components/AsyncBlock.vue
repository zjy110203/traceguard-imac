<script setup lang="ts">
/** 所有数据页统一三态：加载骨架 / 错误 / 空状态。 */
import { CircleAlert, Inbox } from '@lucide/vue'

withDefaults(defineProps<{
  loading?: boolean
  error?: string | null
  empty?: boolean
  emptyTitle?: string
  emptyHint?: string
  loadingText?: string
  skeleton?: number
}>(), { skeleton: 3 })
</script>

<template>
  <div v-if="loading" class="skeleton-stack" role="status" :aria-label="loadingText || '加载中'">
    <div v-for="n in skeleton" :key="n" class="skeleton-row" :style="{ width: `${100 - (n % 3) * 12}%` }" />
    <span class="sr-only">{{ loadingText || '加载中…' }}</span>
  </div>
  <div v-else-if="error" class="state error" role="alert">
    <CircleAlert :size="18" aria-hidden="true" />
    <div class="state-title">数据加载失败</div>
    <div class="small">{{ error }}</div>
  </div>
  <div v-else-if="empty" class="empty">
    <div class="empty-icon"><Inbox :size="22" :stroke-width="1.5" aria-hidden="true" /></div>
    <div class="title">{{ emptyTitle || '暂无数据' }}</div>
    <div v-if="emptyHint" class="small">{{ emptyHint }}</div>
    <slot name="empty-action" />
  </div>
  <slot v-else />
</template>
