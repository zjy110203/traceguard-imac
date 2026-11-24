<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, artifactUrl, fmtTime } from '../api'
import AsyncBlock from '../components/AsyncBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { notify, notifyError } from '../toast'
import type { Diagnosis, Disposition, Evidence, ProbeExecution, ProbePlan } from '../types'

const route = useRoute()
const router = useRouter()
const id = String(route.params.id)
const diag = ref<Diagnosis | null>(null)
const dispositions = ref<Disposition[]>([])
const evidence = ref<Evidence[]>([])
const probes = ref<ProbeExecution[]>([])
const plan = ref<ProbePlan | null>(null)
const suggestions = ref<{ probe_id: string; params: Record<string, unknown>; why: string; score: number }[]>([])
const loading = ref(true)
const error = ref('')
const partialError = ref('')
const busy = ref('')
const reviewer = ref('role:qa-lead')

const disposition = computed(() => dispositions.value.find((p) => p.diagnosis_id === id))
const conflicts = computed(() => diag.value?.unresolved_conflicts ?? [])

/** 主动诊断闭环：证据缺口 → 探针 → 回灌 → 重新裁决（一次调用内完成，可复现） */
async function runActiveLoop() {
  if (!diag.value) return
  busy.value = 'loop'
  try {
    const r = await api.post<ProbePlan>(
      `/runs/${diag.value.run_id}/probe-plan?diagnosis_id=${id}`,
      {},
    )
    plan.value = r
    notify(`主动诊断：${r.stop_reason}`)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    busy.value = ''
  }
}

