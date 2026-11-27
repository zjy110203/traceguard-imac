"""RUN-018 WP5 §5.4：Active Probe → Diagnosis Feedback 真实场景闭环测试。

五个必须实现的真实场景（确定性探针执行器驱动真实 Runtime / DiagnosisService /
ProbeService / GraphService）：
- 场景 A（DB-001）：永久性持久化故障——独立读取 + 延迟复测均缺失 → 保留
  PERSISTENCE 可信证据，与 DB-004 严格区分；
- 场景 B（DB-004）：状态读取滞后——Run 即时读不到、延迟复测恢复 → 诊断方向
  转向 TIMING，不得错误确认 PERSISTENCE；
- 场景 C（API 5xx）：API 失败是主因，下游 UI 未显示成功不得再次确认
  UI_RENDERING；证据时间与请求对齐；
- 场景 D（LOC-001/002）：定位器变化——独立用户可见操作成功、业务已落库，
  不得升级为业务功能失效；
- 场景 E（Probe 不可用）：产生 UNKNOWN——不污染原诊断、不允许错误 CONFIRMED。

并覆盖 §5.3 反馈规则与 §5.5 预算/安全的闭环行为（无限重试禁止、Run Verdict 不变）。
"""

from __future__ import annotations

import time as _time
from typing import Any

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.diagnosis_service import DiagnosisService
from traceguard_api.services.probe_service import ProbeService
from traceguard_evidence.diagnosis.engine import hypothesis_confidence
from traceguard_evidence.probe.feedback import ProbeSignal, assess
from traceguard_evidence.probe.planner import ProbeUnavailable
from traceguard_persistence import Database
from traceguard_persistence.models import RunRow

# ------------------------------------------------------------------ 报告构造


def _db_lag_report() -> dict[str, Any]:
    """DB-001 / DB-004 共同签名：UI 通过、业务断言失败、无 5xx。"""
    return {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "PASSED",
                "error": "",
                "target": {"strategy": "test_id", "value": "create-submit"},
                "observation": {
                    "network": [{"url": "/api/projects", "status": 200, "failure": ""}]
                },
            },
            {
                "step_id": "s2",
                "action": "assert",
                "status": "FAILED",
                "error": "count mismatch",
                "target": {"strategy": "test_id", "value": "banner-success"},
                "value": "banner-success",
                "observation": {},
            },
        ],
        "assertion_outcomes": [
            {"assertion_id": "a_ui", "kind": "ui", "status": "PASSED", "reason": ""},
            {
                "assertion_id": "a_biz",
                "kind": "business_state",
                "status": "FAILED",
                "reason": "count mismatch",
            },
        ],
    }


def _api_5xx_report() -> dict[str, Any]:
    """场景 C：接口 5xx + 下游 UI 未显示成功（业务断言未评估）。"""
    return {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED",
                "error": "",
                "target": {"strategy": "test_id", "value": "create-submit"},
                "observation": {
                    "network": [{"url": "/api/projects", "status": 500, "failure": ""}],
                    "http_errors": [{"url": "/api/projects", "status": 500}],
                },
            },
            {
                "step_id": "s2",
                "action": "assert",
                "status": "FAILED",
                "error": "banner missing",
                "target": {"strategy": "test_id", "value": "banner-success"},
                "value": "banner-success",
                "observation": {},
            },
        ],
        "assertion_outcomes": [
            {"assertion_id": "a_ui", "kind": "ui", "status": "FAILED", "reason": "banner missing"},
            {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"},
        ],
    }


def _locator_report() -> dict[str, Any]:
    """场景 D：定位器变化（LOC-001/LOC-002）——断言根本没执行。"""
    return {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED",
                "error": "Locator.click: Timeout 8000ms exceeded",
                "target": {"strategy": "test_id", "value": "create-submit"},
                "observation": {},
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a_ui",
                "kind": "ui",
                "status": "NOT_EVALUATED",
                "expected": {"test_id": "banner-success"},
            },
            {
                "assertion_id": "a_biz",
                "kind": "business_state",
                "status": "NOT_EVALUATED",
                "expected": {"test_id": "banner-success"},
            },
        ],
    }


