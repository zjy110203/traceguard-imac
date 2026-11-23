"""RUN-007-FINALIZATION-2：UI-FaultBench v3 多臂实验（A / C / D / E，30 场景真实执行）。

本轮相对 RUN-007-RESUME 的修正：

WP1-A（自愈闭环可信）
- 修复基线版本**动态读取**（不再硬编码 base_version=1）；只接受最高 APPROVED 版本，
  且校验"该版本确实是预期故障基线"（定位器/断言/Oracle 绑定/工作区/摘要），
  不符合预期就停止该场景并报告，绝不在错误版本上继续修复；
- LOC-002 的步骤索引修正为 3（0-based，第 0 步是故障注入 api 步骤）；
  修复后的定位器使用可访问名称（textbox:项目名称），不是按钮角色；
- Promotion fail-closed：status/promoted_version/promotion_record/failure_reason/
  数据库正式版本指针全部一致才允许回归，否则只记录、不回归、不回滚；
- 回归执行固定 `?version=promoted_version`，并校验 run.testcase_version_id；
- 回滚做内容级验证（指针 + 步骤/定位器 + 断言 + Oracle 绑定），不只是"接口返回成功"；
- 每个场景在 finally 中清理 TeamFlow 故障。

WP3（评测口径纠偏）
- 执行状态与测试 Verdict 分开统计（expected_execution_status / expected_test_verdict /
  diagnosis_eligible / expected_failure_mechanism）；
- Macro-F1 使用浮点分子（各类别 F1 之和）与真实类别数，并输出各类别 F1；
- E 臂禁止静默回退：探针/图/重诊断失败时如实记录异常并标记 INCONCLUSIVE/NOT_RUN，
  单独统计探针执行成功率与探针后有效诊断率；
- 独立重复实验：每次运行写入独立样本文件（不覆盖），逐条记录 dataset/run/version/
  diagnosis/probe/evidence/执行状态/最终 verdict/真实耗时，失败样本全部保留。

Ground Truth 只在评测进程内读取（`benchmark/traceguard_benchmark/scenarios.py`），
不通过任何 API 暴露给被评测系统。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

from traceguard_benchmark.evaluation import DiagnosisEvaluator, SelfHealingEvaluator  # noqa: E402
from traceguard_benchmark.scenarios import (  # noqa: E402
    DATASET_ID,
    DATASET_VERSION,
    SCENARIOS,
    Scenario,
    dataset_statistics,
)

TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}
FLAKY_REPEATS = 5


# ------------------------------------------------------------------ HTTP
class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/") + "/api/v1"

    def call(self, method: str, path: str, body: dict | None = None, timeout: int = 300) -> Any:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:300]}") from exc

    def get(self, path: str, timeout: int = 120) -> Any:
        return self.call("GET", path, timeout=timeout)


def reset_teamflow(teamflow: str) -> bool:
    """清理被测应用全部故障与数据；任何场景异常后都必须调用。"""
    try:
        urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(
                f"{teamflow.rstrip('/')}/api/test/reset",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            ),
            timeout=15,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ 执行
def execute(
    api: Api, testcase_id: str, *, version: int | None = None, timeout_s: float = 240.0
) -> tuple[dict, dict]:
    """同步接口入队 + 轮询真实 Worker 执行到终态。

    `version` 非空时使用 `POST /testcases/{id}/execute?version=N`：
    必须显式固定版本执行，不能依赖 "latest approved" 的隐式选择。
    """
    t0 = time.time()
    path = f"/testcases/{testcase_id}/execute"
    if version is not None:
        path += f"?version={int(version)}"
    created = api.call("POST", path, {"sync": False}, timeout=120)
    run_id = created["run_id"]
    requested_version = int(created.get("version") or 0)
    final: dict = {}
    while time.time() - t0 < timeout_s:
        final = api.get(f"/runs/{run_id}", timeout=30)
        if final.get("execution_status") in TERMINAL:
            break
        time.sleep(0.4)
    meta = {
        "elapsed_s": round(time.time() - t0, 1),
        "run_id": run_id,
        "requested_version": requested_version,
        "version_fixed_on_request": version is not None,
        "request_path": path,
    }
    return final, meta


# ------------------------------------------------------- 基线解析（WP1-A/A2）
class BaselineError(RuntimeError):
    """正式版本不是预期的故障基线时，必须停止该场景而不是继续修复。"""


def _digest(doc: dict[str, Any]) -> str:
    canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def latest_approved_version(api: Api, testcase_id: str) -> dict[str, Any] | None:
    versions = api.get(f"/testcases/{testcase_id}/versions", timeout=60) or []
    approved = [v for v in versions if v.get("review_status") == "APPROVED"]
    if not approved:
        return None
    approved.sort(key=lambda v: int(v.get("version") or 0))
    return approved[-1]


def approved_version_number(api: Api, testcase_id: str) -> int:
    rec = latest_approved_version(api, testcase_id)
    return int((rec or {}).get("version") or 0)


def resolve_baseline(
    api: Api,
    sc: Scenario,
    *,
    step_index: int,
    expected_locator: dict[str, str],
) -> dict[str, Any]:
    """读取当前最高 APPROVED 版本，并验证它确实是预期故障基线。"""
    meta = api.get(f"/testcases/{sc.testcase_id}", timeout=60) or {}
    rec = latest_approved_version(api, sc.testcase_id)
    if rec is None:
        raise BaselineError(f"NO_APPROVED_VERSION: {sc.testcase_id}")
    doc = dict(rec.get("doc") or {})
    steps = doc.get("steps") or []
    if step_index >= len(steps):
        raise BaselineError(f"STEP_INDEX_OUT_OF_RANGE: steps={len(steps)} index={step_index}")
    step = steps[step_index]
    target = dict(step.get("target") or {})
    if (
        str(target.get("strategy") or "") != expected_locator["strategy"]
        or str(target.get("value") or "") != expected_locator["value"]
    ):
        raise BaselineError(
            "BASELINE_NOT_FAULTY: "
            f"step[{step_index}] target={target} 期望={expected_locator}；"
            "当前正式版本不是该场景的故障基线（可能已被修复或 Seed 未恢复）"
        )
    assertions = doc.get("assertions") or []
    if not assertions:
        raise BaselineError("BASELINE_WITHOUT_ASSERTIONS")
    unbound = [a.get("assertion_id") for a in assertions if not a.get("oracle_id")]
    if unbound:
        raise BaselineError(f"BASELINE_ORACLE_BINDING_MISSING: {unbound}")

    oracles: list[dict[str, Any]] = []
    for aid_oracle in sorted({str(a["oracle_id"]) for a in assertions}):
        try:
            oracles.append(api.get(f"/oracles/{aid_oracle}", timeout=60))
        except Exception as exc:  # noqa: BLE001
            raise BaselineError(f"ORACLE_NOT_FOUND: {aid_oracle} ({exc})") from exc

    return {
        "testcase_id": sc.testcase_id,
        "base_version": int(rec["version"]),
        "testcase_version_id": f"{sc.testcase_id}@{int(rec['version'])}",
        "workspace_id": str(doc.get("workspace_id") or meta.get("workspace_id") or "default"),
        "step_index": step_index,
        "original_locator": target,
        "assertions": [
            {
                "assertion_id": a.get("assertion_id"),
                "kind": a.get("kind"),
                "expected": a.get("expected"),
                "oracle_id": a.get("oracle_id"),
            }
            for a in assertions
        ],
        "oracle_bindings": doc.get("oracle_bindings") or [],
        "oracles": [
            {"oracle_id": o.get("oracle_id"), "version": o.get("version")} for o in oracles
        ],
        "version_sha256": _digest(doc),
    }


def verify_promotion(
    api: Api,
    response: dict[str, Any],
    *,
    proposal_id: str,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Promotion fail-closed：五项全部满足才算真实晋升。"""
    pv = response.get("promoted_version")
    valid_int = isinstance(pv, int) and not isinstance(pv, bool) and pv > 0
    record = dict(response.get("promotion_record") or {})
    current = approved_version_number(api, baseline["testcase_id"])
    checks = {
        "status_promoted": response.get("status") == "PROMOTED",
        "promoted_version_valid_int": valid_int,
        "record_target_version_matches": bool(valid_int)
        and int(record.get("target_version") or 0) == int(pv),
        "failure_reason_empty": not (response.get("failure_reason") or ""),
        "db_latest_equals_promoted": bool(valid_int) and current == int(pv),
    }
    promoted_doc: dict[str, Any] = {}
    if bool(valid_int):
        promoted_doc = (
            api.get(f"/testcases/{baseline['testcase_id']}/versions/{int(pv)}", timeout=60) or {}
        )
        step = (promoted_doc.get("steps") or [])[baseline["step_index"]]
        checks["promoted_content_is_candidate"] = (
            dict(step.get("target") or {}).get("value") != baseline["original_locator"]["value"]
        )
        checks["promoted_assertions_preserved"] = [
            (a.get("assertion_id"), a.get("oracle_id"))
            for a in promoted_doc.get("assertions") or []
        ] == [(a["assertion_id"], a["oracle_id"]) for a in baseline["assertions"]]
    else:
        checks["promoted_content_is_candidate"] = False
        checks["promoted_assertions_preserved"] = False
    return {
        "proposal_id": proposal_id,
        "base_version": baseline["base_version"],
        "promoted_version": pv if valid_int else None,
        "current_approved_version": current,
        "checks": checks,
        "all_ok": all(checks.values()),
        "full_response": response,
    }


