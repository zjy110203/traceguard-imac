export type Verdict = 'PASSED' | 'FAILED' | 'INCONCLUSIVE' | 'NOT_EVALUATED'
export type ExecStatus =
  | 'QUEUED'
  | 'PREPARING'
  | 'RUNNING'
  | 'FINALIZING'
  | 'COMPLETED'
  | 'FAILED'
  | 'TIMED_OUT'
  | 'CANCELLED'

export interface Workspace {
  workspace_id: string
  name: string
  description: string
  created_at: string
}

export interface Intent {
  intent_id: string
  title: string
  description: string
  status: string
  owner: string
  workspace_id: string
  requirement_refs: string[]
  candidate_assertions: string[]
  created_at: string
  updated_at: string
}

export interface Locator {
  strategy: string
  value: string
  source?: string
}

export interface TestStep {
  step_id: string
  action: string
  target?: Locator | null
  value?: string | null
  description?: string
}

export interface AssertionSpec {
  assertion_id: string
  description: string
  kind: string
  required: boolean
  expected: Record<string, unknown>
  oracle_id?: string | null
}

export interface TestCaseVersion {
  testcase_id: string
  version: number
  intent_ref?: string | null
  steps: TestStep[]
  assertions: AssertionSpec[]
  review_status: string
  compile_source: string
  run_config: Record<string, unknown>
  cleanup_policy: string
  created_at: string
}

export interface TestCaseSummary {
  testcase_id: string
  title: string
  workspace_id: string
  intent_ref: string | null
  review_status: string
  latest_version: number
  compile_source: string
  step_count: number
  assertion_count: number
  updated_at: string
}

export interface TestCaseDetail extends TestCaseSummary {
  latest: TestCaseVersion | null
}

export interface StepRow {
  step_id: string
  run_id: string
  index: number
  action: string
  status: string
  error: string | null
  duration_ms: number
  observation: Record<string, unknown>
}

export interface AssertionOutcome {
  assertion_id: string
  status: string
  required: boolean
  oracle_available: boolean
  reason: string
}

export interface RunStepReport {
  step_id: string
  action: string
  status: string
  error: string | null
  duration_ms: number
  observation: Record<string, unknown>
  artifacts: Record<string, ArtifactMeta>
  evidence_ids?: string[]
}

export interface RunReport {
  run_id: string
  task_id: string
  workspace_id: string
  testcase_id?: string
  testcase_version?: number
  observer_mode: string
  browser?: Record<string, unknown>
  execution_status: string
  test_verdict: string
  duration_ms: number
  steps: RunStepReport[]
  assertion_outcomes: AssertionOutcome[]
  summary: { total: number; passed: number; failed: number; not_evaluated: number; inconclusive: number }
  oracle_state?: Record<string, unknown>
  evidence_ids?: string[]
  trace_artifact?: ArtifactMeta | null
  errors?: string[]
  error?: string | null
}

export interface Run {
  run_id: string
  task_id: string
  workspace_id: string
  attempt: number
  testcase_version_id: string
  execution_status: ExecStatus
  test_verdict: Verdict
  observer_mode: string
  fencing_token: number
  lease_owner: string | null
  lease_expires_at?: number
  report: RunReport | null
  error: string | null
  created_at: number
  started_at: number | null
  ended_at: number | null
}

export interface TaskRow {
  task_id: string
  workspace_id: string
  testcase_ref: string
  testcase_version_id: string
  application: string
  status: string
  mode: string
  created_at: number
}

export interface ArtifactMeta {
  artifact_id: string
  sha256: string
  size: number
  media_type: string
  original_name?: string
}

export interface Evidence {
  evidence_id: string
  kind: string
  source_type: string
  source_ref: string
  sha256: string
  workspace_id: string
  content: Record<string, any>
  raw_ref?: string | null
  derivation_rule?: string | null
  attachment_ref?: string | null
  integrity_check: string
  source_trust: string
  redacted: boolean
  captured_at: string
}

export interface EventRow {
  event_id: string
  event_type: string
  workspace_id: string
  context_refs: Record<string, string>
  producer_id: string
  producer_instance_id: string
  sequence: number
  occurred_at: string
  observed_at: string
  payload: Record<string, unknown>
}