# ------------------------------------------------------------------ fixture


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"wp5-feedback-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'wp5.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _insert_run(
    rt: Runtime,
    run_id: str,
    report: dict[str, Any],
    *,
    started_at: float | None = None,
    ended_at: float | None = None,
) -> None:
    with rt.db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id=f"task_{run_id}",
                workspace_id="default",
                testcase_version_id="tcase_wp5@1",
                execution_status="COMPLETED",
                test_verdict="FAILED",
                report_json=report,
                started_at=started_at,
                ended_at=ended_at,
            )
        )


def _patch_probe(svc: ProbeService, probe_id: str, data: dict[str, Any] | Exception) -> None:
    def executor(_params: dict[str, Any]) -> dict[str, Any]:
        if isinstance(data, Exception):
            raise data
        return data

    definition = svc.engine.definitions[probe_id]
    svc.engine.register(definition, executor)


def _diagnose(rt: Runtime, run_id: str) -> dict[str, Any]:
    return DiagnosisService(rt).diagnose(run_id, use_llm_advisor=False)


def _state_result(count: int) -> dict[str, Any]:
    return {
        "template": "get_state_projects",
        "response": {"ok": True, "count": count, "keys": ["k"] * count},
    }


def _add_run_state_evidence(rt: Runtime, run_id: str, *, count: int, at: float) -> str:
    """Run 作用域的独立状态查询证据（TRUSTED，时间落在 Run 窗口内）。"""
    from traceguard_core.ids import new_id

    evd_id = new_id("evd")
    rt.evidence.add_raw(
        evd_id,
        "api",
        "state_query:get_state_projects",
        "default",
        {"step_id": "s1", "count": count, "keys": []},
        source_trust="TRUSTED",
        captured_at=_iso(at),
        run_id=run_id,
    )
    return evd_id


def _iso(epoch: float) -> str:
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(epoch))


def _by_mechanism(doc: dict[str, Any], mechanism: str) -> list[dict[str, Any]]:
    return [h for h in doc["hypotheses"] if h["failure_mechanism"] == mechanism]


# ================================================================== 场景 A
def test_scenario_a_db001_permanent_persistence_keeps_trusted_evidence(rt: Runtime) -> None:
    """场景 A（DB-001）：独立读取确认数据不存在，延迟复测仍不存在 →
    保留 PERSISTENCE 的可信证据，且与 DB-004（生成 TIMING）严格区分。"""
    now = _time.time()
    _insert_run(rt, "run_a", _db_lag_report(), started_at=now - 100, ended_at=now - 30)
    evd_state = _add_run_state_evidence(rt, "run_a", count=0, at=now - 40)
    diag = _diagnose(rt, "run_a")
    # v1：Run 作用域独立读取（count=0）已给 PERSISTENCE 方向性支持（非确权）
    assert diag["outcome"] != "CONFIRMED"
    assert _by_mechanism(diag, "PERSISTENCE")

    svc = ProbeService(rt)
    dsvc = DiagnosisService(rt)

    # 第一次独立读取：count=0
    _patch_probe(svc, "http_state", _state_result(0))
    plan1 = svc.plan_and_probe("run_a", diag["diagnosis_id"], max_iterations=1)
    assert plan1["steps"][0]["status"] == "SUCCEEDED"

    # 延迟复测：count 仍为 0
    _patch_probe(svc, "http_state", _state_result(0))
    plan2 = svc.plan_and_probe("run_a", diag["diagnosis_id"], max_iterations=1)
    assert plan2["steps"][0]["status"] == "SUCCEEDED"

    updated = rt.docs.get(diag["diagnosis_id"]).doc
    persist = _by_mechanism(updated, "PERSISTENCE")
    assert persist, updated["hypotheses"]
    p = persist[0]

    # 1) PERSISTENCE 的可信证据保留（Run 作用域 TRUSTED 证据不被丢弃）
    assert any(r.get("id") == evd_state and r.get("trust") == "TRUSTED" for r in p["supporting"]), (
        p["supporting"]
    )

    # 2) 两次独立读取的证据都以支持附着（可追溯），且均未确权（单查询不确认永久故障）
    probe_refs = [r for r in p["supporting"] if str(r.get("id")) != evd_state]
    assert len(probe_refs) >= 2, p["supporting"]
    assert not any(r.get("confirming") for r in p["supporting"])

    # 3) 与 DB-004 区分：持续缺失不得生成"查询滞后/写入延迟"TIMING 假设
    timing = [
        h
        for h in updated["hypotheses"]
        if h["failure_mechanism"] == "TIMING" and h.get("suggested_by") == "rule:probe-timing"
    ]
    assert not timing, f"DB-001（持续缺失）不得生成 TIMING 滞后假设: {timing}"

    # 4) 无反驳证据、未被排除、未错误确认，也不因证据不足而丢失方向
    assert p["contradicting"] == []
    assert p["excluded_by"] == []
    assert p["status"] == "SUPPORTED"
    assert updated["root_causes"] == []
    assert updated["outcome"] != "CONFIRMED"

    # 5) 每次重诊断留痕：版本链保留原/新诊断与裁决原因
    revisions = dsvc.list_revisions(diag["diagnosis_id"])
    assert len(revisions) >= 3  # v1 初始 + 两次重诊断
    assert revisions[0]["trigger"] == "initial" and revisions[0]["before"] is None
    last = revisions[-1]
    assert last["before"] is not None and last["after"] is not None
    assert last["hypothesis_changes"] and last["confidence_changes"] is not None
    assert last["new_evidence_ids"], "重诊断必须引用新增证据"
    assert last["final_reason"], "必须记录最终裁决原因"