def verify_rollback(
    api: Api,
    response: dict[str, Any],
    *,
    baseline: dict[str, Any],
    promoted_version: int,
) -> dict[str, Any]:
    """回滚必须证明"正式版本内容确实恢复"，而不是只看接口返回成功。"""
    record = dict(response.get("rollback_record") or {})
    restored = record.get("restored_version")
    valid_int = isinstance(restored, int) and not isinstance(restored, bool) and restored > 0
    current = approved_version_number(api, baseline["testcase_id"])
    restored_doc: dict[str, Any] = {}
    checks = {
        "status_rolled_back": response.get("status") == "ROLLED_BACK",
        "rollback_record_present": bool(record),
        "restored_version_valid_int": valid_int,
        "db_latest_equals_restored": bool(valid_int) and current == int(restored),
        "promoted_version_still_readable": bool(
            api.get(f"/testcases/{baseline['testcase_id']}/versions/{promoted_version}", timeout=60)
        ),
    }
    if bool(valid_int):
        restored_doc = (
            api.get(f"/testcases/{baseline['testcase_id']}/versions/{int(restored)}", timeout=60)
            or {}
        )
        step = (restored_doc.get("steps") or [])[baseline["step_index"]]
        checks["content_restored"] = dict(step.get("target") or {}) == baseline["original_locator"]
        checks["assertions_restored"] = [
            (a.get("assertion_id"), a.get("kind"), json.dumps(a.get("expected"), sort_keys=True))
            for a in restored_doc.get("assertions") or []
        ] == [
            (
                a["assertion_id"],
                a["kind"],
                json.dumps(a["expected"], sort_keys=True),
            )
            for a in baseline["assertions"]
        ]
        checks["oracle_bindings_restored"] = (restored_doc.get("oracle_bindings") or []) == (
            baseline["oracle_bindings"] or []
        )
    else:
        checks["content_restored"] = False
        checks["assertions_restored"] = False
        checks["oracle_bindings_restored"] = False
    return {
        "restored_version": restored if valid_int else None,
        "current_approved_version": current,
        "checks": checks,
        "all_ok": all(checks.values()),
        "full_response": response,
    }


