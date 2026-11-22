<script setup lang="ts">
defineProps<{ label: string; value: string | number; hint?: string; tone?: 'pass' | 'fail' | 'warn' | 'info' }>()
</script>

<template>
  <div class="metric">
    <div class="label">{{ label }}</div>
    <div class="value" :style="tone === 'fail' ? 'color:var(--fail)' : tone === 'pass' ? 'color:var(--pass)' : tone === 'warn' ? 'color:var(--warn)' : ''">
      {{ value }}
    </div>
    <div v-if="hint" class="hint">{{ hint }}</div>
  </div>
</template>