# ================================================================== 场景 B
def test_scenario_b_db004_state_lag_turns_to_timing_not_persistence(rt: Runtime) -> None:
    """场景 B（DB-004）：Run 即时读不到、独立读取最终发现数据存在、延迟复测恢复 →
    诊断方向转向 TIMING；不得错误确认 PERSISTENCE（也不得事后排除 Run 时假设）。"""
    now = _time.time()
    _insert_run(rt, "run_b", _db_lag_report(), started_at=now - 100, ended_at=now - 30)
    evd_state = _add_run_state_evidence(rt, "run_b", count=0, at=now - 40)
    diag = _diagnose(rt, "run_b")

    svc = ProbeService(rt)
    # 即时独立读取：count=0（与 DB-001 相同）
    _patch_probe(svc, "http_state", _state_result(0))
    svc.plan_and_probe("run_b", diag["diagnosis_id"], max_iterations=1)
    # 延迟复测：数据已存在（写入延迟/查询滞后）
    _patch_probe(svc, "http_state", _state_result(3))
    svc.plan_and_probe("run_b", diag["diagnosis_id"], max_iterations=1)

    updated = rt.docs.get(diag["diagnosis_id"]).doc
    persist = _by_mechanism(updated, "PERSISTENCE")[0]

    # 1) 诊断方向转向 TIMING（确定性规则生成"状态查询滞后"）
    timing = [
        h
        for h in updated["hypotheses"]
        if h["failure_mechanism"] == "TIMING" and h.get("suggested_by") == "rule:probe-timing"
    ]
    assert timing, updated["hypotheses"]
    assert "滞后" in timing[0]["description"]
    assert timing[0]["supporting"], "TIMING 假设必须引用新增证据"

    # 2) 不得错误确认 PERSISTENCE
    assert persist["status"] != "CONFIRMED"
    assert updated["root_causes"] == []
    assert updated["outcome"] != "CONFIRMED"

    # 3) 事后证据不得排除 Run 时假设；反驳以 POST_HOC 附着（留痕但无裁决权）
    assert persist["excluded_by"] == []
    assert persist["contradicting"], "延迟复测的反驳证据应附着（可追溯）"
    assert all(
        r.get("trust") == "POST_HOC" or r.get("time_alignment") == "POST_HOC"
        for r in persist["contradicting"]
    )
    # Run 作用域可信证据仍在（原诊断不被覆盖）
    assert any(r.get("id") == evd_state for r in persist["supporting"])

    # 4) 置信度变化可审计：PERSISTENCE 因反驳降权记录在版本链
    from traceguard_api.services.diagnosis_service import DiagnosisService as _DS

    revisions = _DS(rt).list_revisions(diag["diagnosis_id"])
    last = revisions[-1]
    changes = {c["hypothesis_id"]: c for c in last["hypothesis_changes"]}
    assert any(c["added_by_probe"] for c in last["hypothesis_changes"]), "TIMING 为探针新增假设"
    assert changes, last
    assert last["final_reason"]


