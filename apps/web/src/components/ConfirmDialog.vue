<script setup lang="ts">
import { TriangleAlert } from '@lucide/vue'
withDefaults(defineProps<{
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
}>(), { confirmLabel: '确认', danger: false })
const emit = defineEmits<{ confirm: []; cancel: [] }>()
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop" @click.self="emit('cancel')">
      <div class="confirm-dialog" role="alertdialog" aria-modal="true" :aria-label="title">
        <div class="confirm-icon" :class="{ danger }"><TriangleAlert :size="20" /></div>
        <h2>{{ title }}</h2>
        <p class="small">{{ message }}</p>
        <div class="confirm-actions">
          <button type="button" @click="emit('cancel')">取消</button>
          <button type="button" :class="danger ? 'danger' : 'primary'" @click="emit('confirm')">{{ confirmLabel }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