# ------------------------------------------------------- 预测（与 v2 runner 同口径）
def baseline_raw_report(obs: dict) -> dict[str, Any]:
    """传统自动化基线：只看 UI 断言，不看业务状态/网络证据。"""
    report = obs.get("report") or {}
    kinds = obs.get("assertion_kinds") or {}
    failed = [
        a
        for a in report.get("assertion_outcomes") or []
        if a.get("status") == "FAILED" and kinds.get(a.get("assertion_id"), "ui") == "ui"
    ]
    if not failed:
        return {"mechanism": "NONE", "conclusive": True, "verdict": "PASSED"}
    return {"mechanism": "UI_RENDERING", "conclusive": True, "verdict": "FAILED"}


def diag_to_pred(diag: dict | None) -> dict[str, Any]:
    if not diag:
        return {"mechanism": "UNKNOWN", "conclusive": False, "outcome": "NONE"}
    hyps = diag.get("hypotheses") or []
    conclusive = diag.get("outcome") in {"CONFIRMED", "PROBABLE"}
    chosen = next((h for h in hyps if h.get("status") == "CONFIRMED"), None)
    if chosen is None:
        chosen = next(
            (h for h in hyps if h.get("status") in {"SUPPORTED", "CANDIDATE"}),
            hyps[0] if hyps else None,
        )
    return {
        "mechanism": (chosen or {}).get("failure_mechanism", "UNKNOWN"),
        "conclusive": conclusive,
        "outcome": diag.get("outcome") if diag else "NONE",
        "diagnosis_id": (diag or {}).get("diagnosis_id"),
    }


def graph_enhanced(diag: dict | None, graph: dict | None) -> dict[str, Any]:
    pred = diag_to_pred(diag)
    if not diag or not graph:
        return pred
    sources = {
        (n.get("properties") or {}).get("source_type")
        for n in graph.get("nodes", [])
        if n.get("node_type") == "Evidence"
    }
    if {"browser", "api"} <= sources and pred.get("outcome") == "PROBABLE":
        pred["outcome"] = "CONFIRMED"
        pred["conclusive"] = True
        pred["graph_enhanced"] = True
    pred["graph_sources"] = sorted(s for s in sources if s)
    return pred


def probe_arm_prediction(obs: dict) -> dict[str, Any]:
    """E 臂：只有探针真实成功且探针后诊断真实拿到结论时才计分。

    禁止把 C 臂诊断 / D 臂图复制过来充数；无有效结果时显式标记 NOT_RUN / INCONCLUSIVE。
    """
    state = str(obs.get("probe_state") or "NOT_RUN")
    if state != "OK":
        return {"mechanism": "UNKNOWN", "conclusive": False, "outcome": state}
    if obs.get("probe_error") or obs.get("diag_after_probe") is None:
        return {"mechanism": "UNKNOWN", "conclusive": False, "outcome": "INCONCLUSIVE"}
    pred = graph_enhanced(obs.get("diag_after_probe"), obs.get("graph_after_probe"))
    if pred.get("outcome") in {"NONE", "NOT_RUN"}:
        return {"mechanism": "UNKNOWN", "conclusive": False, "outcome": "INCONCLUSIVE"}
    return pred


def _metric(name: str, num: float, den: float, note: str = "") -> dict[str, Any]:
    from traceguard_benchmark.evaluation import safe_ratio

    return {
        "metric": name,
        "value": round(safe_ratio(num, den), 4),
        "numerator": num,
        "denominator": den,
        "note": note,
    }


# ------------------------------------------------------------- 自愈场景（WP1-A）
LOCATOR_CASES: list[dict[str, Any]] = [
    {
        "scenario": "TF-LOC-001",
        "testcase_id": "tcase_bench_loc001",
        "step_index": 4,  # 0=故障注入 api，1=navigate，2=click 新建，3=fill 名称，4=click 提交
        "expected_locator": {"strategy": "test_id", "value": "create-submit"},
        "healed_locator": {
            "strategy": "role",
            "value": "button:创建",
            "source": "healed",
        },
    },
    {
        "scenario": "TF-LOC-002",
        "testcase_id": "tcase_bench_loc002",
        "step_index": 3,  # 0=故障注入 api，1=navigate，2=click 新建，3=fill 项目名称
        "expected_locator": {"strategy": "test_id", "value": "project-name"},
        # 输入框的可访问名称来自 <label for="name">项目名称</label>；
        # 必须用 textbox 角色而不是 button，也不是 test_id 的机械替换。
        "healed_locator": {
            "strategy": "role",
            "value": "textbox:项目名称",
            "source": "healed",
        },
    },
]