# ================================================================== 场景 C
def test_scenario_c_api_5xx_is_root_cause_and_ui_not_reconfirmed(rt: Runtime) -> None:
    """场景 C：API 失败是主因；下游 UI 未显示成功不能再次确认 UI_RENDERING；
    证据时间与请求对齐（Run 窗口内证据可权威，POST_HOC 探针不可权威）。"""
    now = _time.time()
    report = _api_5xx_report()
    _insert_run(rt, "run_c", report, started_at=now - 100, ended_at=now - 30)
    from traceguard_core.ids import new_id

    net_evd = new_id("evd")
    rt.evidence.add_raw(
        net_evd,
        "network",
        "step_signals:s1",
        "default",
        {"step_id": "s1", "status": 500, "url": "/api/projects"},
        source_trust="TRUSTED",
        captured_at=_iso(now - 60),
        run_id="run_c",
    )
    diag = _diagnose(rt, "run_c")

    # API 假设是唯一确认根因（含时间对齐核验）
    api_hyps = _by_mechanism(diag, "API")
    ui_hyps = _by_mechanism(diag, "UI_RENDERING")
    assert api_hyps and api_hyps[0]["status"] == "CONFIRMED"
    assert diag["root_causes"] == [api_hyps[0]["hypothesis_id"]]
    assert ui_hyps and ui_hyps[0]["status"] != "CONFIRMED", "UI 缺失不得与 API 并列确认"
    verdicts = {
        v["hypothesis_id"]: v for v in (diag.get("graph_adjudication") or {}).get("verdicts", [])
    }
    api_v = verdicts[api_hyps[0]["hypothesis_id"]]
    assert api_v["authority_confirmed"] is True, "5xx 证据在 Run 窗口内 → 时间对齐可权威"
    assert net_evd not in api_v["time_mismatched"], "主因证据必须与请求时间对齐"

    # 下游 UI 未显示成功不能再次确认 UI_RENDERING：
    # （1）ALIGNED 的 http_state 探针（count>=1）也不得确权 UI_RENDERING（5xx 后果守卫）
    ui_as_dicts = [dict(h, supporting=list(h.get("supporting") or [])) for h in ui_hyps]
    for h in ui_as_dicts:
        h.setdefault("contradicting", [])
        h.setdefault("probe_feedback", [])
        h.setdefault("excluded_by", [])
    fb = assess(
        ProbeSignal(
            probe_id="http_state",
            execution_id="exec_c_aligned",
            status="SUCCEEDED",
            evidence_ids=["evd_c_state"],
            data=_state_result(2),
            observed_at=now - 30,
            run_scope=True,
        ),
        ui_as_dicts,
        report,
        run_ended_at=now - 30,
    )
    assert fb.updates, fb.to_dict()
    assert all(u.confirming is False for u in fb.updates), (
        "Run 存在 5xx 时 UI_RENDERING 不得被探针确权（症状不能冒充根因）"
    )

    # （2）真实探针闭环后，UI 假设依旧未确认、根因仍只有 API
    svc = ProbeService(rt)
    _patch_probe(svc, "http_state", _state_result(2))
    exec_doc = svc.execute(
        "http_state",
        {"template": "get_state_projects", "run_id": "run_c"},
        "default",
        run_id="run_c",
        diagnosis_id=diag["diagnosis_id"],
    )
    assert exec_doc["probe_status"] == "SUCCEEDED"
    assert exec_doc["time_alignment"] == "POST_HOC", "Run 结束后探针 = 事后观测"
    signal = ProbeSignal(
        probe_id="http_state",
        execution_id=exec_doc["execution_id"],
        status="SUCCEEDED",
        evidence_ids=list(exec_doc.get("evidence_refs") or []),
        data=exec_doc.get("result") or {},
        observed_at=exec_doc.get("observed_at") or _time.time(),
        run_scope=False,
    )
    out = DiagnosisService(rt).reassess(diag["diagnosis_id"], [signal], run_report=report)
    after = out["diagnosis"]
    ui_after = _by_mechanism(after, "UI_RENDERING")
    api_after = _by_mechanism(after, "API")
    assert all(h["status"] != "CONFIRMED" for h in ui_after)
    assert after["root_causes"] == [api_after[0]["hypothesis_id"]]
    for h in ui_after:
        for r in h.get("supporting", []):
            assert not r.get("confirming"), "UI 假设不得持有确权证据"
    # POST_HOC 探针证据不具权威
    for r in api_after[0].get("supporting", []):
        if r.get("time_alignment") == "POST_HOC":
            assert not r.get("confirming")
    assert out["new_evidence_ids"], "重诊断必须引用新增证据"
    assert out["final_reason"]