export interface Hypothesis {
  hypothesis_id: string
  description: string
  failure_domain: string
  failure_mechanism: string
  supporting: { id: string; trust: string; confirming: boolean }[]
  contradicting: { id: string; trust: string }[]
  status: string
  contributing_factors: string[]
  flaky_characteristic: boolean
  suggested_by: string
  // RUN-006：探针证据回灌记录与排除依据
  probe_feedback?: {
    probe_id: string
    execution_id: string
    relation: string
    confirming: boolean
    exclude: boolean
    reason: string
  }[]
  excluded_by?: { probe_id: string; evidence_id: string; reason: string }[]
}

export interface Diagnosis {
  diagnosis_id: string
  run_id: string
  workspace_id: string
  testcase_id?: string
  symptom: string
  outcome: string
  disposition: string
  root_causes: string[]
  hypotheses: Hypothesis[]
  evidence_refs: string[]
  flaky_characteristic: boolean
  engine: string
  llm_advisor?: {
    called?: boolean
    skipped?: string
    added?: string[]
    error?: string
    model?: string
  } | null
  created_at: string
  disposition_id?: string
  // RUN-006：未解决冲突（同一假设同时有可信支持与反驳证据）
  unresolved_conflicts?: string[]
  probe_feedback?: Record<string, unknown>[]
  evaluation_history?: {
    at: string
    trigger: string
    probes: string[]
    outcome_before: string
    outcome_after: string
    root_causes_after: string[]
    unresolved_conflicts: string[]
  }[]
}

export interface ProbePlan {
  plan_id: string
  run_id: string
  diagnosis_id?: string | null
  stop_reason: string
  stop_detail: string
  steps: {
    iteration: number
    probe_id: string
    status: string
    reason: string
    score: number
    applied_to_diagnosis: boolean
    evidence_ids: string[]
    hypothesis_updates?: { hypothesis_id: string; relation: string; exclude: boolean; reason: string }[]
    diagnosis_after?: { outcome: string; root_causes: string[]; unresolved_conflicts: string[] }
  }[]
  diagnosis_before?: { outcome: string } | null
  diagnosis_after?: { outcome: string } | null
  unresolved_conflicts?: string[]
  budget: { scope_key: string; probes_used: number; probes_left: number; time_left: number }
  llm_planner?: { called?: boolean; effect?: string; params_used_in_execution?: boolean }
}

export interface Disposition {
  disposition_id: string
  diagnosis_id: string
  run_id: string
  kind: string
  route_target: string
  review_status: string
  reason: string
  created_at: string
}

export interface GraphNode {
  node_id: string
  node_type: string
  ref_id: string
  properties: Record<string, unknown>
}

export interface GraphEdge {
  edge_id: string
  src: string
  dst: string
  relation: string
  basis: string
}

export interface EvidenceGraph {
  run_id: string
  workspace_id: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: { nodes: number; edges: number }
  generated_at: string
}

export interface Repair {
  proposal_id: string
  testcase_id: string
  workspace_id: string
  base_testcase_version: string
  base_version: number
  status: string
  sandbox_status: string
  sandbox_run_id?: string | null
  sandbox_verdict?: string
  assertion_preservation: string
  oracle_integrity: string
  diff_allowed: boolean
  diff: { path: string; from: unknown; to: unknown; step_index?: number; reason?: string }[]
  reason: string
  created_by: string
  created_at: string
  approval_ref: string | null
  approved_by?: string
  promotion_record: Record<string, any>
  promoted_version?: number
  rollback_record?: Record<string, any>
  failure_reason?: string
  // RUN-014 WP1：Regression 是独立验证阶段，不得从 promotion_record 推导
  regression_status?: 'NOT_STARTED' | 'QUEUED' | 'RUNNING' | 'PASSED' | 'FAILED' | 'INCONCLUSIVE' | 'BLOCKED'
  regression_run_id?: string | null
  regression_testcase_version_id?: string | null
  regression_verdict?: string
  regression_started_at?: string | null
  regression_finished_at?: string | null
  regression_failure_reason?: string
  regression_evidence_ids?: string[]
  regression_oracle_refs?: { assertion_id?: string; oracle_id: string; oracle_version?: number | null; status?: string }[]
  regression_stale?: boolean
  regression_history?: Record<string, any>[]
  // RUN-017 WP3：INCONCLUSIVE 的人工授权重试（新 Attempt，不改写历史）
  regression_attempt_no?: number
  regression_max_attempts?: number
  regression_attempts?: {
    attempt: number
    run_id: string
    parent_run_id?: string | null
    status: string
    verdict?: string
    reason_code?: string
    authorized_by?: string
    authorized_at?: string
    note?: string
    evidence_count?: number
  }[]
  regression_parent_run_id?: string | null
  regression_retry_allowed?: boolean
  regression_retry_reason_code?: string
  regression_retry_decision?: string
  regression_retry_block_reason?: string
  regression_retry_authorizations?: Record<string, any>[]
}

