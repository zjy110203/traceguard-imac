"""RUN-017 WP2.6：30 场景 × 2 轮 规则组新口径资格实验（真实环境，单臂）。

实验口径（与指令 §4.6 对齐）：

    场景准备 → 记录健康基线 → 激活故障 → 记录 Fault Activation
    → 独立 Probe（API/状态/一致性/浏览器）→ 真实浏览器执行
    → 独立业务状态检查 → 记录 Test Verdict
    → 评测端读取 Ground Truth → 统计

硬约束：

- 独立 TeamFlow 进程 + 独立数据库 + 独立 API + 独立 Worker（API 不内嵌 Worker）；
- 每个场景执行前重置测试数据；本脚本单线程串行，不存在并发 RESET_DATA；
- 原始样本逐条 JSONL 落盘（异常中断不丢已完成样本）；
- 不把 UNKNOWN 计入已确认故障效果；
- 不使用 Run Verdict 证明 Fault Effect；Ground Truth 只在评测端读取。

安全约束：本脚本**不读取宿主机环境变量**，子进程环境全部由显式字面量白名单构造
（与 `scripts/business_loop_ui_acceptance_run012.py` 的既定约定一致）。

场景计数：当前数据集是 30 个场景（28 个故障场景 + 2 个正常对照）。
其中 23 个场景有 API/状态级独立探针（指令所指的 23×2），
另外 7 个纯 UI / 定位器 / 复合场景由 RUN-017 新增的浏览器观测覆盖（+7×2）。
脚本默认跑全部 30×2 = 60 个样本，并在统计里同时给出 23 场景子集。

用法：
    .venv/bin/python scripts/bench_v2_run017.py --out docs/assets/run017/wp2 --rounds 2
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
    "apps/api",
    "apps/teamflow",
    "apps/workers",
    "benchmark",
):
    sys.path.insert(0, str(ROOT / _p))

from traceguard_benchmark.browser_effect import BrowserEffectObserver  # noqa: E402
from traceguard_benchmark.fault_effect import (  # noqa: E402
    BENCHMARK_SCHEMA_VERSION,
    EffectStatus,
    capture_baseline,
    new_probe_token,
    observe_fault_activation,
    observe_fault_effect,
    probe_kinds_for,
)
from traceguard_benchmark.scenarios import SCENARIOS  # noqa: E402

#: 有 API/状态级独立探针的 23 个场景（指令口径）＝ 全部场景 − 7 个纯浏览器场景
BROWSER_ONLY_MECHANISMS = {
    "TF-UI-001",
    "TF-UI-002",
    "TF-UI-003",
    "TF-UI-004",
    "TF-LOC-001",
    "TF-LOC-002",
    "TF-COMP-003",
}

_PATHS = (
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
    "apps/api",
    "apps/teamflow",
    "apps/workers",
    "benchmark",
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def base_env() -> dict[str, str]:
    """子进程环境：全部是字面量/构造值，不读取宿主机环境变量。"""
    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": ":".join(str(ROOT / p) for p in _PATHS),
        "TG_ENVIRONMENT": "ci",
        "TG_AUTH_ENABLED": "false",
        "TG_LLM_PROVIDER": "",
        "TG_BROWSER_HEADLESS": "true",
    }


def http_json(
    method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 60.0
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 本地受控
            payload = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(payload or "{}")
            except json.JSONDecodeError:
                return resp.status, {"raw": payload[:400]}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(text or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": text[:400]}
    except Exception as exc:  # noqa: BLE001 - 环境不可达也要记录
        return 0, {"error": f"{type(exc).__name__}: {exc}"[:300]}


def wait_health(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def seed_catalog(db_url: str, teamflow_base: str) -> dict[str, Any]:
    """把 TeamFlow 场景资产（Intent/TestCase/Oracle）写入实验数据库。"""
    from traceguard_api.runtime import Runtime
    from traceguard_api.seed import seed_teamflow_catalog
    from traceguard_persistence import Database

    db = Database(db_url)
    db.create_schema()
    try:
        rt = Runtime(db)
        return seed_teamflow_catalog(rt, base_url=teamflow_base)
    finally:
        db.dispose()


class Env:
    """实验环境：独立 TeamFlow / API / Worker / 数据库 / Artifact 目录。"""

    def __init__(self, workdir: Path, db_url: str, workers: int = 2) -> None:
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.logs = self.workdir / "logs"
        self.logs.mkdir(exist_ok=True)
        self.artifacts = self.workdir / "artifacts"
        self.artifacts.mkdir(exist_ok=True)
        self.teamflow_port = free_port()
        self.api_port = free_port()
        self.teamflow_db = workdir / "teamflow.db"
        self.db_url = db_url

        env = base_env()
        env["TG_TEAMFLOW_DB"] = f"sqlite:///{self.teamflow_db}"
        self.teamflow_proc = self._spawn(
            "teamflow",
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
            env,
        )
        self.teamflow_base = f"http://127.0.0.1:{self.teamflow_port}"
        if not wait_health(self.teamflow_base + "/health", 60):
            raise RuntimeError("TeamFlow 未启动")

        self.api_base = f"http://127.0.0.1:{self.api_port}"
        api_env = base_env()
        api_env.update(
            {
                "TG_DATABASE_URL": db_url,
                "TG_ARTIFACT_DIR": str(self.artifacts),
                "TG_INLINE_WORKER": "false",
                "TG_TEAMFLOW_BASE_URL": self.teamflow_base,
            }
        )
        self.api_proc = self._spawn(
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "traceguard_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.api_port),
            ],
            api_env,
        )
        if not wait_health(self.api_base + "/health", 90):
            raise RuntimeError("API 未启动")

        self.workers: list[subprocess.Popen[bytes]] = []
        for idx in range(workers):
            worker_env = base_env()
            worker_env.update(
                {
                    "TG_DATABASE_URL": db_url,
                    "TG_ARTIFACT_DIR": str(self.artifacts),
                    "TG_TEAMFLOW_BASE_URL": self.teamflow_base,
                    "TG_WORKER_ID": f"bench-w{idx}",
                    "TG_WORKER_LEASE_TTL_S": "90",
                }
            )
            self.workers.append(
                self._spawn(
                    f"worker{idx}",
                    [sys.executable, "-m", "traceguard_worker.main", "--poll", "0.2"],
                    worker_env,
                )
            )
        time.sleep(1.0)

    def _spawn(self, name: str, cmd: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
        out = (self.logs / f"{name}.log").open("wb")
        return subprocess.Popen(  # noqa: S603 - 受控本地子进程，显式白名单环境
            cmd, cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT
        )

    def close(self) -> None:
        procs = [*self.workers, self.api_proc, self.teamflow_proc]
        for proc in procs:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        for proc in procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


# ------------------------------------------------------------------ 评测数据
def load_ground_truth() -> dict[str, dict[str, Any]]:
    """评测端私有标签（只在统计阶段读取，绝不进入探针/诊断）。"""
    import traceguard_teamflow.main  # noqa: F401 - 导入即写入 GT 种子（评测侧进程内）
    from traceguard_teamflow.ground_truth import GROUND_TRUTH

    out: dict[str, dict[str, Any]] = {}
    for scenario_id in GROUND_TRUTH.all_public_refs():
        record = GROUND_TRUTH.get(scenario_id)
        if record is None:
            continue
        out[scenario_id] = {
            "fault_mechanism_id": record.fault_mechanism_id,
            "expected_failure_class": record.expected_failure_class,
            "expected_business_state": record.expected_business_state,
            "notes": record.notes,
        }
    return out


def scenario_faults(scenario: Any) -> list[str]:
    if scenario.fault_mechanism in {"", "NONE"}:
        return []
    return [scenario.fault_mechanism]


# ------------------------------------------------------------------ 单个样本
def reset_environment(env: Env) -> dict[str, Any]:
    """串行重置：清数据 + 清全部故障（sticky 也必须显式清）。"""
    status, faults = http_json("GET", env.teamflow_base + "/api/faults")
    available = (faults.get("available") or []) if status == 200 else []
    for fault in available:
        http_json("POST", f"{env.teamflow_base}/api/faults/{fault}/clear", {})
    r_status, reset = http_json("POST", env.teamflow_base + "/api/test/reset", {})
    return {"status": r_status, "reset": reset}


def activate(env: Env, faults: list[str]) -> None:
    for fault in faults:
        http_json("POST", f"{env.teamflow_base}/api/faults/{fault}/activate", {"sticky": True})


def wait_run(env: Env, run_id: str, timeout_s: float = 240.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        status, payload = http_json("GET", f"{env.api_base}/api/v1/runs/{run_id}", timeout=15)
        if status == 200:
            last = payload
            if str(payload.get("execution_status")) in {
                "COMPLETED",
                "FAILED",
                "TIMED_OUT",
                "CANCELLED",
            }:
                return payload
        time.sleep(0.5)
    return last


def combine_effects(
    api_effect: dict[str, Any] | None,
    browser: dict[str, Any] | None,
    faults: list[str] | None = None,
) -> dict[str, Any]:
    """合并 API 探针与浏览器观测：任一 CONFIRMED 即确认；全部 NOT_TRIGGERED 才否定。"""
    if faults is not None and not faults:
        # 正常对照：没有注入故障，"故障效果"不适用（不是 UNKNOWN，也不是 NOT_TRIGGERED）
        return {
            "fault_effect_status": "NOT_APPLICABLE",
            "fault_effect_source": "POSITIVE_CONTROL_NO_FAULT",
            "fault_effect_evidence_refs": [],
            "fault_effect_classification": "NO_FAULT_INJECTED",
            "fault_effect_note": "正向对照场景：未注入故障，不计入故障效果分母",
        }
    parts = [p for p in (api_effect, browser) if p]
    if not parts:
        return {
            "fault_effect_status": EffectStatus.UNKNOWN.value,
            "fault_effect_source": "NO_OBSERVER",
            "fault_effect_evidence_refs": [],
            "fault_effect_classification": "NO_OBSERVER",
            "fault_effect_note": "没有可用的独立观测器",
        }
    statuses = [p.get("fault_effect_status") for p in parts]
    if EffectStatus.CONFIRMED.value in statuses:
        status = EffectStatus.CONFIRMED.value
    elif all(s == EffectStatus.NOT_TRIGGERED.value for s in statuses):
        status = EffectStatus.NOT_TRIGGERED.value
    else:
        status = EffectStatus.UNKNOWN.value
    refs: list[str] = []
    for part in parts:
        refs.extend(part.get("fault_effect_evidence_refs") or [])
    primary = next(
        (p for p in parts if p.get("fault_effect_status") == EffectStatus.CONFIRMED.value),
        parts[0],
    )
    return {
        "fault_effect_status": status,
        "fault_effect_source": " + ".join(str(p.get("fault_effect_source")) for p in parts),
        "fault_effect_evidence_refs": refs,
        "fault_effect_classification": primary.get("fault_effect_classification"),
        "fault_effect_note": "; ".join(
            str(p.get("fault_effect_note") or "") for p in parts if p.get("fault_effect_note")
        )[:400],
        "fault_effect_parts": parts,
    }


def run_sample(
    env: Env,
    observer: BrowserEffectObserver,
    scenario: Any,
    round_id: int,
    gt: dict[str, Any] | None,
) -> dict[str, Any]:
    faults = scenario_faults(scenario)
    kinds = probe_kinds_for(faults)
    token = new_probe_token()
    started = time.perf_counter()

    reset_info = reset_environment(env)
    baseline = capture_baseline(env.teamflow_base, faults, probe_token=token) if faults else None
    activate(env, faults)
    activation = observe_fault_activation(env.teamflow_base, faults)

    business_before = http_json("GET", env.teamflow_base + "/api/state/projects?workspace=default")[
        1
    ]

    api_effect = None
    browser_effect = None
    if faults and [k for k in kinds if k != "browser"]:
        api_effect = observe_fault_effect(env.teamflow_base, faults, baseline=baseline, timeout=8.0)
    if faults and "browser" in kinds:
        browser_effect = observer.observe(faults)
    effect = combine_effects(api_effect, browser_effect, faults)

    # ---- 真实执行（独立 Worker + 真实 Chromium + 真实 TeamFlow） ----
    task_status, task = http_json(
        "POST",
        env.api_base + "/api/v1/tasks",
        {
            "testcase_ref": scenario.testcase_id,
            "idempotency_key": (
                f"bench-v2-{scenario.scenario_id}-r{round_id}-{uuid.uuid4().hex[:8]}"
            ),
        },
        timeout=30,
    )
    run: dict[str, Any] = {}
    run_started = time.perf_counter()
    if task_status in (200, 201):
        r_status, created = http_json(
            "POST", f"{env.api_base}/api/v1/tasks/{task['task_id']}/runs", timeout=30
        )
        if r_status in (200, 201):
            run = wait_run(env, created["run_id"])
    run_ms = round((time.perf_counter() - run_started) * 1000, 1)

    diagnosis: dict[str, Any] = {}
    if run.get("run_id") and run.get("execution_status") in {"COMPLETED", "FAILED", "TIMED_OUT"}:
        d_status, d_payload = http_json(
            "POST", f"{env.api_base}/api/v1/runs/{run['run_id']}/diagnose", timeout=120
        )
        if d_status in (200, 201):
            diagnosis = d_payload

    business_after = http_json("GET", env.teamflow_base + "/api/state/projects?workspace=default")[
        1
    ]

    reset_environment(env)
    cleanup = [
        http_json(
            "POST",
            env.teamflow_base + "/api/test/probe-cleanup",
            {"token": token, "workspace": "tg-benchmark-probe"},
        )[1],
        http_json(
            "POST",
            env.teamflow_base + "/api/test/probe-cleanup",
            {"token": token, "workspace": "default", "allow_default": True},
        )[1],
    ]

    diag_primary = None
    diag_outcome = None
    if diagnosis:
        diag_outcome = diagnosis.get("outcome")
        hyps = {h.get("hypothesis_id"): h for h in diagnosis.get("hypotheses") or []}
        roots = diagnosis.get("root_causes") or []
        if roots and roots[0] in hyps:
            diag_primary = hyps[roots[0]].get("failure_mechanism")

    return {
        "scenario_id": scenario.scenario_id,
        "round_id": round_id,
        "testcase_id": scenario.testcase_id,
        "faults": faults,
        "fault_mechanism_gt": scenario.fault_mechanism,
        "expected_test_verdict": scenario.expected_test_verdict or scenario.expected_verdict,
        "diagnosis_eligible": scenario.diagnosis_eligible,
        "fault_activation_status": activation.get("fault_activation_status"),
        "fault_activation_source": activation.get("fault_activation_source"),
        "fault_activation_active": activation.get("fault_activation_active"),
        "fault_effect_status": effect.get("fault_effect_status"),
        "fault_effect_source": effect.get("fault_effect_source"),
        "fault_effect_evidence_refs": effect.get("fault_effect_evidence_refs"),
        "fault_effect_classification": effect.get("fault_effect_classification"),
        "fault_effect_note": effect.get("fault_effect_note"),
        "fault_effect_requires_environment_reset": any(
            bool((p or {}).get("fault_effect_requires_environment_reset"))
            for p in (api_effect, browser_effect)
        ),
        "run_execution_status": run.get("execution_status"),
        "run_verdict_observed": run.get("test_verdict"),
        "run_id": run.get("run_id"),
        "run_duration_ms": run_ms,
        "diagnosis_primary_cause": diag_primary,
        "diagnosis_outcome": diag_outcome,
        "diagnosis_id": diagnosis.get("diagnosis_id"),
        "ground_truth": gt,
        "business_state_before": {
            "count": business_before.get("count"),
            "http_status": business_before.get("http_status"),
        },
        "business_state_after": {
            "count": business_after.get("count"),
            "http_status": business_after.get("http_status"),
        },
        "baseline_captured": bool(baseline),
        "probe_token": token,
        "reset": reset_info,
        "cleanup_ok": all(bool(c.get("ok")) for c in cleanup if c),
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "probe_cost_ms": round((time.perf_counter() - started) * 1000, 1),
    }


# --------------------------------------------------------------------- 统计
def wilson_ci(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total <= 0:
        return None
    phat = successes / total
    denom = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * ((phat * (1 - phat) / total + z * z / (4 * total * total)) ** 0.5)
    return [round((centre - margin) / denom, 4), round((centre + margin) / denom, 4)]


def compute_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(samples)
    unknown = sum(1 for s in samples if s["fault_effect_status"] == "UNKNOWN")
    confirmed = sum(1 for s in samples if s["fault_effect_status"] == "CONFIRMED")
    not_triggered = sum(1 for s in samples if s["fault_effect_status"] == "NOT_TRIGGERED")
    resolved = confirmed + not_triggered
    faulty_samples = [s for s in samples if s["faults"]]
    controls = [s for s in samples if not s["faults"]]
    activation_active = sum(1 for s in faulty_samples if s["fault_activation_status"] == "ACTIVE")

    false_pass = [
        s
        for s in faulty_samples
        if s["run_verdict_observed"] == "PASSED" and s["fault_effect_status"] == "CONFIRMED"
    ]
    false_negative = [
        s
        for s in faulty_samples
        if s["fault_effect_status"] == "NOT_TRIGGERED"
        and s["run_verdict_observed"] in {"FAILED", "NOT_EVALUATED"}
        and bool(s["diagnosis_eligible"])
    ]

    diag_samples = [
        s for s in faulty_samples if s["diagnosis_primary_cause"] and s.get("ground_truth")
    ]
    correct = [
        s
        for s in diag_samples
        if s["diagnosis_primary_cause"] == s["ground_truth"]["fault_mechanism_id"]
    ]

    labels = sorted(
        {s["ground_truth"]["fault_mechanism_id"] for s in diag_samples if s["ground_truth"]}
    )
    f1s: list[float] = []
    per_label: dict[str, Any] = {}
    for label in labels:
        tp = sum(
            1
            for s in diag_samples
            if s["diagnosis_primary_cause"] == label
            and s["ground_truth"]["fault_mechanism_id"] == label
        )
        fp = sum(
            1
            for s in diag_samples
            if s["diagnosis_primary_cause"] == label
            and s["ground_truth"]["fault_mechanism_id"] != label
        )
        fn = sum(
            1
            for s in diag_samples
            if s["diagnosis_primary_cause"] != label
            and s["ground_truth"]["fault_mechanism_id"] == label
        )
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1s.append(f1)
        per_label[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    probe_ms = [s["probe_cost_ms"] for s in samples]
    return {
        "samples_total": total,
        "faulty_samples": len(faulty_samples),
        "positive_controls": len(controls),
        "unknown_samples": unknown,
        "unknown_ratio": round(unknown / total, 4) if total else None,
        "confirmed_samples": confirmed,
        "not_triggered_samples": not_triggered,
        "resolved_samples": resolved,
        "fault_activation_coverage": (
            round(activation_active / len(faulty_samples), 4) if faulty_samples else None
        ),
        "fault_effect_confirmation_rate": round(confirmed / resolved, 4) if resolved else None,
        "fault_effect_confirmation_rate_ci95": wilson_ci(confirmed, resolved),
        "false_pass": len(false_pass),
        "false_pass_rate_over_faulty": (
            round(len(false_pass) / len(faulty_samples), 4) if faulty_samples else None
        ),
        "false_pass_samples": [f"{s['scenario_id']}#r{s['round_id']}" for s in false_pass],
        "false_negative": len(false_negative),
        "false_negative_samples": [f"{s['scenario_id']}#r{s['round_id']}" for s in false_negative],
        "diagnosed_samples": len(diag_samples),
        "primary_cause_accuracy": (
            round(len(correct) / len(diag_samples), 4) if diag_samples else None
        ),
        "primary_cause_accuracy_ci95": wilson_ci(len(correct), len(diag_samples)),
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "per_label": per_label,
        "probe_cost_ms_total": round(sum(probe_ms), 1),
        "probe_cost_ms_median": (
            round(sorted(probe_ms)[len(probe_ms) // 2], 1) if probe_ms else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/assets/run017/wp2")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--db-url", default="")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--workdir", default="")
    parser.add_argument("--keep-env", action="store_true")
    args = parser.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir) if args.workdir else out_dir / "run-env"
    db_url = args.db_url or f"sqlite:///{workdir / 'platform.db'}"

    selected = set(args.scenario)
    scenarios = [s for s in SCENARIOS if not selected or s.scenario_id in selected]
    ground_truth = load_ground_truth()

    samples_path = out_dir / "samples.jsonl"
    if samples_path.exists():
        stamp = time.strftime("%Y%m%dT%H%M%S")
        samples_path.rename(out_dir / f"samples-{stamp}.jsonl")

    print(f"[bench] scenarios={len(scenarios)} rounds={args.rounds} db={db_url}", flush=True)
    env = Env(workdir, db_url, workers=args.workers)
    seeded = seed_catalog(db_url, env.teamflow_base)
    print(f"[bench] seeded={seeded}", flush=True)
    observer = BrowserEffectObserver(
        env.teamflow_base,
        out_dir=out_dir / "browser-evidence",
        headless=True,
        settle_timeout_ms=6000,
    )
    samples: list[dict[str, Any]] = []
    started = time.time()
    try:
        for round_id in range(1, args.rounds + 1):
            for scenario in scenarios:
                sample = run_sample(
                    env, observer, scenario, round_id, ground_truth.get(scenario.scenario_id)
                )
                samples.append(sample)
                with samples_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
                print(
                    f"[r{round_id}] {scenario.scenario_id:<14} "
                    f"act={sample['fault_activation_status']:<11} "
                    f"effect={sample['fault_effect_status']:<13} "
                    f"({sample['fault_effect_classification']}) "
                    f"run={sample['run_execution_status']}/{sample['run_verdict_observed']} "
                    f"diag={sample['diagnosis_primary_cause']}",
                    flush=True,
                )
    finally:
        if not args.keep_env:
            env.close()

    metrics = compute_metrics(samples)
    metrics["subset_23_scenarios"] = compute_metrics(
        [s for s in samples if s["fault_mechanism_gt"] not in BROWSER_ONLY_MECHANISMS]
    )
    metrics["subset_browser_7_scenarios"] = compute_metrics(
        [s for s in samples if s["fault_mechanism_gt"] in BROWSER_ONLY_MECHANISMS]
    )
    report = {
        "run": "RUN-017 WP2",
        "mode": "rule_group_qualification_single_arm",
        "rounds": args.rounds,
        "scenario_count": len(scenarios),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "duration_s": round(time.time() - started, 1),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "db_url": db_url,
            "teamflow": env.teamflow_base,
            "api": env.api_base,
            "workers": args.workers,
            "api_inline_worker": False,
            "browser": "chromium (playwright)",
        },
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "metrics": metrics,
        "samples_file": str(samples_path),
        "limitations": [
            "单臂（规则组）资格实验：不含 LLM 臂与人工基线，不与历史 v1 数据混算",
            "UNKNOWN 不计入已确认故障效果；未达显著性的结论不外推",
            "TF-TIME-003（双提交竞态）没有独立探针，样本保持 UNKNOWN",
        ],
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
