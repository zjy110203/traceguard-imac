<script setup lang="ts">
/** 统一指标卡：label / value / hint / tone / 可选下钻动作。 */
import { computed, type Component } from 'vue'
import { Activity, CircleAlert, CircleHelp, Gauge, Layers, ShieldCheck } from '@lucide/vue'

const props = withDefaults(defineProps<{
  label: string
  value: string | number
  hint?: string
  tone?: 'pass' | 'fail' | 'warn' | 'info' | 'incon' | 'neutral'
  icon?: 'activity' | 'alert' | 'help' | 'gauge' | 'layers' | 'shield'
  to?: string
}>(), { tone: 'neutral', icon: 'activity' })

const ICONS: Record<string, Component> = {
  activity: Activity, alert: CircleAlert, help: CircleHelp,
  gauge: Gauge, layers: Layers, shield: ShieldCheck,
}
const icon = computed(() => ICONS[props.icon] ?? Activity)
</script>

<template>
  <component :is="to ? 'router-link' : 'div'" :to="to" class="metric" :class="[`tone-${tone}`, { 'metric-link': !!to }]">
    <div class="metric-top">
      <span class="label">{{ label }}</span>
      <component :is="icon" :size="15" :stroke-width="1.8" aria-hidden="true" />
    </div>
    <div class="value">{{ value }}</div>
    <div v-if="hint" class="hint">{{ hint }}</div>
  </component>
</template>
