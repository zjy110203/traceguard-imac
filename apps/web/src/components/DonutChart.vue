<script setup lang="ts">
/** 四态分布环图：每段带真实计数与口径，可点击下钻。 */
import { computed } from 'vue'

interface Segment { label: string; value: number; color: string; hint?: string }
const props = defineProps<{ segments: Segment[]; centerLabel?: string; centerValue?: string | number }>()
const emit = defineEmits<{ select: [label: string] }>()

const total = computed(() => props.segments.reduce((sum, s) => sum + s.value, 0))
const R = 54
const C = 2 * Math.PI * R
const arcs = computed(() => {
  let offset = 0
  return props.segments.map((s) => {
    const share = total.value ? s.value / total.value : 0
    const arc = { ...s, dash: `${share * C} ${C}`, offset: -offset * C, pct: share }
    offset += share
    return arc
  })
})
</script>

<template>
  <div class="donut-wrap">
    <svg viewBox="0 0 140 140" class="donut-svg" role="img" aria-label="状态分布">
      <circle cx="70" cy="70" :r="R" fill="none" stroke="var(--neutral-weak)" stroke-width="18" />
      <circle
        v-for="a in arcs" :key="a.label"
        cx="70" cy="70" :r="R" fill="none" :stroke="a.color" stroke-width="18"
        :stroke-dasharray="a.dash" :stroke-dashoffset="a.offset"
        transform="rotate(-90 70 70)" class="donut-arc" @click="emit('select', a.label)"
      >
        <title>{{ a.label }}：{{ a.value }}（{{ (a.pct * 100).toFixed(1) }}%）</title>
      </circle>
      <text x="70" y="66" text-anchor="middle" class="donut-value">{{ centerValue ?? total }}</text>
      <text x="70" y="82" text-anchor="middle" class="donut-label">{{ centerLabel || '总样本' }}</text>
    </svg>
    <div class="donut-legend">
      <button v-for="s in segments" :key="s.label" class="legend-row" type="button" @click="emit('select', s.label)">
        <i class="swatch" :style="{ background: s.color }" />
        <span class="legend-name">{{ s.label }}</span>
        <strong>{{ s.value }}</strong>
        <span class="muted small">{{ total ? ((s.value / total) * 100).toFixed(1) : '0.0' }}%</span>
      </button>
    </div>
  </div>
</template>
