"""RUN-019 WP2：PERSISTENCE/TIMING/LOCATOR 独立证据链确认规则（不降低门槛）。"""

from __future__ import annotations

import time
from typing import Any

from traceguard_core.ids import new_id
from traceguard_persistence import Database
from traceguard_persistence.models import RunRow


def _iso(ts: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def _report(*, biz_failed: bool = True, locator: bool = False) -> dict[str, Any]:
    network = [{"url": "/api/projects", "status": 200, "failure": "", "entity_id": "e1"}]
    if locator:
        return {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "click",
                    "status": "FAILED",
                    "error": "Locator.click: Timeout 8000ms exceeded",
                    "target": {"strategy": "test_id", "value": "create-submit"},
                    "observation": {"network": network, "user_action_succeeded": True},
                }
            ],
            "assertion_outcomes": [
                {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"}
            ],
        }
    return {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "PASSED",
                "observation": {"network": network},
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a_biz",
                "kind": "business_state",
                "status": "FAILED" if biz_failed else "PASSED",
                "reason": "count mismatch",
            }
        ],
    }


def _diagnose(
    tmp_path: Any, report: dict[str, Any], evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    from traceguard_api.runtime import Runtime
    from traceguard_api.services.diagnosis_service import DiagnosisService

    db = Database(f"sqlite:///{tmp_path / 'diag.db'}")
    db.create_schema()
    rt = Runtime(db)
    now = time.time()
    run_id = "run_run019_chain"
    with db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id="task_run019_chain",
                workspace_id="default",
                testcase_version_id="tcase_chain@1",
                execution_status="COMPLETED",
                test_verdict="FAILED",
                report_json=report,
                started_at=now - 100,
                ended_at=now - 30,
            )
        )
    for ev in evidence:
        rt.evidence.add_raw(
            new_id("evd"),
            ev["kind"],
            ev["source_ref"],
            "default",
            ev["content"],
            source_trust=ev.get("trust", "TRUSTED"),
            captured_at=_iso(now + ev["at_offset"]),
            run_id=run_id,
        )
    return DiagnosisService(rt).diagnose(run_id, use_llm_advisor=False)


def _confirmed(diag: dict[str, Any]) -> set[str]:
    return {h["failure_mechanism"] for h in diag["hypotheses"] if h["status"] == "CONFIRMED"}


def test_persistence_requires_immediate_and_delayed_same_entity(tmp_path):  # type: ignore[no-untyped-def]
    diag = _diagnose(
        tmp_path,
        _report(),
        [
            {
                "kind": "api",
                "source_ref": "state_query:immediate",
                "content": {"entity_id": "e1", "count": 0},
                "at_offset": -80,
            },
            {
                "kind": "api",
                "source_ref": "state_query:delayed",
                "content": {"entity_id": "e1", "count": 0},
                "at_offset": -40,
            },
        ],
    )
    assert "PERSISTENCE" in _confirmed(diag), diag["hypotheses"]


def test_post_hoc_delayed_read_does_not_confirm(tmp_path):  # type: ignore[no-untyped-def]
    """延迟复测落在 Run 结束之后：不得把事后证据伪装为 Run 期间权威证据。"""
    diag = _diagnose(
        tmp_path,
        _report(),
        [
            {
                "kind": "api",
                "source_ref": "state_query:immediate",
                "content": {"entity_id": "e1", "count": 0},
                "at_offset": -80,
            },
            {
                "kind": "api",
                "source_ref": "state_query:delayed",
                "content": {"entity_id": "e1", "count": 0},
                "at_offset": -10,
            },
        ],
    )
    assert "PERSISTENCE" not in _confirmed(diag), diag["hypotheses"]


def test_timing_recovered_within_run_window(tmp_path):  # type: ignore[no-untyped-def]
    diag = _diagnose(
        tmp_path,
        _report(),
        [
            {
                "kind": "api",
                "source_ref": "state_query:immediate",
                "content": {"entity_id": "e1", "count": 0},
                "at_offset": -80,
            },
            {
                "kind": "api",
                "source_ref": "state_query:delayed",
                "content": {"entity_id": "e1", "count": 3},
                "at_offset": -40,
            },
        ],
    )
    assert "TIMING" in _confirmed(diag), diag["hypotheses"]


def test_legacy_holdout_shape_still_abstains(tmp_path):  # type: ignore[no-untyped-def]
    """RUN-018 旧样本没有 entity_id/Run 窗口内 DOM：新规则不得污染其弃权口径。"""
    diag = _diagnose(
        tmp_path,
        _report(),
        [
            {
                "kind": "api",
                "source_ref": "state_query:immediate",
                "content": {"count": 0},
                "at_offset": -80,
            },
            {
                "kind": "api",
                "source_ref": "state_query:delayed",
                "content": {"count": 0},
                "at_offset": -10,
            },
        ],
    )
    assert _confirmed(diag) == set(), diag["hypotheses"]


def test_locator_requires_dom_and_business_state(tmp_path):  # type: ignore[no-untyped-def]
    diag = _diagnose(
        tmp_path,
        _report(locator=True),
        [
            {
                "kind": "browser",
                "source_ref": "dom_snapshot:s1",
                "content": {
                    "entity_id": "e1",
                    "matched": True,
                    "visible": True,
                    "role": "button",
                    "name": "新建",
                },
                "at_offset": -70,
            },
            {
                "kind": "api",
                "source_ref": "state_query:delayed",
                "content": {"entity_id": "e1", "count": 1},
                "at_offset": -40,
            },
        ],
    )
    assert "LOCATOR" in _confirmed(diag), diag["hypotheses"]
    assert "PRODUCT_DEFECT" not in str(diag.get("disposition", {})), diag.get("disposition")


def test_locator_without_business_state_stays_unconfirmed(tmp_path):  # type: ignore[no-untyped-def]
    diag = _diagnose(
        tmp_path,
        _report(locator=True),
        [
            {
                "kind": "browser",
                "source_ref": "dom_snapshot:s1",
                "content": {"entity_id": "e1", "matched": True, "visible": True},
                "at_offset": -70,
            }
        ],
    )
    assert "LOCATOR" not in _confirmed(diag), diag["hypotheses"]
