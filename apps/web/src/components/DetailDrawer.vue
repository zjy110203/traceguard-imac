<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { X } from '@lucide/vue'

const props = withDefaults(defineProps<{ open: boolean; title: string; subtitle?: string; width?: string }>(), { width: '520px' })
const emit = defineEmits<{ close: [] }>()
function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && props.open) emit('close') }
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="drawer-backdrop" @click.self="emit('close')">
      <aside class="drawer" :style="{ width }" role="dialog" aria-modal="true" :aria-label="title">
        <header class="drawer-head">
          <div>
            <h2>{{ title }}</h2>
            <div v-if="subtitle" class="small muted mono">{{ subtitle }}</div>
          </div>
          <button class="icon-btn" aria-label="关闭详情" @click="emit('close')"><X :size="16" /></button>
        </header>
        <div class="drawer-body"><slot /></div>
        <footer v-if="$slots.footer" class="drawer-foot"><slot name="footer" /></footer>
      </aside>
    </div>
  </Teleport>
</template>