def run_repair_case(api: Api, teamflow: str, case: dict[str, Any]) -> dict[str, Any]:
    """单个定位器场景的真实自愈闭环；任何异常都不跳过 TeamFlow 清理。"""
    rec: dict[str, Any] = {
        "scenario": case["scenario"],
        "testcase_id": case["testcase_id"],
        "step_index": case["step_index"],
        "outcome": "NOT_RUN",
    }
    proposal_id = ""
    promoted_version = 0
    try:
        baseline = resolve_baseline(
            api,
            next(s for s in SCENARIOS if s.scenario_id == case["scenario"]),
            step_index=case["step_index"],
            expected_locator=case["expected_locator"],
        )
        rec["baseline"] = baseline
        proposal = api.call(
            "POST",
            "/repairs",
            {
                "testcase_id": case["testcase_id"],
                "base_version": baseline["base_version"],
                "changes": [
                    {
                        "path": "steps[].target",
                        "step_index": case["step_index"],
                        "value": case["healed_locator"],
                        "reason": (
                            f"{case['scenario']}: 用可访问名称替代失效 test_id"
                            f"（原 {case['expected_locator']['value']}）"
                        ),
                    }
                ],
                "reason": "RUN-007-FINALIZATION-2 自愈臂（真实故障驱动）",
            },
            timeout=120,
        )
        proposal_id = proposal["proposal_id"]
        rec["proposal_id"] = proposal_id
        rec["base_version"] = baseline["base_version"]
        validated = api.call("POST", f"/repairs/{proposal_id}/validate", timeout=120)
        rec["validation"] = validated.get("status")
        rec["assertion_preservation"] = validated.get("assertion_preservation")
        rec["oracle_integrity"] = validated.get("oracle_integrity")
        if validated.get("status") != "VALIDATED":
            rec["outcome"] = "VALIDATION_FAILED"
            rec["failure_reason"] = validated.get("failure_reason")
            return rec

        sandbox = api.call("POST", f"/repairs/{proposal_id}/sandbox-run", timeout=600)
        rec["sandbox_status"] = sandbox.get("sandbox_status")
        rec["sandbox_verdict"] = sandbox.get("sandbox_verdict")
        rec["sandbox_worker"] = sandbox.get("sandbox_executed_by")
        if sandbox.get("sandbox_status") != "PASSED":
            rec["outcome"] = "SANDBOX_FAILED"
            rec["failure_reason"] = sandbox.get("failure_reason")
            return rec

        api.call("POST", f"/repairs/{proposal_id}/submit", timeout=60)
        api.call(
            "POST",
            f"/repairs/{proposal_id}/approve",
            {"reviewer": "role:qa-lead", "note": "RUN-007-FINALIZATION-2"},
            timeout=60,
        )
        promoted = api.call(
            "POST",
            f"/repairs/{proposal_id}/promote",
            {"idempotency_key": f"run007f2-{proposal_id}"},
            timeout=180,
        )
        checks = verify_promotion(api, promoted, proposal_id=proposal_id, baseline=baseline)
        rec["promotion"] = checks
        if not checks["all_ok"]:
            # fail-closed：不做回归、不做回滚，原样记录可复核证据
            rec["outcome"] = "PROMOTION_NOT_CONFIRMED"
            rec["failure_reason"] = promoted.get("failure_reason") or "PROMOTION_CHECKS_FAILED"
            return rec
        promoted_version = int(checks["promoted_version"])

        reg_run, reg_meta = execute(api, case["testcase_id"], version=promoted_version)
        expected_ref = f"{case['testcase_id']}@{promoted_version}"
        rec["regression"] = {
            "request": reg_meta,
            "run_id": reg_run.get("run_id"),
            "testcase_version_id": reg_run.get("testcase_version_id"),
            "expected_testcase_version_id": expected_ref,
            "version_binding_ok": reg_run.get("testcase_version_id") == expected_ref,
            "execution_status": reg_run.get("execution_status"),
            "test_verdict": reg_run.get("test_verdict"),
            "elapsed_s": reg_meta["elapsed_s"],
            "worker": reg_run.get("lease_owner"),
        }
        if reg_run.get("testcase_version_id") != expected_ref:
            rec["outcome"] = "REGRESSION_WRONG_VERSION"
            return rec
        rec["outcome"] = (
            "APPROVED_AND_REGRESSION_PASSED"
            if reg_run.get("test_verdict") == "PASSED"
            else "REGRESSION_FAILED"
        )
    except BaselineError as exc:
        rec["outcome"] = "BASELINE_MISMATCH"
        rec["failure_reason"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        rec["outcome"] = "ERROR"
        rec["failure_reason"] = str(exc)[:400]
    finally:
        if proposal_id and promoted_version:
            try:
                rolled = api.call(
                    "POST",
                    f"/repairs/{proposal_id}/rollback",
                    {
                        "operator": "role:qa-lead",
                        "reason": "RUN-007-FINALIZATION-2 自愈场景收尾回滚",
                    },
                    timeout=180,
                )
                rec["rollback"] = verify_rollback(
                    api, rolled, baseline=rec["baseline"], promoted_version=promoted_version
                )
            except Exception as exc:  # noqa: BLE001
                rec["rollback"] = {"error": str(exc)[:300], "all_ok": False}
        # 故障清理必须无条件执行（中途异常也不能跳过）
        rec["teamflow_reset"] = reset_teamflow(teamflow)
    return rec


# ------------------------------------------------------------------ 单轮实验
def run_pass(
    api: Api,
    args: argparse.Namespace,
    scenarios: list[Scenario],
    *,
    repeat: int,
    samples_path: Path,
) -> dict[str, Any]:
    """一轮独立实验：执行 → 计分样本 → A/C/D/E 诊断 → 指标。"""
    observations: list[dict[str, Any]] = []
    flaky_runs: dict[str, list[dict[str, Any]]] = {}
    started = time.time()
    samples_path.parent.mkdir(parents=True, exist_ok=True)

    with samples_path.open("w", encoding="utf-8") as sink:
        for i, sc in enumerate(scenarios, 1):
            repeats = FLAKY_REPEATS if sc.scenario_id.startswith("TF-FLAKY") else 1
            for rep in range(repeats):
                try:
                    run, meta = execute(api, sc.testcase_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"[r{repeat} {i}/{len(scenarios)}] {sc.scenario_id} 执行失败：{exc}")
                    observations.append(
                        {
                            "repeat_pass": repeat,
                            "scenario": sc.scenario_id,
                            "testcase_id": sc.testcase_id,
                            "gt_mechanism": sc.gt_mechanism,
                            "gt_faulty": sc.gt_faulty,
                            "error": str(exc)[:200],
                            "repeat": rep,
                        }
                    )
                    continue
                ver = (
                    api.get(
                        f"/testcases/{sc.testcase_id}/versions/{meta['requested_version']}",
                        timeout=30,
                    )
                    or {}
                )
                kinds = {
                    a["assertion_id"]: a.get("kind", "ui") for a in ver.get("assertions") or []
                }
                exec_status = run.get("execution_status")
                verdict = run.get("test_verdict")
                obs = {
                    "repeat_pass": repeat,
                    "dataset_version": DATASET_VERSION,
                    "scenario": sc.scenario_id,
                    "testcase_id": sc.testcase_id,
                    "gt_mechanism": sc.gt_mechanism,
                    "gt_faulty": sc.gt_faulty,
                    "expected_execution_status": sc.expected_execution_status,
                    "expected_test_verdict": sc.expected_test_verdict,
                    "expected_failure_mechanism": sc.expected_failure_mechanism,
                    "diagnosis_eligible": sc.diagnosis_eligible,
                    "run_id": run.get("run_id"),
                    "testcase_version_id": run.get("testcase_version_id"),
                    "execution_status": exec_status,
                    "test_verdict": verdict,
                    "execution_status_matches": exec_status == sc.expected_execution_status,
                    "test_verdict_matches": verdict == sc.expected_test_verdict,
                    "worker": run.get("lease_owner"),
                    "fencing_token": run.get("fencing_token"),
                    "elapsed_s": meta["elapsed_s"],
                    "assertion_kinds": kinds,
                    "report": run.get("report"),
                    "repeat": rep,
                }
                sink.write(json.dumps(obs, ensure_ascii=False) + "\n")
                sink.flush()
                observations.append(obs)
                if sc.scenario_id.startswith("TF-FLAKY"):
                    flaky_runs.setdefault(sc.scenario_id, []).append(
                        {"run_id": obs["run_id"], "verdict": verdict}
                    )
                print(
                    f"[r{repeat} {i}/{len(scenarios)}] {sc.scenario_id} rep{rep} "
                    f"exec={exec_status} verdict={verdict} ({meta['elapsed_s']}s)"
                )

    scoring: list[dict[str, Any]] = []
    for sc in scenarios:
        pool = [o for o in observations if o.get("scenario") == sc.scenario_id]
        if not pool:
            continue
        expected = [
            o
            for o in pool
            if o.get("execution_status") == sc.expected_execution_status
            and o.get("test_verdict") == sc.expected_test_verdict
        ]
        if expected:
            scoring.append(expected[0])
            continue
        # Flaky/竞态场景允许多次尝试，优先保留真实失败样本，失败样本不删除
        failed = [
            o
            for o in pool
            if o.get("test_verdict") in {"FAILED", "NOT_EVALUATED"}
            or o.get("execution_status") in {"FAILED", "TIMED_OUT"}
        ]
        scoring.append((failed or pool)[0])

    failures = [
        o
        for o in scoring
        if o.get("diagnosis_eligible")
        and (
            o.get("test_verdict") in {"FAILED", "NOT_EVALUATED"}
            or o.get("execution_status") in {"FAILED", "TIMED_OUT", "CANCELLED"}
        )
        and "error" not in o
    ]
    print(f"\n[r{repeat}] 诊断计分样本 {len(failures)} 个，开始 A/C/D/E 对照…")

    for obs in failures:
        run_id = obs["run_id"]
        t0 = time.time()
        try:
            obs["diag_rule"] = api.call(
                "POST", f"/runs/{run_id}/diagnose?use_llm=false", timeout=300
            )
            obs["diag_error"] = None
        except Exception as exc:  # noqa: BLE001
            obs["diag_rule"] = None
            obs["diag_error"] = str(exc)[:200]
        obs["time_to_diagnosis_s"] = round(time.time() - t0, 2)
        try:
            obs["graph"] = api.call("POST", f"/runs/{run_id}/graph", {}, timeout=180)
            obs["graph_error"] = None
        except Exception as exc:  # noqa: BLE001
            obs["graph"] = None
            obs["graph_error"] = str(exc)[:200]

        obs["probe_error"] = None
        obs["probe_plan"] = None
        try:
            obs["probe_plan"] = api.call(
                "POST", f"/runs/{run_id}/probe-plan?max_iterations=3", {}, timeout=300
            )
        except Exception as exc:  # noqa: BLE001
            obs["probe_error"] = str(exc)[:200]
        probes = [
            s
            for s in ((obs["probe_plan"] or {}).get("steps") or [])
            if s.get("status") == "SUCCEEDED"
        ]
        obs["probe_count"] = len(probes)
        if obs["probe_error"]:
            obs["probe_state"] = "NOT_RUN"
        elif probes:
            obs["probe_state"] = "OK"
        else:
            obs["probe_state"] = "INCONCLUSIVE"

        # E 臂：探针之后必须**真实重新诊断**；失败就如实记录，绝不复制 C 臂结论。
        obs["diag_after_probe"] = None
        obs["diag_after_probe_error"] = None
        obs["graph_after_probe"] = None
        obs["graph_after_probe_error"] = None
        if obs["probe_state"] == "OK":
            try:
                obs["diag_after_probe"] = api.call(
                    "POST", f"/runs/{run_id}/diagnose?use_llm=false", timeout=300
                )
            except Exception as exc:  # noqa: BLE001
                obs["diag_after_probe_error"] = str(exc)[:200]
            try:
                obs["graph_after_probe"] = api.call(
                    "POST", f"/runs/{run_id}/graph", {}, timeout=180
                )
            except Exception as exc:  # noqa: BLE001
                obs["graph_after_probe_error"] = str(exc)[:200]
        try:
            obs["evidence"] = api.get(f"/runs/{run_id}/evidence", timeout=120)
        except Exception:  # noqa: BLE001
            obs["evidence"] = []
        obs["evidence_count"] = len(obs["evidence"] or [])
        obs["evidence_refs"] = [e.get("evidence_id") for e in obs["evidence"] or []]
        obs["probe_plan_id"] = (obs["probe_plan"] or {}).get("plan_id")
        obs["diagnosis_id"] = (obs["diag_rule"] or {}).get("diagnosis_id")

    gt = {o["scenario"]: o["expected_failure_mechanism"] for o in failures}
    evaluator = DiagnosisEvaluator(gt)
    arms: dict[str, list[dict[str, Any]]] = {
        "A_raw_report": [],
        "C_rule": [],
        "D_graph": [],
        "E_probe": [],
    }
    for obs in failures:
        arms["A_raw_report"].append(baseline_raw_report(obs))
        arms["C_rule"].append(diag_to_pred(obs.get("diag_rule")))
        arms["D_graph"].append(graph_enhanced(obs.get("diag_rule"), obs.get("graph")))
        arms["E_probe"].append(probe_arm_prediction(obs))

    metrics: list[dict[str, Any]] = []
    for arm, preds in arms.items():
        correct = sum(
            1
            for p, o in zip(preds, failures, strict=False)
            if p.get("mechanism") == o["expected_failure_mechanism"]
        )
        conclusive = sum(1 for p in preds if p.get("conclusive"))
        conclusive_correct = sum(
            1
            for p, o in zip(preds, failures, strict=False)
            if p.get("conclusive") and p.get("mechanism") == o["expected_failure_mechanism"]
        )
        fp_den = sum(1 for o in failures if o["gt_mechanism"] == "PERSISTENCE")
        fp = sum(
            1
            for p, o in zip(preds, failures, strict=False)
            if o["gt_mechanism"] == "PERSISTENCE" and p.get("verdict") == "PASSED"
        )
        metrics += [
            _metric(
                f"primary_cause_accuracy[{arm}]",
                correct,
                len(failures),
                "主因类别命中/诊断计分样本数",
            ),
            _metric(
                f"diagnosis_coverage[{arm}]",
                conclusive,
                len(failures),
                "给出确定结论的样本/诊断计分样本数",
            ),
            _metric(
                f"selective_accuracy[{arm}]",
                conclusive_correct,
                conclusive,
                "有结论样本里主因正确/有结论样本数（须与 coverage 同看）",
            ),
            _metric(
                f"false_pass_rate[{arm}]",
                fp,
                fp_den,
                "仅 A 臂直接产出 verdict；其余臂的共享假通过率见 false_pass_rate[execution]",
            ),
        ]
        preds_map = {
            o["scenario"]: p.get("mechanism", "UNKNOWN")
            for o, p in zip(failures, preds, strict=False)
        }
        m = evaluator.macro_f1(preds_map).to_dict()
        m["metric"] = f"macro_f1[{arm}]"
        metrics.append(m)

    # 假通过率是执行层事实（不依赖任何诊断臂）：GT 为持久化故障却判 PASSED 的比例
    fp_den_all = sum(
        1 for o in scoring if o.get("gt_mechanism") == "PERSISTENCE" and "error" not in o
    )
    fp_all = sum(
        1
        for o in scoring
        if o.get("gt_mechanism") == "PERSISTENCE" and o.get("test_verdict") == "PASSED"
    )
    metrics.append(
        _metric(
            "false_pass_rate[execution]",
            fp_all,
            fp_den_all,
            "执行层假通过：GT 为持久化故障却判 PASSED 的样本/持久化故障样本数",
        )
    )

    probe_ok = sum(1 for o in failures if o.get("probe_state") == "OK")
    diag_valid = sum(
        1
        for o in failures
        if o.get("probe_state") == "OK" and o.get("diag_after_probe") is not None
    )
    metrics += [
        _metric(
            "probe_execution_success_rate",
            probe_ok,
            len(failures),
            "至少执行成功一个只读探针的样本/诊断计分样本数（E 臂前提）",
        ),
        _metric(
            "probe_post_diagnosis_valid_rate",
            diag_valid,
            len(failures),
            "探针后真实拿到诊断结论的样本/诊断计分样本数",
        ),
        _metric(
            "probe_not_run_rate",
            len(failures) - probe_ok,
            len(failures),
            "探针未执行成功（NOT_RUN/INCONCLUSIVE）的样本占比，禁止静默回退",
        ),
    ]

    ttd = [o["time_to_diagnosis_s"] for o in failures if o.get("time_to_diagnosis_s")]
    metrics += [
        {
            "metric": "time_to_diagnosis_s",
            "value": round(statistics.mean(ttd), 2) if ttd else None,
            "median": round(statistics.median(ttd), 2) if ttd else None,
            "numerator": len(ttd),
            "denominator": len(failures),
            "note": "从调用诊断接口到返回结论的秒数（不含执行时间）",
        },
        {
            "metric": "probe_count_per_failure",
            "value": round(
                sum(o.get("probe_count") or 0 for o in failures) / max(1, len(failures)), 2
            ),
            "numerator": sum(o.get("probe_count") or 0 for o in failures),
            "denominator": len(failures),
            "note": "E 臂在每个样本上真实执行成功的只读探针数",
        },
        {
            "metric": "evidence_coverage_per_failure",
            "value": round(
                sum(o.get("evidence_count") or 0 for o in failures) / max(1, len(failures)), 2
            ),
            "numerator": sum(o.get("evidence_count") or 0 for o in failures),
            "denominator": len(failures),
            "note": "每个失败样本关联的真实 Evidence 数（平均）",
        },
    ]

    arm_predictions = {
        arm: [
            {
                "scenario": o["scenario"],
                "gt": o["expected_failure_mechanism"],
                "pred": p.get("mechanism"),
                "conclusive": p.get("conclusive"),
                "outcome": p.get("outcome"),
            }
            for o, p in zip(failures, preds, strict=False)
        ]
        for arm, preds in arms.items()
    }

    # 诊断/探针/证据信息产生于执行之后，必须回写到样本文件（原始文件不删不改口径，
    # 只补齐同一次实验的关联 id），否则"D4 逐条记录"无法复核。
    with samples_path.open("w", encoding="utf-8") as sink:
        for o in observations:
            sink.write(json.dumps(o, ensure_ascii=False, default=str) + "\n")

    return {
        "repeat_pass": repeat,
        "duration_s": round(time.time() - started, 1),
        "runs_executed": len([o for o in observations if o.get("run_id")]),
        "failure_samples": len(failures),
        "metrics": metrics,
        "arm_predictions": arm_predictions,
        "flaky_repeats": flaky_runs,
        "observations": observations,
        "scoring": scoring,
        "failures": failures,
        "probe_states": {
            state: sum(1 for o in failures if o.get("probe_state") == state)
            for state in {"OK", "INCONCLUSIVE", "NOT_RUN"}
        },
    }


# ------------------------------------------------------------------ main
def _unique_path(path: Path) -> Path:
    """原始数据不得覆盖：同名文件已存在时追加时间戳。"""
    if not path.exists():
        return path
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return path.with_name(f"{path.stem}-{stamp}{path.suffix}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8020")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--out", default="docs/assets/run007/benchmark/benchmark-v3-f2.json")
    ap.add_argument("--samples", default="docs/assets/run007/benchmark/samples-f2.jsonl")
    ap.add_argument("--only-faulty", action="store_true", help="只跑故障场景（调试用）")
    ap.add_argument("--only-repair", action="store_true", help="只跑自愈闭环（调试用）")
    ap.add_argument("--skip-repair", action="store_true")
    ap.add_argument("--repeats", type=int, default=2, help="独立重复实验轮数（>=2 才构成重复实验）")
    args = ap.parse_args(argv)

    api = Api(args.api)
    api.call("POST", "/system/seed", timeout=600)
    health = api.get("/system/health")
    if (health.get("worker") or {}).get("inline_running"):
        print("WARN：API 启用了内嵌 Worker，本轮 Run 可能由 API 执行")
    if not ((health.get("database") or {}).get("ok")):
        print("数据库不可用，终止")
        return 2

    scenarios = [s for s in SCENARIOS if (s.gt_faulty or not args.only_faulty)]
    started = time.time()
    passes: list[dict[str, Any]] = []
    samples_files: list[str] = []

    for r in range(0 if args.only_repair else max(1, args.repeats)):
        base = ROOT / args.samples
        path = base if r == 0 else base.with_name(f"{base.stem}.repeat{r}{base.suffix}")
        path = _unique_path(path)
        samples_files.append(str(path.relative_to(ROOT)))
        result = run_pass(api, args, scenarios, repeat=r, samples_path=path)
        passes.append(
            {
                "repeat_pass": r,
                "samples_file": samples_files[-1],
                "duration_s": result["duration_s"],
                "runs_executed": result["runs_executed"],
                "failure_samples": result["failure_samples"],
                "metrics": result["metrics"],
                "arm_predictions": result["arm_predictions"],
                "flaky_repeats": result["flaky_repeats"],
                "probe_states": result["probe_states"],
            }
        )
        # 每轮结束都清理被测应用故障，避免跨轮污染
        reset_teamflow(args.teamflow)

    # 自愈闭环只在第一轮跑（真实浏览器 + 沙箱 + 晋升 + 回归 + 回滚，耗时最长）
    repair_records: list[dict[str, Any]] = []
    repair_metrics: list[dict[str, Any]] = []
    if not args.skip_repair:
        for case in LOCATOR_CASES:
            repair_records.append(run_repair_case(api, args.teamflow, case))
        pres_done = {
            r["scenario"]: r.get("assertion_preservation") == "PASS"
            for r in repair_records
            if r.get("validation")
        }
        if pres_done:
            repair_metrics.append(SelfHealingEvaluator.assertion_preservation(pres_done).to_dict())
        acc = {
            r["scenario"]: str(r.get("outcome") or "")
            for r in repair_records
            if r.get("validation")
        }
        if acc:
            repair_metrics.append(SelfHealingEvaluator.acceptance_rate(acc).to_dict())
        reg_done = {
            r["scenario"]: (r.get("regression") or {}).get("test_verdict")
            for r in repair_records
            if (r.get("regression") or {}).get("test_verdict")
        }
        if reg_done:
            repair_metrics.append(
                _metric(
                    "repair_regression_pass_rate",
                    sum(1 for v in reg_done.values() if v == "PASSED"),
                    len(reg_done),
                    "晋升后按固定版本回归执行 PASSED 的比例（样本量小，不作外推）",
                )
            )

    verdict_dist: dict[str, int] = {}
    mismatch: list[dict[str, Any]] = []
    for r in range(len(passes)):
        base = ROOT / samples_files[r]
        if not base.exists():
            continue
        for line in base.read_text(encoding="utf-8").splitlines():
            o = json.loads(line)
            if o.get("error"):
                continue
            key = (
                f"{o['scenario']}:exec={o.get('execution_status')}/verdict={o.get('test_verdict')}"
            )
            verdict_dist[key] = verdict_dist.get(key, 0) + 1
            if o.get("scenario", "").startswith("TF-FLAKY"):
                continue  # 概率场景不做单次严格对齐
            if not o.get("test_verdict_matches") or not o.get("execution_status_matches"):
                mismatch.append(
                    {
                        "repeat_pass": r,
                        "scenario": o["scenario"],
                        "expected_execution_status": o.get("expected_execution_status"),
                        "expected_test_verdict": o.get("expected_test_verdict"),
                        "got_execution_status": o.get("execution_status"),
                        "got_test_verdict": o.get("test_verdict"),
                    }
                )

    if not passes:  # --only-repair：只有自愈记录，不伪造实验指标
        primary = {
            "failure_samples": 0,
            "metrics": [],
            "arm_predictions": {},
            "flaky_repeats": {},
        }
    else:
        primary = passes[0]
    report = {
        "round": "RUN-007-FINALIZATION-2",
        "dataset": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "dataset_statistics": dataset_statistics(),
        "scenarios": len(scenarios),
        "repeats": len(passes),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "duration_s": round(time.time() - started, 1),
        "api": args.api,
        "execution_mode": "independent-worker-queue",
        "api_inline_worker": bool((health.get("worker") or {}).get("inline_running")),
        "failure_samples": primary["failure_samples"],
        "gt_isolated": True,
        "metrics": primary["metrics"],
        "passes": passes,
        "repeat_note": (
            "每轮是独立实验：独立执行、独立诊断、独立样本文件；原始数据不覆盖；"
            "失败样本全部保留，不取平均、不删样本"
        ),
        "repair_metrics": repair_metrics,
        "repair_records": repair_records,
        "arm_predictions": primary["arm_predictions"],
        "flaky_repeats": primary["flaky_repeats"],
        "flaky_note": f"Flaky 场景重复 {FLAKY_REPEATS} 次，保留每次真实 verdict，不取平均、不删样本",
        "verdict_mismatch": mismatch,
        "verdict_distribution": verdict_dist,
        "blocked": [
            {
                "name": "LLM 诊断对照（DeepSeek Flash/Pro/Routed）",
                "status": "BLOCKED",
                "reason": "本轮预算用于真实执行/重复实验/自愈闭环；LLM 组未执行，不以规则兜底冒充 LLM 结论",
            },
            {
                "name": "人工修复基线",
                "status": "BLOCKED",
                "reason": "无真实人类工程师参与，不产生人工基线数值",
            },
        ],
        "limitations": [
            "单机 SQLite + 单浏览器实例；并发与资源竞争未做生产级压测",
            "Flaky 场景 5 次重复，其余场景每轮 1 次计分样本，置信区间宽，不做强外推",
            "诊断耗时只覆盖规则/图/探针路径，不含 LLM 顾问",
        ],
        "samples_files": samples_files,
    }
    out = _unique_path(ROOT / args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n{len(passes)} 轮 × {len(scenarios)} 场景；失败样本 {primary['failure_samples']}")
    for m in primary["metrics"]:
        if m["metric"].startswith(("primary_cause", "macro_f1", "false_pass", "probe_")):
            print(f"  {m['metric']:<40} {m['value']} ({m['numerator']}/{m['denominator']})")
    print(f"证据：{out}  用时 {report['duration_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
