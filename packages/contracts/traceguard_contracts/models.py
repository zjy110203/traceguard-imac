"""C01～C12 核心对象 Pydantic 模型（初始版本，渐进冻结）。

字段来源：资料/项目资料/04 数据模型 + 05 契约设计 + 路线图 v1.1。
注意：概念字段落地为初始结构；待确认事项（如 P03 Oracle 多来源裁决）不在此虚构规则。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .common import (
    AssertionStatus,
    ContractBase,
    DiagnosisOutcome,
    DiagnosisStatus,
    DispositionKind,
    ExecutionStatus,
    RepairStatus,
    TestVerdict,
)

SCHEMA_VERSION = "0.1.0"


# ---------------- C01 TestIntent ----------------
class TestIntent(ContractBase):
    intent_id: str = Field(pattern=r"^intent_[0-9a-f]+$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    requirement_refs: list[str] = Field(default_factory=list)
    business_context: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    candidate_assertions: list[str] = Field(default_factory=list)
    owner: str
    status: Literal["DRAFT", "REVIEW", "APPROVED", "DEPRECATED"] = "DRAFT"
    version: int = 1
    schema_version: str = SCHEMA_VERSION


# ---------------- C02 TestCase / TestCaseVersion ----------------
class LocatorSpec(ContractBase):
    strategy: Literal["role", "accessible_name", "label", "placeholder", "test_id", "css", "xpath"]
    value: str = Field(min_length=1)
    source: Literal["human", "compiler", "healed"] = "human"
    stability_score: float | None = Field(default=None, ge=0, le=1)


class TestStep(ContractBase):
    step_id: str
    action: Literal["navigate", "click", "fill", "select", "press", "assert", "wait", "api"]
    target: LocatorSpec | None = None
    value: str | None = None
    description: str = ""


class AssertionSpec(ContractBase):
    assertion_id: str
    description: str = Field(min_length=1)
    kind: Literal["ui", "business_state", "api", "network", "consistency"]
    required: bool = True
    expected: dict[str, Any] = Field(default_factory=dict)
    oracle_id: str | None = None


class TestCaseVersion(ContractBase):
    testcase_id: str = Field(pattern=r"^tcase_[0-9a-f]+$")
    version: int = Field(ge=1)
    intent_ref: str | None = None
    steps: list[TestStep] = Field(min_length=1)
    assertions: list[AssertionSpec] = Field(min_length=1)  # F05：禁止空必需断言集
    test_data_refs: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    cleanup_policy: Literal["NONE", "RESET_DATA", "CUSTOM"] = "NONE"
    run_config: dict[str, Any] = Field(default_factory=dict)
    compile_source: Literal["human", "compiler", "healed"] = "human"
    review_status: Literal["DRAFT", "PENDING", "APPROVED", "REJECTED"] = "DRAFT"
    immutable: bool = True  # 版本不可原地覆盖
    created_at: str
    schema_version: str = SCHEMA_VERSION


# ---------------- C03 Oracle ----------------
class OracleSpec(ContractBase):
    oracle_id: str = Field(pattern=r"^oracle_[0-9a-f]+$")
    assertion_id: str
    expected_behavior: str = Field(min_length=1)
    requirement_ref: str
    expectation_approval: dict[str, Any] = Field(default_factory=dict)  # 来源与审批记录
    evaluation_rule: dict[str, Any] = Field(default_factory=dict)
    consistency_window_ms: int = Field(default=0, ge=0)
    required: bool = True
    version: int = Field(ge=1)
    # D-11 / 架构原则11：Agent 不得自行批准正式 Oracle。Literal[False] 从契约层面强制该不变量。
    approved_by_agent: Literal[False] = False
    schema_version: str = SCHEMA_VERSION


# ---------------- C04 Execution ----------------
class Task(ContractBase):
    task_id: str = Field(pattern=r"^task_[0-9a-f]+$")
    testcase_ref: str
    oracle_refs: list[str] = Field(default_factory=list)
    application: str = "teamflow"
    status: Literal[
        "CREATED", "QUEUED", "RUNNING", "WAITING", "COMPLETED", "FAILED", "CANCELLED"
    ] = "CREATED"
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    schema_version: str = SCHEMA_VERSION


class Run(ContractBase):
    run_id: str = Field(pattern=r"^run_[0-9a-f]+$")
    task_id: str
    attempt: int = Field(ge=1)
    testcase_version_id: str  # 绑定具体版本，不引用可变指针
    oracle_versions: dict[str, int] = Field(default_factory=dict)
    browser_version: str | None = None
    runner_version: str | None = None
    environment: str = "local"
    execution_status: ExecutionStatus = ExecutionStatus.QUEUED
    test_verdict: TestVerdict = TestVerdict.NOT_EVALUATED
    started_at: str | None = None
    ended_at: str | None = None
    schema_version: str = SCHEMA_VERSION


class Step(ContractBase):
    step_id: str
    run_id: str
    index: int = Field(ge=0)
    status: Literal[
        "PENDING", "RUNNING", "PASSED", "FAILED", "SKIPPED", "TIMED_OUT", "CANCELLED", "BLOCKED"
    ] = "PENDING"
    skip_reason: str | None = None
    error: str | None = None
    schema_version: str = SCHEMA_VERSION


class AssertionResult(ContractBase):
    assertion_id: str
    status: AssertionStatus
    oracle_id: str | None = None
    observed: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


# ---------------- C05 Event ----------------
class EventEnvelope(ContractBase):
    """F01/F02/G02/G05 修订后：workspace_id 必填且服务端注入；context_refs 统一关联；
    occurred_at 与 observed_at 分离；幂等键 producer 维度唯一。"""

    event_id: str = Field(pattern=r"^evt_[0-9a-f]+$")
    event_type: str
    schema_version: str = SCHEMA_VERSION
    workspace_id: str  # 必填，服务端可信上下文注入
    context_refs: dict[str, str] = Field(
        default_factory=dict
    )  # run/step/exploration/probe/experiment/trace/span/request
    producer_id: str
    producer_instance_id: str  # 重启换新实例
    sequence: int = Field(ge=1)  # (producer_instance_id, sequence) 唯一
    occurred_at: str
    observed_at: str
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------- C06 Evidence ----------------
class Evidence(ContractBase):
    evidence_id: str = Field(pattern=r"^evd_[0-9a-f]+$")
    kind: Literal["raw", "normalized", "derived", "diagnostic"]
    source_type: Literal["browser", "network", "api", "backend_log", "database", "agent", "probe"]
    source_ref: str
    raw_ref: str | None = None  # 派生证据指向原始证据
    derivation_rule: str | None = None
    model_version: str | None = None
    workspace_id: str
    run_id: str | None = None
    sha256: str
    content_summary: str = ""
    attachment_ref: str | None = None
    integrity_check: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"  # 与 source_trust 分开
    source_trust: Literal["TRUSTED", "UNTRUSTED", "UNKNOWN"] = "UNKNOWN"
    redacted: bool = False
    captured_at: str
    schema_version: str = SCHEMA_VERSION


# ---------------- C07 Evidence Graph ----------------
EDGE_TYPES = Literal[
    "parent_child",
    "emitted_by",
    "derived_from",
    "correlated_by_trace_id",
    "correlated_by_request_id",
    "same_execution_context",
    "temporal",
    "supports",
    "contradicts",
    "evaluates",
]


class GraphNode(ContractBase):
    node_id: str
    node_type: Literal[
        "Run", "Step", "Event", "Evidence", "Assertion", "Hypothesis", "ProbeExecution", "Source"
    ]
    ref_id: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(ContractBase):
    edge_id: str
    src: str
    dst: str
    relation: EDGE_TYPES
    basis: str = Field(min_length=1)  # 建边依据，关联不等于因果
    workspace_id: str
    schema_version: str = SCHEMA_VERSION


# ---------------- C08 Diagnosis ----------------
class Hypothesis(ContractBase):
    hypothesis_id: str
    description: str
    failure_domain: str = ""
    failure_mechanism: str = ""
    symptom: str = ""
    status: Literal["PROPOSED", "SUPPORTED", "CONFIRMED", "REJECTED", "INCONCLUSIVE"] = "PROPOSED"
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    contributing_factors: list[str] = Field(default_factory=list)
    flaky_characteristic: bool = False
    created_by: Literal["agent", "rule", "human"] = "rule"


class Diagnosis(ContractBase):
    diagnosis_id: str
    run_id: str
    diagnosis_status: DiagnosisStatus
    diagnosis_outcome: DiagnosisOutcome
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    root_causes: list[str] = Field(default_factory=list)  # 仅 CONFIRMED 可进入；可多个
    unresolved_conflicts: list[str] = Field(default_factory=list)
    probe_history: list[str] = Field(default_factory=list)
    stop_reason: str = ""
    strategy_version: str = "v1"
    schema_version: str = SCHEMA_VERSION


# ---------------- C09 Probe ----------------
class ProbeDefinition(ContractBase):
    probe_id: str
    name: str
    capability: Literal[
        "dom_snapshot",
        "screenshot",
        "network_read",
        "console_read",
        "api_read",
        "backend_log_read",
        "database_read",
    ]
    readonly: bool = True
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    required_permissions: list[str] = Field(default_factory=list)
    allowed_templates: list[str] = Field(default_factory=list)  # F07：白名单，禁任意 SQL
    timeout_ms: int = 5000
    version: int = 1


class ProbeExecution(ContractBase):
    execution_id: str
    probe_id: str
    workspace_id: str
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT", "BLOCKED", "CANCELLED"]
    permission_decision: Literal["ALLOW", "DENY"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    budget_ms: int = Field(ge=0)
    evidence_refs: list[str] = Field(default_factory=list)
    block_reason: str | None = None
    schema_version: str = SCHEMA_VERSION


# ---------------- C10 Disposition ----------------
class Disposition(ContractBase):
    disposition_id: str
    diagnosis_id: str
    kind: DispositionKind
    route_target: str
    review_status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING"
    reason: str = ""
    schema_version: str = SCHEMA_VERSION


# ---------------- C11 Repair ----------------
class RepairProposal(ContractBase):
    proposal_id: str
    base_testcase_version: str  # F10：base/candidate/target 版本保护
    candidate_testcase_version: str
    target_testcase_version: str | None = None
    diff: list[dict[str, Any]] = Field(default_factory=list)
    affected_assertions: list[str] = Field(default_factory=list)
    assertion_preservation: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"
    oracle_integrity: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"
    sandbox_validation_status: Literal[
        "NOT_STARTED", "RUNNING", "PASSED", "FAILED", "TIMED_OUT", "BLOCKED"
    ] = "NOT_STARTED"
    status: RepairStatus = RepairStatus.DRAFT
    approval_ref: str | None = None
    regression_ref: str | None = None
    promotion_record: dict[str, Any] = Field(default_factory=dict)
    created_by_agent: bool = True  # Agent 不得自批自修
    schema_version: str = SCHEMA_VERSION


# ---------------- C12 Benchmark ----------------
class BenchmarkCase(ContractBase):
    case_id: str
    scenario_id: str
    fault_mechanism_id: str
    application_version: str
    seed_ref: str
    ground_truth_ref: str  # Ground Truth 隔离，仅评测侧可读
    cleanup_method: str = ""


class EvaluationResult(ContractBase):
    experiment_id: str
    baseline: str
    metric: str
    value: float
    numerator: int | None = None
    denominator: int | None = None
    note: str = ""  # 统计口径说明
    schema_version: str = SCHEMA_VERSION
