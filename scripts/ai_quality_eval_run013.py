"""RUN-013 WP5：AI 质量可量化评估（真实 Provider，低样本）。

6 个诊断场景使用**同一份输入**分别评估：
- 规则组：按失败证据类型映射失败域（确定性基线，不调模型）；
- LLM 组：真实 DeepSeek `LLMDiagnosisAdvisor`（Schema/防虚构/去重均走生产代码）。

Ground Truth 只在本脚本内用于打分，不进入模型上下文。
样本量小，报告必须标注统计限制，不得外推为总体准确率。

用法：
    .venv/bin/python scripts/ai_quality_eval_run013.py
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from traceguard_agents.gateway.llm_gateway import (  # noqa: E402
    LLMDiagnosisAdvisor,
    build_llm_stack,
)
from traceguard_core.config import get_settings  # noqa: E402

CASES: list[dict[str, Any]] = [
    {
        "id": "timing",
        "symptom": "创建项目后成功提示 3 秒后才出现，断言在 2 秒超时判定失败",
        "failed_steps": [
            {
                "step_id": "s5",
                "action": "assert",
                "error": "Timeout 2000ms exceeded waiting for banner",
            }
        ],
        "evidence": [
            {"evidence_id": "ev_timing_1", "kind": "trace", "summary": "banner rendered at 3012ms"},
            {
                "evidence_id": "ev_timing_2",
                "kind": "screenshot",
                "summary": "success banner visible",
            },
        ],
        "gt_domain": "TIMING",
    },
    {
        "id": "locator",
        "symptom": "点击提交按钮时提示 locator not found，页面结构已改版",
        "failed_steps": [
            {"step_id": "s4", "action": "click", "error": "locator #create-submit not found"}
        ],
        "evidence": [
            {"evidence_id": "ev_loc_1", "kind": "dom", "summary": "button data-testid=create-btn"},
        ],
        "gt_domain": "LOCATOR",
    },
    {
        "id": "environment",
        "symptom": "执行时连接被测服务被拒绝",
        "failed_steps": [
            {"step_id": "s1", "action": "navigate", "error": "net::ERR_CONNECTION_REFUSED"}
        ],
        "evidence": [
            {"evidence_id": "ev_env_1", "kind": "log", "summary": "target 127.0.0.1:8001 refused"},
        ],
        "gt_domain": "ENVIRONMENT",
    },
    {
        "id": "product",
        "symptom": "页面显示成功提示，但后端项目列表没有新增记录",
        "failed_steps": [
            {"step_id": "s6", "action": "assert", "error": "business_state assertion failed"}
        ],
        "evidence": [
            {
                "evidence_id": "ev_prod_1",
                "kind": "api",
                "summary": "GET /api/projects returns 0 items",
            },
            {"evidence_id": "ev_prod_2", "kind": "screenshot", "summary": "banner-success visible"},
        ],
        "gt_domain": "PRODUCT",
    },
    {
        "id": "network",
        "symptom": "提交请求返回 503，重试后仍然失败",
        "failed_steps": [
            {"step_id": "s4", "action": "click", "error": "response 503 from /api/projects"}
        ],
        "evidence": [
            {"evidence_id": "ev_net_1", "kind": "network", "summary": "POST /api/projects 503"},
        ],
        "gt_domain": "NETWORK",
    },
    {
        "id": "ui",
        "symptom": "数据已落库，但成功提示元素不可见（display:none）",
        "failed_steps": [
            {"step_id": "s5", "action": "assert", "error": "element not visible: banner-success"}
        ],
        "evidence": [
            {"evidence_id": "ev_ui_1", "kind": "dom", "summary": "banner class=hidden"},
            {"evidence_id": "ev_ui_2", "kind": "api", "summary": "project persisted"},
        ],
        "gt_domain": "UI",
    },
]


def _rule_domain(case: dict[str, Any]) -> str:
    text = " ".join(
        [case["symptom"]]
        + [str(s.get("error", "")) for s in case["failed_steps"]]
        + [str(e.get("summary", "")) for e in case["evidence"]]
    ).lower()
    if "timeout" in text or "exceeded" in text:
        return "TIMING"
    if "locator" in text or "not found" in text:
        return "LOCATOR"
    if "connection refused" in text or "econnrefused" in text:
        return "ENVIRONMENT"
    if "503" in text or "502" in text or "network" in text:
        return "NETWORK"
    if "not visible" in text or "display:none" in text or "hidden" in text:
        return "UI"
    return "PRODUCT"


def _domain_norm(domain: str) -> str:
    """把模型域的细分标签映射到评估用的粗粒度域（仅用于打分，不改变模型输出）。"""
    mapping = {
        "TIMEOUT": "TIMING",
        "TIMING": "TIMING",
        "LOCATOR": "LOCATOR",
        "NETWORK": "NETWORK",
        "API": "NETWORK",
        "ENVIRONMENT": "ENVIRONMENT",
        "PERSISTENCE": "PRODUCT",
        "PRODUCT": "PRODUCT",
        "UI": "UI",
        "JS": "UI",
    }
    return mapping.get(str(domain).upper(), str(domain).upper())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def main() -> int:
    settings = get_settings()
    stack = build_llm_stack(settings, root_dir=".")
    if not stack:
        print("NO_CREDENTIALS: 真实 Provider 未配置，评估标记 NOT_RUN")
        return 2

    advisor = LLMDiagnosisAdvisor(
        provider=stack["provider"],
        router=stack["router"],
        prompts=stack["prompts"],
        tracker=stack["tracker"],
    )
    tracker = stack["tracker"]
    day = time.strftime("%Y-%m-%d")

    llm_rows: list[dict[str, Any]] = []
    rule_rows: list[dict[str, Any]] = []
    for case in CASES:
        before = tracker.usage_for_date(day)
        started = time.time()
        advice = advisor.suggest(case["symptom"], case["failed_steps"], case["evidence"])
        latency_ms = round((time.time() - started) * 1000)
        after = tracker.usage_for_date(day)
        tokens_in = int(after.get("input_tokens", 0)) - int(before.get("input_tokens", 0))
        tokens_out = int(after.get("output_tokens", 0)) - int(before.get("output_tokens", 0))
        cost = float(after.get("estimated_cost_usd") or 0.0) - float(
            before.get("estimated_cost_usd") or 0.0
        )
        valid_ids = {e["evidence_id"] for e in case["evidence"]}
        candidates = advice.candidates
        supported = [
            c
            for c in candidates
            if c.supporting_evidence_ids
            and all(eid in valid_ids for eid in c.supporting_evidence_ids)
        ]
        seen: set[tuple[str, str]] = set()
        duplicates = 0
        for c in candidates:
            key = (c.failure_domain, _norm(c.failure_mechanism)[:80])
            if key in seen:
                duplicates += 1
            seen.add(key)
        hit = any(c.failure_domain == case["gt_domain"] for c in candidates)
        hit_norm = any(_domain_norm(c.failure_domain) == case["gt_domain"] for c in candidates)
        llm_rows.append(
            {
                "case": case["id"],
                "gt_domain": case["gt_domain"],
                "called": advice.called,
                "fallback": advice.fallback,
                "error": advice.error,
                "model": advice.model,
                "candidates": len(candidates),
                "supported": len(supported),
                "dropped": advice.dropped,
                "duplicates": duplicates,
                "domains": [c.failure_domain for c in candidates],
                "hit": hit,
                "hit_normalized": hit_norm,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": round(cost, 6),
            }
        )
        rule_rows.append(
            {
                "case": case["id"],
                "gt_domain": case["gt_domain"],
                "domain": _rule_domain(case),
                "hit": _rule_domain(case) == case["gt_domain"],
            }
        )

    total_candidates = sum(r["candidates"] for r in llm_rows)
    total_supported = sum(r["supported"] for r in llm_rows)
    total_dropped = sum(r["dropped"] for r in llm_rows)
    total_dupes = sum(r["duplicates"] for r in llm_rows)
    called = [r for r in llm_rows if r["called"]]
    fallback = [r for r in llm_rows if r["fallback"]]
    metrics = {
        "workpackage": "RUN-013 WP5",
        "provider": "deepseek",
        "model": sorted({r["model"] for r in llm_rows if r["model"]}),
        "cases": len(CASES),
        "statistical_limit": "n=6，仅用于回归与相对比较，不能外推总体准确率",
        "rule_group": {
            "domain_accuracy": round(sum(r["hit"] for r in rule_rows) / len(rule_rows), 4),
            "rows": rule_rows,
        },
        "llm_group": {
            "structure_rate": round(len(called) / len(llm_rows), 4),
            "schema_pass_rate": round(
                sum(1 for r in llm_rows if r["candidates"] > 0) / len(llm_rows), 4
            ),
            "evidence_supported_ratio": round(
                total_supported / total_candidates if total_candidates else 0.0, 4
            ),
            "invalid_candidate_ratio": round(
                total_dropped / (total_candidates + total_dropped)
                if (total_candidates + total_dropped)
                else 0.0,
                4,
            ),
            "duplicate_ratio": round(
                total_dupes / total_candidates if total_candidates else 0.0, 4
            ),
            "adoption_rate": round(sum(r["hit"] for r in llm_rows) / len(llm_rows), 4),
            "adoption_rate_normalized": round(
                sum(r["hit_normalized"] for r in llm_rows) / len(llm_rows), 4
            ),
            "fallback_rate": round(len(fallback) / len(llm_rows), 4),
            "avg_latency_ms": round(sum(r["latency_ms"] for r in llm_rows) / len(llm_rows), 1),
            "tokens_in": sum(r["tokens_in"] for r in llm_rows),
            "tokens_out": sum(r["tokens_out"] for r in llm_rows),
            "estimated_cost_usd": round(sum(r["cost_usd"] for r in llm_rows), 6),
            "rows": llm_rows,
        },
    }
    out = ROOT / "docs/assets/run013/wp5"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ai-quality-eval.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
