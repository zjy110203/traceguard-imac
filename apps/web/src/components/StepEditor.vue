<script setup lang="ts">
/** RUN-019 WP1：TestCase 可视化步骤/断言编辑器（支持 JSON 高级模式）。 */
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Plus, Trash2 } from '@lucide/vue'
import { notify } from '../toast'

export interface EditableStep {
  step_id: string
  action: string
  target?: { strategy?: string; value?: string; source?: string } | null
  value?: string | null
  description?: string
}

export interface EditableAssertion {
  assertion_id: string
  description: string
  kind: string
  required: boolean
  expected: Record<string, unknown>
  oracle_id?: string | null
  oracle_version?: number | null
}

const props = defineProps<{
  steps: EditableStep[]
  assertions: EditableAssertion[]
  oracles?: Array<{ oracle_id: string; version: number; approved: boolean }>
}>()
const emit = defineEmits<{
  (e: 'update:steps', value: EditableStep[]): void
  (e: 'update:assertions', value: EditableAssertion[]): void
}>()

const ACTIONS = ['navigate', 'click', 'fill', 'select', 'press', 'assert', 'wait', 'api']
const STRATEGIES = ['role', 'accessible_name', 'label', 'placeholder', 'test_id', 'css', 'xpath']
const KINDS = ['ui', 'business_state', 'api', 'network', 'consistency']

const mode = ref<'visual' | 'json'>('visual')
const jsonText = ref('')
const advancedError = ref('')

