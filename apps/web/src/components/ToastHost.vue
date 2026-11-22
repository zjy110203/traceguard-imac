<script setup lang="ts">
import { toastState } from '../toast'
</script>

<template>
  <div v-if="toastState" class="toast" :class="{ error: toastState.error }">{{ toastState.text }}</div>
</template>
