"""Explorer / Compiler 服务：TestIntent → 真实浏览器探索轨迹 → 候选用例（待审核）。

边界：
- Explorer 只能调用 ToolGateway 白名单工具（navigate/click/fill/read_page）；
- 页面内容是不可信数据，不提升权限；
- 探索成功 ≠ 测试通过：轨迹不产生 verdict；
- Compiler 产出的是 PENDING 侯选用例，必须人工审核，不能自动成为正式用例；
- 所有动作与轨迹落库，可复现。
"""

from __future__ import annotations

import re
from typing import Any

from traceguard_agents.compiler.compiler import TestCaseCompiler
from traceguard_agents.explorer.workflow import ExplorationTrace, Explorer
from traceguard_agents.gateway.model_gateway import ModelDecision, RuleBasedPlanner
from traceguard_agents.gateway.tool_gateway import Budget, ToolGateway
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import utc_now_iso
from traceguard_core.logging import get_logger
from traceguard_execution.browser.manager import get_browser_manager
from traceguard_execution.browser.session import BrowserSession

from traceguard_api.runtime import Runtime

logger = get_logger("traceguard.service.exploration")

MAX_STEPS = 10


class LocalPlanner(RuleBasedPlanner):
    """把规则 planner 的目标地址重写到配置的 TeamFlow 地址（其余逻辑不变）。"""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def plan(
        self, intent: str, observation: dict[str, Any], history: list[ModelDecision]
    ) -> ModelDecision:
        decision = super().plan(intent, observation, history)
        url = decision.params.get("url")
        if isinstance(url, str) and url.startswith("http"):
            # 只替换 host:port，保留 path
            path = re.sub(r"^https?://[^/]+", "", url)
            decision.params["url"] = self.base_url + (path or "/")
        return decision