# ================================================================== 场景 D
def test_scenario_d_locator_drift_not_escalated_to_business_failure(rt: Runtime) -> None:
    """场景 D（LOC-001/LOC-002）：定位器变化——独立用户可见操作仍然成功、
    业务结果已落库，诊断不得把它升级为业务功能失效。"""
    now = _time.time()
    _insert_run(rt, "run_d", _locator_report(), started_at=now - 100, ended_at=now - 30)
    diag = _diagnose(rt, "run_d")

    # 初始：LOCATOR 方向（候选级），无任何业务失效假设
    loc = _by_mechanism(diag, "LOCATOR")
    assert loc, diag["hypotheses"]
    assert not _by_mechanism(diag, "PERSISTENCE")

    svc = ProbeService(rt)
    # 独立用户可见操作成功：DOM 中期望元素存在；业务结果已落库：count>=1
    _patch_probe(
        svc,
        "dom_snapshot",
        {
            "template": "run_dom",
            "evidence_id": "evd_dom_src",
            "artifact_id": "art_d",
            "size": 120,
            "excerpt": (
                '<button data-testid="nav-new">新建</button>'
                '<div data-testid="banner-success">创建成功</div>'
            ),
        },
    )
    _patch_probe(svc, "http_state", _state_result(1))
    _patch_probe(
        svc, "network_trace", {"template": "run_network", "run_id": "run_d", "entries": []}
    )
    _patch_probe(svc, "backend_log", {"template": "tail_teamflow", "path": "x", "lines": []})
    plan = svc.plan_and_probe("run_d", diag["diagnosis_id"], max_iterations=4)

    updated = rt.docs.get(diag["diagnosis_id"]).doc

    # 1) 不得升级为业务功能失效：无业务失效根因被确认
    assert updated["root_causes"] == []
    assert updated["outcome"] != "CONFIRMED"
    for mechanism in ("PERSISTENCE", "UI_RENDERING", "API"):
        for h in _by_mechanism(updated, mechanism):
            assert h["status"] != "CONFIRMED", f"{mechanism} 不得被确认为业务失效"
    assert updated["disposition"] != "PRODUCT_DEFECT", "定位器问题不得分流为产品缺陷"

    # 2) 定位器方向保留且有 DOM 机制特异性证据（失败目标不在 DOM 中）
    loc_after = _by_mechanism(updated, "LOCATOR")
    assert loc_after, updated["hypotheses"]
    dom_feedback = [
        f for f in loc_after[0].get("probe_feedback", []) if f.get("probe_id") == "dom_snapshot"
    ]
    assert dom_feedback, loc_after[0]
    assert "create-submit" in dom_feedback[0]["reason"]
    assert loc_after[0]["status"] in {"SUPPORTED", "CANDIDATE"}

    # 3) 业务已落库事实以证据留痕（可追溯到 Run / Workspace / 时间）
    assert plan["evidence_ids"], "探针证据必须落库"
    probe_evidence = [e for e in rt.evidence.list(run_id="run_d", source_type="probe", limit=50)]
    assert probe_evidence
    for e in probe_evidence:
        c = e.content.get("probe_evidence_contract") or {}
        assert c.get("run_id") == "run_d"
        assert c.get("workspace_id") == "default"
        assert c.get("observed_at") and c.get("captured_at")
        assert c.get("time_alignment") in {"ALIGNED", "POST_HOC", "UNKNOWN"}


