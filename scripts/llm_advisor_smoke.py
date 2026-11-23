"""真实 LLM 冒烟：三类扩展顾问各调用一次（有界预算，不读 Ground Truth）。

用法：
    TG_API_URL=http://127.0.0.1:8010 .venv/bin/python scripts/llm_advisor_smoke.py

行为：
- 从真实 API 取一个失败 Run 的报告与用例版本，构造真实结构的输入；
- 依次调用 LLMCompilerAdvisor / LLMProbePlannerAdvisor / LLMRepairAdvisor；
- 输出模型、延迟、结果摘要与 fallback 标记；未配置凭据时明确 BLOCKED。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

from traceguard_agents.gateway.llm_gateway import build_llm_stack
from traceguard_agents.gateway.llm_planners import (
    LLMCompilerAdvisor,
    LLMProbePlannerAdvisor,
    LLMRepairAdvisor,
)
from traceguard_core.config import get_settings

BASE = os.environ.get("TG_API_URL", "http://127.0.0.1:8010")


def _get(path: str):  # type: ignore[no-untyped-def]
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    settings = get_settings()
    stack = build_llm_stack(settings, root_dir=".")
    if not stack:
        print(json.dumps({"status": "BLOCKED", "reason": "LLM 未配置"}, ensure_ascii=False))
        return 3
    tracker = stack["tracker"]
    if tracker.should_skip_llm():
        print(json.dumps({"status": "BLOCKED", "reason": "预算耗尽"}, ensure_ascii=False))
        return 3

    runs = _get("/api/v1/runs?limit=50")
    failed = next((r for r in runs if r["test_verdict"] in {"FAILED", "INCONCLUSIVE"}), None)
    if not failed:
        print(json.dumps({"status": "BLOCKED", "reason": "没有失败 Run 可用于冒烟"}))
        return 3
    run = _get(f"/api/v1/runs/{failed['run_id']}")
    report = run.get("report") or {}
    tc_id = (report.get("testcase_id") or "").strip()
    version_doc = {}
    if tc_id:
        try:
            version_doc = _get(f"/api/v1/testcases/{tc_id}")["latest"]
        except Exception:  # noqa: BLE001
            version_doc = {}

    steps = version_doc.get("steps") or []
    action_sequence = []
    for i, s in enumerate(steps):
        params = {}
        target = s.get("target") or {}
        if target.get("value"):
            params[target.get("strategy", "test_id")] = target["value"]
        if s.get("value"):
            params["value"] = s["value"]
        action_sequence.append({"action": s.get("action"), "params": params, "index": i})
    trace = {
        "exploration_id": "smoke-derived-from-run",
        "action_sequence": action_sequence,
        "business_state_api_available": next(
            (
                a["expected"].get("api")
                for a in version_doc.get("assertions", [])
                if isinstance(a.get("expected"), dict) and a["expected"].get("api")
            ),
            None,
        ),
        "elements": [],
    }

    out: dict = {
        "run_id": run["run_id"],
        "model": stack["router"].default_model,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    compiler = LLMCompilerAdvisor(
        provider=stack["provider"],
        router=stack["router"],
        prompts=stack["prompts"],
        tracker=tracker,
        run_id=run["run_id"],
    )
    advice = compiler.suggest(trace, "根据失败用例的步骤结构重新编译候选")
    out["compiler"] = {
        "called": advice.called,
        "fallback": advice.fallback,
        "error": advice.error,
        "steps": len(advice.steps),
        "assertions": len(advice.assertions),
        "dropped_steps": advice.dropped_steps,
        "warnings": advice.warnings[:5],
    }

    gaps = [
        {
            "gap_id": "business_state_unknown",
            "probe_id": "http_state",
            "why": "业务状态证据不足，需要独立查询确认是否持久化",
            "info_gain": 0.8,
        },
        {
            "gap_id": "network_unverified",
            "probe_id": "network_trace",
            "why": "核对网络请求状态码与失败原因",
            "info_gain": 0.55,
        },
    ]

    class _Def:
        def __init__(self, pid: str, templates: set[str], cost: float) -> None:
            self.probe_id = pid
            self.capability = "api_read"
            self.risk_level = "LOW"
            self.allowed_templates = templates
            self.estimated_cost = cost

    probe = LLMProbePlannerAdvisor(
        provider=stack["provider"],
        router=stack["router"],
        prompts=stack["prompts"],
        tracker=tracker,
        run_id=run["run_id"],
    )
    probe_advice = probe.suggest(
        gaps,
        {
            "http_state": _Def("http_state", {"get_state_projects", "get_state_project"}, 1.0),
            "network_trace": _Def("network_trace", {"run_network"}, 0.2),
        },
    )
    out["probe_planner"] = {
        "called": probe_advice.called,
        "fallback": probe_advice.fallback,
        "error": probe_advice.error,
        "dropped": probe_advice.dropped,
        "selections": probe_advice.selections,
    }

    symptom = " | ".join(str(e) for e in (report.get("errors") or [])[:2]) or "run failed"
    repair = LLMRepairAdvisor(
        provider=stack["provider"],
        router=stack["router"],
        prompts=stack["prompts"],
        tracker=tracker,
        run_id=run["run_id"],
    )
    repair_advice = repair.suggest(
        symptom=symptom,
        steps=steps,
        assertions=version_doc.get("assertions") or [],
        step_count=len(steps) or 1,
    )
    out["repair_planner"] = {
        "called": repair_advice.called,
        "fallback": repair_advice.fallback,
        "error": repair_advice.error,
        "changes": repair_advice.changes,
        "warnings": repair_advice.warnings[:5],
    }
    out["usage"] = tracker.summary()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    outcome_dir = os.path.join(".workbuddy-run")
    os.makedirs(outcome_dir, exist_ok=True)
    with open(os.path.join(outcome_dir, "llm-advisor-smoke.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
