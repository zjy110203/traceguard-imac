<script setup lang="ts">
/** 真实时间序列折线图：X 轴为调用方传入的时间桶，不做累计计数伪装。 */
import { computed } from 'vue'

interface Series { label: string; color: string; values: number[] }
const props = withDefaults(defineProps<{
  series: Series[]
  labels: string[]
  height?: number
  max?: number
  format?: (v: number) => string
}>(), { height: 190 })

const emit = defineEmits<{ select: [index: number] }>()

const W = 720
const H = computed(() => props.height)
const PAD = { l: 38, r: 12, t: 14, b: 26 }
const plotW = computed(() => W - PAD.l - PAD.r)
const plotH = computed(() => H.value - PAD.t - PAD.b)
const upper = computed(() => {
  if (props.max !== undefined) return Math.max(1, props.max)
  const all = props.series.flatMap((s) => s.values)
  const peak = all.length ? Math.max(...all) : 1
  return Math.ceil(peak * 1.15) || 1
})
const count = computed(() => Math.max(props.labels.length, ...props.series.map((s) => s.values.length), 1))
const x = (i: number) => PAD.l + (count.value <= 1 ? plotW.value / 2 : (i / (count.value - 1)) * plotW.value)
const y = (v: number) => PAD.t + plotH.value - (Math.min(v, upper.value) / upper.value) * plotH.value
const ticks = computed(() => Array.from({ length: 4 }, (_, i) => Math.round((upper.value / 3) * i)))
const points = (s: Series) => s.values.map((v, i) => `${x(i)},${y(v)}`).join(' ')
const display = (v: number) => (props.format ? props.format(v) : String(v))
const shownLabels = computed(() => {
  const every = Math.max(1, Math.ceil(count.value / 7))
  return props.labels.map((label, i) => ({ label, i })).filter(({ i }) => i % every === 0 || i === count.value - 1)
})
</script>

<template>
  <div class="chart-frame">
    <svg :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="趋势图">
      <g v-for="t in ticks" :key="t">
        <line :x1="PAD.l" :x2="W - PAD.r" :y1="y(t)" :y2="y(t)" class="chart-grid-line" />
        <text :x="PAD.l - 7" :y="y(t) + 4" text-anchor="end" class="chart-axis-text">{{ display(t) }}</text>
      </g>
      <g v-for="l in shownLabels" :key="l.i">
        <text :x="x(l.i)" :y="H - 7" text-anchor="middle" class="chart-axis-text">{{ l.label }}</text>
      </g>
      <g v-for="s in series" :key="s.label">
        <polyline :points="points(s)" fill="none" :stroke="s.color" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        <circle v-for="(v, i) in s.values" :key="i" :cx="x(i)" :cy="y(v)" r="3" :fill="s.color" class="chart-point">
          <title>{{ s.label }} · {{ labels[i] || i }}：{{ display(v) }}</title>
        </circle>
      </g>
      <rect
        v-for="(_, i) in labels" :key="'hit' + i"
        :x="x(i) - plotW / Math.max(count - 1, 1) / 2" :y="PAD.t"
        :width="plotW / Math.max(count - 1, 1)" :height="plotH"
        fill="transparent" class="chart-hit" @click="emit('select', i)"
      ><title>{{ labels[i] }}</title></rect>
    </svg>
    <div class="chart-legend">
      <span v-for="s in series" :key="s.label" class="legend-item">
        <i class="swatch" :style="{ background: s.color }" />{{ s.label }}
      </span>
    </div>
  </div>
</template>
