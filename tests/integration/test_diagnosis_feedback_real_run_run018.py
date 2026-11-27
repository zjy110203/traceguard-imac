"""RUN-018 WP5 §5.3：Active Probe → Diagnosis Feedback 真实运行验证。

与 test_diagnosis_feedback_scenarios_run018.py（patched 探针执行器）不同，本文件：
- 启动**真实 TeamFlow 进程**（独立 OS 进程 + 独立库）；
- 对真实 TeamFlow 注入 TF-DB-001（持久化静默失败）；
- 通过真实 HTTP 调用创建项目 → 观察到「API 返回成功但 persisted=False」的真实业务签名；
- 由该真实观测构造 Run 报告与证据 → 真实 DiagnosisService 生成初始诊断（revision 1）；
- 真实 ProbeService.plan_and_probe 完整闭环（Evidence Gap → Probe Plan → 真实只读探针
  → 新 Evidence → 回灌假设 → 重新裁决 → revision 追加）；
- 新 Evidence 落库（probe_evidence_contract 完整、Workspace 继承 Run 归属）；
- GraphService 重建图（ProbeExecution 节点进入图）；
- 版本链可追溯（revisions 2 条，v1 状态可恢复，假设/置信度变化有依据）；
- 探针不可用变体：TeamFlow 宕机 → UNKNOWN 不污染原诊断、不改写 Run Verdict；
- 清理故障与探针数据（真实 probe-cleanup）。

前端查看完整历史由 DiagnosisDetailView 通过 /diagnoses/{id}/revisions 读取——
本测试同时校验该数据源的契约。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

TEAMFLOW_TOKEN = "a1b2c3d4e5f6a7b8"  # 只允许 [0-9a-f]（与 WP3 清理契约一致）


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - 本地测试目标
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            detail = {}
        return exc.code, detail


def _wait_health(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def teamflow(tmp_path_factory):
    work = tmp_path_factory.mktemp("run018-wp5")
    port = _free_port()
    pythonpath = ":".join(
        str(ROOT / p)
        for p in (
            "packages/core",
            "packages/contracts",
            "packages/execution",
            "packages/agents",
            "packages/evidence",
            "packages/persistence",
            "apps/api",
            "apps/teamflow",
        )
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(work)),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "PYTHONPATH": pythonpath,
        "TG_TEAMFLOW_DB": f"sqlite:///{work / 'teamflow.db'}",
    }
    proc = subprocess.Popen(  # noqa: S603 - 受控本地子进程
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    if not _wait_health(base + "/health"):
        proc.terminate()
        pytest.skip("TeamFlow 未能在本机启动")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


@pytest.fixture()
def rt(teamflow, work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"wp5-real-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    monkeypatch.setenv("TG_TEAMFLOW_BASE_URL", teamflow)
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    from traceguard_api.runtime import Runtime
    from traceguard_persistence import Database

    db = Database(f"sqlite:///{base / 'wp5-real.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()


def _activate_fault(teamflow: str, fault_id: str, params: dict) -> None:
    status, _ = _http("POST", f"{teamflow}/api/faults/{fault_id}/activate", params)
    assert status in (200, 201)


def _clear_fault(teamflow: str, fault_id: str) -> None:
    _http("POST", f"{teamflow}/api/faults/{fault_id}/clear", {})


def _state_count(teamflow: str) -> int:
    _, state = _http("GET", teamflow + "/api/state/projects")
    return int(state.get("count") or 0)


def _insert_run(rt, run_id: str, report: dict, verdict: str = "FAILED") -> None:  # type: ignore[no-untyped-def]
    from traceguard_persistence.models import RunRow

    with rt.db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id=f"task_{run_id}",
                workspace_id="default",
                testcase_version_id="tcase_wp5_real@1",
                execution_status="COMPLETED",
                test_verdict=verdict,
                report_json=report,
                started_at=time.time() - 60,
                ended_at=time.time() - 30,
            )
        )


def test_real_run_diagnosis_feedback_full_loop(rt, teamflow) -> None:  # type: ignore[no-untyped-def]
    """真实运行闭环：真实故障 → 真实观测 → 诊断 v1 → plan_and_probe 完整闭环 →
    新证据 → 图更新 → 版本链 → 清理。"""
    from traceguard_api.services.diagnosis_service import DiagnosisService
    from traceguard_api.services.graph_service import GraphService
    from traceguard_api.services.probe_service import ProbeService
    from traceguard_core.ids import new_id
    from traceguard_persistence.models import EvidenceRow

    # ---- 1. 真实故障注入（TF-DB-001：持久化静默失败）
    _activate_fault(teamflow, "TF-DB-001", {})
    try:
        before_count = _state_count(teamflow)
        # ---- 2. 真实业务调用：API 返回 ok（UI 显示成功），但持久化被跳过
        proj_name = f"bp:{TEAMFLOW_TOKEN}:wp5-real-loop"
        status, create_resp = _http(
            "POST",
            teamflow + "/api/projects",
            {"name": proj_name, "description": "wp5-real-loop", "workspace_slug": "default"},
        )
        assert status in (200, 201), create_resp
        assert create_resp.get("ok") is True
        assert create_resp.get("persisted") is False, "TF-DB-001 下持久化必须被跳过"
        # 独立状态查询：业务实际未落库（真实状态回读）
        after_count = _state_count(teamflow)
        assert after_count == before_count, "TF-DB-001 下独立状态查询不得看到新项目"

        # ---- 3. 由真实观测构造 Run（报告 = 真实网络/断言结果）
        run_id = f"run_{int(time.time() * 1000):x}real"
        report = {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "click",
                    "status": "PASSED",
                    "error": "",
                    "target": {"strategy": "test_id", "value": "create-submit"},
                    "observation": {
                        "network": [{"url": "/api/projects", "status": status, "failure": ""}]
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
        _insert_run(rt, run_id, report)
        # Run 作用域真实证据：真实业务调用结果（TRUSTED）
        evd_create = new_id("evd")
        rt.evidence.add_raw(
            evd_create,
            "api",
            "http:create_project",
            "default",
            {"step_id": "s1", "response": create_resp, "persisted": create_resp.get("persisted")},
            source_trust="TRUSTED",
            captured_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            run_id=run_id,
        )

        # ---- 4. 初始诊断（revision 1）
        svc = DiagnosisService(rt)
        diag = svc.diagnose(run_id, use_llm_advisor=False)
        assert diag["run_id"] == run_id
        assert diag["revision_no"] == 1
        diag_id = str(diag["diagnosis_id"])

        # ---- 5-7. plan_and_probe 完整闭环（真实 http_state 探针 → 真实 TeamFlow）
        probe_svc = ProbeService(rt)
        plan = probe_svc.plan_and_probe(run_id, diag_id, max_iterations=3)
        assert plan["steps"], "闭环必须产生真实探针执行步骤"
        http_steps = [st for st in plan["steps"] if st.get("probe_id") == "http_state"]
        assert http_steps, f"必须执行真实 http_state 探针: {plan['steps']}"
        assert all(st.get("status") == "SUCCEEDED" for st in http_steps), http_steps
        # 探针观测到的真实状态：从探针证据回读 count，与独立状态查询一致
        # （未包含未持久化项目 = 真实读到了目标系统的权威状态）
        probe_counts = []
        for st in http_steps:
            for eid in st.get("evidence_ids") or []:
                rec = rt.evidence.get(str(eid))
                assert rec is not None, f"探针证据 {eid} 必须可回读"
                raw = (rec.content.get("data") or {}).get("response", {}).get("count")
                assert raw is not None, f"探针证据必须含真实 count: {rec.content.get('data')}"
                probe_counts.append(int(raw))
        assert probe_counts, "http_state 步骤必须产出可回读证据"
        assert before_count in probe_counts, (
            f"真实探针回读必须等于真实状态 {before_count}: {probe_counts}"
        )
        # 闭环回灌：applied_to_diagnosis
        assert any(st.get("applied_to_diagnosis") for st in http_steps), http_steps

        # ---- 新 Evidence 落库（正确 Workspace + 探针证据契约）
        http_evidence_ids = [
            str(eid) for st in http_steps for eid in (st.get("evidence_ids") or [])
        ]
        assert http_evidence_ids, "http_state 步骤必须落库证据"
        with rt.db.session() as s:
            rows = s.query(EvidenceRow).filter_by(run_id=run_id).all()
            probe_rows = [r for r in rows if r.source_type == "probe"]
            assert probe_rows, "探针证据必须落库"
            probe_evidence = next(r for r in probe_rows if r.evidence_id in http_evidence_ids)
            assert probe_evidence.workspace_id == "default", "新证据必须进入 Run 的真实 Workspace"
            # EvidenceRow.doc 是 rec.to_dict()（内容嵌套在 content 字段下）
            doc = (probe_evidence.doc or {}).get("content") or probe_evidence.doc or {}
            assert doc.get("probe_evidence_contract"), "探针证据必须带完整契约"
            assert doc.get("diagnosis_id") == diag_id
            assert doc.get("observed_at") is not None

        # ---- 8. Graph 更新（探针证据节点进入图）
        graph = GraphService(rt).build_for_run(run_id)
        kinds = {n["node_type"] for n in graph["nodes"]}
        assert "ProbeExecution" in kinds, kinds
        assert any(e["relation"] == "evaluates" for e in graph["edges"])

        # ---- 9. 诊断更新（同一诊断 + revision 链追加写，v1 可恢复）
        updated = rt.docs.get(diag_id).doc
        assert updated["revision_no"] >= 2, (
            f"探针证据必须产生新 revision: {updated.get('revision_no')}"
        )
        assert updated["probe_feedback"], "必须记录探针反馈"
        assert updated["evaluation_history"], "必须记录重诊断历史"
        # v1 状态可恢复：revision 链第 1 条保存了原始快照（DiagnosisDetailView 数据源）
        revisions = svc.list_revisions(diag_id)
        assert len(revisions) >= 2, f"版本链应有 ≥2 条: {len(revisions)}"
        rev1 = [r for r in revisions if r.get("revision_no") == 1][0]
        assert rev1.get("before") or rev1.get("trigger") == "initial", rev1.get("trigger")
        rev2 = revisions[-1]
        assert rev2.get("trigger") == "probe_evidence"
        assert rev2.get("probe_ids"), rev2
        assert "confidence_changes" in rev2 or "hypothesis_changes" in rev2
        # PERSISTENCE 假设获得真实证据支撑（独立查询确认未持久化）
        persistence = [h for h in updated["hypotheses"] if h["failure_mechanism"] == "PERSISTENCE"]
        assert persistence, "真实签名必须生成 PERSISTENCE 假设"
        assert persistence[0].get("supporting"), "真实探针证据必须附着为支持证据"
        # Run Verdict 不被探针改写
        from sqlalchemy import select

        with rt.db.session() as s:
            from traceguard_persistence.models import RunRow

            row = s.scalars(select(RunRow).where(RunRow.run_id == run_id)).first()
            assert row is not None and row.test_verdict == "FAILED", "探针不得改写 Run Verdict"
    finally:
        # ---- 10. 清理：清除故障 + 清理探针数据（真实 probe-cleanup）
        _clear_fault(teamflow, "TF-DB-001")
        status, result = _http(
            "POST",
            teamflow + "/api/test/probe-cleanup",
            {"token": TEAMFLOW_TOKEN, "workspace": "default", "allow_default": True},
        )
        assert status == 200 and result.get("ok") is True, result


def test_real_run_probe_unavailable_keeps_v1_verdict(rt, teamflow) -> None:  # type: ignore[no-untyped-def]
    """真实探针不可用（TeamFlow 宕机）→ 探针失败不留假证据、不确认假设、
    不改写 Run Verdict，版本链如实记录。"""
    from traceguard_api.services.diagnosis_service import DiagnosisService
    from traceguard_api.services.probe_service import ProbeService

    run_id = f"run_{int(time.time() * 1000):x}dead"
    report = {
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
            }
        ],
        "assertion_outcomes": [
            {"assertion_id": "a_ui", "kind": "ui", "status": "PASSED", "reason": ""},
            {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"},
        ],
    }
    _insert_run(rt, run_id, report, verdict="INCONCLUSIVE")
    svc = DiagnosisService(rt)
    diag = svc.diagnose(run_id, use_llm_advisor=False)
    diag_id = str(diag["diagnosis_id"])

    # 把探针指向不可达端口（模拟 TeamFlow 宕机）
    probe_svc = ProbeService(rt)
    probe_svc.rt.settings.teamflow_base_url = "http://127.0.0.1:1"
    plan = probe_svc.plan_and_probe(run_id, diag_id, max_iterations=2)
    # 目标不可达的 http_state 探针不得伪造成 SUCCEEDED
    # （network_trace 只读 Run 自身报告，与 TeamFlow 可达性无关，允许成功）
    http_steps = [st for st in plan["steps"] if st.get("probe_id") == "http_state"]
    assert http_steps, plan["steps"]
    assert all(st.get("status") != "SUCCEEDED" for st in http_steps), http_steps

    updated = rt.docs.get(diag_id).doc
    for h in updated["hypotheses"]:
        refs = (h.get("supporting") or []) + (h.get("contradicting") or [])
        assert h.get("status") != "CONFIRMED" or refs, (
            f"探针不可用时任何 CONFIRMED 都必须有真实证据: {h['failure_mechanism']}"
        )
    # Run Verdict 保持 INCONCLUSIVE
    from sqlalchemy import select

    with rt.db.session() as s:
        from traceguard_persistence.models import RunRow

        row = s.scalars(select(RunRow).where(RunRow.run_id == run_id)).first()
        assert row is not None and row.test_verdict == "INCONCLUSIVE"
