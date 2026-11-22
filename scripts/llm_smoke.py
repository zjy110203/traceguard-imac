"""RUN-003-G 真实接入验收（真实 DeepSeek API Smoke Test）。

前置：.env 中配置 DEEPSEEK_API_KEY（已被 .gitignore 排除）。
输出：JSON 结果写入 .workbuddy-run/llm-smoke-results.json（不含密钥）。
任何失败如实记录，不伪造 PASS。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from traceguard_agents.gateway.llm_gateway import (  # noqa: E402
    DeepSeekProvider,
    LLMError,
    ModelRouter,
    TokenUsageTracker,
)
from traceguard_core.config import get_settings  # noqa: E402

RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"check": name, "ok": ok, "detail": detail})
    print(("PASS" if ok else "FAIL"), name, ("| " + detail) if detail else "")


def main() -> int:
    settings = get_settings()
    if not settings.deepseek_api_key:
        print("DEEPSEEK_API_KEY 未配置 → 全部验收标记 BLOCKED")
        record("config", False, "DEEPSEEK_API_KEY missing")
        return 1

    tracker = TokenUsageTracker(daily_budget_usd=float(settings.llm_daily_budget_usd))
    provider = DeepSeekProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        tracker=tracker,
    )
    router = ModelRouter(settings.llm_default_model, settings.llm_reasoning_model)

    # 1. 连通性：官方模型列表（验证配置模型真实存在，不凭历史假设）
    try:
        models = [m["id"] for m in provider.list_models()]
        record("connectivity_models", True, f"models={models}")
        record(
            "configured_models_exist",
            settings.llm_default_model in models and settings.llm_reasoning_model in models,
            f"default={settings.llm_default_model} reasoning={settings.llm_reasoning_model}",
        )
    except LLMError as exc:
        record("connectivity_models", False, exc.kind)
        return 1

    # 2. Flash 普通文本调用 + Token 统计
    try:
        r = provider.chat(
            [{"role": "user", "content": "用一句话说明什么是冒烟测试。"}],
            model=router.default_model,
            agent="smoke",
            prompt_version="smoke@v1",
            max_tokens=100,
        )
        u = r["usage"]
        record("flash_text_call", bool(r["content"]), f"{u['input_tokens']}in/{u['output_tokens']}out {r['latency_ms']}ms")
        record("token_usage_recorded", u["input_tokens"] > 0 and u["output_tokens"] > 0, f"cached={u['cached_tokens']}")
    except LLMError as exc:
        record("flash_text_call", False, exc.kind)

    # 3. JSON Output（Flash）
    try:
        parsed, r = provider.chat_json(
            [
                {"role": "system", "content": '只输出 JSON，格式：{"action": "stop", "params": {}, "reason": "停止原因"}'},
                {"role": "user", "content": "目标已达成，请停止。"},
            ],
            model=router.default_model,
            agent="smoke",
            max_tokens=80,
        )
        record("flash_json_output", "action" in parsed, f"keys={sorted(parsed.keys())[:4]}")
    except LLMError as exc:
        record("flash_json_output", False, exc.kind)

    # 4. Pro 复杂推理调用
    try:
        r = provider.chat(
            [{"role": "user", "content": "一个测试偶发失败，重跑即过。给出两种可能原因，各一句话。"}],
            model=router.reasoning_model,
            agent="smoke",
            prompt_version="smoke@v1",
            max_tokens=300,
        )
        record("pro_reasoning_call", bool(r["content"]), f"model={r['model']} {r['latency_ms']}ms")
    except LLMError as exc:
        record("pro_reasoning_call", False, exc.kind)

    # 5. 超时边界（timeout=0.001s 必然超时 → LLMError(timeout)，不重试无限等待）
    t0 = time.monotonic()
    short = DeepSeekProvider(api_key=settings.deepseek_api_key, base_url=settings.llm_base_url, timeout_s=0.001, max_retries=0)
    try:
        short.chat([{"role": "user", "content": "hi"}], model=router.default_model)
        record("timeout_boundary", False, "未触发超时（意外成功）")
    except LLMError as exc:
        dt = time.monotonic() - t0
        record("timeout_boundary", exc.kind == "timeout" and dt < 5, f"kind={exc.kind} elapsed={dt:.2f}s")

    # 6. 无效输出处理（max_tokens=5 强制截断 → 大概率 invalid_json 或正常短输出，均应显式处理）
    try:
        parsed, r = provider.chat_json(
            [{"role": "system", "content": '输出JSON：{"a":1,"b":2,"c":3,"d":4,"e":5}'}],
            model=router.default_model,
            agent="smoke",
            max_tokens=5,
        )
        record("truncated_output_handled", True, f"解析成功 keys={list(parsed.keys())[:3]}")
    except LLMError as exc:
        record("truncated_output_handled", exc.kind in {"invalid_json", "server"}, f"kind={exc.kind}")

    # 7. 成本计算 + 用量报告（估算值，明确标记）
    report = tracker.usage_for_date()
    record(
        "cost_estimated_marked",
        report["cost_is_estimated"] is True,
        f"calls={report['calls']} cost=${report['estimated_cost_usd']}",
    )

    # 8. 密钥脱敏：Settings.public_dict 与异常消息不泄露 Key
    pub = settings.public_dict()
    key_leak = settings.deepseek_api_key in json.dumps(pub)
    record("key_redaction_settings", not key_leak)
    try:
        DeepSeekProvider(api_key="", base_url=settings.llm_base_url)
    except LLMError as exc:
        record("key_redaction_error_msg", settings.deepseek_api_key not in str(exc), exc.kind)

    provider.close()

    out = Path(".workbuddy-run/llm-smoke-results.json")
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2))
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    print(f"\n{passed}/{total} passed → {out}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
