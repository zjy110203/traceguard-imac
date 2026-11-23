<script setup lang="ts">
/** 轻量代码审查式 Diff：按行对比 JSON 序列化结果，标注新增/删除/未变。 */
import { computed } from 'vue'

const props = defineProps<{ before: unknown; after: unknown; label?: string }>()
const serialize = (v: unknown) => {
  if (typeof v === 'string') return v.split('\n')
  try { return JSON.stringify(v, null, 2).split('\n') } catch { return [String(v)] }
}
const beforeLines = computed(() => serialize(props.before))
const afterLines = computed(() => serialize(props.after))

function annotate() {
  const beforeCount = new Map<string, number>()
  for (const line of beforeLines.value) beforeCount.set(line, (beforeCount.get(line) || 0) + 1)
  const afterCount = new Map<string, number>()
  for (const line of afterLines.value) afterCount.set(line, (afterCount.get(line) || 0) + 1)
  const left = beforeLines.value.map((line) => {
    const used = afterCount.get(line) || 0
    if (used > 0) { afterCount.set(line, used - 1); return { line, state: 'same' } }
    return { line, state: 'removed' }
  })
  const remain = new Map<string, number>()
  for (const line of beforeLines.value) remain.set(line, (remain.get(line) || 0) + 1)
  const right = afterLines.value.map((line) => {
    const used = remain.get(line) || 0
    if (used > 0) { remain.set(line, used - 1); return { line, state: 'same' } }
    return { line, state: 'added' }
  })
  return { left, right }
}
const diff = computed(annotate)
const added = computed(() => diff.value.right.filter((l) => l.state === 'added').length)
const removed = computed(() => diff.value.left.filter((l) => l.state === 'removed').length)
</script>

<template>
  <div class="code-diff">
    <div class="diff-head">
      <span class="small strong">{{ label || '版本差异' }}</span>
      <span class="diff-stat add">+{{ added }}</span>
      <span class="diff-stat del">−{{ removed }}</span>
    </div>
    <div class="diff-grid">
      <div class="diff-col">
        <div class="diff-col-head">原始版本</div>
        <pre><span v-for="(l, i) in diff.left" :key="i" class="diff-line" :class="l.state"><span class="diff-sign">{{ l.state === 'removed' ? '−' : ' ' }}</span>{{ l.line }}</span></pre>
      </div>
      <div class="diff-col">
        <div class="diff-col-head">修复候选</div>
        <pre><span v-for="(l, i) in diff.right" :key="i" class="diff-line" :class="l.state"><span class="diff-sign">{{ l.state === 'added' ? '+' : ' ' }}</span>{{ l.line }}</span></pre>
      </div>
    </div>
  </div>
</template>
