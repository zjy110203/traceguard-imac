"""UI-FaultBench v4 场景集（RUN-009 WP6/WP7）：12 个带独立 GT 的新场景。

本文件与 v2 (`scenarios.py`) 完全独立：
- 不 import v2 的 SCENARIOS / GT，场景 ID 与 GT 都是 v4 独立声明；
- GT 受评测令牌保护（`V4GroundTruthManager`），被评测的 Agent / 诊断引擎
  只能看到 `visible_refs()` 返回的场景 ID 引用，拿不到答案；
- 每个场景声明"必须存在的真实证据签名"，用来判定一次执行是否足以计分，
  证据不足时写 `INCONCLUSIVE` / `BLOCKED_NOT_SEEDED`，不编造结论。

重要状态（诚实标注）：
- 本轮没有为这些新场景在 TeamFlow 中植入故障（禁止修改源码 / seed），
  因此 `seeded=False`、`execution_state=BLOCKED_NOT_SEEDED`；
- v4 场景的**定义与 GT 隔离**已可评测；真实执行需要后续补故障植入，
  在此之前不得把 v3 的证据冒充成 v4 的实验结果。

覆盖矩阵（至少 10 类，本文件 12 个场景）：
多源冲突 / 相似故障 / 时序 / 复合 / UI 正常后端失败 / 网络与应用错误混淆 /
定位器波动 / Flaky / 权限错误 / 证据不完整 / 派生重复证据 / 跨版本状态漂移。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class V4Scenario:
    """v4 场景定义（不含 GT 答案本体，GT 见 V4GroundTruthManager）。"""

    scenario_id: str
    family: str  # 覆盖类别
    description: str
    #: 该场景若要真实执行，必须具备的证据签名（缺任一项 → 证据不完整，不得下结论）
    required_evidence: tuple[str, ...] = field(default_factory=tuple)
    #: 期望的执行终态 / 测试判定（用于回归，不属于"主因 GT"）
    expected_execution_status: str = "COMPLETED"
    expected_test_verdict: str = "FAILED"
    diagnosis_eligible: bool = True
    #: 是否已经在本机 TeamFlow 植入对应故障（本轮全部 False）
    seeded: bool = False
    execution_state: str = "BLOCKED_NOT_SEEDED"
    #: 建议关联的既有 testcase（仅供后续植入时复用；不是本轮证据来源）
    suggested_testcase: str = ""
    #: 执行提示（不是 GT 答案）
    suggestion_note: str = ""


V4_SCENARIOS: tuple[V4Scenario, ...] = (
    V4Scenario(
        "V4-MULTISRC-001",
        "多源冲突",
        "UI 断言 PASSED、API 网络轨迹出现 500、权威业务状态 count=0：三源冲突时以可确认的权威证据裁决主因。",
        ("ui_assertion", "network_5xx", "authoritative_state"),
        suggested_testcase="tcase_bench_comp001",
    ),
    V4Scenario(
        "V4-MULTISRC-002",
        "多源冲突",
        "API 回报成功且 UI 成功，权威状态 count=0：UI+API 同为表层证据，不能压倒权威状态。",
        ("ui_assertion", "api_success", "authoritative_state"),
        suggested_testcase="tcase_tf_false_pass",
    ),
    V4Scenario(
        "V4-SIMILAR-001",
        "相似故障",
        "DOM 中期望 test_id 存在但成功提示未渲染：与定位器失效表现相似，主因是渲染。",
        ("dom_snapshot", "ui_assertion", "business_state"),
        suggested_testcase="tcase_tf_ui_missing",
    ),
    V4Scenario(
        "V4-SIMILAR-002",
        "相似故障",
        "DOM 中期望 test_id 缺失导致步骤超时，业务状态未改变：主因是定位器资产，不是产品渲染缺陷。",
        ("dom_snapshot", "locator_timeout", "business_state"),
        suggested_testcase="tcase_bench_loc001",
    ),
    V4Scenario(
        "V4-TEMPORAL-001",
        "时序",
        "状态查询滞后 9000ms：断言窗口内读到旧值，但延迟后数据最终落库；症状像未持久化，主因是时序。",
        ("network_latency", "assertion_outcome", "delayed_state"),
        suggested_testcase="tcase_bench_db004",
    ),
    V4Scenario(
        "V4-COMPOUND-001",
        "复合",
        "接口 500 同时成功提示被误导为成功文案：多现象并发，需区分主因与伴随症状。",
        ("network_5xx", "ui_assertion", "authoritative_state"),
        suggested_testcase="tcase_bench_comp001",
    ),
    V4Scenario(
        "V4-UIOK-BACKEND-001",
        "UI 正常后端失败",
        "UI 全程正常且成功提示出现，权威状态 count=0：典型 False Pass，UI 证据不能作通过结论。",
        ("ui_assertion", "authoritative_state", "business_state"),
        suggested_testcase="tcase_tf_false_pass",
    ),
    V4Scenario(
        "V4-NETVSAPP-001",
        "网络与应用错误混淆",
        "网络请求失败（status=0 / timeout）但无 5xx：链路问题，不应误判为接口返回错误。",
        ("network_failure", "no_5xx", "timing_observation"),
        suggested_testcase="tcase_bench_net002",
    ),
    V4Scenario(
        "V4-NETVSAPP-002",
        "网络与应用错误混淆",
        "网络轨迹覆盖失败步骤且存在 500：应用接口故障，不是链路层抖动。",
        ("network_5xx", "failed_step_coverage", "server_log"),
        suggested_testcase="tcase_bench_api002",
    ),
    V4Scenario(
        "V4-LOCFLUX-001",
        "定位器波动",
        "test_id 在 5 次回放中 4 次可解析、1 次超时：定位器资产波动，不是产品缺陷。",
        ("repeated_dom_snapshots", "locator_timeout", "stable_business_state"),
        suggestion_note="需 5 次回放；单次快照不足以定性",
        suggested_testcase="tcase_bench_loc001",
    ),
    V4Scenario(
        "V4-FLAKY-001",
        "Flaky",
        "同一用例 5 次回放 2 次 PASSED / 3 次 FAILED：Flaky 是稳定性特征，不能当作主因本身。",
        ("repeated_verdicts", "state_diff", "timing_observation"),
        suggested_testcase="tcase_bench_flaky001",
    ),
    V4Scenario(
        "V4-AUTH-001",
        "权限错误",
        "创建接口返回 403，UI 只显示通用失败文案：需要 HTTP 状态码证据才能与 5xx / 渲染区分。",
        ("http_403", "ui_assertion", "business_state"),
        suggested_testcase="tcase_bench_sec001",
    ),
    V4Scenario(
        "V4-INCOMPLETE-001",
        "证据不完整",
        "执行在断言前中断，只有网络轨迹、无 DOM / 无权威状态：必须判 INCONCLUSIVE 并请求补证据。",
        ("network_trace_only",),
        expected_execution_status="FAILED",
        expected_test_verdict="NOT_EVALUATED",
        suggested_testcase="tcase_bench_comp003",
    ),
    V4Scenario(
        "V4-DUP-001",
        "派生重复证据",
        "同一浏览器源派生出 3 条证据，另 1 条来自独立 API：来源独立性分析后只能算 2 条独立佐证。",
        ("derived_evidence", "independent_api_evidence", "ui_assertion"),
        suggested_testcase="tcase_tf_ui_missing",
    ),
)
assert len(V4_SCENARIOS) >= 10, f"v4 至少 10 个场景，当前 {len(V4_SCENARIOS)}"


# ---------------------------------------------------------------------------
# 独立 GT（评测端私有）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class V4GroundTruth:
    scenario_id: str
    primary_failure_class: str  # 权威主因类别
    failure_mechanism: str
    expected_business_state: str  # PERSISTED / NOT_PERSISTED / UNCHANGED / UNKNOWN
    disposition: str
    rationale: str


_GT: dict[str, V4GroundTruth] = {
    "V4-MULTISRC-001": V4GroundTruth(
        "V4-MULTISRC-001", "API", "API", "NOT_PERSISTED", "PRODUCT_DEFECT",
        "网络轨迹含可信 500 且覆盖失败步骤；UI PASSED 只是表层现象，权威状态 count=0 是接口失败后果。",
    ),
    "V4-MULTISRC-002": V4GroundTruth(
        "V4-MULTISRC-002", "PERSISTENCE", "PERSISTENCE", "NOT_PERSISTED", "PRODUCT_DEFECT",
        "UI 与 API 的成功回报都不是权威状态；count=0 为直接确认证据。",
    ),
    "V4-SIMILAR-001": V4GroundTruth(
        "V4-SIMILAR-001", "UI_RENDERING", "UI_RENDERING", "PERSISTED", "PRODUCT_DEFECT",
        "DOM 中期望元素存在且业务已落库，说明定位成功、仅渲染反馈缺失。",
    ),
    "V4-SIMILAR-002": V4GroundTruth(
        "V4-SIMILAR-002", "LOCATOR", "LOCATOR", "UNCHANGED", "TEST_ASSET",
        "DOM 缺失期望 test_id 导致步骤超时；业务状态未改变，属用例资产问题。",
    ),
    "V4-TEMPORAL-001": V4GroundTruth(
        "V4-TEMPORAL-001", "TIMING", "TIMING", "PERSISTED", "PRODUCT_DEFECT",
        "延迟后状态可读，断言窗口内旧值是时序后果，不是未持久化。",
    ),
    "V4-COMPOUND-001": V4GroundTruth(
        "V4-COMPOUND-001", "API", "API", "NOT_PERSISTED", "PRODUCT_DEFECT",
        "500 是可确认的直接证据；误导性成功文案是伴随症状，不作为主因。",
    ),
    "V4-UIOK-BACKEND-001": V4GroundTruth(
        "V4-UIOK-BACKEND-001", "PERSISTENCE", "PERSISTENCE", "NOT_PERSISTED", "PRODUCT_DEFECT",
        "UI 正常不能推出业务成功；权威状态 count=0 直接确认持久化失败。",
    ),
    "V4-NETVSAPP-001": V4GroundTruth(
        "V4-NETVSAPP-001", "NETWORK", "NETWORK", "UNKNOWN", "ENVIRONMENT",
        "链路层失败且无 5xx；服务端未收到完整请求，不能归因为应用接口错误。",
    ),
    "V4-NETVSAPP-002": V4GroundTruth(
        "V4-NETVSAPP-002", "API", "API", "NOT_PERSISTED", "PRODUCT_DEFECT",
        "网络轨迹覆盖失败步骤且含 500，后端日志有 ERROR，主因为应用接口。",
    ),
    "V4-LOCFLUX-001": V4GroundTruth(
        "V4-LOCFLUX-001", "LOCATOR", "LOCATOR", "PERSISTED", "TEST_ASSET",
        "业务状态稳定而定位偶发失败；Flaky 特征只作稳定性标注，主因是定位器资产。",
    ),
    "V4-FLAKY-001": V4GroundTruth(
        "V4-FLAKY-001", "TIMING", "TIMING", "UNKNOWN", "NEEDS_MORE_EVIDENCE",
        "多次回放结论不一致是 Flaky 特征；没有单次权威失败证据时不得确认根因，先补证据。",
    ),
    "V4-AUTH-001": V4GroundTruth(
        "V4-AUTH-001", "AUTHORIZATION", "AUTHORIZATION", "NOT_PERSISTED", "ENVIRONMENT",
        "403 是权限拒绝而非 5xx；必须有 HTTP 状态码证据才能定性。",
    ),
    "V4-INCOMPLETE-001": V4GroundTruth(
        "V4-INCOMPLETE-001", "UNKNOWN", "UNKNOWN", "UNKNOWN", "NEEDS_MORE_EVIDENCE",
        "断言前中断且无 DOM / 无权威状态：不得猜测主因，判 INCONCLUSIVE 并请求补证据。",
    ),
    "V4-DUP-001": V4GroundTruth(
        "V4-DUP-001", "UI_RENDERING", "UI_RENDERING", "PERSISTED", "PRODUCT_DEFECT",
        "业务已落库且 UI 渲染失败；3 条同源派生证据不得当作 3 条独立佐证。",
    ),
}
assert set(_GT) == {s.scenario_id for s in V4_SCENARIOS}, "每个 v4 场景必须有且仅有一条独立 GT"


class V4GroundTruthManager:
    """GT 独立受控域：无评测令牌时不可读（与 `dataset.py` 的隔离规则一致）。"""

    def __init__(self) -> None:
        self._token = secrets.token_urlsafe(24)
        self._records = dict(_GT)

    @property
    def evaluation_token(self) -> str:
        return self._token

    def check_token(self, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(str(token), self._token)

    def visible_refs(self) -> list[str]:
        """只暴露场景 ID 引用，不含答案。"""
        return sorted(self._records)

    def get(self, scenario_id: str, token: str | None) -> V4GroundTruth | None:
        if not self.check_token(token):
            return None
        return self._records.get(scenario_id)

    def all(self, token: str | None) -> dict[str, V4GroundTruth]:
        if not self.check_token(token):
            return {}
        return dict(self._records)

    def isolation_check(self, token: str | None) -> dict[str, Any]:
        """可复核的隔离检查：无令牌读取必须返回空。"""
        leaked = self.all(None)
        return {
            "gt_count": len(self._records),
            "visible_ref_count": len(self.visible_refs()),
            "no_token_read_count": len(leaked),
            "token_granted_read_count": len(self.all(token)),
            "isolated": not leaked and len(self.all(token)) == len(self._records),
            "note": "无令牌读取返回 0 条；有令牌读取返回全部；不通过 API 暴露 GT 正文",
        }


GT_MANAGER = V4GroundTruthManager()
DATASET_ID = "teamflow-faultbench-v4"
DATASET_VERSION = "v4.0"
GT_ISOLATED = True

__all__ = [
    "DATASET_ID",
    "DATASET_VERSION",
    "GT_ISOLATED",
    "GT_MANAGER",
    "V4_SCENARIOS",
    "V4GroundTruth",
    "V4GroundTruthManager",
    "V4Scenario",
]
