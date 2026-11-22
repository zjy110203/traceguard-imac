<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EvidenceGraph, GraphEdge, GraphNode } from '../types'

const props = defineProps<{ graph: EvidenceGraph | null }>()

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

interface Pos {
  node: GraphNode
  x: number
  y: number
}

const layout = computed(() => {
  const nodes = props.graph?.nodes ?? []
  const grouped: Record<string, GraphNode[]> = {}
  for (const n of nodes) {
    const key = ORDER.includes(n.node_type) ? n.node_type : 'Source'
    ;(grouped[key] ||= []).push(n)
  }
  const cols = ORDER.filter((k) => grouped[k]?.length)
  const positions: Record<string, Pos> = {}
  const W = Math.max(760, cols.length * 165)
  cols.forEach((k, ci) => {
    const list = grouped[k].slice(0, MAX_PER_COL)
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
function label(n: GraphNode): string {
  if (n.node_type === 'Run') return 'Run'
  if (n.node_type === 'Step') return String(n.properties.index ?? '') + ' · ' + String(n.properties.action ?? '')
  if (n.node_type === 'Evidence') return String(n.properties.kind ?? 'evd')
  if (n.node_type === 'Assertion') return String(n.ref_id).split(':').pop() ?? 'assert'
  if (n.node_type === 'Hypothesis') return String(n.properties.mechanism ?? 'hyp')
  return String(n.ref_id).slice(0, 18)
}
function isDim(n: GraphNode) {
  return activeSet.value ? !activeSet.value.has(n.node_id) : false
}
</script>

<template>
  <div v-if="!graph" class="empty">暂无图数据</div>
  <div v-else>
    <div class="toolbar" style="margin-bottom: 8px">
      <span v-for="k in layout.cols" :key="k" class="chip">
        <span :style="`display:inline-block;width:8px;height:8px;border-radius:2px;background:${COLOR[k]};margin-right:5px`" />
        {{ k }} {{ layout.grouped[k]?.length }}
      </span>
      <span class="muted small right">{{ layout.edges.length }} 条语义边（省略同上下文边）</span>
    </div>
    <div class="scroll-x" style="border: 1px solid var(--border); border-radius: 8px; background: #fcfcfd">
      <svg :width="layout.width" height="430" style="display: block">
        <g v-for="e in layout.edges" :key="e.edge_id">
          <path
            :d="edgePath(e)"
            fill="none"
            :stroke="activeSet && (activeSet.has(e.src) && activeSet.has(e.dst)) ? '#2f5bea' : '#d0d5dd'"
            :stroke-width="activeSet && (activeSet.has(e.src) && activeSet.has(e.dst)) ? 1.8 : 1"
          />
        </g>
        <g v-for="p in Object.values(layout.positions)" :key="p.node.node_id" :opacity="isDim(p.node) ? 0.25 : 1"
          @mouseenter="hovered = p.node.node_id" @mouseleave="hovered = null">
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