export interface ProbeExecution {
  execution_id: string
  probe_id: string
  workspace_id: string
  run_id?: string | null
  status: string
  permission_decision: string
  risk_level: string
  params: Record<string, unknown>
  result: Record<string, unknown>
  reason: string
  evidence_refs: string[]
  executed_at: string
}

export interface BenchmarkMetric {
  baseline: string
  metric: string
  value: number
  numerator?: number | null
  denominator?: number | null
  ci_low?: number | null
  ci_high?: number | null
  note?: string
}

export interface BenchmarkExperiment {
  experiment_id: string
  name: string
  kind: string
  status: string
  dataset: string
  config: Record<string, unknown>
  metrics: BenchmarkMetric[]
  blocked_baselines?: { name: string; reason: string }[]
  started_at?: string
  ended_at?: string
  notes?: string
}

export interface SystemHealth {
  status: string
  app: string
  environment: string
  database: { ok: boolean; dialect: string; error: string }
  artifact_dir: string
  worker: { id: string; inline_running: boolean }
  counts: Record<string, number>
}

export interface BrowserHealth {
  available: boolean
  running?: boolean
  channel?: string
  version?: string
  smoke?: boolean
  config?: Record<string, unknown>
  error?: string
}

/** RUN-004-FE：GET /api/v1/system/llm/summary 的只读聚合视图。 */
export interface LLMUsageBucket {
  calls: number
  errors: number
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number | null
  cost_is_estimated: boolean
  retries?: number
  avg_latency_ms?: number
  p95_latency_ms?: number
  latency_sample?: number
  date?: string
  budget_usd?: number
  budget_exceeded?: boolean
}

export interface LLMUsageSummary {
  data_available: boolean
  record_count: number
  date: string
  configured: boolean
  connected: 'unknown' | 'ok' | 'error'
  provider: string
  default_model: string
  reasoning_model: string
  enabled: { explorer: boolean; diagnosis: boolean }
  note: string
  generated_at: string
  calls: number
  errors: number
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number | null
  cost_is_estimated: boolean
  budget_usd: number
  budget_exceeded: boolean
  retries: number
  avg_latency_ms: number
  p95_latency_ms: number
  latency_sample: number
  total: LLMUsageBucket
  by_agent: Record<string, LLMUsageBucket>
  by_model: Record<string, LLMUsageBucket>
  daily_series: LLMUsageBucket[]
  latency_summary: { avg_ms: number; p95_ms: number; sample: number }
  prompt_versions: Record<string, number>
  recent_calls: {
    ts: string
    model: string
    agent: string
    request_status: string
    error_kind: string
    latency_ms: number
    input_tokens: number
    output_tokens: number
  }[]
  decisions: {
    llm_calls_recorded: number
    fallback_calls: number
    rule_explorations: number
    diagnosis_advisor_called: number
    diagnosis_advisor_skipped: number
    note: string
  }
}

export interface ExplorationAction {
  action: string
  params: Record<string, unknown>
  reason?: string
  result?: string
  gateway?: string
  error?: string
  evidence_refs?: string[]
  page_state_refs?: string[]
}

export interface ExplorationDetail {
  exploration_id: string
  intent_id: string
  workspace_id: string
  status: string
  error?: string
  engine?: string
  llm_stats?: { llm_calls: number; fallback_calls: number; last_error: string } | null
  action_count: number
  stop_reason: string
  created_at: string
  trace?: {
    exploration_id?: string
    agent_version?: string
    prompt_version?: string
    tool_version?: string
    browser_version?: string
    application_version?: string
    stop_reason?: string
    action_sequence: ExplorationAction[]
    page_state_refs?: string[]
    event_refs?: string[]
    evidence_refs?: string[]
    started_at?: string
  }
  gateway_audit?: Record<string, unknown>[]
  compiled?: {
    testcase_id: string
    version: number
    warnings: string[]
    compiler_version: string
    prompt_version: string
    model_version: string
  } | null
}
