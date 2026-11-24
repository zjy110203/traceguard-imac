"""RUN-007 WP6 补充：LLM 对照臂（受预算约束，真实调用，不使用规则兜底冒充）。

设计取舍（必须随结果一起读）：
- 只在**故障场景子集**上跑（默认 8 个），因为每个场景的 `diagnose?use_llm=true` 会真实调用
  DeepSeek（既有日预算约 1 USD，全量 19 场景 × 多模型会超预算）；
- 每个场景都记录**实际使用的模型**（来自诊断文档的 llm 元数据），
  以便区分 `deepseek-flash` / `deepseek-pro` / 路由升级（routed），不靠命令行自报；
- GT 只在本进程读取（`benchmark/traceguard_benchmark/scenarios.py`），不传给被测系统；
- 缺凭据 / 调用失败 → 记录 `fallback` 与错误，**不重试成"成功"**，也不把 fallback 结果算作 LLM 能力。

产出：docs/assets/run007/benchmark/llm-arm-<label>.json
"""

from __future__ import annotations

import argparse
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

from traceguard_benchmark.scenarios import SCENARIOS  # noqa: E402

TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


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
            detail = exc.read().decode()[:400]
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc

    def get(self, path: str, timeout: int = 120) -> Any:
        return self.call("GET", path, timeout=timeout)


def _execute(api: Api, testcase_id: str, timeout_s: float = 240.0) -> tuple[dict, dict]:
    t0 = time.time()
    created = api.call("POST", f"/testcases/{testcase_id}/execute", {"sync": False}, timeout=120)
    run_id = created["run_id"]
    final: dict = {}
    while time.time() - t0 < timeout_s:
        final = api.get(f"/runs/{run_id}", timeout=30)
        if final.get("execution_status") in TERMINAL:
            break
        time.sleep(0.4)
    return final, {"elapsed_s": round(time.time() - t0, 1), "run_id": run_id}


def _root_mechanism(diag: dict) -> tuple[str, str]:
    """返回 (mechanism, 依据)：优先已确认假设，其次 SUPPORTED/CANDIDATE。"""
    hyps = diag.get("hypotheses") or []
    roots = set(diag.get("root_causes") or [])
    chosen = next((h for h in hyps if h.get("hypothesis_id") in roots), None)
    for status in ("CONFIRMED", "SUPPORTED", "CANDIDATE"):
        if chosen:
            break
        chosen = next((h for h in hyps if h.get("status") == status), None)
    if not chosen:
        return "UNKNOWN", "no-hypothesis"
    return str(chosen.get("failure_mechanism") or "UNKNOWN"), str(chosen.get("status") or "")


def _llm_candidate(diag: dict) -> tuple[str, str]:
    """LLM 臂的真实预测：只取 `suggested_by` 以 llm 开头的候选假设。

    注意：LLM 假设状态被设计为 CANDIDATE，不能 CONFIRMED（安全约束），
    因此不能用"根因"字段评价 LLM 能力，必须单独取 LLM 候选。
    """
    hyps = diag.get("hypotheses") or []
    llm_hyps = [h for h in hyps if str(h.get("suggested_by") or "").startswith("llm")]
    if not llm_hyps:
        return "NONE", "no-llm-candidate"
    best = sorted(llm_hyps, key=lambda h: -(h.get("confidence") or 0))[0]
    return str(best.get("failure_mechanism") or "UNKNOWN"), str(best.get("status") or "")