function newId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 8)}`
}

function syncJson(): void {
  if (mode.value === 'json') return
  jsonText.value = JSON.stringify({ steps: props.steps, assertions: props.assertions }, null, 2)
}
watch(() => [props.steps, props.assertions], syncJson, { deep: true, immediate: true })

function toggleMode(next: 'visual' | 'json'): void {
  if (next === mode.value) return
  if (next === 'visual') {
    try {
      const parsed = JSON.parse(jsonText.value) as {
        steps?: EditableStep[]
        assertions?: EditableAssertion[]
      }
      if (!Array.isArray(parsed.steps) || !Array.isArray(parsed.assertions)) {
        throw new Error('JSON 必须包含 steps 与 assertions 数组')
      }
      emit('update:steps', parsed.steps)
      emit('update:assertions', parsed.assertions)
      advancedError.value = ''
    } catch (e) {
      advancedError.value = e instanceof Error ? e.message : String(e)
      notify('JSON 解析失败，已保留高级编辑内容', true)
      return
    }
  } else {
    syncJson()
  }
  mode.value = next
}

function addStep(): void {
  emit('update:steps', [
    ...props.steps,
    { step_id: newId('s'), action: 'click', target: { strategy: 'test_id', value: '' }, value: '', description: '' },
  ])
}

function updateStep(index: number, patch: Partial<EditableStep>): void {
  const next = props.steps.map((s, i) => (i === index ? { ...s, ...patch } : s))
  emit('update:steps', next)
}

function updateTarget(index: number, patch: Record<string, string>): void {
  const step = props.steps[index]
  updateStep(index, { target: { ...(step.target || {}), ...patch } })
}

function removeStep(index: number): void {
  emit('update:steps', props.steps.filter((_, i) => i !== index))
}

function moveStep(index: number, delta: number): void {
  const to = index + delta
  if (to < 0 || to >= props.steps.length) return
  const next = [...props.steps]
  const [item] = next.splice(index, 1)
  next.splice(to, 0, item)
  emit('update:steps', next)
}

function addAssertion(): void {
  emit('update:assertions', [
    ...props.assertions,
    {
      assertion_id: newId('a'),
      description: '',
      kind: 'business_state',
      required: true,
      expected: {},
      oracle_id: null,
      oracle_version: null,
    },
  ])
}

function updateAssertion(index: number, patch: Partial<EditableAssertion>): void {
  emit(
    'update:assertions',
    props.assertions.map((a, i) => (i === index ? { ...a, ...patch } : a)),
  )
}

function removeAssertion(index: number): void {
  emit('update:assertions', props.assertions.filter((_, i) => i !== index))
}

const availableOracles = computed(() => props.oracles || [])
</script>

<template>
  <div class="step-editor">
    <div class="toolbar" style="justify-content: space-between">
      <div class="flex" style="gap: 6px">
        <button type="button" class="sm" :class="mode === 'visual' ? 'primary' : ''" @click="toggleMode('visual')">可视化编辑</button>
        <button type="button" class="sm" :class="mode === 'json' ? 'primary' : ''" @click="toggleMode('json')">JSON 高级模式</button>
      </div>
      <span class="small muted">{{ steps.length }} 步骤 · {{ assertions.length }} 断言</span>
    </div>

    <template v-if="mode === 'visual'">
      <h4>执行步骤</h4>
      <div v-for="(s, idx) in steps" :key="s.step_id + '-' + idx" class="step-row">
        <div class="step-head">
          <span class="step-index">#{{ idx + 1 }}</span>
          <input :aria-label="`步骤 ${idx + 1} ID`" :value="s.step_id" class="mono small id-input" readonly />
          <select :aria-label="`步骤 ${idx + 1} 动作`" :value="s.action" @change="updateStep(idx, { action: ($event.target as HTMLSelectElement).value })">
            <option v-for="a in ACTIONS" :key="a" :value="a">{{ a }}</option>
          </select>
          <button type="button" class="sm" :aria-label="`上移步骤 ${idx + 1}`" :disabled="idx === 0" @click="moveStep(idx, -1)"><ArrowUp :size="12" /></button>
          <button type="button" class="sm" :aria-label="`下移步骤 ${idx + 1}`" :disabled="idx === steps.length - 1" @click="moveStep(idx, 1)"><ArrowDown :size="12" /></button>
          <button type="button" class="sm danger" :aria-label="`删除步骤 ${idx + 1}`" @click="removeStep(idx)"><Trash2 :size="12" /></button>
        </div>
        <div class="step-fields">
          <select :aria-label="`步骤 ${idx + 1} 定位策略`" :value="s.target?.strategy || ''" @change="updateTarget(idx, { strategy: ($event.target as HTMLSelectElement).value })">
            <option value="">（无需定位）</option>
            <option v-for="t in STRATEGIES" :key="t" :value="t">{{ t }}</option>
          </select>
          <input :aria-label="`步骤 ${idx + 1} 目标元素`" :value="s.target?.value || ''" placeholder="目标元素 / locator 值"
            @input="updateTarget(idx, { value: ($event.target as HTMLInputElement).value })" />
          <input :aria-label="`步骤 ${idx + 1} 输入值`" :value="s.value || ''" placeholder="输入值 / URL / 参数"
            @input="updateStep(idx, { value: ($event.target as HTMLInputElement).value })" />
          <input :aria-label="`步骤 ${idx + 1} 描述`" :value="s.description || ''" placeholder="步骤描述"
            @input="updateStep(idx, { description: ($event.target as HTMLInputElement).value })" />
        </div>
      </div>
      <button type="button" class="sm" @click="addStep"><Plus :size="12" /> 新增步骤</button>

      <h4 style="margin-top: 16px">断言与 Oracle 绑定</h4>
      <div v-for="(a, idx) in assertions" :key="a.assertion_id + '-' + idx" class="step-row">
        <div class="step-head">
          <input :aria-label="`断言 ${idx + 1} ID`" :value="a.assertion_id" class="mono small id-input" readonly />
          <select :aria-label="`断言 ${idx + 1} 类型`" :value="a.kind" @change="updateAssertion(idx, { kind: ($event.target as HTMLSelectElement).value })">
            <option v-for="k in KINDS" :key="k" :value="k">{{ k }}</option>
          </select>
          <label class="flex small"><input type="checkbox" :checked="a.required"
            @change="updateAssertion(idx, { required: ($event.target as HTMLInputElement).checked })" /> required</label>
          <button type="button" class="sm danger" :aria-label="`删除断言 ${idx + 1}`" @click="removeAssertion(idx)"><Trash2 :size="12" /></button>
        </div>
        <div class="step-fields">
          <input :aria-label="`断言 ${idx + 1} 描述`" :value="a.description" placeholder="断言描述"
            @input="updateAssertion(idx, { description: ($event.target as HTMLInputElement).value })" />
          <select :aria-label="`断言 ${idx + 1} Oracle`" :value="a.oracle_id || ''"
            @change="updateAssertion(idx, { oracle_id: ($event.target as HTMLSelectElement).value || null })">
            <option value="">（未绑定）</option>
            <option v-for="o in availableOracles" :key="o.oracle_id + '@' + o.version" :value="o.oracle_id">
              {{ o.oracle_id }} @v{{ o.version }}{{ o.approved ? '' : '（未审批）' }}
            </option>
          </select>
          <input :aria-label="`断言 ${idx + 1} Oracle 版本`" type="number" min="1" :value="a.oracle_version ?? ''"
            placeholder="固定版本" @input="updateAssertion(idx, { oracle_version: ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : null })" />
        </div>
      </div>
      <button type="button" class="sm" @click="addAssertion"><Plus :size="12" /> 新增断言</button>
    </template>

    <template v-else>
      <label for="step-json">steps / assertions JSON</label>
      <textarea id="step-json" v-model="jsonText" class="mono" rows="18" spellcheck="false" />
      <p v-if="advancedError" class="small" role="alert">{{ advancedError }}</p>
      <p class="small muted">切回可视化编辑时会解析 JSON 并覆盖当前步骤；解析失败则保留原文。</p>
    </template>
  </div>
</template>

<style scoped>
.step-row { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 8px; }
.step-head, .step-fields { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.step-fields { margin-top: 8px; }
.step-fields input, .step-fields select { flex: 1 1 180px; min-width: 140px; }
.step-index { font-weight: 600; min-width: 30px; }
.id-input { width: 130px; flex: 0 0 130px; }
</style>
