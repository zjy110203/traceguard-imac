<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, artifactUrl, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Diagnosis, Disposition, Evidence, ProbeExecution } from '../types'

const route = useRoute()
const router = useRouter()
const id = String(route.params.id)
const diag = ref<Diagnosis | null>(null)
const dispositions = ref<Disposition[]>([])
const evidence = ref<Evidence[]>([])
const probes = ref<ProbeExecution[]>([])
const suggestions = ref<{ probe_id: string; params: Record<string, unknown>; why: string; score: number }[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const reviewer = ref('role:qa-lead')

const disposition = computed(() => dispositions.value.find((p) => p.diagnosis_id === id))

async function load() {
  loading.value = true
  try {
    const d = await api.get<Diagnosis>(`/diagnoses/${id}`)
    diag.value = d
    const [disp, pe, pex] = await Promise.all([
      api.get<Disposition[]>('/dispositions?limit=200'),
      Promise.all((d.evidence_refs ?? []).map((eid) => api.get<Evidence>(`/evidence/${eid}`).catch(() => null))),
      api.get<ProbeExecution[]>(`/probes/executions?run_id=${d.run_id}&limit=50`),
    ])
    dispositions.value = disp
    evidence.value = pe.filter((x): x is Evidence => !!x)
    probes.value = pex
    suggestions.value = await api
      .get<{ probe_id: string; params: Record<string, unknown>; why: string; score: number }[]>(
        `/runs/${d.run_id}/probe-suggestions?diagnosis_id=${id}`,
      )
      .catch(() => [])
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function runProbe(p: { probe_id: string; params: Record<string, unknown> }) {
  if (!diag.value) return
  busy.value = 'probe'
  try {
    const r = await api.post<ProbeExecution>('/probes/execute', {
      probe_id: p.probe_id,
      params: { ...p.params, run_id: diag.value.run_id, diagnosis_id: id },
    })
    notify('探针执行：' + r.status)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function reviewDisposition(status: string) {
  if (!disposition.value) return
  busy.value = 'review'
  try {
    await api.post(`/dispositions/${disposition.value.disposition_id}/review`, {
      review_status: status,
      reviewer: reviewer.value,
      note: '控制台处置',
    })
    notify('处置已更新：' + status)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div>
    <AsyncBlock :loading="loading" :error="error">
      <div v-if="diag">
        <div class="page-head">
          <div>
            <div class="flex">
              <h1>诊断详情</h1>
              <StatusBadge :value="diag.outcome" />
              <StatusBadge :value="diag.disposition" />
            </div>
            <div class="sub mono">{{ diag.diagnosis_id }} · run {{ diag.run_id }}</div>
          </div>
          <div class="actions">
            <button @click="router.push('/diagnoses')">返回</button>
            <button @click="router.push(`/runs/${diag.run_id}`)">查看 Run</button>
          </div>
        </div>

        <div class="split">
          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>症状与假设</h2><div class="actions"><span class="small muted">engine {{ diag.engine }}</span></div></div>
              <div class="card-body">
                <div class="kv">
                  <dt>症状</dt><dd class="mono small">{{ diag.symptom }}</dd>
                  <dt>Flaky 特征</dt><dd>{{ diag.flaky_characteristic ? '是（多次执行结果不一致）' : '否' }}</dd>
                  <dt>已确认根因</dt><dd>{{ diag.root_causes.length }} 条</dd>
                </div>
                <div class="hr" />
                <div v-for="h in diag.hypotheses" :key="h.hypothesis_id" class="card" style="box-shadow:none; margin-bottom: 10px">
                  <div class="card-body">
                    <div class="flex wrap">
                      <StatusBadge :value="h.status" />
                      <span class="badge neutral">{{ h.failure_domain }}</span>
                      <span class="badge neutral">{{ h.failure_mechanism }}</span>
                      <span class="badge neutral">by {{ h.suggested_by }}</span>
                      <span v-if="h.flaky_characteristic" class="badge warn">flaky</span>
                    </div>
                    <div class="strong" style="margin-top: 6px">{{ h.description }}</div>
                    <div v-if="h.contributing_factors.length" class="small muted" style="margin-top: 4px">
                      贡献因素：{{ h.contributing_factors.join(' / ') }}
                    </div>
                    <div class="hr" />
                    <div class="small muted">支持证据</div>
                    <div class="chip-row" style="margin-top: 4px">
                      <span v-for="s in h.supporting" :key="s.id" class="chip">{{ s.id }} · {{ s.trust }}{{ s.confirming ? ' · 权威确认' : '' }}</span>
                      <span v-if="h.supporting.length === 0" class="small muted">无</span>
                    </div>
                    <div class="small muted" style="margin-top: 8px">反驳证据</div>
                    <div class="chip-row" style="margin-top: 4px">
                      <span v-for="s in h.contradicting" :key="s.id" class="chip">{{ s.id }}</span>
                      <span v-if="h.contradicting.length === 0" class="small muted">无</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-head"><h2>探针建议与执行记录</h2>
                <div class="actions"><span class="small muted">只读 · 白名单 · 预算受限</span></div>
              </div>
              <div class="card-body tight scroll-x">
                <table>
                  <thead><tr><th>探针</th><th>目的</th><th>状态</th><th></th></tr></thead>
                  <tbody>
                    <tr v-for="p in suggestions" :key="'s-' + p.probe_id">
                      <td class="mono small">{{ p.probe_id }}</td>
                      <td class="small">{{ p.why }}</td>
                      <td class="small muted">建议 · 评分 {{ p.score }}</td>
                      <td><button class="sm" :disabled="busy === 'probe'" @click="runProbe(p)">执行</button></td>
                    </tr>
                    <tr v-for="x in probes" :key="x.execution_id">
                      <td class="mono small">{{ x.probe_id }}</td>
                      <td class="mono small truncate" style="max-width: 320px">{{ JSON.stringify(x.result) }}</td>
                      <td><StatusBadge :value="x.status" /></td>
                      <td class="small muted">{{ x.permission_decision }}</td>
                    </tr>
                    <tr v-if="suggestions.length === 0 && probes.length === 0"><td colspan="4"><div class="empty">暂无探针</div></td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>处置（Disposition）</h2></div>
              <div class="card-body">
                <div v-if="disposition">
                  <div class="kv">
                    <dt>类型</dt><dd><StatusBadge :value="disposition.review_status" /> {{ disposition.kind }}</dd>
                    <dt>路由目标</dt><dd class="mono small">{{ disposition.route_target }}</dd>
                    <dt>原因</dt><dd class="small">{{ disposition.reason }}</dd>
                  </div>
                  <div class="hr" />
                  <label>审核人</label>
                  <input v-model="reviewer" />
                  <div class="flex" style="margin-top: 8px">
                    <button class="sm primary" :disabled="busy === 'review'" @click="reviewDisposition('APPROVED')">批准分流</button>
                    <button class="sm danger" :disabled="busy === 'review'" @click="reviewDisposition('REJECTED')">驳回</button>
                  </div>
                </div>
                <div v-else class="empty">无处置记录</div>
              </div>
            </div>

            <div class="card">
              <div class="card-head"><h2>引用证据</h2><div class="actions"><span class="small muted">{{ evidence.length }}</span></div></div>
              <div class="card-body">
                <div v-for="e in evidence" :key="e.evidence_id" class="flex" style="justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border)">
                  <div style="min-width: 0">
                    <div class="mono small truncate">{{ e.evidence_id }}</div>
                    <div class="muted" style="font-size: 10.5px">{{ e.source_type }} · {{ e.source_trust }} · {{ fmtTime(e.captured_at) }}</div>
                  </div>
                  <a v-if="e.attachment_ref" :href="artifactUrl(e.attachment_ref)" target="_blank" class="small">附件</a>
                </div>
                <div v-if="evidence.length === 0" class="empty">无</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </AsyncBlock>
  </div>
</template>
