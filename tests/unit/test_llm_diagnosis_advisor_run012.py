"""RUN-012 WP5：LLM 诊断顾问可靠性离线单测（FakeProvider，不访问真实模型）。

覆盖：
- Schema 校验：顶层非对象、hypotheses 非列表、条目非对象、缺描述、域越界；
- 虚构证据：引用不存在的 evidence_id 一律剔除，无真实证据的假设整条丢弃；
- 重试：JSON 解析失败有限重试，其它错误（鉴权/限流）立即回退不重试；
- Fallback 标注：调用过但无可用候选必须 `fallback=True`，不伪装成"有 AI 建议"；
- 追踪字段：attempts / dropped / error / model 可回读。
"""

from __future__ import annotations

import json

from traceguard_agents.gateway.llm_gateway import (
    LLMDiagnosisAdvisor,
    LLMError,
    ModelRouter,
    PromptManager,
)

EVIDENCE = [
    {"evidence_id": "evd_1", "source_type": "network", "brief": "500 on POST /api/projects"},
    {"evidence_id": "evd_2", "source_type": "ui", "brief": "banner missing"},
]


class FakeProvider:
    """可编程的假 Provider：按脚本返回 payload 或抛错。"""

    def __init__(self, payloads: list | None = None, error: LLMError | None = None) -> None:
        self.payloads = list(payloads or [])
        self.error = error
        self.calls = 0
        self.last_kwargs: dict = {}

    def chat_json(self, messages, **kw):  # noqa: ANN001, ANN003
        self.calls += 1
        self.last_kwargs = kw
        if self.error:
            raise self.error
        if not self.payloads:
            return {}, {"content": "{}", "latency_ms": 5}
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload, {"content": json.dumps(payload), "latency_ms": 5}


def _advisor(provider: FakeProvider) -> LLMDiagnosisAdvisor:
    return LLMDiagnosisAdvisor(
        provider=provider,
        router=ModelRouter(default_model="deepseek-flash", reasoning_model="deepseek-v4-pro"),
        prompts=PromptManager(),
        tracker=None,
    )


def _suggest(provider: FakeProvider):
    return _advisor(provider).suggest(
        symptom="创建项目后没有成功提示",
        failed_steps=[{"step_id": "s5", "action": "assert", "error": "banner missing"}],
        evidence_summary=EVIDENCE,
    )


# ---------------------------------------------------------------- Schema 校验


def test_valid_hypothesis_is_kept_with_real_evidence() -> None:
    provider = FakeProvider(
        [
            {
                "hypotheses": [
                    {
                        "description": "创建接口返回 500",
                        "failure_domain": "API",
                        "failure_mechanism": "server_error",
                        "supporting_evidence_ids": ["evd_1"],
                        "confidence": 0.8,
                    }
                ]
            }
        ]
    )
    advice = _suggest(provider)
    assert advice.called is True
    assert advice.fallback is False
    assert len(advice.candidates) == 1
    c = advice.candidates[0]
    assert c.description == "创建接口返回 500"
    assert c.failure_domain == "API"
    assert c.supporting_evidence_ids == ["evd_1"]
    assert c.confidence == 0.8
    assert c.suggested_by == "llm:deepseek-flash"


def test_missing_hypotheses_field_is_fallback() -> None:
    advice = _suggest(FakeProvider([{"foo": "bar"}]))
    assert advice.called is True
    assert advice.fallback is True
    assert advice.error == "llm_returned_no_valid_hypothesis"
    assert advice.candidates == []


def test_hypotheses_not_a_list_is_dropped() -> None:
    advice = _suggest(FakeProvider([{"hypotheses": {"a": 1}}]))
    assert advice.fallback is True
    assert advice.candidates == []
    assert any("not_a_list" in w for w in advice.warnings)


def test_non_object_entry_is_dropped_and_counted() -> None:
    advice = _suggest(FakeProvider([{"hypotheses": ["not an object"]}]))
    assert advice.fallback is True
    assert advice.dropped == 1


def test_domain_outside_whitelist_is_dropped() -> None:
    advice = _suggest(
        FakeProvider(
            [
                {
                    "hypotheses": [
                        {
                            "description": "未知域",
                            "failure_domain": "MYSTERY",
                            "supporting_evidence_ids": ["evd_1"],
                        }
                    ]
                }
            ]
        )
    )
    assert advice.fallback is True
    assert advice.dropped == 1


def test_missing_description_is_dropped() -> None:
    advice = _suggest(
        FakeProvider(
            [
                {
                    "hypotheses": [
                        {
                            "description": "   ",
                            "failure_domain": "API",
                            "supporting_evidence_ids": ["evd_1"],
                        }
                    ]
                }
            ]
        )
    )
    assert advice.fallback is True
    assert advice.dropped == 1


