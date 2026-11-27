<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  endpoints,
  fmtTime,
  type OracleReference,
  type OracleVersionRecord,
} from '../api'
import { authState, can, currentActor } from '../auth'
import AsyncBlock from '../components/AsyncBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'

const route = useRoute()
const oracleId = computed(() => String(route.params.id || ''))
const versions = ref<OracleVersionRecord[]>([])
const selectedVersion = ref<number | null>(null)
const detail = ref<Record<string, unknown>>({})
const references = ref<OracleReference[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref(false)
const note = ref('')

const current = computed(() => versions.value.find((v) => v.version === selectedVersion.value))
const allowed = (action: string): boolean =>
  authState.status?.auth_enabled === false || can(action)

async function load() {
  loading.value = true
  try {
    versions.value = await endpoints.oracleVersions(oracleId.value)
    error.value = ''
    if (!selectedVersion.value || !current.value) {
      selectedVersion.value = versions.value[0]?.version ?? null
    }
    await loadVersion()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function loadVersion() {
  if (!selectedVersion.value) return
  detail.value = await endpoints.oracleVersion(oracleId.value, selectedVersion.value)
  references.value = await endpoints.oracleReferences(oracleId.value, selectedVersion.value)
}

watch(selectedVersion, () => {
  loadVersion().catch((e) => notifyError(e))
})

async function approve() {
  if (!selectedVersion.value) return
  busy.value = true
  try {
    const result = await endpoints.approveOracle(
      oracleId.value,
      selectedVersion.value,
      currentActor(),
      note.value,
    )
    notify(`已基于 v${selectedVersion.value} 创建审批版本 v${Number(result.version)}`)
    note.value = ''
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader eyebrow="Catalog / Oracle Detail" :title="`Oracle ${oracleId}`"
      subtitle="历史版本不可覆盖；审批以服务端可信主体创建新版本，Agent / LLM 禁止自批。">
      <template #actions><button @click="load">刷新</button></template>
    </PageHeader>

    <AsyncBlock :loading="loading" :error="error" :empty="!versions.length" empty-title="Oracle 不存在">
      <div class="oracle-detail-layout">
        <div class="card">
          <div class="card-body tight">
            <h4>历史版本</h4>
            <ul class="version-list">
              <li v-for="v in versions" :key="v.version" :class="{ active: v.version === selectedVersion }">
                <button class="linklike" @click="selectedVersion = v.version">
                  v{{ v.version }} · {{ v.approved ? '已审批' : '待审批' }}
                </button>
                <span class="small muted">{{ fmtTime(v.created_at) }}</span>
              </li>
            </ul>
          </div>
        </div>

        <div class="card">
          <div class="card-body">
            <h4>v{{ selectedVersion }} 详情</h4>
            <p><b>预期行为</b>：{{ detail.expected_behavior || '—' }}</p>
            <p class="small"><b>需求引用</b>：{{ detail.requirement_ref || '—' }} ·
              <b>审批人</b>：{{ detail.approved_by || '—' }}</p>
            <pre class="json-block">{{ JSON.stringify(detail.evaluation_rule ?? {}, null, 2) }}</pre>
            <div v-if="allowed('oracle.create')" class="toolbar">
              <label for="oracle-note">审批备注</label>
              <input id="oracle-note" v-model="note" placeholder="复核说明" />
              <button class="primary" :disabled="busy" @click="approve">审批并新建版本</button>
            </div>
            <p class="small muted">审批不会替换历史版本；已审核用例绑定的 Oracle 版本保持固定。</p>
          </div>
        </div>

        <div class="card">
          <div class="card-body">
            <h4>审批记录</h4>
            <div class="scroll-x" tabindex="0">
              <table>
                <thead><tr><th>结果</th><th>主体</th><th>说明</th><th>时间</th></tr></thead>
                <tbody>
                  <tr v-for="(a, idx) in current?.approval_history || []" :key="idx">
                    <td><StatusBadge :value="a.approved ? 'APPROVED' : 'PENDING'" /></td>
                    <td class="mono small">{{ a.approved_by || '—' }}</td>
                    <td class="small">{{ a.note || '—' }}</td>
                    <td class="small muted">{{ fmtTime(String(a.at || '')) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <h4 style="margin-top: 18px">关联 TestCase（{{ references.length }}）</h4>
            <p v-if="!references.length" class="small muted">当前版本暂无用例绑定。</p>
            <div v-else class="scroll-x" tabindex="0">
              <table>
                <thead><tr><th>用例</th><th>版本</th><th>绑定版本</th><th>审核状态</th></tr></thead>
                <tbody>
                  <tr v-for="r in references" :key="r.testcase_id + '@' + r.version">
                    <td class="mono small">{{ r.testcase_id }}</td>
                    <td>v{{ r.version }}</td>
                    <td>v{{ r.oracle_version ?? '—' }}</td>
                    <td><StatusBadge :value="r.review_status" /></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>
  </div>
</template>

<style scoped>
.oracle-detail-layout { display: grid; gap: 16px; }
.version-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
.version-list li { display: flex; justify-content: space-between; gap: 8px; padding: 6px 8px; border-radius: 6px; }
.version-list li.active { background: var(--bg-subtle, rgba(0, 0, 0, 0.05)); }
.linklike { background: none; border: none; padding: 0; color: var(--brand, #2563eb); cursor: pointer; }
.json-block { margin-top: 10px; max-height: 220px; overflow: auto; font-size: 12px; }
</style>