class ExplorationService:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.compiler = TestCaseCompiler()

    def explore(self, intent_id: str, max_steps: int = MAX_STEPS) -> dict[str, Any]:
        intent = self.rt.catalog.get_intent(intent_id)
        if not intent:
            raise AppError(ErrorCode.NOT_FOUND, "intent not found")
        base = self.rt.settings.teamflow_base_url
        port = int(base.rsplit(":", 1)[-1]) if ":" in base else 80

        session = BrowserSession(
            get_browser_manager(), base_url=base, run_id=f"explore-{intent_id}", trace_enabled=False
        )
        gateway = ToolGateway(
            allowed_hosts={"127.0.0.1", "localhost"},
            allowed_ports={port},
            budget=Budget(
                max_steps=max_steps, max_tool_calls=max_steps * 2, max_duration_seconds=90
            ),
        )
        page = session.page

        def _navigate(params: dict[str, Any]) -> str:
            page.goto(str(params.get("url")), wait_until="domcontentloaded")
            session.wait_network_settle()
            return page.url

        def _fill(params: dict[str, Any]) -> str:
            tid = params.get("test_id")
            if not tid:
                raise ValueError("fill requires test_id")
            page.get_by_test_id(str(tid)).fill(str(params.get("value", "")))
            return "filled"

        def _click(params: dict[str, Any]) -> str:
            tid = params.get("test_id")
            if not tid:
                raise ValueError("click requires test_id")
            page.get_by_test_id(str(tid)).click()
            session.wait_network_settle()
            return page.url

        def _read_page(params: dict[str, Any]) -> str:
            return page.locator("body").inner_text()[:2000]

        gateway.register_executor("navigate", _navigate)
        gateway.register_executor("fill", _fill)
        gateway.register_executor("click", _click)
        gateway.register_executor("read_page", _read_page)

        def observer(_params: dict[str, Any]) -> dict[str, Any]:
            try:
                return {
                    "url": page.url,
                    "title": page.title(),
                    "text": page.locator("body").inner_text()[:1000],
                }
            except Exception:  # noqa: BLE001
                return {"url": "about:blank", "title": "", "text": ""}

        status = "COMPLETED"
        error = ""
        try:
            explorer = Explorer(gateway, LocalPlanner(base), observer)
            text = f"{intent.get('title', '')} {intent.get('description', '')} {intent_id}"
            trace = explorer.explore(text, max_steps=max_steps)
        except Exception as exc:  # noqa: BLE001
            status, error = "FAILED", f"{type(exc).__name__}: {exc}"
            trace = ExplorationTrace(
                exploration_id=f"exploration_failed_{intent_id}",
                test_intent_ref=intent_id,
                application_version="teamflow-1.0.0",
                browser_version="playwright",
                agent_version="explorer-v1",
                prompt_version="rule-based-v1",
                tool_version="tool-gateway-v1",
                stop_reason="ERROR",
            )
        finally:
            session.close()

        doc = {
            "exploration_id": trace.exploration_id,
            "intent_id": intent_id,
            "workspace_id": intent.get("workspace_id", "default"),
            "status": status,
            "error": error,
            "trace": trace.to_dict(),
            "gateway_audit": gateway.audit_log() if hasattr(gateway, "audit_log") else [],
            "action_count": len(trace.action_sequence),
            "stop_reason": trace.stop_reason,
            "created_at": utc_now_iso(),
        }
        self.rt.docs.put(
            doc_id=trace.exploration_id,
            kind="exploration",
            doc=doc,
            workspace_id=doc["workspace_id"],
            ref_id=intent_id,
            status=status,
        )
        return doc

    def compile(self, exploration_id: str) -> dict[str, Any]:
        rec = self.rt.docs.get(exploration_id)
        if not rec or rec.kind != "exploration":
            raise AppError(ErrorCode.NOT_FOUND, "exploration not found")
        doc = dict(rec.doc)
        trace = doc.get("trace") or {}
        if not trace.get("action_sequence"):
            raise AppError(ErrorCode.PRECONDITION_FAILED, "探索轨迹为空，无法编译")
        intent = self.rt.catalog.get_intent(doc["intent_id"]) or {}
        intent_text = f"{intent.get('title', '')} {intent.get('description', '')}"
        result = self.compiler.compile(trace, intent_text)
        candidate = result.candidate.model_dump()
        candidate["review_status"] = "PENDING"
        candidate["run_config"] = {
            **(candidate.get("run_config") or {}),
            "base_url": self.rt.settings.teamflow_base_url,
            "fail_fast": False,
            "trace": True,
        }
        created = self.rt.catalog.create_testcase(
            candidate,
            title=f"探索生成：{intent.get('title', doc['intent_id'])}",
            workspace_id=doc["workspace_id"],
            review_status="PENDING",
        )
        doc["compiled"] = {
            "testcase_id": created["testcase_id"],
            "version": created["version"],
            "warnings": result.warnings,
            "compiler_version": result.compiler_version,
            "prompt_version": result.prompt_version,
            "model_version": result.model_version,
        }
        doc["status"] = "COMPILED"
        self.rt.docs.put(
            doc_id=exploration_id,
            kind="exploration",
            doc=doc,
            workspace_id=doc["workspace_id"],
            ref_id=doc["intent_id"],
            status="COMPILED",
        )
        return doc

    def list(self, workspace_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        recs = self.rt.docs.list(kind="exploration", workspace_id=workspace_id, limit=limit)
        return [
            {
                "exploration_id": r.doc.get("exploration_id"),
                "intent_id": r.doc.get("intent_id"),
                "status": r.status,
                "action_count": r.doc.get("action_count", 0),
                "stop_reason": r.doc.get("stop_reason", ""),
                "compiled": r.doc.get("compiled"),
                "created_at": r.doc.get("created_at"),
            }
            for r in recs
        ]

    def get(self, exploration_id: str) -> dict[str, Any] | None:
        rec = self.rt.docs.get(exploration_id)
        return rec.to_dict() if rec and rec.kind == "exploration" else None