def _llm_calls(api: Api) -> int | None:
    """从系统 LLM 汇总里取累计调用数（拿不到就返回 None，不猜）。"""
    try:
        s = api.get("/system/llm/summary", timeout=30)
    except Exception:  # noqa: BLE001
        return None
    for key in ("calls", "total_calls", "llm_calls"):
        v = s.get(key)
        if isinstance(v, int):
            return v
        if isinstance(v, dict) and isinstance(v.get("total"), int):
            return int(v["total"])
    stats = s.get("stats") if isinstance(s.get("stats"), dict) else None
    if stats and isinstance(stats.get("llm_calls"), int):
        return int(stats["llm_calls"])
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8020")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--label", default="flash", help="臂标签（仅用于文件名，不代表模型）")
    ap.add_argument("--limit", type=int, default=8, help="故障场景子集大小（预算约束）")
    ap.add_argument(
        "--out", default="", help="默认 docs/assets/run007/benchmark/llm-arm-<label>.json"
    )
    args = ap.parse_args(argv)

    out = ROOT / (args.out or f"docs/assets/run007/benchmark/llm-arm-{args.label}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    api = Api(args.api)

    health = api.get("/system/health")
    if (health.get("worker") or {}).get("inline_running"):
        print("WARN：API 启用了内嵌 Worker，本轮 Run 可能由 API 执行")

    faulty = [s for s in SCENARIOS if s.gt_faulty][: args.limit]
    rows: list[dict[str, Any]] = []
    started = time.time()

    for i, sc in enumerate(faulty, 1):
        row: dict[str, Any] = {
            "scenario": sc.scenario_id,
            "testcase_id": sc.testcase_id,
            "gt": sc.gt_mechanism,
        }
        try:
            # 每个场景重置被测应用数据，避免跨场景污染
            urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(f"{args.teamflow}/api/test/reset", method="POST"),
                timeout=60,
            ).read()
        except Exception as exc:  # noqa: BLE001 - 重置失败也要记录
            row["reset_error"] = str(exc)[:160]

        try:
            final, meta = _execute(api, sc.testcase_id)
            row.update({"run_id": meta["run_id"], "elapsed_s": meta["elapsed_s"]})
            row["verdict"] = final.get("test_verdict")
            row["execution_status"] = final.get("execution_status")
            row["worker"] = final.get("lease_owner") or ""

            t0 = time.time()
            calls_before = _llm_calls(api)
            diag = api.call("POST", f"/runs/{meta['run_id']}/diagnose?use_llm=true", timeout=300)
            calls_after = _llm_calls(api)
            row["diagnose_s"] = round(time.time() - t0, 1)
            row["outcome"] = diag.get("outcome")
            final_mech, basis = _root_mechanism(diag)
            llm_mech, llm_basis = _llm_candidate(diag)
            row["pred_final"] = final_mech
            row["pred_final_basis"] = basis
            row["pred"] = llm_mech  # LLM 臂以 LLM 候选为准
            row["pred_basis"] = llm_basis
            row["correct"] = llm_mech == sc.gt_mechanism
            row["final_correct"] = final_mech == sc.gt_mechanism

            advisor = diag.get("llm_advisor") or {}
            row["llm"] = {
                "engine": diag.get("engine"),
                "called": advisor.get("called"),
                "model": advisor.get("model"),
                "added": advisor.get("added"),
                "skipped": advisor.get("skipped"),
                "fallbacks": advisor.get("fallbacks"),
                "calls_delta": (
                    calls_after - calls_before
                    if calls_before is not None and calls_after is not None
                    else None
                ),
            }
        except Exception as exc:  # noqa: BLE001 - 失败样本必须保留
            row["error"] = f"{type(exc).__name__}: {exc}"[:300]
            row["correct"] = False
        rows.append(row)
        print(
            f"[{i}/{len(faulty)}] {sc.scenario_id} gt={sc.gt_mechanism} "
            f"pred={row.get('pred')} ok={row.get('correct')} "
            f"model={(row.get('llm') or {}).get('model')} "
            f"calls={(row.get('llm') or {}).get('calls_delta')}"
        )

    ok = [r for r in rows if r.get("correct")]
    final_ok = [r for r in rows if r.get("final_correct")]
    models: dict[str, int] = {}
    for r in rows:
        m = str((r.get("llm") or {}).get("model") or "unknown")
        models[m] = models.get(m, 0) + 1
    calls = [int((r.get("llm") or {}).get("calls_delta") or 0) for r in rows]
    not_called = [r["scenario"] for r in rows if not (r.get("llm") or {}).get("called")]

    report = {
        "round": "RUN-007-RESUME",
        "arm": "LLM",
        "label": args.label,
        "api": args.api,
        "teamflow": args.teamflow,
        "subset": len(faulty),
        "subset_note": "故障场景子集（预算约束），不是全量 19 个故障场景",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "duration_s": round(time.time() - started, 1),
        "llm_candidate_accuracy": {
            "num": len(ok),
            "den": len(rows),
            "value": round(len(ok) / len(rows), 4) if rows else None,
        },
        "final_diagnosis_accuracy": {
            "num": len(final_ok),
            "den": len(rows),
            "value": round(len(final_ok) / len(rows), 4) if rows else None,
        },
        "models_seen": models,
        "llm_calls": {
            "total_delta": sum(c for c in calls if c),
            "median_per_case": statistics.median(calls) if calls else 0,
        },
        "llm_not_called": not_called,
        "rows": rows,
        "limits": [
            "子集样本，不做显著性外推；不得用该数字代表全量能力",
            "模型由服务端路由决定，臂标签只用于区分批次，不代表命令行指定模型",
            "LLM 候选状态固定为 CANDIDATE（不能 CONFIRMED），因此以 LLM 候选机制计分，不以最终根因计分",
            "llm_not_called 非空时，该臂不是完整 LLM 覆盖（缺凭据/超预算），必须如实标注",
        ],
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\nLLM候选 {len(ok)}/{len(rows)} 最终诊断 {len(final_ok)}/{len(rows)} "
        f"models={models} calls={sum(c for c in calls if c)}\n-> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
