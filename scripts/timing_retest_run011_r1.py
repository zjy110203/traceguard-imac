"""RUN-011-R1 WP2：TF-DB-004 延迟复测真实场景（OPEN-RISK-02，指令 §4.1）。

对真实 TeamFlow 注入时序故障，按指令七步执行：
1. 注入状态查询延迟（TF-DB-004 查询滞后 9s / TF-DB-005 写入慢 4s / TF-DB-001 永久回滚）；
2. Run 内即时查询（ALIGNED）；
3. 保存真实结果（count / observed_at）；
4. 延迟后再次查询（POST_HOC）；
5. 保存新的 observed_at；
6. 比较两次结果；
7. 用真实判定引擎（traceguard_evidence.probe.feedback + DiagnosisEngine）重新评估
   PERSISTENCE 与 TIMING。

四场景（预期矩阵）：
- 正常场景：数据可见 → PERSISTENCE 被排除；
- 真实延迟场景（TF-DB-004）：即时 count=0、延迟后 count>=1 → 识别 TIMING 方向，
  不确认 PERSISTENCE、不伪造 Run 当时已成功；
- 永久失败场景（TF-DB-001）：两次 count=0 → PERSISTENCE 保留候选（不因单查询确认）；
- 探针超时场景：真实 HTTP 超时 → 零证据、零假设变更。

用法：
    .venv/bin/python scripts/timing_retest_run011_r1.py \
        --teamflow http://127.0.0.1:8111 \
        --out docs/assets/run011-r1/timing/timing-retest.json

安全：只访问本地 TeamFlow 测试目标；不读取凭据、不构造子进程。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from traceguard_evidence.diagnosis.engine import (
    DiagnosisEngine,
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
)
from traceguard_evidence.probe.feedback import ProbeSignal, merge_signals

RESULT: dict[str, Any] = {"scenarios": [], "checks": []}
CHECKS: list[dict[str, Any]] = RESULT["checks"]


def _now() -> float:
    return time.time()


def check(name: str, ok: bool, detail: Any = "", scenario: str = "") -> bool:
    item = {"name": name, "scenario": scenario, "ok": bool(ok), "detail": detail, "at": _now()}
    CHECKS.append(item)
    print(
        f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" :: {str(detail)[:300]}"),
        flush=True,
    )
    return bool(ok)


class TF:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode() or "{}")

    def get(self, path: str, timeout: float = 30) -> dict[str, Any]:
        with urllib.request.urlopen(self.base + path, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode() or "{}")

    def reset(self) -> None:
        self.post("/api/test/reset", {})
        # reset 后逐个清残留（含 sticky）
        state = self.get("/api/faults").get("state") or {}
        for fid in list(state.get("active") or []):
            self.post(f"/api/faults/{fid}/clear", {})

    def activate(self, fault_id: str, params: dict | None = None) -> None:
        self.post(f"/api/faults/{fault_id}/activate", params or {})

    def clear(self, fault_id: str) -> None:
        self.post(f"/api/faults/{fault_id}/clear", {})

    def state_count(self) -> tuple[int, float]:
        """独立权威状态查询；返回 (count, observed_at)。"""
        at = _now()
        data = self.get("/api/state/projects?workspace=default")
        return int(data.get("count") or 0), at

    def create_project(self, name: str, timeout: float = 30) -> dict[str, Any]:
        return self.post(
            "/api/projects",
            {"name": name, "description": "RUN-011-R1 timing retest", "workspace_slug": "default"},
        )


def _hyp() -> dict[str, Any]:
    return {
        "hypothesis_id": "h_persist",
        "description": "业务状态未持久化",
        "failure_domain": "PRODUCT",
        "failure_mechanism": "PERSISTENCE",
        "supporting": [],
        "contradicting": [],
        "probe_feedback": [],
        "excluded_by": [],
    }


def _signal(
    exec_id: str, count: int, observed_at: float, *, run_scope: bool, status: str = "SUCCEEDED"
) -> ProbeSignal:
    return ProbeSignal(
        probe_id="http_state",
        execution_id=exec_id,
        status=status,
        evidence_ids=[f"evd_timing_{exec_id}"] if status == "SUCCEEDED" else [],
        data={"response": {"ok": True, "count": count, "keys": []}},
        observed_at=observed_at,
        run_scope=run_scope,
    )


def reevaluate(signals: list[ProbeSignal], run_ended_at: float | None) -> dict[str, Any]:
    """真实判定引擎重新评估 PERSISTENCE / TIMING。"""
    merged, results = merge_signals([_hyp()], signals, {}, run_ended_at=run_ended_at)
    engine = DiagnosisEngine()
    hyps = [
        Hypothesis(
            hypothesis_id=h["hypothesis_id"],
            description=h.get("description", ""),
            failure_domain=FailureDomain(h.get("failure_domain", "UNKNOWN")),
            failure_mechanism=FailureMechanism(h["failure_mechanism"]),
            supporting=[
                EvidenceRef(r["id"], r["trust"], bool(r.get("confirming")))
                for r in h.get("supporting") or []
            ],
            contradicting=[
                EvidenceRef(r["id"], r["trust"], False) for r in h.get("contradicting") or []
            ],
        )
        for h in merged
    ]
    diag = engine.diagnose("timing-retest", hyps)
    persist = next(h for h in merged if h["failure_mechanism"] == "PERSISTENCE")
    timing = [h for h in merged if h["failure_mechanism"] == "TIMING"]
    return {
        "signals": [
            {
                "execution_id": r.execution_id,
                "status": r.status,
                "time_alignment": r.time_alignment,
                "skipped_reason": r.skipped_reason,
                "updates": [u.to_dict() for u in r.updates],
            }
            for r in results
        ],
        "persistence_status": diag.hypotheses[0].status.value if diag.hypotheses else None,
        "persistence_supporting": persist.get("supporting"),
        "persistence_contradicting": persist.get("contradicting"),
        "persistence_excluded": bool(persist.get("excluded_by")),
        "timing_hypotheses": [
            {"description": t.get("description"), "suggested_by": t.get("suggested_by")}
            for t in timing
        ],
        "outcome": diag.outcome.value,
        "root_causes": diag.root_causes,
    }


def scenario_normal(tf: TF) -> dict[str, Any]:
    """场景1 正常：数据可见 → PERSISTENCE 被排除。"""
    tf.reset()
    tf.create_project("Timing-Normal")
    count_i, at_i = tf.state_count()  # Run 内即时查询（ALIGNED）
    run_ended = at_i + 1.0
    ev = reevaluate(
        [_signal("normal", count_i, at_i, run_scope=True)],
        run_ended,
    )
    ok = count_i >= 1 and ev["persistence_excluded"] and not ev["root_causes"]
    check(
        "S1 正常场景：数据可见 → PERSISTENCE 被排除",
        ok,
        {"count": count_i, **ev},
        scenario="normal",
    )
    return {
        "name": "normal",
        "immediate": {"count": count_i, "observed_at": at_i},
        "revaluation": ev,
    }


def scenario_real_delay(tf: TF) -> dict[str, Any]:
    """场景2 真实延迟（TF-DB-004 查询滞后 9s）：识别 TIMING 方向。"""
    tf.reset()
    tf.activate("TF-DB-004")
    tf.create_project("Timing-Delay")
    count_i, at_i = tf.state_count()  # 滞后窗口内：count=0（滞后快照）
    run_ended = at_i + 1.0
    time.sleep(9.5)  # 等滞后窗口过去
    count_d, at_d = tf.state_count()  # 延迟后复查：count=1（真实值）
    tf.clear("TF-DB-004")
    ev = reevaluate(
        [
            _signal("delay-immediate", count_i, at_i, run_scope=True),
            _signal("delay-later", count_d, at_d, run_scope=False),  # Run 后观测 → POST_HOC
        ],
        run_ended,
    )
    ok = (
        count_i == 0
        and count_d >= 1
        and bool(ev["timing_hypotheses"])
        and ev["root_causes"] == []
        and not ev["persistence_excluded"]
        and ev["persistence_status"] in {"SUPPORTED", "CANDIDATE"}
    )
    check(
        "S2 真实延迟场景：即时 count=0 + 延迟后 count>=1 → 识别 TIMING，不确认 PERSISTENCE",
        ok,
        {"immediate": count_i, "later": count_d, **ev},
        scenario="real_delay",
    )
    return {
        "name": "real_delay",
        "fault": "TF-DB-004",
        "immediate": {"count": count_i, "observed_at": at_i},
        "delayed": {"count": count_d, "observed_at": at_d, "lag_s": round(at_d - at_i, 2)},
        "revaluation": ev,
    }


def scenario_permanent_failure(tf: TF) -> dict[str, Any]:
    """场景3 永久失败（TF-DB-001）：PERSISTENCE 保留候选，不因单查询确认。"""
    tf.reset()
    tf.activate("TF-DB-001")
    tf.create_project("Timing-Permanent")
    count_i, at_i = tf.state_count()
    run_ended = at_i + 1.0
    time.sleep(2.0)
    count_d, at_d = tf.state_count()  # 延迟后再查：仍然 0（数据永不出现）
    tf.clear("TF-DB-001")
    ev = reevaluate(
        [
            _signal("perm-immediate", count_i, at_i, run_scope=True),
            _signal("perm-later", count_d, at_d, run_scope=False),
        ],
        run_ended,
    )
    ok = (
        count_i == 0
        and count_d == 0
        and ev["persistence_status"] in {"SUPPORTED", "CANDIDATE"}
        and ev["root_causes"] == []
        and not ev["timing_hypotheses"]
    )
    check(
        "S3 永久失败场景：两次 count=0 → PERSISTENCE 保留候选（单查询不确认永久故障）",
        ok,
        {"immediate": count_i, "later": count_d, **ev},
        scenario="permanent_failure",
    )
    return {
        "name": "permanent_failure",
        "fault": "TF-DB-001",
        "immediate": {"count": count_i, "observed_at": at_i},
        "delayed": {"count": count_d, "observed_at": at_d},
        "revaluation": ev,
    }


def scenario_probe_timeout(tf: TF) -> dict[str, Any]:
    """场景4 探针超时：真实 HTTP 超时 → 零证据、零假设变更。"""
    tf.reset()
    tf.activate("TF-NET-002")  # 9s 强延迟 → 客户端 1s 超时是真实超时
    at = _now()
    status = "TIMED_OUT"
    try:
        urllib.request.urlopen(tf.base + "/api/projects", timeout=1.0)  # noqa: S310
        status = "SUCCEEDED"  # 不应到达
    except TimeoutError:
        status = "TIMED_OUT"
    except urllib.error.URLError as exc:
        status = "TIMED_OUT" if "timed out" in str(exc).lower() else f"ERROR:{type(exc).__name__}"
    tf.clear("TF-NET-002")
    sig = _signal("timeout", 0, at, run_scope=False, status=status)
    ev = reevaluate([sig], at + 1.0)
    ok = (
        status == "TIMED_OUT"
        and ev["persistence_status"] in {"CANDIDATE", None}
        and not ev["persistence_supporting"]
        and not ev["persistence_contradicting"]
        and ev["root_causes"] == []
    )
    check(
        "S4 探针超时场景：TIMED_OUT 零证据、零假设变更（不冒充目标系统故障）",
        ok,
        {"probe_status": status, **ev},
        scenario="probe_timeout",
    )
    return {
        "name": "probe_timeout",
        "fault": "TF-NET-002",
        "probe_status": status,
        "observed_at": at,
        "revaluation": ev,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teamflow", default="http://127.0.0.1:8111")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tf = TF(args.teamflow)
    RESULT["teamflow"] = args.teamflow
    RESULT["started_at"] = _now()
    try:
        RESULT["scenarios"].append(scenario_normal(tf))
        RESULT["scenarios"].append(scenario_real_delay(tf))
        RESULT["scenarios"].append(scenario_permanent_failure(tf))
        RESULT["scenarios"].append(scenario_probe_timeout(tf))
    finally:
        tf.reset()
    RESULT["ended_at"] = _now()
    RESULT["summary"] = {
        "total": len(CHECKS),
        "passed": sum(1 for c in CHECKS if c["ok"]),
        "failed": [c["name"] for c in CHECKS if not c["ok"]],
    }
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[out] {p}")
    print(json.dumps(RESULT["summary"], ensure_ascii=False))
    return 0 if not RESULT["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
