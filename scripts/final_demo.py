"""阶段13 最终演示：十个场景的真实执行证据。

需先启动 TeamFlow(:8011)。脚本对每个场景执行真实操作并输出结构化结果到
tests/reports/final_demo_evidence.json。所有结果来自真实运行，非 Mock。

十个场景：
1. 正常创建项目
2. UI 成功但持久化失败
3. API 500
4. 网络超时
5. Locator 变化
6. 异步时序异常
7. 浏览器环境不可用
8. 多源证据冲突
9. Probe 越权请求
10. 测试脚本修复
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [
    str(ROOT / "apps" / "api"), str(ROOT / "packages" / "core"),
    str(ROOT / "apps" / "teamflow"), str(ROOT / "packages" / "contracts"),
    str(ROOT / "packages" / "execution"), str(ROOT / "packages" / "evidence"),
    str(ROOT / "packages" / "agents"), str(ROOT / "benchmark"),
]

BASE = "http://127.0.0.1:8011"


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, {"raw": raw[:200]}
    except urllib.error.HTTPError as e:
        return e.code, {"detail": e.read().decode()[:200]}


def _reset():
    _req("POST", "/api/test/reset", {})


def scenario_1_normal_create() -> dict:
    _reset()
    status, created = _req("POST", "/api/projects", {"name": "Demo-Normal", "workspace_slug": "default"})
    _, state = _req("GET", "/api/state/projects?workspace=default")
    return {"scenario": "1_正常创建项目", "api_status": status,
            "persisted": created.get("persisted"), "state_count": state.get("count"),
            "verdict": "PASS" if status == 200 and state.get("count") == 1 else "FAIL"}


def scenario_2_ui_success_db_fail() -> dict:
    _reset()
    _req("POST", "/api/faults/TF-DB-001/activate", {})
    status, created = _req("POST", "/api/projects", {"name": "Demo-Ghost", "workspace_slug": "default"})
    _, state = _req("GET", "/api/state/projects?workspace=default")
    _reset()
    detected = created.get("persisted") is False and state.get("count") == 0
    return {"scenario": "2_UI成功但持久化失败", "ui_ok": created.get("ok"),
            "api_persisted_flag": created.get("persisted"), "authoritative_count": state.get("count"),
            "false_pass_detected": detected,
            "verdict": "PASS" if detected else "FAIL"}


def scenario_3_api_500() -> dict:
    _reset()
    _req("POST", "/api/faults/TF-API-001/activate", {})
    status, resp = _req("POST", "/api/projects", {"name": "Demo-500", "workspace_slug": "default"})
    _, state = _req("GET", "/api/state/projects?workspace=default")
    _reset()
    return {"scenario": "3_API500", "api_status": status, "state_count": state.get("count"),
            "verdict": "PASS" if status == 500 and state.get("count") == 0 else "FAIL"}


def scenario_4_network_timeout() -> dict:
    _reset()
    _req("POST", "/api/faults/TF-NET-001/activate", {"delay_ms": 1200})
    t0 = time.time()
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + "/api/auth/login",
            data=json.dumps({"username": "tester", "password": "tester123"}).encode(),
            headers={"Content-Type": "application/json"}), timeout=0.4)
        timed_out = False
    except (urllib.error.URLError, TimeoutError):
        timed_out = True
    elapsed = time.time() - t0
    _reset()
    return {"scenario": "4_网络超时", "timed_out": timed_out, "elapsed_sec": round(elapsed, 2),
            "verdict": "PASS" if timed_out else "FAIL"}


def scenario_5_locator_change() -> dict:
    """Locator 变化：验证 Compiler 的 Locator Resolver 与 healing 更新。"""
    from traceguard_agents.compiler.compiler import TestCaseCompiler as Compiler

    c = Compiler()
    old = c._resolve_locator({"test_id": "old-submit"})
    healed = c._resolve_locator({"test_id": "new-submit", "css": "#submit"})
    return {"scenario": "5_Locator变化", "old_locator": old.value if old else None,
            "healed_locator": healed.value if healed else None,
            "verdict": "PASS" if old and healed and old.value != healed.value else "FAIL"}


def scenario_6_timing() -> dict:
    _reset()
    _req("POST", "/api/faults/TF-TIME-001/activate", {"delay_ms": 1000})
    t0 = time.time()
    _req("GET", "/api/projects?workspace=default")
    elapsed = time.time() - t0
    _reset()
    return {"scenario": "6_异步时序异常", "delay_ms": 1000, "elapsed_sec": round(elapsed, 2),
            "verdict": "PASS" if elapsed >= 0.9 else "FAIL"}


def scenario_7_browser_unavailable() -> dict:
    """浏览器环境不可用：PlaywrightObserver 应明确报错，不静默降级。"""
    try:
        from traceguard_execution.runtime.playwright_observer import PlaywrightObserver

        obs = PlaywrightObserver()
        try:
            obs.setup({"base_url": BASE})
            result = "UNEXPECTED_BROWSER_AVAILABLE"
        except Exception as e:  # noqa: BLE001
            result = f"BROWSER_UNAVAILABLE_REPORTED: {type(e).__name__}"
        finally:
            try:
                obs.teardown()
            except Exception:  # noqa: BLE001
                pass
    except ImportError:
        result = "PLAYWRIGHT_NOT_INSTALLED_REPORTED"
    return {"scenario": "7_浏览器环境不可用", "behavior": result,
            "verdict": "PASS" if ("REPORTED" in result or "UNAVAILABLE" in result) else "FAIL"}


def scenario_8_multi_source_conflict() -> dict:
    from traceguard_evidence.graph.graph import EvidenceGraph

    g = EvidenceGraph()
    h = g.add_node("Hypothesis", "hyp_conflict")
    e = g.add_node("Evidence", "evd_conflict")
    g.add_support(h.node_id, e.node_id, "ws")
    g.add_contradict(h.node_id, e.node_id, "ws")
    conflicts = g.conflicts(h.node_id)
    return {"scenario": "8_多源证据冲突", "conflicts": conflicts,
            "verdict": "PASS" if conflicts and conflicts[0]["status"] == "CONFLICT_UNRESOLVED" else "FAIL"}


def scenario_9_probe_overreach() -> dict:
    from traceguard_evidence.probe.planner import ProbeDefinition, ProbeEngine

    eng = ProbeEngine()
    eng.grant_permission("probe.read")
    eng.register(ProbeDefinition("db", "database_read", "probe.read",
                                 allowed_tables={"tf_project"}), lambda p: {})
    r = eng.execute("db", {"workspace": "ws", "query": "DROP TABLE tf_project"}, "ws")
    return {"scenario": "9_Probe越权请求", "status": r.status, "reason": r.reason,
            "verdict": "PASS" if r.status == "BLOCKED" and r.reason == "WRITE_SQL_FORBIDDEN" else "FAIL"}


def scenario_10_script_repair() -> dict:
    from traceguard_agents.healing.service import SelfHealingService
    from traceguard_contracts.models import AssertionSpec, LocatorSpec, TestCaseVersion as TCVersion, TestStep

    svc = SelfHealingService()
    base = TCVersion(
        testcase_id="tcase_demo10", version=1,
        steps=[TestStep(step_id="s1", action="click",
                        target=LocatorSpec(strategy="test_id", value="old", source="human"))],
        assertions=[AssertionSpec(assertion_id="a1", description="x", kind="ui", required=True,
                                  expected={"test_id": "b", "text_contains": "ok"}, oracle_id="o1")],
        immutable=True, review_status="APPROVED", created_at="2026-11-21T00:00:00Z",
    )
    svc.register_official(base)
    cand = base.model_copy(update={"version": 2, "steps": [base.steps[0].model_copy(update={
        "target": LocatorSpec(strategy="test_id", value="new", source="healed")
    })]})
    prop = svc.propose(base, cand, [{"path": "steps[].target", "from": "old", "to": "new"}])
    svc.validate(prop.proposal_id)
    svc.submit_for_approval(prop.proposal_id)
    svc.approve(prop.proposal_id, "human:qa")
    svc.promote(prop.proposal_id, idempotency_key="demo10")
    return {"scenario": "10_测试脚本修复", "assertion_preserved": prop.assertion_preservation,
            "status": prop.status.value, "official_version": svc.official_version("tcase_demo10"),
            "verdict": "PASS" if prop.assertion_preservation == "PASS" and prop.status.value == "PROMOTED" else "FAIL"}


SCENARIOS = [
    scenario_1_normal_create, scenario_2_ui_success_db_fail, scenario_3_api_500,
    scenario_4_network_timeout, scenario_5_locator_change, scenario_6_timing,
    scenario_7_browser_unavailable, scenario_8_multi_source_conflict,
    scenario_9_probe_overreach, scenario_10_script_repair,
]


def main() -> None:
    results = []
    for fn in SCENARIOS:
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001
            results.append({"scenario": fn.__name__, "verdict": "ERROR", "error": str(e)[:200]})
        print(f"[{results[-1]['verdict']}] {results[-1]['scenario']}")

    out = ROOT / "tests" / "reports" / "final_demo_evidence.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(1 for r in results if r.get("verdict") == "PASS")
    print(f"\n结果：{passed}/{len(results)} 场景 PASS；证据：{out}")


if __name__ == "__main__":
    main()
