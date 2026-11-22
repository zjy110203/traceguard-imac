<script setup lang="ts">
defineProps<{ title: string; width?: string }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal" :style="width ? `width:${width}` : ''">
      <div class="modal-head">
        <h3>{{ title }}</h3>
        <button class="ghost sm x" @click="emit('close')">✕</button>
      </div>
      <div class="modal-body"><slot /></div>
      <div class="modal-foot"><slot name="footer" /></div>
    </div>
  </div>
</template>
