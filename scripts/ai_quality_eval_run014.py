"""RUN-014 WP5：AI 质量评估（n=32，8 域分层）与四类顾问 Smoke。

设计要点：
- 规则组与 LLM 组使用**同一份输入**（symptom + failed_steps + evidence）；
- Ground Truth 只在本脚本内打分，**不进入模型上下文**；
- 保留三套标签：模型原始域、细粒度归一化（8 域，默认评分口径）、
  RUN-013 粗粒度归一化（PRODUCT/NETWORK/UI 合并口径，仅作对比，不替换）；
- 统计 Domain Accuracy / Macro-F1（细/粗）/ Precision / Recall / 候选采纳 /
  证据支持 / 无效候选 / 重复 / Fallback / 平均与 P95 延迟 / Token / 成本；
- 先估算成本，超过当日剩余预算即 BLOCKED（不发起计费调用）；
- 四类顾问各自单次真实 Smoke；Probe Planner 只在本次进程内强制开启，
  不改生产默认值（脚本结束即恢复）。

用法：.venv/bin/python scripts/ai_quality_eval_run014.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from traceguard_agents.gateway.llm_gateway import (  # noqa: E402
    LLMDiagnosisAdvisor,
    build_llm_stack,
)
from traceguard_agents.gateway.llm_planners import (  # noqa: E402
    LLMCompilerAdvisor,
    LLMProbePlannerAdvisor,
    LLMRepairAdvisor,
)
from traceguard_core.config import get_settings  # noqa: E402

OUT = ROOT / "docs/assets/run014/wp5"
DOMAINS = ["TIMING", "NETWORK", "UI", "LOCATOR", "PERSISTENCE", "ENVIRONMENT", "API", "JS"]


def _case(cid: str, domain: str, symptom: str, step: str, err: str, ev: list[tuple[str, str]]) -> dict:
    return {
        "id": cid,
        "symptom": symptom,
        "failed_steps": [{"step_id": "s_fail", "action": step, "error": err}],
        "evidence": [
            {"evidence_id": f"ev_{cid}_{i}", "kind": kind, "summary": summary}
            for i, (kind, summary) in enumerate(ev)
        ],
        "gt_domain": domain,
    }


CASES: list[dict[str, Any]] = [
    # ---- TIMING (4)
    _case("timing_banner", "TIMING", "创建成功后成功提示 3 秒后才出现，断言 2 秒超时",
          "assert", "Timeout 2000ms exceeded waiting for banner",
          [("trace", "banner rendered at 3012ms"), ("screenshot", "success banner visible")]),
    _case("timing_api", "TIMING", "接口响应慢，前端请求 15 秒后才返回，步骤等待超时",
          "click", "waiting for response /api/projects exceeded 8000ms",
          [("network", "POST /api/projects took 15230ms")]),
    _case("timing_anim", "TIMING", "列表动画 1.2 秒后才可点击，点击时元素仍在过渡",
          "click", "element not stable: animation in progress (timeout 1000ms)",
          [("dom", "transition duration 1200ms"), ("screenshot", "row fading in")]),
    _case("timing_race", "TIMING", "轮询间隔导致状态更新晚于断言时刻",
          "assert", "expected count=1 but got 0 (poll delayed 2 cycles)",
          [("api", "state updated after 4.1s"), ("trace", "assertion at 2.0s")]),
    # ---- NETWORK (4)
    _case("net_503", "NETWORK", "提交请求返回 503，重试仍失败",
          "click", "response 503 from /api/projects",
          [("network", "POST /api/projects 503")]),
    _case("net_timeout", "NETWORK", "请求连接超时，未收到任何响应",
          "click", "net::ERR_TIMED_OUT",
          [("network", "POST /api/projects no response in 30s")]),
    _case("net_reset", "NETWORK", "连接被重置，前端显示网络错误",
          "click", "net::ERR_CONNECTION_RESET",
          [("network", "connection reset by peer")]),
    _case("net_cors", "NETWORK", "跨域预检失败，请求未发出",
          "click", "CORS preflight failed: no Access-Control-Allow-Origin",
          [("console", "blocked by CORS policy")]),
    # ---- UI (4)
    _case("ui_hidden", "UI", "数据已落库，但成功提示元素 display:none 不可见",
          "assert", "element not visible: banner-success",
          [("dom", "banner class=hidden"), ("api", "project persisted")]),
    _case("ui_overlay", "UI", "弹窗遮罩覆盖按钮，点击命中遮罩",
          "click", "element intercepted by .modal-backdrop",
          [("screenshot", "overlay covers form"), ("dom", "z-index 999 overlay")]),
    _case("ui_text", "UI", "提示文案与断言不一致（“已保存” vs “创建成功”）",
          "assert", "text mismatch: expected 创建成功, got 已保存",
          [("dom", "banner text=已保存")]),
    _case("ui_render", "UI", "列表渲染为空骨架屏，数据行未挂载",
          "assert", "count of [data-testid=project-row] == 0",
          [("dom", "skeleton placeholder present"), ("api", "GET /api/projects returns 1 item")]),
    # ---- LOCATOR (4)
    _case("loc_submit", "LOCATOR", "点击提交按钮提示 locator not found，页面结构已改版",
          "click", "locator #create-submit not found",
          [("dom", "button data-testid=create-btn")]),
    _case("loc_name", "LOCATOR", "输入框 test_id 改名，fill 无法定位",
          "fill", "get_by_test_id('project-name') not found",
          [("dom", "input name=projectName")]),
    _case("loc_strict", "LOCATOR", "同一 test_id 命中多个元素，严格模式报错",
          "click", "strict mode violation: resolved to 3 elements",
          [("dom", "three nodes share data-testid=row")]),
    _case("loc_iframe", "LOCATOR", "元素位于 iframe 内，主文档定位失败",
          "click", "locator not found in main frame (element inside iframe#editor)",
          [("dom", "iframe id=editor detected")]),
    # ---- PERSISTENCE (4)
    _case("persist_missing", "PERSISTENCE", "页面显示成功但后端列表没有新增记录",
          "assert", "business_state assertion failed: count unchanged",
          [("api", "GET /api/state/projects returns 0 items"), ("screenshot", "banner-success visible")]),
    _case("persist_dup", "PERSISTENCE", "提交后数据库出现两条重复记录",
          "assert", "expected count=1, got 2 (duplicate rows)",
          [("api", "two rows share same name"), ("log", "retry inserted again")]),
    _case("persist_lag", "PERSISTENCE", "写入延迟 9 秒才可见，断言时读不到",
          "assert", "expected count=1 got 0; row appears 9s later",
          [("api", "row visible after 9s"), ("trace", "assert at 2s")]),
    _case("persist_rollback", "PERSISTENCE", "后续步骤失败触发回滚，数据未提交",
          "assert", "transaction rolled back: 0 rows persisted",
          [("log", "ROLLBACK executed"), ("api", "count=0")]),
    # ---- ENVIRONMENT (4)
    _case("env_refused", "ENVIRONMENT", "执行时连接被测服务被拒绝",
          "navigate", "net::ERR_CONNECTION_REFUSED",
          [("log", "target 127.0.0.1:8001 refused")]),
    _case("env_dns", "ENVIRONMENT", "域名解析失败，页面无法打开",
          "navigate", "net::ERR_NAME_NOT_RESOLVED",
          [("log", "dns lookup failed for app.local")]),
    _case("env_browser", "ENVIRONMENT", "浏览器二进制缺失，执行器启动失败",
          "navigate", "BrowserType.launch: Executable doesn't exist",
          [("log", "chromium not installed")]),
    _case("env_disk", "ENVIRONMENT", "磁盘写满导致截图与证据落盘失败",
          "assert", "OSError: [Errno 28] No space left on device",
          [("log", "artifact write failed: ENOSPC")]),
    # ---- API (4)
    _case("api_500", "API", "创建接口返回 500，服务端异常",
          "click", "response 500 from POST /api/projects",
          [("api", "traceback: KeyError 'name'"), ("network", "500 Internal Server Error")]),
    _case("api_validation", "API", "请求体校验失败，返回 422",
          "click", "response 422: field required: name",
          [("api", "422 Unprocessable Entity")]),
    _case("api_contract", "API", "响应字段改名，前端读取 undefined",
          "assert", "expected items[0].title, got undefined",
          [("api", '{"projects":[{"name":"x"}]}'), ("console", "TypeError reading title")]),
    _case("api_auth", "API", "会话过期导致 401，操作被拒绝",
          "click", "response 401: UNAUTHENTICATED",
          [("api", "401 on POST /api/projects"), ("log", "session expired")]),
    # ---- JS (4)
    _case("js_typeerror", "JS", "页面脚本抛出 TypeError，交互失效",
          "click", "pageerror: TypeError: Cannot read properties of null",
          [("console", "Uncaught TypeError at app.js:120")]),
    _case("js_promise", "JS", "未捕获的 Promise 拒绝，提交后无反馈",
          "click", "pageerror: Unhandled promise rejection",
          [("console", "UnhandledPromiseRejection: submit failed")]),
    _case("js_syntax", "JS", "新发布的前端包语法错误，整页白屏",
          "navigate", "pageerror: SyntaxError: Unexpected token '<'",
          [("console", "SyntaxError in chunk-vendors.js")]),
    _case("js_infinite", "JS", "渲染循环导致主线程卡死，步骤超时",
          "click", "page unresponsive: main thread blocked",
          [("trace", "long task 12000ms"), ("console", "Maximum update depth exceeded")]),
]


def _rule_domain(case: dict[str, Any]) -> str:
    """确定性规则基线（与 LLM 组同输入，不用 GT）。顺序即优先级，需在报告中公开。"""
    text = " ".join(
        [case["symptom"]]
        + [str(s.get("error", "")) for s in case["failed_steps"]]
        + [str(e.get("summary", "")) for e in case["evidence"]]
    ).lower()

    def has(*words: str) -> bool:
        return any(w in text for w in words)

    if has("pageerror", "unhandled promise", "syntaxerror", "maximum update depth", "uncaught"):
        return "JS"
    if has("connection refused", "econnrefused", "err_name_not_resolved", "executable doesn't exist", "enospc"):
        return "ENVIRONMENT"
    if has("locator", "strict mode", "get_by_test_id", "not found in main frame", "iframe"):
        return "LOCATOR"
    if has("503", "502", "err_timed_out", "err_connection_reset", "cors", "no response in"):
        return "NETWORK"
    if has("500 ", " 500", "422", "401", "response 5", "undefined"):
        return "API"
    if has("count unchanged", "duplicate rows", "rollback", "row appears", "transaction rolled back"):
        return "PERSISTENCE"
    if has("timeout", "exceeded", "delayed", "poll delayed", "not stable"):
        return "TIMING"
    if has("not visible", "hidden", "intercepted", "overlay", "text mismatch", "skeleton", "count of ["):
        return "UI"
    return "UI"


def _norm_fine(domain: str) -> str:
    """细粒度归一（8 域，默认评分口径）：只做大小写与同义词收敛，不合并语义域。"""
    mapping = {
        "TIMEOUT": "TIMING",
        "TIMING": "TIMING",
        "PERFORMANCE": "TIMING",
        "NETWORK": "NETWORK",
        "API": "API",
        "BACKEND": "API",
        "PERSISTENCE": "PERSISTENCE",
        "DATA": "PERSISTENCE",
        "LOCATOR": "LOCATOR",
        "SELECTOR": "LOCATOR",
        "ENVIRONMENT": "ENVIRONMENT",
        "INFRA": "ENVIRONMENT",
        "UI": "UI",
        "JS": "JS",
        "JAVASCRIPT": "JS",
        "PRODUCT": "PERSISTENCE",
    }
    return mapping.get(str(domain).upper(), str(domain).upper())


def _norm_coarse(domain: str) -> str:
    """RUN-013 粗粒度口径（PRODUCT/NETWORK/UI 合并），仅用于与历史结果对比。"""
    fine = _norm_fine(domain)
    return {
        "PERSISTENCE": "PRODUCT",
        "PRODUCT": "PRODUCT",
        "API": "NETWORK",
        "JS": "UI",
    }.get(fine, fine)


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _prf(rows: list[dict[str, Any]], gt_key: str, pred_key: str, labels: list[str]) -> dict[str, Any]:
    per: dict[str, Any] = {}
    f1s = []
    for label in labels:
        tp = sum(1 for r in rows if r[gt_key] == label and r[pred_key] == label)
        fp = sum(1 for r in rows if r[gt_key] != label and r[pred_key] == label)
        fn = sum(1 for r in rows if r[gt_key] == label and r[pred_key] != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if tp + fn:
            f1s.append(f1)
        per[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }
    return {"macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0, "per_class": per}


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


class _Def:
    """探针规划顾问 smoke 的最小定义视图（与生产 ProbeDefinition 同字段）。"""

    def __init__(self, probe_id: str, capability: str, cost: float) -> None:
        self.probe_id = probe_id
        self.capability = capability
        self.risk_level = "LOW"
        self.allowed_templates = {f"get_{capability}"}
        self.estimated_cost = cost


def _advisor_smoke(stack: dict[str, Any]) -> dict[str, Any]:
    """四类顾问各一次真实调用；失败/未配置如实记录，不伪造。"""
    tracker = stack["tracker"]
    out: dict[str, Any] = {}

    def _usage(before: dict[str, Any]) -> tuple[int, int, float]:
        after = tracker.usage_for_date(time.strftime("%Y-%m-%d"))
        return (
            int(after.get("input_tokens", 0)) - int(before.get("input_tokens", 0)),
            int(after.get("output_tokens", 0)) - int(before.get("output_tokens", 0)),
            round(
                float(after.get("estimated_cost_usd") or 0.0)
                - float(before.get("estimated_cost_usd") or 0.0),
                6,
            ),
        )

    day = time.strftime("%Y-%m-%d")
    trace = {
        "action_sequence": [
            {"action": "navigate", "params": {"url": "/projects/new"}, "result": "/projects/new"},
            {"action": "fill", "params": {"test_id": "project-name", "value": "X"}, "result": "filled"},
            {"action": "click", "params": {"test_id": "create-submit"}, "result": "/projects?created=1"},
        ],
        "business_state_api_available": "/api/state/projects",
    }
    before = tracker.usage_for_date(day)
    t0 = time.time()
    cadv = LLMCompilerAdvisor(
        provider=stack["provider"], router=stack["router"], prompts=stack["prompts"], tracker=tracker
    ).suggest(trace, "创建项目后必须持久化并可回读")
    out["compiler_advisor"] = {
        "called": cadv.called,
        "fallback": cadv.fallback,
        "error": getattr(cadv, "error", ""),
        "steps": len(cadv.steps),
        "assertions": len(cadv.assertions),
        "dropped_steps": getattr(cadv, "dropped_steps", 0),
        "warnings": list(cadv.warnings)[:5],
        "latency_ms": round((time.time() - t0) * 1000),
        "tokens": _usage(before),
    }

    defs = {
        "http_state": _Def("http_state", "state_query", 0.05),
        "network_trace": _Def("network_trace", "network", 0.1),
        "dom_snapshot": _Def("dom_snapshot", "dom", 0.08),
    }
    gaps = [
        {"gap_id": "gap_1", "probe_id": "http_state", "why": "业务状态未确认", "info_gain": 0.8},
        {"gap_id": "gap_2", "probe_id": "network_trace", "why": "接口返回未知", "info_gain": 0.5},
        {"gap_id": "gap_3", "probe_id": "dom_snapshot", "why": "DOM 结构未知", "info_gain": 0.3},
    ]
    before = tracker.usage_for_date(day)
    t0 = time.time()
    padv = LLMProbePlannerAdvisor(
        provider=stack["provider"], router=stack["router"], prompts=stack["prompts"], tracker=tracker
    ).suggest(gaps, defs)
    selected = (padv.selections or [{}])[0].get("probe_id", "") if padv.selections else ""
    out["probe_planner"] = {
        "called": padv.called,
        "fallback": padv.fallback,
        "error": getattr(padv, "error", ""),
        "probe_id": selected,
        "selections": len(padv.selections),
        "dropped": padv.dropped,
        "valid": selected in defs,
        "latency_ms": round((time.time() - t0) * 1000),
        "tokens": _usage(before),
        "note": "仅在本次评估进程内直接调用顾问类；生产默认开关未被修改",
    }

    before = tracker.usage_for_date(day)
    t0 = time.time()
    radv = LLMRepairAdvisor(
        provider=stack["provider"], router=stack["router"], prompts=stack["prompts"], tracker=tracker
    ).suggest(
        symptom="点击提交提示 locator not found",
        steps=[{"step_id": "s2", "action": "click", "target": {"strategy": "test_id", "value": "create-submit"}}],
        assertions=[{"assertion_id": "a1", "expected": {"test_id": "banner-success"}}],
        step_count=5,
    )
    out["repair_planner"] = {
        "called": radv.called,
        "fallback": radv.fallback,
        "error": getattr(radv, "error", ""),
        "changes": len(radv.changes),
        "warnings": list(radv.warnings)[:5],
        "latency_ms": round((time.time() - t0) * 1000),
        "tokens": _usage(before),
    }
    return out


def main() -> int:
    if "--advisors-only" in sys.argv:
        return _advisors_only()
    settings = get_settings()
    stack = build_llm_stack(settings, root_dir=".")
    if not stack:
        print("NO_CREDENTIALS: 真实 Provider 未配置，评估标记 NOT_RUN")
        return 2
    tracker = stack["tracker"]
    day = time.strftime("%Y-%m-%d")
    usage_before = tracker.usage_for_date(day)
    spent = float(usage_before.get("estimated_cost_usd") or 0.0)
    budget = float(settings.llm_daily_budget_usd)
    estimated = 0.0006 * (len(CASES) + 4)  # 32 诊断 + 3 顾问（按 RUN-013 实测单次成本上界估算）
    if spent + estimated > budget:
        metrics = {
            "workpackage": "RUN-014 WP5",
            "status": "BLOCKED",
            "reason": "预算不足，未发起计费调用（需额外授权）",
            "spent_usd": round(spent, 6),
            "estimated_usd": round(estimated, 6),
            "budget_usd": budget,
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "ai-quality-n32.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        return 3

    print(
        f"budget_check: spent={spent:.6f} estimated={estimated:.4f} budget={budget}",
        flush=True,
    )
    advisor = LLMDiagnosisAdvisor(
        provider=stack["provider"],
        router=stack["router"],
        prompts=stack["prompts"],
        tracker=tracker,
    )
    llm_rows: list[dict[str, Any]] = []
    rule_rows: list[dict[str, Any]] = []
    for case in CASES:
        before = tracker.usage_for_date(day)
        started = time.time()
        advice = advisor.suggest(case["symptom"], case["failed_steps"], case["evidence"])
        latency_ms = round((time.time() - started) * 1000)
        after = tracker.usage_for_date(day)
        valid_ids = {e["evidence_id"] for e in case["evidence"]}
        candidates = advice.candidates
        supported = [
            c
            for c in candidates
            if c.supporting_evidence_ids
            and all(e in valid_ids for e in c.supporting_evidence_ids)
        ]
        seen: set[tuple[str, str]] = set()
        duplicates = 0
        for c in candidates:
            key = (c.failure_domain, _norm_text(c.failure_mechanism)[:80])
            if key in seen:
                duplicates += 1
            seen.add(key)
        domains = [c.failure_domain for c in candidates]
        row = {
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
            "domains_raw": domains,
            "domains_fine": [_norm_fine(d) for d in domains],
            "domains_coarse": [_norm_coarse(d) for d in domains],
            "gt_coarse": _norm_coarse(case["gt_domain"]),
            "hit_raw": case["gt_domain"] in domains,
            "hit_fine": case["gt_domain"] in [_norm_fine(d) for d in domains],
            "hit_coarse": _norm_coarse(case["gt_domain"])
            in [_norm_coarse(d) for d in domains],
            "latency_ms": latency_ms,
            "tokens_in": int(after.get("input_tokens", 0)) - int(before.get("input_tokens", 0)),
            "tokens_out": int(after.get("output_tokens", 0)) - int(before.get("output_tokens", 0)),
            "cost_usd": round(
                float(after.get("estimated_cost_usd") or 0.0)
                - float(before.get("estimated_cost_usd") or 0.0),
                6,
            ),
        }
        llm_rows.append(row)
        rule_rows.append(
            {
                "case": case["id"],
                "gt_domain": case["gt_domain"],
                "domain": _rule_domain(case),
                "hit": _rule_domain(case) == case["gt_domain"],
            }
        )
        print(
            f"[{case['id']}] gt={case['gt_domain']} rule={rule_rows[-1]['domain']} "
            f"llm={row['domains_fine']} fallback={row['fallback']}",
            flush=True,
        )

    total_candidates = sum(r["candidates"] for r in llm_rows)
    total_supported = sum(r["supported"] for r in llm_rows)
    total_dropped = sum(r["dropped"] for r in llm_rows)
    total_dupes = sum(r["duplicates"] for r in llm_rows)
    latencies = [r["latency_ms"] for r in llm_rows]
    rule_hits = sum(r["hit"] for r in rule_rows)
    llm_hits_fine = sum(r["hit_fine"] for r in llm_rows)
    llm_hits_raw = sum(r["hit_raw"] for r in llm_rows)
    llm_hits_coarse = sum(r["hit_coarse"] for r in llm_rows)

    rule_prf = _prf(
        [{"gt": r["gt_domain"], "pred": r["domain"]} for r in rule_rows], "gt", "pred", DOMAINS
    )
    llm_prf = _prf(
        [
            {
                "gt": r["gt_domain"],
                "pred": (r["domains_fine"] or ["UNKNOWN"])[0],
            }
            for r in llm_rows
        ],
        "gt",
        "pred",
        DOMAINS,
    )
    coarse_labels = sorted({(r.get("gt_coarse") or _norm_coarse(r["gt_domain"])) for r in llm_rows})
    llm_prf_coarse = _prf(
        [
            {
                "gt": r.get("gt_coarse") or _norm_coarse(r["gt_domain"]),
                "pred": (r["domains_coarse"] or ["UNKNOWN"])[0],
            }
            for r in llm_rows
        ],
        "gt",
        "pred",
        coarse_labels,
    )

    advisors = _advisor_smoke(stack)
    after_all = tracker.usage_for_date(day)
    metrics = {
        "workpackage": "RUN-014 WP5",
        "status": "COMPLETED",
        "provider": "deepseek",
        "model": sorted({r["model"] for r in llm_rows if r["model"]}),
        "cases": len(CASES),
        "domains": DOMAINS,
        "statistical_limit": "n=32（每域 4 例），用于回归与相对比较；不等同于总体准确率",
        "ground_truth_usage": "仅测试端打分，未进入模型上下文",
        "rule_group": {
            "domain_accuracy": round(rule_hits / len(rule_rows), 4),
            "macro_f1": rule_prf["macro_f1"],
            "per_class": rule_prf["per_class"],
            "rows": rule_rows,
        },
        "llm_group": {
            "structure_rate": round(sum(1 for r in llm_rows if r["called"]) / len(llm_rows), 4),
            "schema_pass_rate": round(
                sum(1 for r in llm_rows if r["candidates"] > 0) / len(llm_rows), 4
            ),
            "domain_accuracy_raw": round(llm_hits_raw / len(llm_rows), 4),
            "domain_accuracy_fine": round(llm_hits_fine / len(llm_rows), 4),
            "domain_accuracy_coarse": round(llm_hits_coarse / len(llm_rows), 4),
            "macro_f1_fine": llm_prf["macro_f1"],
            "macro_f1_coarse": llm_prf_coarse["macro_f1"],
            "per_class_coarse": llm_prf_coarse["per_class"],
            "per_class_fine": llm_prf["per_class"],
            "evidence_supported_ratio": round(
                total_supported / total_candidates if total_candidates else 0.0, 4
            ),
            "invalid_candidate_ratio": round(
                total_dropped / (total_candidates + total_dropped)
                if (total_candidates + total_dropped)
                else 0.0,
                4,
            ),
            "duplicate_ratio": round(total_dupes / total_candidates if total_candidates else 0.0, 4),
            "false_positive_cases": sum(
                1
                for r in llm_rows
                if r["candidates"] and not r["hit_fine"] and not r["fallback"]
            ),
            "fallback_rate": round(sum(1 for r in llm_rows if r["fallback"]) / len(llm_rows), 4),
            "avg_latency_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
            "p95_latency_ms": _p95([float(x) for x in latencies]),
            "tokens_in": sum(r["tokens_in"] for r in llm_rows),
            "tokens_out": sum(r["tokens_out"] for r in llm_rows),
            "estimated_cost_usd": round(sum(r["cost_usd"] for r in llm_rows), 6),
            "rows": llm_rows,
        },
        "advisors": advisors,
        "budget": {
            "day": day,
            "spent_before_usd": round(spent, 6),
            "spent_after_usd": round(float(after_all.get("estimated_cost_usd") or 0.0), 6),
            "daily_budget_usd": budget,
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ai-quality-n32.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "rule_acc": metrics["rule_group"]["domain_accuracy"],
                "rule_macro_f1": metrics["rule_group"]["macro_f1"],
                "llm_acc_fine": metrics["llm_group"]["domain_accuracy_fine"],
                "llm_acc_coarse": metrics["llm_group"]["domain_accuracy_coarse"],
                "llm_macro_f1_fine": metrics["llm_group"]["macro_f1_fine"],
                "fallback": metrics["llm_group"]["fallback_rate"],
                "cost": metrics["llm_group"]["estimated_cost_usd"],
                "p95_ms": metrics["llm_group"]["p95_latency_ms"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _advisors_only() -> int:
    """只重跑四类顾问 Smoke 并合并进已有评估结果（避免重复计费的 32 次诊断调用）。"""
    path = OUT / "ai-quality-n32.json"
    if not path.exists():
        print("NO_BASELINE: 先运行完整评估")
        return 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    settings = get_settings()
    stack = build_llm_stack(settings, root_dir=".")
    if not stack:
        print("NO_CREDENTIALS: 顾问 Smoke 标记 NOT_RUN")
        return 2
    day = time.strftime("%Y-%m-%d")
    before = float(stack["tracker"].usage_for_date(day).get("estimated_cost_usd") or 0.0)
    payload["advisors"] = _advisor_smoke(stack)
    after = float(stack["tracker"].usage_for_date(day).get("estimated_cost_usd") or 0.0)
    payload.setdefault("budget", {})["advisors_only_spent_usd"] = round(after - before, 6)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["advisors"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