def test_confidence_out_of_range_is_clamped() -> None:
    advice = _suggest(
        FakeProvider(
            [
                {
                    "hypotheses": [
                        {
                            "description": "高置信",
                            "failure_domain": "UI",
                            "supporting_evidence_ids": ["evd_2"],
                            "confidence": 9.5,
                        }
                    ]
                }
            ]
        )
    )
    assert advice.candidates[0].confidence == 1.0


def test_non_numeric_confidence_becomes_zero() -> None:
    advice = _suggest(
        FakeProvider(
            [
                {
                    "hypotheses": [
                        {
                            "description": "置信度非法",
                            "failure_domain": "UI",
                            "supporting_evidence_ids": ["evd_2"],
                            "confidence": "很高",
                        }
                    ]
                }
            ]
        )
    )
    assert advice.candidates[0].confidence == 0.0


def test_more_than_three_hypotheses_are_truncated() -> None:
    payload = {
        "hypotheses": [
            {
                "description": f"假设{i}",
                "failure_domain": "API",
                "supporting_evidence_ids": ["evd_1"],
            }
            for i in range(5)
        ]
    }
    advice = _suggest(FakeProvider([payload]))
    assert len(advice.candidates) == 3
    assert advice.dropped == 2


# ------------------------------------------------------------------ 防虚构


def test_fabricated_evidence_id_is_removed_but_hypothesis_kept() -> None:
    advice = _suggest(
        FakeProvider(
            [
                {
                    "hypotheses": [
                        {
                            "description": "引用了不存在的证据",
                            "failure_domain": "API",
                            "supporting_evidence_ids": ["evd_1", "evd_fake"],
                        }
                    ]
                }
            ]
        )
    )
    assert len(advice.candidates) == 1
    assert advice.candidates[0].supporting_evidence_ids == ["evd_1"]
    assert "fabricated_evidence_id_removed" in advice.candidates[0].warnings


def test_hypothesis_without_any_real_evidence_is_dropped() -> None:
    advice = _suggest(
        FakeProvider(
            [
                {
                    "hypotheses": [
                        {
                            "description": "完全虚构证据",
                            "failure_domain": "API",
                            "supporting_evidence_ids": ["evd_fake"],
                        }
                    ]
                }
            ]
        )
    )
    assert advice.candidates == []
    assert advice.fallback is True
    assert advice.dropped == 1


# -------------------------------------------------------------- 重试与回退


def test_invalid_json_is_retried_once_then_fallback() -> None:
    provider = FakeProvider([LLMError("invalid_json", "bad json"), LLMError("invalid_json", "bad")])
    advice = _suggest(provider)
    assert provider.calls == 2
    assert advice.fallback is True
    assert advice.error == "invalid_json"
    assert advice.attempts == 2


def test_non_json_error_is_not_retried() -> None:
    provider = FakeProvider([LLMError("unauthorized", "401")])
    advice = _suggest(provider)
    assert provider.calls == 1
    assert advice.fallback is True
    assert advice.error == "unauthorized"
    assert advice.attempts == 1


def test_retry_recovers_after_invalid_json() -> None:
    provider = FakeProvider(
        [
            LLMError("invalid_json", "bad"),
            {
                "hypotheses": [
                    {
                        "description": "重试后成功",
                        "failure_domain": "NETWORK",
                        "supporting_evidence_ids": ["evd_1"],
                    }
                ]
            },
        ]
    )
    advice = _suggest(provider)
    assert provider.calls == 2
    assert advice.fallback is False
    assert len(advice.candidates) == 1
    assert advice.attempts == 2


def test_fallback_does_not_prevent_second_call_from_producing_candidates() -> None:
    """调用失败后不得返回空列表冒充成功，必须显式 fallback。"""
    provider = FakeProvider([LLMError("rate_limited", "429")])
    advice = _suggest(provider)
    assert advice.called is True
    assert advice.fallback is True
    assert advice.candidates == []
    assert advice.error == "rate_limited"


def test_json_retries_are_not_delegated_to_provider_layer() -> None:
    """重试由本层控制，避免 provider 层再重试导致重复计费。"""
    provider = FakeProvider([{"hypotheses": []}])
    _suggest(provider)
    assert provider.last_kwargs.get("json_retries") == 0


def test_model_and_attempts_are_recorded() -> None:
    provider = FakeProvider([{"hypotheses": []}])
    advice = _suggest(provider)
    assert advice.model == "deepseek-flash"
    assert advice.attempts == 1
