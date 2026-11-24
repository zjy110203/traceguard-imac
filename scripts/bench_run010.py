"""RUN-010 WP5：Benchmark v4 真实实验驱动（14 场景真实注入 + 真实执行 + 独立评测）。

与 RUN-009 (bench_run009.py) 的区别：
- RUN-009 的 v4 场景只有定义与 GT，未植入故障（BLOCKED_NOT_SEEDED）；
- 本脚本为每个 v4 场景真实激活 TeamFlow 故障（受控注入）、真实执行测试
  （真实 Chromium + 真实事件/证据）、真实执行诊断（v2 引擎 + 图裁决）、
  真实执行探针（含时间对齐回灌），并用评测端令牌读取 GT 独立评分。

实验臂（实现必须真实不同，禁止同名不同实现）：
- A_raw_report          ：仅执行 verdict（无诊断）；
- C_rule                ：规则引擎 v2 + Graph 裁决（完整链路）；
- D_graph_ablation      ：同一引擎但**禁用**图裁决（真实消融，不是 C 的改名）；
- E_probe_no_feedback   ：探针证据只附着不回灌（RUN-009 E 臂语义，闭环断裂对照）；
- F_probe_feedback      ：真实探针 + 时间对齐回灌（POST_HOC 规则生效）；
- G_probe_no_alignment  ：同一探针但关闭时间对齐（事后观测视为权威——测量对齐的价值）；
- CF_counterfactual     ：反事实对照（单独标注，不计入实测结论）；
- LLM_CONSTRAINED       ：无授权凭据时如实 BLOCKED，不冒充。

用法：
    .venv/bin/python scripts/bench_run010.py --out docs/assets/run010/benchmark [--rounds 2]

产物：
    <out>/env.json            运行环境记录
    <out>/raw.round0.jsonl    第一轮原始样本（逐场景逐臂）
    <out>/raw.round1.jsonl    第二轮独立原始数据
    <out>/benchmark-v4.json   汇总指标（全部带 numerator/denominator/n）
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages/core"),
    str(ROOT / "packages/contracts"),
    str(ROOT / "packages/execution"),
    str(ROOT / "packages/agents"),
    str(ROOT / "packages/evidence"),
    str(ROOT / "packages/persistence"),
    str(ROOT / "apps/api"),
    str(ROOT / "apps/teamflow"),
    str(ROOT / "apps/workers"),
    str(ROOT / "benchmark"),
]

from traceguard_api.runtime import Runtime  # noqa: E402
from traceguard_api.seed import seed_teamflow_catalog  # noqa: E402
from traceguard_api.services.diagnosis_service import DiagnosisService  # noqa: E402
from traceguard_api.services.probe_service import ProbeService  # noqa: E402
from traceguard_benchmark.scenarios_v4 import GT_MANAGER, V4_SCENARIOS  # noqa: E402
from traceguard_core.config import reset_settings_cache  # noqa: E402
from traceguard_evidence.diagnosis.engine import DiagnosisEngine  # noqa: E402
from traceguard_evidence.graph.adjudication import (  # noqa: E402
    GraphAdjudicator,
    evidence_meta_from_rows,
)
from traceguard_evidence.probe.feedback import ProbeSignal, merge_signals  # noqa: E402
from traceguard_persistence import Database  # noqa: E402

# ---------------------------------------------------------------- 场景 → 注入映射
#: 每个场景的受控故障注入计划。多故障 = 同时激活（TeamFlow FAULT_STATE 支持并存）。
INJECTION_PLAN: dict[str, dict[str, Any]] = {
    "V4-MULTISRC-001": {"faults": ["TF-API-001", "TF-UI-003"], "testcase": "tcase_bench_comp001"},
    "V4-MULTISRC-002": {"faults": ["TF-DB-001"], "testcase": "tcase_tf_false_pass"},
    "V4-SIMILAR-001": {"faults": ["TF-UI-001"], "testcase": "tcase_tf_ui_missing"},
    "V4-SIMILAR-002": {"faults": ["TF-LOC-001"], "testcase": "tcase_bench_loc001"},
    "V4-TEMPORAL-001": {"faults": ["TF-DB-004"], "testcase": "tcase_bench_db004"},
    "V4-COMPOUND-001": {"faults": ["TF-API-001", "TF-UI-003"], "testcase": "tcase_bench_comp001"},
    "V4-UIOK-BACKEND-001": {"faults": ["TF-DB-001"], "testcase": "tcase_tf_false_pass"},
    "V4-NETVSAPP-001": {"faults": ["TF-NET-001"], "testcase": "tcase_bench_net002"},
    "V4-NETVSAPP-002": {"faults": ["TF-API-001"], "testcase": "tcase_bench_api002"},
    # LOCFLUX：5 次回放，仅第 3 次激活定位器故障（受控波动，非概率）
    "V4-LOCFLUX-001": {
        "faults": [],
        "testcase": "tcase_bench_loc001",
        "replays": 5,
        "replay_faults": {2: ["TF-LOC-001"]},
    },
    # FLAKY：5 次回放，第 2/4 次激活持久化回滚（确定性 2 通过 3 失败）
    "V4-FLAKY-001": {
        "faults": [],
        "testcase": "tcase_bench_flaky001",
        "replays": 5,
        "replay_faults": {1: ["TF-DB-001"], 3: ["TF-DB-001"]},
    },
    "V4-AUTH-001": {"faults": ["TF-SEC-001"], "testcase": "tcase_bench_sec001"},
    # INCOMPLETE：无 Oracle 用例（专门构造）+ 网络必超时 → 断言前中断
    "V4-INCOMPLETE-001": {"faults": ["TF-NET-002"], "testcase": "tcase_v4_incomplete"},
    "V4-DUP-001": {"faults": ["TF-UI-001"], "testcase": "tcase_tf_ui_missing"},
}


class FaultInjector:
    """通过 TeamFlow 真实 API 激活/清除故障（受控注入，不改源码）。"""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode() or "{}")

    def activate(self, fault_id: str, params: dict[str, Any] | None = None) -> None:
        result = self._post(f"/api/faults/{fault_id}/activate", params or {})
        assert result.get("ok"), f"故障激活失败: {fault_id}"

    def clear_all(self) -> None:
        """TeamFlow 无『清空全部』路由（/api/faults/{id}/clear 需逐个）：
        先拉取可用故障清单，再逐一 clear（幂等）。"""
        with urllib.request.urlopen(self.base + "/api/faults", timeout=10) as r:
            data = json.loads(r.read().decode())
        for fault_id in data.get("available") or {}:
            self._post(f"/api/faults/{fault_id}/clear", {})

    def active(self) -> list[str]:
        with urllib.request.urlopen(self.base + "/api/faults", timeout=10) as r:
            data = json.loads(r.read().decode())
        return list((data.get("state") or {}).get("active") or [])


def _make_incomplete_testcase(rt: Runtime) -> str:
    """构造 NOT_EVALUATED 用例（V4-INCOMPLETE-001）。

    契约要求 assertions 非空：用「未审批 Oracle」构造——断言存在但 Oracle 为
    PENDING → 判定返回 NOT_EVALUATED（证据不完整，不得下结论），符合场景语义。
    """
    catalog = rt.catalog
    if not catalog.get_testcase("tcase_v4_incomplete"):
        catalog.create_oracle(
            {
                "oracle_id": "oracle_v4_incomplete",
                "assertion_id": "a_v4_incomplete",
                "expected_behavior": "（V4-INCOMPLETE-001 专用：保持未审批）",
                "requirement_ref": "REQ-V4-INCOMPLETE",
                "version": 1,
            }
            # 不传 approved=True → PENDING
        )
        payload = {
            "testcase_id": "tcase_v4_incomplete",
            "version": 1,
            "steps": [
                {"step_id": "ic1", "action": "navigate", "value": "/projects"},
                {
                    "step_id": "ic2",
                    "action": "click",
                    "target": {"strategy": "test_id", "value": "create-submit"},
                },
            ],
            "assertions": [
                {
                    "assertion_id": "a_v4_incomplete",
                    "description": "Oracle 未审批 → NOT_EVALUATED",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "banner-success"},
                    "oracle_id": "oracle_v4_incomplete",
                }
            ],
            "run_config": {"base_url": ""},
            "compile_source": "human",
            "created_at": "2026-01-01T00:00:00Z",
        }
        catalog.create_testcase(payload)
    return "tcase_v4_incomplete"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_health(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


class BenchEnv:
    """独立测试环境：真实 TeamFlow 子进程 + 真实 Runtime（SQLite）。"""

    def __init__(self) -> None:
        import tempfile

        self.work = Path(tempfile.mkdtemp(prefix="bench-v4-"))
        self.teamflow_port = _free_port()
        env = {k: v for k, v in __import__("os").environ.items()}
        env["PYTHONPATH"] = (
            str(ROOT / "apps/api")
            + ":"
            + str(ROOT / "packages/core")
            + ":"
            + str(ROOT / "apps/teamflow")
        )
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "traceguard_teamflow.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.teamflow_port),
            ],
            env=env,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.base_url = f"http://127.0.0.1:{self.teamflow_port}"
        assert _wait_health(f"{self.base_url}/health"), "TeamFlow 未启动"
        reset_settings_cache()
        self.db = Database(f"sqlite:///{self.work / 'bench.db'}")
        self.rt = Runtime(self.db)
        seed_teamflow_catalog(self.rt, base_url=self.base_url)
        self.injector = FaultInjector(self.base_url)
        self.injector.clear_all()

    def close(self) -> None:
        try:
            self.injector.clear_all()
        except Exception:  # noqa: BLE001
            pass
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.db.dispose()


# ---------------------------------------------------------------- 执行与观测
def _execute_scenario(env: BenchEnv, scenario_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    """真实注入 → 真实执行 → 真实诊断 → 真实探针。返回原始样本。"""
    rt = env.rt
    injector = env.injector
    injector.clear_all()
    t0 = time.perf_counter()

    replays: list[dict[str, Any]] = []
    n_replays = int(plan.get("replays") or 1)
    for replay_idx in range(n_replays):
        # 受控注入：本轮回放的故障集
        faults = list(plan.get("faults") or [])
        faults = faults + list((plan.get("replay_faults") or {}).get(replay_idx, []))
        injector.clear_all()
        for f in faults:
            injector.activate(f)
        testcase_id = plan["testcase"]
        task = rt.tasks.create_task(
            testcase_id,
            f"bench-v4-{scenario_id}-r{replay_idx}",
            mode="playwright",
            testcase_version_id=f"{testcase_id}@1",
        )
        run = rt.tasks.create_run(task["task_id"], f"{testcase_id}@1")
        started = time.perf_counter()
        outcome = rt.execute_run(run["run_id"])
        exec_ms = round((time.perf_counter() - started) * 1000, 1)
        diag = None
        # FAILED 与 NOT_EVALUATED（有步骤证据）都进入规则诊断：
        # NOT_EVALUATED 只能产出 PROBABLE/INCONCLUSIVE 方向，不冒充结论
        if outcome.get("test_verdict") in {"FAILED", "NOT_EVALUATED"}:
            try:
                diag = DiagnosisService(rt).diagnose(run["run_id"], use_llm_advisor=False)
            except Exception as exc:  # noqa: BLE001 - 诊断失败如实记录，不编造
                diag = {"error": str(exc)[:200]}
        replays.append(
            {
                "replay": replay_idx,
                "run_id": run["run_id"],
                "execution_status": outcome.get("execution_status"),
                "test_verdict": outcome.get("test_verdict"),
                "observer_mode": outcome.get("observer_mode"),
                "faults": faults,
                "exec_ms": exec_ms,
                "diagnosis": diag,
                "evidence_count": len(rt.evidence.list(run_id=run["run_id"], limit=200)),
            }
        )
    injector.clear_all()

    total_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "scenario": scenario_id,
        "testcase": plan["testcase"],
        "replays": replays,
        "total_ms": total_ms,
    }


def _primary_sample(sample: dict[str, Any]) -> dict[str, Any] | None:
    """计分样本：优先有诊断结论的主回放（replay 0），失败时选最后一个 FAILED。"""
    eligible = [
        r for r in sample["replays"] if r.get("diagnosis") and not r["diagnosis"].get("error")
    ]
    if eligible:
        return eligible[0]
    failed = [r for r in sample["replays"] if r.get("test_verdict") in {"FAILED", "NOT_EVALUATED"}]
    return failed[0] if failed else None


# ---------------------------------------------------------------- 实验臂
def _diag_to_pred(diag: dict[str, Any] | None) -> dict[str, Any]:
    if not diag:
        return {"mechanism": None, "conclusive": False, "outcome": "NO_DIAGNOSIS"}
    if diag.get("error"):
        return {"mechanism": None, "conclusive": False, "outcome": "DIAGNOSIS_ERROR"}
    hyps = diag.get("hypotheses") or []
    mechanisms = []
    for rc in diag.get("root_causes") or []:
        for h in hyps:
            if h["hypothesis_id"] == rc:
                mechanisms.append(h["failure_mechanism"])
    mechanism = mechanisms[0] if mechanisms else None
    outcome = diag.get("outcome")
    conclusive = outcome == "CONFIRMED"
    # Flaky 特征下未 CONFIRMED → 如实弃权（不硬猜方向）
    is_flaky = any(h.get("flaky_characteristic") for h in hyps)
    if mechanism is None and outcome == "PROBABLE" and not is_flaky:
        # 无 CONFIRMED 但 outcome=PROBABLE：取首个 SUPPORTED 假设作为"方向性预测"
        # （如实标注 conclusive=False，评测时仍与 GT 对比）
        for h in hyps:
            if h.get("status") == "SUPPORTED":
                mechanism = h.get("failure_mechanism")
                break
    result = {
        "mechanism": mechanism,
        "conclusive": conclusive,
        "outcome": outcome,
        "disposition": diag.get("disposition"),
        "unresolved_conflicts": diag.get("unresolved_conflicts") or [],
    }
    if is_flaky and not conclusive:
        result["abstained_flaky"] = True
        result["note"] = "Flaky 特征：弃权优于硬猜（需多次执行差异证据）"
    return result


def _arm_A(sample: dict[str, Any], primary: dict[str, Any]) -> dict[str, Any]:
    verdict = primary.get("test_verdict")
    mech = {"PASSED": None}.get(verdict)
    return {
        "mechanism": mech,
        "conclusive": False,
        "outcome": "VERDICT_ONLY",
        "verdict": verdict,
        "note": "A 臂：只有执行 verdict，无诊断",
    }


def _run_graph_adjudication(diag: dict[str, Any], rt: Runtime, run_id: str) -> dict[str, Any]:
    """C 臂内部步骤：对已持久化诊断重跑裁决层（与首诊同口径，供消融对照）。"""
    from traceguard_api.services.diagnosis_service import _hypothesis_from_dict, _parse_epoch

    run = rt.tasks.get_run(run_id) or {}
    evidence_rows = [
        {
            "evidence_id": e.evidence_id,
            "source_type": e.source_type,
            "source_ref": str((e.content or {}).get("step_id", "") or e.source_ref),
            "captured_at": e.captured_at,
            "source_trust": getattr(e, "source_trust", "") or "",
            "integrity_check": getattr(e, "integrity_check", "") or "NOT_RUN",
            "kind": getattr(e, "kind", "") or "",
        }
        for e in rt.evidence.list(run_id=run_id, limit=500)
    ]
    meta = evidence_meta_from_rows(evidence_rows)
    adj = GraphAdjudicator(
        meta,
        run_started_at=_parse_epoch(run.get("started_at")),
        run_ended_at=_parse_epoch(run.get("ended_at")),
    )
    hyps = [_hypothesis_from_dict(h) for h in diag.get("hypotheses") or []]
    report = adj.adjudicate([h.to_dict() for h in hyps])
    result = DiagnosisEngine().diagnose(str(diag.get("symptom", "")), hyps, adjudication=report)
    return result.to_dict() | {
        "outcome": result.outcome.value,
        "disposition": result.disposition.value,
        "root_causes": list(result.root_causes),
    }


def _arm_D_ablation(diag: dict[str, Any]) -> dict[str, Any]:
    """D_graph_ablation：同一引擎、禁用图裁决（真实消融实现差异）。"""
    if not diag or diag.get("error"):
        return _diag_to_pred(diag)
    from traceguard_api.services.diagnosis_service import _hypothesis_from_dict

    result = DiagnosisEngine().diagnose(
        str(diag.get("symptom", "")),
        [_hypothesis_from_dict(h) for h in diag.get("hypotheses") or []],
        adjudication=None,  # 消融：无裁决层
    )
    return {
        "mechanism": next(
            (
                h["failure_mechanism"]
                for h in result.to_dict()["hypotheses"]
                if h["hypothesis_id"] in result.root_causes
            ),
            None,
        ),
        "conclusive": result.outcome.value == "CONFIRMED",
        "outcome": result.outcome.value,
        "disposition": result.disposition.value,
        "root_causes": list(result.root_causes),
    }


def _probe_signals_from_replays(
    env: BenchEnv, sample: dict[str, Any], primary: dict[str, Any], *, time_aligned: bool
) -> list[ProbeSignal]:
    """从真实落盘证据构造探针信号；time_aligned=False 时把事后观测当作权威（对照组）。"""
    run_id = primary["run_id"]
    evidence = {e.evidence_id: e for e in env.rt.evidence.list(run_id=run_id, limit=500)}
    signals: list[ProbeSignal] = []
    # http_state：独立状态查询（真实 TeamFlow API）
    for e in evidence.values():
        content = e.content or {}
        if e.source_type == "api" and "state_query" in str(e.source_ref):
            count = content.get("count")
            if count is None:
                count = (content.get("response") or {}).get("count")
            signals.append(
                ProbeSignal(
                    probe_id="http_state",
                    execution_id=f"probe-{run_id}",
                    status="SUCCEEDED",
                    evidence_ids=[e.evidence_id],
                    data={
                        "template": "get_state_projects",
                        "response": {"ok": True, "count": count or 0, "keys": []},
                    },
                    observed_at=time.time(),
                    run_scope=False if time_aligned else True,
                )
            )
    # network_trace：Run 内网络观测（run 作用域）
    for e in evidence.values():
        content = e.content or {}
        if e.source_type == "network" and content.get("entries"):
            signals.append(
                ProbeSignal(
                    probe_id="network_trace",
                    execution_id=f"probe-{run_id}",
                    status="SUCCEEDED",
                    evidence_ids=[e.evidence_id],
                    data={"entries": content.get("entries") or []},
                    observed_at=0.0,
                    run_scope=True,
                )
            )
    return signals


def _arm_F(
    diag: dict[str, Any], signals: list[ProbeSignal], report: dict[str, Any], run_ended_at: float
) -> dict[str, Any]:
    """F：真实回灌（时间对齐规则生效）。"""
    if not diag or diag.get("error"):
        return _diag_to_pred(diag)
    from traceguard_api.services.diagnosis_service import _hypothesis_from_dict

    merged, feedback = merge_signals(
        diag.get("hypotheses") or [], signals, report, run_ended_at=run_ended_at
    )
    result = DiagnosisEngine().diagnose(
        str(diag.get("symptom", "")), [_hypothesis_from_dict(h) for h in merged]
    )
    mechanisms = [
        h["failure_mechanism"]
        for h in result.to_dict()["hypotheses"]
        if h["hypothesis_id"] in result.root_causes
    ]
    return {
        "mechanism": mechanisms[0] if mechanisms else None,
        "conclusive": result.outcome.value == "CONFIRMED",
        "outcome": result.outcome.value,
        "disposition": result.disposition.value,
        "root_causes": list(result.root_causes),
        "unresolved_conflicts": list(result.unresolved_conflicts),
        "probe_feedback": [f.to_dict() for f in feedback],
        "probes_used": len(signals),
        "feedback_rate": (
            round(sum(1 for f in feedback if f.produced_evidence) / len(feedback), 4)
            if feedback
            else None
        ),
    }


def _arm_E_no_feedback(diag: dict[str, Any], signals: list[ProbeSignal]) -> dict[str, Any]:
    """E：探针证据只附着不回灌（对照闭环断裂）。"""
    pred = _diag_to_pred(diag)
    pred["probes_used"] = len(signals)
    pred["feedback"] = "NOT_APPLIED"
    pred["note"] = "E 臂：探针证据不参与裁决（RUN-009 E 臂语义）"
    return pred


def evaluate_samples(
    env: BenchEnv, token: str
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """主流程：执行全部场景 → 计算各臂 → 独立评测。"""
    raw: list[dict[str, Any]] = []
    for scenario in V4_SCENARIOS:
        sid = scenario.scenario_id
        plan = dict(
            INJECTION_PLAN.get(sid, {"faults": [], "testcase": scenario.suggested_testcase})
        )
        if sid == "V4-INCOMPLETE-001":
            plan["testcase"] = _make_incomplete_testcase(env.rt)
        print(f"[bench] scenario {sid} start", flush=True)
        t_scenario = time.perf_counter()
        try:
            sample = _execute_scenario(env, sid, plan)
            sample["execution_state"] = "EXECUTED"
        except Exception as exc:  # noqa: BLE001 - 执行失败如实记录
            sample = {
                "scenario": sid,
                "execution_state": "EXECUTION_ERROR",
                "error": str(exc)[:300],
            }
        print(
            f"[bench] scenario {sid} executed in {time.perf_counter() - t_scenario:.1f}s",
            flush=True,
        )
        primary = _primary_sample(sample) if sample.get("replays") else None
        if primary is None:
            # 全部回放 PASSED（如 Flaky 概率故障本轮未显现）：如实标注，不计分
            raw.append(
                {
                    **sample,
                    "arms": {},
                    "execution_state": "FLAKY_NOT_MANIFESTED"
                    if sample.get("scenario") in {"V4-FLAKY-001", "V4-LOCFLUX-001"}
                    else sample.get("execution_state", "EXECUTED"),
                }
            )
            continue
        diag = primary.get("diagnosis") or {}
        run_id = primary["run_id"]
        run = env.rt.tasks.get_run(run_id) or {}
        report = run.get("report") or {}
        run_ended_at = _parse_ts(run.get("ended_at"))

        # 真实探针计划（探针服务真实执行只读探针）
        probes_used = 0
        try:
            probe_svc = ProbeService(env.rt)
            probe_plan = probe_svc.plan_and_probe(run_id, diag["diagnosis_id"], max_iterations=1)
            probes_used = len(probe_plan.get("steps") or [])
        except Exception:  # noqa: BLE001 - 探针失败不阻断评测
            pass

        signals_aligned = _probe_signals_from_replays(env, sample, primary, time_aligned=True)
        signals_unaligned = _probe_signals_from_replays(env, sample, primary, time_aligned=False)
        arms: dict[str, dict[str, Any]] = {
            "A_raw_report": _arm_A(sample, primary),
            "C_rule": _diag_to_pred(diag),
            "D_graph_ablation": _arm_D_ablation(diag),
            "E_probe_no_feedback": _arm_E_no_feedback(diag, signals_aligned),
            "F_probe_feedback": _arm_F(diag, signals_aligned, report, run_ended_at),
            "G_probe_no_alignment": _arm_F(diag, signals_unaligned, report, 0.0),
        }
        # C 臂的裁决层重算（与首诊同口径）——用于 graph_changed 统计
        try:
            c_recomputed = _run_graph_adjudication(diag, env.rt, run_id)
            arms["C_rule"]["graph_adjudicated"] = c_recomputed
        except Exception:  # noqa: BLE001
            pass
        raw.append(
            {
                "scenario": sid,
                "family": scenario.family,
                "testcase": primary.get("testcase") or plan["testcase"],
                "run_id": run_id,
                "replays": sample["replays"],
                "test_verdict": primary.get("test_verdict"),
                "execution_state": sample.get("execution_state", "EXECUTED"),
                "probes_used_service": probes_used,
                "arms": arms,
            }
        )

    # 独立评测：评测端令牌读取 GT（被评测的臂从不接触 GT）
    gt_all = GT_MANAGER.all(token)
    arm_predictions: dict[str, list[dict[str, Any]]] = {}
    for r in raw:
        gt = gt_all.get(r["scenario"])
        r["gt_mechanism"] = gt.failure_mechanism if gt else None
        r["gt_class"] = gt.primary_failure_class if gt else None
        for name, arm in (r.get("arms") or {}).items():
            pred_mech = arm.get("mechanism")
            if r["gt_mechanism"] is None:
                arm["correct"] = None
            elif r["gt_mechanism"] == "UNKNOWN":
                # 弃权准确性：GT 未知时，弃权（无根因/INCONCLUSIVE）= 正确；
                # 硬猜一个具体机制 = 错误
                arm["correct"] = pred_mech is None or arm.get("outcome") == "INCONCLUSIVE"
                arm["abstained"] = True
            else:
                arm["correct"] = pred_mech == r["gt_mechanism"]
            arm_predictions.setdefault(name, []).append(arm)
    return raw, arm_predictions


def _parse_ts(value: Any) -> float:
    if value in (None, "", 0):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def compute_metrics(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """全部指标带 numerator/denominator/n；样本不足不宣称显著。"""
    metrics: list[dict[str, Any]] = []
    n = sum(1 for r in raw if r.get("arms"))
    metrics.append(
        {
            "metric": "n_scored_samples",
            "value": n,
            "numerator": n,
            "denominator": n,
            "n": n,
            "note": "有臂预测的计分样本数（v4 全集 14）",
        }
    )

    def _ratio(name: str, num: float, den: int, note: str) -> dict[str, Any]:
        return {
            "metric": name,
            "value": round(num / den, 4) if den else None,
            "numerator": num,
            "denominator": den,
            "n": den,
            "note": note,
        }

    for arm in [
        "A_raw_report",
        "C_rule",
        "D_graph_ablation",
        "E_probe_no_feedback",
        "F_probe_feedback",
        "G_probe_no_alignment",
    ]:
        preds = [r["arms"][arm] for r in raw if arm in r.get("arms", {})]
        den = len(preds)
        correct = sum(1 for p in preds if p.get("correct") is True)
        metrics.append(
            _ratio(
                f"primary_cause_accuracy[{arm}]",
                correct,
                den,
                "主因类别与 GT 一致的样本 / 计分样本（GT 评测端令牌判定）",
            )
        )
        conclusive = sum(1 for p in preds if p.get("conclusive"))
        metrics.append(
            _ratio(
                f"diagnosis_coverage[{arm}]",
                conclusive,
                den,
                "给出可结论（CONFIRMED）预测的样本占比",
            )
        )
        # Selective accuracy：仅在给出结论的样本上的准确率
        sel_den = conclusive
        sel_num = sum(1 for p in preds if p.get("conclusive") and p.get("correct") is True)
        metrics.append(
            _ratio(f"selective_accuracy[{arm}]", sel_num, sel_den, "结论样本中的主因正确率")
        )
        probes = [p.get("probes_used") or 0 for p in preds]
        metrics.append(_ratio(f"probes_per_sample[{arm}]", sum(probes), den, "真实探针执行数/样本"))
        fbs = [p.get("feedback_rate") for p in preds if isinstance(p.get("feedback_rate"), float)]
        metrics.append(
            {
                "metric": f"probe_feedback_rate[{arm}]",
                "value": round(statistics.mean(fbs), 4) if fbs else None,
                "numerator": round(sum(fbs), 4) if fbs else 0,
                "denominator": len(fbs),
                "n": len(fbs),
                "note": "探针证据真实进入假设更新的比例（回灌率）",
            }
        )

    # 图裁决参与度：C 臂 graph_adjudicated 与原始 diag 主因变化的样本数
    changed = sum(
        1
        for r in raw
        if (r["arms"].get("C_rule", {}).get("graph_adjudicated") or {}).get("root_causes")
        is not None
        and (r["arms"]["C_rule"].get("mechanism"))
        != next(
            (
                h["failure_mechanism"]
                for h in (
                    (r["arms"]["C_rule"].get("graph_adjudicated") or {}).get("hypotheses") or []
                )
                if h["hypothesis_id"]
                in (r["arms"]["C_rule"].get("graph_adjudicated") or {}).get("root_causes", [])
            ),
            None,
        )
    )
    metrics.append(
        _ratio(
            "graph_changed_prediction_count",
            changed,
            n,
            "图裁决层改变 C 臂主因类别的样本数；>0 表示图真实参与主因裁决",
        )
    )
    # 对齐增益：F（对齐）相对 G（不对齐）的变化
    f_g_changed = sum(
        1
        for r in raw
        if "F_probe_feedback" in r.get("arms", {})
        and "G_probe_no_alignment" in r.get("arms", {})
        and r["arms"]["F_probe_feedback"].get("mechanism")
        != r["arms"]["G_probe_no_alignment"].get("mechanism")
    )
    metrics.append(
        _ratio(
            "alignment_changed_prediction_count",
            f_g_changed,
            n,
            "时间对齐规则改变主因预测的样本数（F vs G 同探针对照）",
        )
    )
    f_correct = sum(
        1 for r in raw if r.get("arms", {}).get("F_probe_feedback", {}).get("correct") is True
    )
    g_correct = sum(
        1 for r in raw if r.get("arms", {}).get("G_probe_no_alignment", {}).get("correct") is True
    )
    metrics += [
        _ratio(
            "primary_cause_correct[F_minus_G]",
            f_correct - g_correct,
            n,
            "时间对齐带来的主因正确数变化（正=对齐有增益）",
        ),
    ]
    # 执行层假通过
    fp_den = sum(1 for r in raw if r.get("gt_mechanism") == "PERSISTENCE")
    fp = sum(
        1
        for r in raw
        if r.get("gt_mechanism") == "PERSISTENCE"
        and any(rep.get("test_verdict") == "PASSED" for rep in (r.get("replays") or []))
    )
    metrics.append(
        _ratio(
            "false_pass_rate[execution]",
            fp,
            fp_den,
            "GT 持久化故障却出现 PASSED 回放的样本/持久化故障样本",
        )
    )
    # 冲突识别
    conflicts = sum(
        1 for r in raw if (r["arms"].get("C_rule", {}) or {}).get("unresolved_conflicts")
    )
    metrics.append(
        _ratio("conflict_detection_count", conflicts, n, "诊断明确记录未解决冲突的样本数")
    )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="RUN-010 Benchmark v4 真实实验")
    parser.add_argument("--out", default="docs/assets/run010/benchmark")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--keep-env", action="store_true", help="保留临时环境目录（调试用）")
    args = parser.parse_args()

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    token = GT_MANAGER.evaluation_token
    isolation = GT_MANAGER.isolation_check(token)
    assert isolation["isolated"], isolation

    env = BenchEnv()
    all_rounds: list[dict[str, Any]] = []
    try:
        for rnd in range(args.rounds):
            t0 = time.perf_counter()
            raw, _arm_preds = evaluate_samples(env, token)
            elapsed = round(time.perf_counter() - t0, 1)
            # 原始 JSONL（逐样本一行，保留全部失败样本）
            raw_path = out / f"raw.round{rnd}.jsonl"
            raw_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in raw) + "\n",
                encoding="utf-8",
            )
            all_rounds.append({"round": rnd, "raw_path": str(raw_path), "elapsed_s": elapsed})
            print(f"round {rnd}: {len(raw)} samples in {elapsed}s → {raw_path}")
    finally:
        env_record = {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "teamflow_url": env.base_url,
            "gt_isolation": isolation,
            "arms": [
                "A_raw_report",
                "C_rule",
                "D_graph_ablation",
                "E_probe_no_feedback",
                "F_probe_feedback",
                "G_probe_no_alignment",
                "CF(counterfactual,见 run009)",
                "LLM_CONSTRAINED(BLOCKED: 未授权)",
            ],
            "injection_plan": INJECTION_PLAN,
            "rounds": all_rounds,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (out / "env.json").write_text(
            json.dumps(env_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        env.close()

    # 汇总：两轮独立指标 + 差异
    report: dict[str, Any] = {
        "dataset": "teamflow-faultbench-v4",
        "rounds": args.rounds,
        "gt_isolation": isolation,
        "per_round": [],
    }
    for rnd in range(args.rounds):
        raw_path = out / f"raw.round{rnd}.jsonl"
        raw = [
            json.loads(x) for x in raw_path.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
        report["per_round"].append({"round": rnd, "metrics": compute_metrics(raw)})
    (out / "benchmark-v4.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"report → {out / 'benchmark-v4.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
