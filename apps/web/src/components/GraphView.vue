<script setup lang="ts">
import { computed, ref } from 'vue'
import { Crosshair, Minus, Plus, RotateCcw } from '@lucide/vue'
import type { EvidenceGraph, GraphEdge, GraphNode } from '../types'

const props = defineProps<{ graph: EvidenceGraph | null }>()

const emit = defineEmits<{ select: [node: GraphNode] }>()

const ORDER = ['Run', 'Step', 'Assertion', 'Event', 'Evidence', 'Hypothesis', 'Source']
const COLOR: Record<string, string> = {
  Run: '#2f5bea',
  Step: '#0e9384',
  Assertion: '#7a5af8',
  Event: '#b54708',
  Evidence: '#475467',
  Hypothesis: '#c11574',
  Source: '#344054',
}
const MAX_PER_COL = 14
const hovered = ref<string | null>(null)
const hiddenTypes = ref<string[]>([])
const found = ref('')
const scale = ref(1)
const pan = ref({ x: 0, y: 0 })
const drag = ref<{ x: number; y: number; px: number; py: number } | null>(null)

function toggleType(k: string) {
  hiddenTypes.value = hiddenTypes.value.includes(k) ? hiddenTypes.value.filter((x) => x !== k) : [...hiddenTypes.value, k]
}
function zoom(delta: number) { scale.value = Math.min(2.4, Math.max(0.5, Number((scale.value + delta).toFixed(2)))) }
function resetView() { scale.value = 1; pan.value = { x: 0, y: 0 }; found.value = '' }
function onPointerDown(e: PointerEvent) { drag.value = { x: e.clientX, y: e.clientY, px: pan.value.x, py: pan.value.y } }
function onPointerMove(e: PointerEvent) {
  if (!drag.value) return
  pan.value = { x: drag.value.px + e.clientX - drag.value.x, y: drag.value.py + e.clientY - drag.value.y }
}
function locate() {
  const q = found.value.trim().toLowerCase()
  if (!q) return
  const hit = Object.values(layout.value.positions).find((p) => p.node.ref_id.toLowerCase().includes(q) || label(p.node).toLowerCase().includes(q))
  if (hit) {
    pan.value = { x: 160 - hit.x * scale.value, y: 180 - hit.y * scale.value }
    hovered.value = hit.node.node_id
  }
}
function onWheel(e: WheelEvent) { zoom(e.deltaY < 0 ? 0.1 : -0.1) }

interface Pos {
  node: GraphNode
  x: number
  y: number
}

const layout = computed(() => {
  const nodes = (props.graph?.nodes ?? []).filter((n) => !hiddenTypes.value.includes(ORDER.includes(n.node_type) ? n.node_type : 'Source'))
  const grouped: Record<string, GraphNode[]> = {}
  for (const n of nodes) {
    const key = ORDER.includes(n.node_type) ? n.node_type : 'Source'
    ;(grouped[key] ||= []).push(n)
  }
  const cols = ORDER.filter((k) => grouped[k]?.length)
  const positions: Record<string, Pos> = {}
  const W = Math.max(760, cols.length * 165)
  cols.forEach((k, ci) => {
    const q = found.value.trim().toLowerCase()
    const pool = q
      ? grouped[k].filter((n) => n.ref_id.toLowerCase().includes(q) || label(n).toLowerCase().includes(q))
      : grouped[k]
    const list = pool.slice(0, MAX_PER_COL)
    const total = list.length
    list.forEach((n, i) => {
      const y = total === 1 ? 210 : 40 + (i * 380) / (total - 1)
      positions[n.node_id] = { node: n, x: 60 + ci * 165, y }
    })
  })
  const edges = (props.graph?.edges ?? []).filter(
    (e) => positions[e.src] && positions[e.dst] && e.relation !== 'same_execution_context',
  )
  return { positions, edges, width: W, cols, grouped }
})

const activeSet = computed(() => {
  if (!hovered.value) return null
  const set = new Set<string>([hovered.value])
  for (const e of layout.value.edges) {
    if (e.src === hovered.value) set.add(e.dst)
    if (e.dst === hovered.value) set.add(e.src)
  }
  return set
})