# ================================================================== 场景 E
def test_scenario_e_probe_unavailable_yields_unknown_without_pollution(rt: Runtime) -> None:
    """场景 E：Probe 不可用产生 UNKNOWN——不污染原诊断、不允许错误 CONFIRMED、
    不改变原始 Run Verdict、置信度不提升。"""
    now = _time.time()
    _insert_run(rt, "run_e", _db_lag_report(), started_at=now - 100, ended_at=now - 30)
    diag = _diagnose(rt, "run_e")
    before_state = {
        h["hypothesis_id"]: (h["status"], hypothesis_confidence(h)) for h in diag["hypotheses"]
    }
    verdict_before = rt.tasks.get_run("run_e")["test_verdict"]

    svc = ProbeService(rt)
    for pid in ("http_state", "network_trace", "backend_log", "dom_snapshot"):
        _patch_probe(svc, pid, ProbeUnavailable("PROBE_UNAVAILABLE"))
    plan = svc.plan_and_probe("run_e", diag["diagnosis_id"], max_iterations=3)

    # 1) 产生 UNKNOWN，不生成任何证据
    attempts = plan["steps"]
    assert attempts, plan
    assert all(s["status"] == "UNKNOWN" for s in attempts), attempts
    assert all(s["evidence_ids"] == [] for s in attempts)
    assert all(s["applied_to_diagnosis"] is False for s in attempts)
    assert plan["evidence_ids"] == []

    # 2) 执行记录：probe_status=UNKNOWN + failure_reason，零证据引用
    executions = svc.list_executions(run_id="run_e", limit=20)
    assert executions
    for ex in executions:
        rec = ex.get("doc") or ex
        assert rec.get("probe_status") == "UNKNOWN"
        assert rec.get("failure_reason")
        assert rec.get("evidence_refs") == []

    # 3) 不污染原诊断：状态与置信度零变化，不允许错误 CONFIRMED
    updated = rt.docs.get(diag["diagnosis_id"]).doc
    after_state = {
        h["hypothesis_id"]: (h["status"], hypothesis_confidence(h)) for h in updated["hypotheses"]
    }
    assert after_state == before_state, "Probe UNKNOWN 不得改变假设状态/置信度"
    assert updated["root_causes"] == []
    assert updated["outcome"] != "CONFIRMED"

    # 4) Probe 失败不改变原始 Run Verdict
    assert rt.tasks.get_run("run_e")["test_verdict"] == verdict_before == "FAILED"


# ================================================== §5.3/§5.5 闭环补充规则
def test_probe_pass_alone_never_confirms_root_cause(rt: Runtime) -> None:
    """Probe PASS（SUCCEEDED）本身不能自动确认根因：无判定内容 → 零更新。"""
    hyps = [
        {
            "hypothesis_id": "h1",
            "failure_mechanism": "PERSISTENCE",
            "supporting": [],
            "contradicting": [],
            "probe_feedback": [],
            "excluded_by": [],
        }
    ]
    sig = ProbeSignal(
        probe_id="http_state",
        execution_id="e_pass",
        status="SUCCEEDED",
        evidence_ids=["evd_pass"],
        data={"response": {}},  # 无可判定内容
        observed_at=_time.time(),
        run_scope=True,
    )
    fb = assess(sig, hyps, {}, run_ended_at=_time.time())
    assert fb.updates == []
    assert fb.skipped_reason == "STATE_COUNT_UNPARSEABLE"


def test_plan_does_not_retry_same_gap_and_stops_at_iteration_limit(rt: Runtime) -> None:
    """§5.5 不允许无限重试：同一缺口不重复尝试，迭代有上限。"""
    now = _time.time()
    _insert_run(rt, "run_r", _db_lag_report(), started_at=now - 100, ended_at=now - 30)
    diag = _diagnose(rt, "run_r")
    svc = ProbeService(rt)
    _patch_probe(svc, "http_state", _state_result(1))
    _patch_probe(
        svc, "network_trace", {"template": "run_network", "run_id": "run_r", "entries": []}
    )
    _patch_probe(svc, "backend_log", {"template": "tail_teamflow", "path": "x", "lines": []})
    plan = svc.plan_and_probe("run_r", diag["diagnosis_id"], max_iterations=2)
    assert plan["stop_reason"] == "STOP_ITERATION_LIMIT"
    assert len(plan["steps"]) == 2
    gap_ids = [s["gap_id"] for s in plan["steps"]]
    assert len(set(gap_ids)) == len(gap_ids), "同一证据缺口不得重复尝试"
