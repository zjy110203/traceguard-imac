<script setup lang="ts">
/** 真实分类对比条形图：显示样本量/口径，可点击下钻。 */
import { computed } from 'vue'

interface Item { label: string; value: number; color?: string; hint?: string }
const props = withDefaults(defineProps<{ items: Item[]; max?: number; format?: (v: number) => string }>(), {})
const emit = defineEmits<{ select: [label: string] }>()
const peak = computed(() => props.max ?? Math.max(1, ...props.items.map((i) => i.value)))
const display = (v: number) => (props.format ? props.format(v) : String(v))
</script>

<template>
  <div class="bar-chart">
    <button v-for="item in items" :key="item.label" type="button" class="bar-row" @click="emit('select', item.label)">
      <span class="bar-name" :title="item.label">{{ item.label }}</span>
      <span class="bar-track"><span class="bar-fill" :style="{ width: `${Math.max(0, Math.min(100, (item.value / peak) * 100))}%`, background: item.color || 'var(--brand)' }" /></span>
      <span class="bar-value mono">{{ display(item.value) }}</span>
      <span v-if="item.hint" class="bar-hint small muted">{{ item.hint }}</span>
    </button>
    <div v-if="!items.length" class="empty small">暂无数据</div>
  </div>
</template>
