<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ data: unknown; label?: string }>()
const text = computed(() => {
  try {
    return JSON.stringify(props.data, null, 2)
  } catch {
    return String(props.data)
  }
})
</script>

<template>
  <div>
    <div v-if="label" class="small muted" style="margin-bottom: 4px">{{ label }}</div>
    <pre class="json" tabindex="0">{{ text }}</pre>
  </div>
</template>