function edgePath(e: GraphEdge): string {
  const a = layout.value.positions[e.src]
  const b = layout.value.positions[e.dst]
  const x1 = a.x + 104
  const y1 = a.y + 10
  const x2 = b.x
  const y2 = b.y + 10
  const mx = (x1 + x2) / 2
  return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`
}
function shortId(id: unknown): string {
  const s = String(id ?? '')
  return s.length > 8 ? s.slice(-6) : s
}
function label(n: GraphNode): string {
  if (n.node_type === 'Run') return 'Run'
  if (n.node_type === 'Step') return String(n.properties.index ?? '') + ' · ' + String(n.properties.action ?? '')
  if (n.node_type === 'Evidence') return String(n.properties.source_type ?? 'evd') + ' ' + shortId(n.ref_id)
  if (n.node_type === 'Event') return String(n.properties.event_type ?? 'event').replace('run.', '').replace('step.', 'step:')
  if (n.node_type === 'Assertion') return String(n.ref_id).split(':').pop() ?? 'assert'
  if (n.node_type === 'Hypothesis') return String(n.properties.mechanism ?? 'hyp')
  return shortId(n.ref_id)
}
function isDim(n: GraphNode) {
  return activeSet.value ? !activeSet.value.has(n.node_id) : false
}
</script>

<template>
  <div v-if="!graph" class="empty">暂无图数据</div>
  <div v-else>
    <div class="toolbar" style="margin-bottom: 8px">
      <button v-for="k in ORDER.filter((x) => layout.grouped[x]?.length)" :key="k" type="button" class="sm"
        :class="{ primary: !hiddenTypes.includes(k) }" :aria-pressed="!hiddenTypes.includes(k)" @click="toggleType(k)">
        <span :style="`display:inline-block;width:8px;height:8px;border-radius:2px;background:${COLOR[k]};margin-right:5px`" />
        {{ k }} {{ layout.grouped[k]?.length }}
      </button>
      <div class="graph-controls right">
        <input v-model="found" placeholder="定位节点 ID" aria-label="定位节点" style="width: 150px" @keyup.enter="locate" />
        <button class="sm" type="button" aria-label="定位" @click="locate"><Crosshair :size="13" /></button>
        <button class="sm" type="button" aria-label="缩小" @click="zoom(-0.15)"><Minus :size="13" /></button>
        <span class="small mono">{{ Math.round(scale * 100) }}%</span>
        <button class="sm" type="button" aria-label="放大" @click="zoom(0.15)"><Plus :size="13" /></button>
        <button class="sm" type="button" aria-label="重置视图" @click="resetView"><RotateCcw :size="13" /></button>
      </div>
    </div>
    <div v-if="(graph?.stats?.nodes ?? 0) > Object.keys(layout.positions).length" class="small muted" style="margin-bottom: 6px">
      当前显示 {{ Object.keys(layout.positions).length }} / {{ graph?.stats?.nodes }} 节点；可输入节点 ID 定位被折叠的节点。
    </div>
    <div class="graph-stage" @pointerdown="onPointerDown" @pointermove="onPointerMove"
      @pointerup="drag = null" @pointerleave="drag = null" @wheel.prevent="onWheel">
      <svg :width="layout.width" height="430" style="display: block"
        :style="{ transform: `scale(${scale}) translate(${pan.x}px, ${pan.y}px)`, transformOrigin: '0 0' }">
        <g v-for="e in layout.edges" :key="e.edge_id">
          <path
            :d="edgePath(e)"
            fill="none"
            :stroke="activeSet && (activeSet.has(e.src) && activeSet.has(e.dst)) ? '#2f5bea' : '#d0d5dd'"
            :stroke-width="activeSet && (activeSet.has(e.src) && activeSet.has(e.dst)) ? 1.8 : 1"
          />
        </g>
        <g v-for="p in Object.values(layout.positions)" :key="p.node.node_id" :opacity="isDim(p.node) ? 0.25 : 1"
          style="cursor: pointer"
          @mouseenter="hovered = p.node.node_id" @mouseleave="hovered = null" @click="emit('select', p.node)">
          <rect :x="p.x" :y="p.y" width="104" height="22" rx="5"
            :fill="COLOR[p.node.node_type] || '#475467'" />
          <text :x="p.x + 8" :y="p.y + 15" fill="#fff" font-size="10.5" font-family="ui-monospace, monospace">
            {{ label(p.node).slice(0, 15) }}
          </text>
          <title>{{ p.node.node_type }} · {{ p.node.ref_id }}</title>
        </g>
      </svg>
    </div>
  </div>
</template>
