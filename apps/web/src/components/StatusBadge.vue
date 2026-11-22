<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ value: string | null | undefined; kind?: 'verdict' | 'status' | 'review' | 'repair' | 'probe' | 'auto' }>()

const LABELS: Record<string, string> = {
  PASSED: 'PASSED',
  FAILED: 'FAILED',
  INCONCLUSIVE: 'INCONCLUSIVE',
  NOT_EVALUATED: 'NOT_EVAL',
  QUEUED: '排队中',
  PREPARING: '准备中',
  RUNNING: '执行中',
  FINALIZING: '收尾中',
  COMPLETED: '已完成',
  TIMED_OUT: '超时',
  CANCELLED: '已取消',
  APPROVED: '已通过',
  PENDING: '待审核',
  PENDING_APPROVAL: '待审批',
  DRAFT: '草稿',
  REJECTED: '已拒绝',
  VALIDATING: '校验中',
  VALIDATED: '已校验',
  VALIDATION_FAILED: '校验失败',
  PROMOTING: '晋升中',
  PROMOTED: '已晋升',
  PROMOTION_FAILED: '晋升失败',
  ROLLED_BACK: '已回滚',
  SUCCEEDED: '成功',
  BLOCKED: '已拦截',
  CONFIRMED: '已确认',
  PROBABLE: '可能',
  SUPPORTED: '有支持',
  CONTRADICTED: '被反驳',
  CANDIDATE: '候选',
}

const tone = computed(() => {
  const v = (props.value ?? '').toUpperCase()
  if (['PASSED', 'COMPLETED', 'APPROVED', 'SUCCEEDED', 'PROMOTED', 'CONFIRMED', 'VALIDATED'].includes(v)) return 'pass'
  if (['FAILED', 'TIMED_OUT', 'REJECTED', 'VALIDATION_FAILED', 'PROMOTION_FAILED', 'BLOCKED', 'CONTRADICTED'].includes(v)) return 'fail'
  if (['PENDING', 'PENDING_APPROVAL', 'RUNNING', 'QUEUED', 'PREPARING', 'FINALIZING', 'INCONCLUSIVE', 'PROBABLE', 'SUPPORTED', 'CANDIDATE', 'VALIDATING', 'PROMOTING', 'DRAFT'].includes(v)) return 'warn'
  if (['NOT_EVALUATED', 'CANCELLED', 'ROLLED_BACK'].includes(v)) return 'neutral'
  return 'neutral'
})

const label = computed(() => {
  const v = props.value ?? '—'
  return LABELS[v.toUpperCase()] ?? v
})
</script>

<template>
  <span class="badge" :class="tone">{{ label }}</span>
</template>
