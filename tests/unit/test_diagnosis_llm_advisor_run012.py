"""RUN-012 WP5：诊断服务调用层的 LLM 顾问可靠性契约。

覆盖：
- 顾问返回结构化结果后，诊断文档记录 called/fallback/error/attempts/dropped；
- LLM 失败必须回退为「未调用」语义，**不得**影响确定性规则诊断结果；
- LLM 候选假设固定为 CANDIDATE，永不 CONFIRMED（能力层 ≠ 事实层）；
- LLM 候选不得改写既有规则假设。
"""

from __future__ import annotations

import time as _time
from typing import Any

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.diagnosis_service import DiagnosisService
from traceguard_persistence import Database

REPORT: dict[str, Any] = {
    "steps": [
        {
            "step_id": "s1",
            "action": "click",
            "status": "FAILED",
            "error": "banner missing",
            "observation": {"network": [], "http_errors": []},
        }
    ],
    "assertion_outcomes": [
        {"assertion_id": "a_ui", "kind": "ui", "status": "FAILED", "reason": "banner missing"}
    ],
}


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"diagadv-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'diagadv.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _insert_run(rt: Runtime, run_id: str) -> None:
    from traceguard_persistence.models import RunRow

    with rt.db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id=f"task_{run_id}",
                workspace_id="default",
                testcase_version_id="tcase_diag@1",
                execution_status="COMPLETED",
                test_verdict="FAILED",
                report_json=REPORT,
                ended_at=_time.time(),
            )
        )


class _FakeAdvisor:
    """模拟顾问返回：可控 candidates / fallback / error。"""

    def __init__(self, advice: Any) -> None:
        self._advice = advice
        self.router = type("R", (), {"default_model": "deepseek-flash"})()

    def suggest(self, **kwargs: Any) -> Any:  # noqa: ANN003
        return self._advice


def _advice(candidates: list, *, fallback: bool = False, error: str = "", attempts: int = 1):
    from traceguard_agents.gateway.llm_gateway import DiagnosisAdvice

    return DiagnosisAdvice(
        candidates=candidates,
        called=True,
        fallback=fallback,
        error=error,
        model="deepseek-flash",
        attempts=attempts,
    )


class _FakeTracker:
    """预算检查桩：默认放行。"""

    def should_skip_llm(self) -> bool:
        return False


def _patch_advisor(monkeypatch: pytest.MonkeyPatch, advisor: Any) -> None:
    monkeypatch.setattr(
        DiagnosisService,
        "_get_advisor",
        lambda self, stack, run_id: advisor,  # noqa: ANN001, ANN202
    )
    monkeypatch.setattr(
        "traceguard_agents.gateway.llm_gateway.build_llm_stack",
        lambda settings, root_dir=".": {"provider": object(), "tracker": _FakeTracker()},  # noqa: ANN005
    )


def test_llm_failure_falls_back_and_keeps_rule_diagnosis(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 不可用时诊断结论必须仍然来自确定性规则，且明确记录回退。"""
    _insert_run(rt, "run_llm_fail")
    _patch_advisor(monkeypatch, _FakeAdvisor(_advice([], fallback=True, error="rate_limit")))

    diag = DiagnosisService(rt).diagnose("run_llm_fail", use_llm_advisor=True)
    notes = diag["llm_advisor"]
    assert notes["called"] is True
    assert notes["fallback"] is True
    assert notes["fallbacks"] == ["rate_limit"]
    assert notes["added"] == []
    # 确定性规则诊断不受 LLM 失败影响
    assert diag["hypotheses"], "规则引擎必须仍产出假设"
    assert diag["outcome"]


def test_llm_candidates_are_candidate_only_never_confirmed(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 候选只能是 CANDIDATE，永不 CONFIRMED。"""
    from traceguard_agents.gateway.llm_gateway import HypothesisCandidate

    _insert_run(rt, "run_llm_cand")
    candidate = HypothesisCandidate(
        description="可能是接口 500",
        failure_domain="API",
        failure_mechanism="server_error",
        supporting_evidence_ids=["evd_x"],
        confidence=0.7,
        suggested_by="llm:deepseek-flash",
    )
    _patch_advisor(monkeypatch, _FakeAdvisor(_advice([candidate])))

    diag = DiagnosisService(rt).diagnose("run_llm_cand", use_llm_advisor=True)
    llm_hyps = [h for h in diag["hypotheses"] if str(h.get("suggested_by", "")).startswith("llm:")]
    assert llm_hyps, "LLM 候选必须出现在假设列表中"
    assert all(h["status"] == "CANDIDATE" for h in llm_hyps)
    assert all(h["description"].startswith("[LLM 候选]") for h in llm_hyps)
    notes = diag["llm_advisor"]
    assert notes["added"], "added 必须记录 LLM 候选摘要"
    assert notes["fallback"] is False
    assert notes["attempts"] == 1


def test_llm_candidates_do_not_overwrite_rule_hypotheses(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 候选是追加，不得改写规则假设。"""
    from traceguard_agents.gateway.llm_gateway import HypothesisCandidate

    _insert_run(rt, "run_llm_append")
    before = DiagnosisService(rt).diagnose("run_llm_append", use_llm_advisor=False)
    rule_hyps = [h for h in before["hypotheses"] if not h["description"].startswith("[LLM 候选]")]

    candidate = HypothesisCandidate(
        description="附加候选",
        failure_domain="UI",
        failure_mechanism="rendering",
        supporting_evidence_ids=["evd_x"],
        suggested_by="llm:deepseek-flash",
    )
    _patch_advisor(monkeypatch, _FakeAdvisor(_advice([candidate])))
    after = DiagnosisService(rt).diagnose("run_llm_append", use_llm_advisor=True)
    after_rule = [h for h in after["hypotheses"] if not h["description"].startswith("[LLM 候选]")]
    assert len(after_rule) == len(rule_hyps)
    assert {h["description"] for h in after_rule} == {h["description"] for h in rule_hyps}


def test_llm_disabled_makes_no_advisor_call(rt: Runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    """use_llm_advisor=False 时不得调用顾问（Benchmark 规则组口径纯净）。"""
    _insert_run(rt, "run_llm_off")
    called = {"n": 0}

    class _Counting(_FakeAdvisor):
        def suggest(self, **kwargs: Any) -> Any:  # noqa: ANN003
            called["n"] += 1
            return _advice([])

    _patch_advisor(monkeypatch, _Counting(_advice([])))
    DiagnosisService(rt).diagnose("run_llm_off", use_llm_advisor=False)
    assert called["n"] == 0


def test_not_configured_stack_returns_none(rt: Runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置凭据时返回 None（不产生 llm_advisor 段落），不伪装成调用过。"""
    _insert_run(rt, "run_llm_none")
    monkeypatch.setattr(
        "traceguard_agents.gateway.llm_gateway.build_llm_stack",
        lambda settings, root_dir=".": None,  # noqa: ANN005
    )
    diag = DiagnosisService(rt).diagnose("run_llm_none", use_llm_advisor=True)
    assert diag.get("llm_advisor") is None