async function load() {
  loading.value = true
  partialError.value = ''
  try {
    const d = await api.get<Diagnosis>(`/diagnoses/${id}`)
    diag.value = d
    const partial: string[] = []
    const [disp, pe, pex, plans] = await Promise.all([
      api.get<Disposition[]>('/dispositions?limit=200'),
      Promise.all(
        (d.evidence_refs ?? []).map((eid) =>
          api
            .get<Evidence>(`/evidence/${eid}`)
            .catch(() => {
              partial.push(`证据 ${eid}`)
              return null
            }),
        ),
      ),
      api.get<ProbeExecution[]>(`/probes/executions?run_id=${d.run_id}&limit=50`),
      api.get<ProbePlan[]>(`/probe-plans?run_id=${d.run_id}&limit=50`).catch(() => {
        partial.push('探针计划')
        return []
      }),
    ])
    dispositions.value = disp
    evidence.value = pe.filter((x): x is Evidence => !!x)
    probes.value = pex
    plan.value = plans.find((p) => p.diagnosis_id === id) ?? null
    suggestions.value = await api
      .get<{ probe_id: string; params: Record<string, unknown>; why: string; score: number }[]>(
        `/runs/${d.run_id}/probe-suggestions?diagnosis_id=${id}`,
      )
      .catch(() => {
        partial.push('探针建议')
        return []
      })
    if (partial.length) partialError.value = `以下数据加载失败，页面可能不完整：${partial.join('、')}`
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
      <div v-if="partialError" class="alert-line" role="alert">{{ partialError }} · 请刷新重试</div>
      <div v-if="diag">
        <PageHeader eyebrow="Diagnosis Intelligence" title="诊断详情"
          :subtitle="`${diag.diagnosis_id} · run ${diag.run_id} · engine ${diag.engine}`">
          <template #actions>
            <button @click="router.push('/diagnoses')">返回诊断列表</button>
            <button @click="router.push(`/runs/${diag.run_id}`)">查看 Run</button>
          </template>
          <template #meta>
            <div class="flex wrap mt">
              <StatusBadge :value="diag.outcome" />
              <StatusBadge :value="diag.disposition" />
              <span class="badge neutral">根因 {{ diag.root_causes.length }}</span>
              <span class="badge neutral">假设 {{ diag.hypotheses.length }}</span>
            </div>
            <div class="reasoning-chain">
              <span>症状</span><b>→</b><span>候选假设</span><b>→</b><span>支持 / 反驳证据</span><b>→</b>
              <span>确定性规则验证</span><b>→</b><span>根因结论</span><b>→</b><span>Disposition</span>
            </div>
            <div v-if="conflicts.length" class="card" style="box-shadow:none; border-color: var(--warn, #b8860b); margin-top: 10px">
              <div class="card-body">
                <div class="flex wrap">
                  <span class="badge warn">证据冲突未裁决</span>
                  <span class="small">影响假设：{{ conflicts.join(', ') }}</span>
                </div>
                <p class="small muted" style="margin-top: 6px">
                  同一假设同时拥有可信支持与可信反驳证据。系统不做自动裁决（P03 未决策），
                  主动探针会立即停止并转人工，避免继续消耗预算。
                </p>
              </div>
            </div>
          </template>
        </PageHeader>

        <div v-if="plan" class="card">
          <div class="card-head">
            <h2>最近一次主动诊断闭环</h2>
            <div class="actions">
              <StatusBadge :value="plan.stop_reason" />
              <span class="small muted">探针 {{ plan.steps.length }} · 预算已用 {{ plan.budget.probes_used }}</span>
            </div>
          </div>
          <div class="card-body">
            <div class="kv">
              <dt>结论变化</dt>
              <dd>{{ plan.diagnosis_before?.outcome || '—' }} → <b>{{ plan.diagnosis_after?.outcome || '—' }}</b></dd>
              <dt>停止原因</dt>
              <dd class="small">{{ plan.stop_reason }}{{ plan.stop_detail ? ' · ' + plan.stop_detail : '' }}</dd>
              <dt>未解决冲突</dt>
              <dd>{{ plan.unresolved_conflicts?.length ? plan.unresolved_conflicts.join(', ') : '无' }}</dd>
              <dt>LLM 规划</dt>
              <dd class="small">
                {{ plan.llm_planner?.called ? '已调用（仅影响候选排序，参数未参与执行）' : '未调用（确定性 Planner 决策）' }}
              </dd>
            </div>
            <div class="hr" />
            <div v-if="plan.stop_reason.includes('BUDGET')" class="small muted" style="margin-bottom: 8px">
              预算按诊断隔离且是 <b>P06 provisional</b> 配置（50 次 / 60 秒）；已关闭或已耗尽的预算不能通过重复请求绕过，
              需要新建诊断后重新探针。
            </div>
            <div class="scroll-x">
              <table>
                <thead><tr><th>#</th><th>探针</th><th>状态</th><th>回灌诊断</th><th>对假设的影响</th></tr></thead>
                <tbody>
                  <tr v-for="s in plan.steps" :key="s.iteration">
                    <td class="small muted">{{ s.iteration }}</td>
                    <td class="mono small">{{ s.probe_id }}</td>
                    <td><StatusBadge :value="s.status" /></td>
                    <td class="small">{{ s.applied_to_diagnosis ? '已回灌' : '未回灌（无证据/无诊断）' }}</td>
                    <td class="small">
                      <span v-if="!s.hypothesis_updates?.length" class="muted">未改变假设</span>
                      <div v-for="(u, i) in s.hypothesis_updates || []" :key="i">
                        {{ u.relation === 'SUPPORT' ? '支持' : '反驳' }}
                        {{ u.exclude ? '· 排除' : '' }}：{{ u.reason }}
                      </div>
                    </td>
                  </tr>
                  <tr v-if="plan.steps.length === 0">
                    <td colspan="5"><div class="empty">本轮未执行探针（{{ plan.stop_reason }}）</div></td>
                  </tr>
                </tbody>
              </table>
            </div>
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
                      <span v-if="String(h.suggested_by || '').startsWith('llm:')" class="source-tag llm">LLM 候选 · {{ h.suggested_by }} · 未确认</span>
                      <span v-else class="source-tag rule">规则生成 · {{ h.suggested_by || 'rule-v1' }}</span>
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
                    <div v-if="h.probe_feedback?.length" style="margin-top: 8px">
                      <div class="small muted">探针证据回灌</div>
                      <div v-for="(f, i) in h.probe_feedback" :key="'pf-' + i" class="small" style="margin-top: 2px">
                        <span class="badge neutral">{{ f.probe_id }}</span>
                        {{ f.relation === 'SUPPORT' ? '支持' : '反驳' }}{{ f.exclude ? ' · 排除' : '' }} · {{ f.reason }}
                      </div>
                    </div>
                    <div v-if="h.excluded_by?.length" style="margin-top: 6px">
                      <span class="badge warn">已被探针排除</span>
                      <span class="small muted">{{ h.excluded_by.map((x) => x.reason).join(' / ') }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-head"><h2>探针建议与执行记录</h2>
                <div class="actions">
                  <button class="sm primary" :disabled="busy === 'loop'" @click="runActiveLoop">
                    {{ busy === 'loop' ? '执行中…' : '运行主动诊断闭环' }}
                  </button>
                  <span class="small muted">只读 · 白名单 · 预算受限</span>
                </div>
              </div>
              <div class="card-body tight scroll-x">
                <table class="probe-table">
                  <thead><tr><th>探针</th><th class="col-purpose">目的</th><th>状态</th><th></th></tr></thead>
                  <tbody>
                    <tr v-for="p in suggestions" :key="'s-' + p.probe_id">
                      <td class="mono small nowrap">{{ p.probe_id }}</td>
                      <td class="small col-purpose">{{ p.why }}</td>
                      <td class="small muted nowrap">建议 · {{ p.score }}</td>
                      <td class="nowrap"><button class="sm" :disabled="busy === 'probe'" @click="runProbe(p)">执行</button></td>
                    </tr>
                    <tr v-for="x in probes" :key="x.execution_id">
                      <td class="mono small nowrap">{{ x.probe_id }}</td>
                      <td class="mono small truncate" style="max-width: 320px">{{ JSON.stringify(x.result) }}</td>
                      <td><StatusBadge :value="x.status" /></td>
                      <td class="small muted nowrap">{{ x.permission_decision }}</td>
                    </tr>
                    <tr v-if="suggestions.length === 0 && probes.length === 0"><td colspan="4"><div class="empty">暂无探针</div></td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="flex-col">
            <div class="card">
              <div class="card-head"><h2>LLM 顾问调用状态</h2><span class="small muted">候选不等于 CONFIRMED</span></div>
              <div class="card-body">
                <div class="kv">
                  <dt>是否调用</dt><dd>{{ diag.llm_advisor?.called ? '是' : '否' }}</dd>
                  <dt>跳过原因</dt><dd class="mono">{{ diag.llm_advisor?.skipped || '—' }}</dd>
                  <dt>新增候选</dt><dd>{{ diag.llm_advisor?.added?.length ?? 0 }} 条</dd>
                  <dt>错误</dt><dd class="mono fail-text">{{ diag.llm_advisor?.error || '—' }}</dd>
                </div>
                <p class="small muted mt">LLM 候选仅以 <b>suggested_by=llm:*</b> 与 <b>CANDIDATE</b> 进入假设池；任何 CONFIRMED 只来自确定性规则与可信证据。</p>
              </div>
            </div>

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

<style scoped>
/* 390px 手机上优先保留"探针 / 状态 / 执行"，目的说明隐藏（仍可通过详情查看） */
@media (max-width: 640px) {
  .probe-table .col-purpose {
    display: none;
  }
}
</style>
