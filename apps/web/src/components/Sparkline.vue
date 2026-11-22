<script setup lang="ts">
/** SVG 迷你趋势图：接受真实数据序列，不支持空想数据。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    series: { label: string; color: string; values: number[] }[]
    height?: number
  }>(),
  { height: 120 },
)

const W = 560
const H = computed(() => props.height)
const PAD = { l: 28, r: 8, t: 8, b: 18 }

const maxY = computed(() => {
  let m = 0
  for (const s of props.series) for (const v of s.values) m = Math.max(m, v)
  return m <= 5 ? 5 : Math.ceil(m / 5) * 5
})

const n = computed(() => Math.max(...props.series.map((s) => s.values.length), 1))

function pt(i: number, v: number): [number, number] {
  const x = PAD.l + (i / Math.max(n.value - 1, 1)) * (W - PAD.l - PAD.r)
  const y = H.value - PAD.b - (v / maxY.value) * (H.value - PAD.t - PAD.b)
  return [x, y]
}

function pathFor(values: number[]): string {
  if (!values.length) return ''
  return values.map((v, i) => `${i === 0 ? 'M' : 'L'}${pt(i, v)[0].toFixed(1)},${pt(i, v)[1].toFixed(1)}`).join(' ')
}

const gridLines = computed(() => {
  const lines: { y: number; label: string }[] = []
  const steps = 3
  for (let i = 0; i <= steps; i++) {
    const v = (maxY.value / steps) * i
    lines.push({ y: H.value - PAD.b - (v / maxY.value) * (H.value - PAD.t - PAD.b), label: String(Math.round(v)) })
  }
  return lines
})
</script>

<template>
  <div v-if="n === 1 && series.every((s) => s.values.length === 0)" class="empty">暂无时间序列数据</div>
  <div v-else class="chart-wrap">
    <svg :viewBox="`0 0 ${W} ${H}`" :height="H" role="img" aria-label="结论趋势图">
      <g v-for="g in gridLines" :key="g.label">
        <line :x1="PAD.l" :y1="g.y" :x2="W - PAD.r" :y2="g.y" stroke="var(--border)" stroke-width="1" />
        <text :x="PAD.l - 5" :y="g.y + 3.5" text-anchor="end" font-size="10" fill="var(--text-3)">{{ g.label }}</text>
      </g>
      <g v-for="s in series" :key="s.label">
        <path :d="pathFor(s.values)" fill="none" :stroke="s.color" stroke-width="1.8" stroke-linejoin="round" />
        <circle
          v-for="(v, i) in s.values"
          :key="i"
          :cx="pt(i, v)[0]"
          :cy="pt(i, v)[1]"
          r="2.4"
          :fill="s.color"
        >
          <title>{{ s.label }}: {{ v }}</title>
        </circle>
      </g>
    </svg>
    <div class="legend" style="margin-top: 4px">
      <span v-for="s in series" :key="s.label"><span class="swatch" :style="`background:${s.color}`" />{{ s.label }}</span>
    </div>
  </div>
</template>
