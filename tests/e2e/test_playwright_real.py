"""真实浏览器 E2E：启动 TeamFlow → 执行已审核用例 → 校验 Verdict / 证据 / 产物。

如果本机没有可启动的浏览器，测试 skip 并给出原因（不伪造通过）。
运行：.venv/bin/python -m pytest tests/e2e/test_playwright_real.py -q
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.request

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.seed import seed_teamflow_catalog
from traceguard_persistence import Database

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(url: str, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return False


@pytest.fixture(scope="module", autouse=True)
def _close_browser_after_module():
    """Playwright sync API 会在主线程留一个事件循环；模块结束后必须释放，
    否则同进程后续测试的 asyncio.run() 会失败。"""
    yield
    from traceguard_execution.browser.manager import close_current_thread_browser

    close_current_thread_browser()


@pytest.fixture(scope="module")
def teamflow_server():  # type: ignore[no-untyped-def]
    import uvicorn
    from traceguard_teamflow.main import create_app

    port = _free_port()
    config = uvicorn.Config(
        create_app(), host="127.0.0.1", port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    if not _wait_health(base):
        server.should_exit = True
        pytest.skip("TeamFlow 未能启动，跳过真实浏览器 E2E")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser_available():  # type: ignore[no-untyped-def]
    from traceguard_execution.browser.manager import BrowserUnavailableError, probe_browser

    try:
        info = probe_browser()
    except BrowserUnavailableError as exc:
        pytest.skip(f"本机浏览器不可用：{exc}")
    if not info.get("smoke"):
        pytest.skip("浏览器启动但自检失败")
    return info


@pytest.fixture(scope="module")
def rt(work_tmp):  # type: ignore[no-untyped-def]

    db = Database(f"sqlite:///{work_tmp / 'e2e-{uuid.uuid4().hex[:6]}.db'}")
    db.create_schema()
    runtime = Runtime(db)
    yield runtime
    runtime.stop_inline_worker()
    db.dispose()


def _execute(rt: Runtime, testcase_id: str) -> dict:  # type: ignore[no-untyped-def]
    task = rt.tasks.create_task(
        testcase_id,
        f"e2e-{testcase_id}-{time.time_ns()}",
        testcase_version_id=f"{testcase_id}@1",
        workspace_id="default",
    )
    run = rt.tasks.create_run(task["task_id"], f"{testcase_id}@1")
    return rt.execute_run(run["run_id"])


def test_real_browser_happy_path(rt, teamflow_server, browser_available) -> None:  # type: ignore[no-untyped-def]
    seed_teamflow_catalog(rt, base_url=teamflow_server)
    out = _execute(rt, "tcase_tf_create_normal")

    assert out["observer_mode"] == "playwright"
    assert out["execution_status"] == "COMPLETED"
    assert out["test_verdict"] == "PASSED", out["report"].get("assertion_outcomes")

    report = out["report"]
    assert report["browser"]["channel"]
    assert len(report["steps"]) == 8
    assert all(s["status"] == "PASSED" for s in report["steps"])

    # 证据：截图 / DOM / ARIA 均落盘，且截图是真实 PNG
    evds = rt.evidence.list(run_id=out["run_id"], limit=200)
    assert len(evds) >= 8
    shots = [e for e in evds if (e.content.get("artifact") or {}).get("media_type") == "image/png"]
    assert shots, "缺少截图证据"
    data, meta = rt.artifacts.read(shots[0].attachment_ref)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert meta["size"] > 5000

    # 步骤观测包含真实页面 URL 与控制台/网络采集
    first = report["steps"][0]["observation"]
    assert first["url"].endswith("/projects")

    # 事件链完整
    events = rt.events.list_events(run_id=out["run_id"], limit=200)
    types = {e.event_type for e in events}
    assert {"run.started", "step.completed", "run.completed"} <= types
    assert rt.events.conflicts() == []


def test_false_pass_is_detected(rt, teamflow_server, browser_available) -> None:  # type: ignore[no-untyped-def]
    """核心价值验证：UI 显示成功但业务未持久化 → 必须判 FAILED（不是 PASSED）。"""
    out = _execute(rt, "tcase_tf_false_pass")
    assert out["observer_mode"] == "playwright"
    assert out["test_verdict"] == "FAILED"
    outcomes = out["report"]["assertion_outcomes"]
    biz = [o for o in outcomes if o["assertion_id"] == "a_biz_count"]
    assert biz and biz[0]["status"] == "FAILED"
    assert "got=0" in biz[0]["reason"] or "count" in biz[0]["reason"]


def test_http_fallback_mode_is_labelled(rt, teamflow_server) -> None:  # type: ignore[no-untyped-def]
    """降级模式必须显式标记为 http-fallback，不能伪装真实浏览器。"""
    task = rt.tasks.create_task(
        "tcase_tf_create_normal",
        f"e2e-http-{time.time_ns()}",
        mode="http",
        testcase_version_id="tcase_tf_create_normal@1",
        workspace_id="default",
    )
    run = rt.tasks.create_run(task["task_id"], "tcase_tf_create_normal@1")
    out = rt.execute_run(run["run_id"])
    assert out["observer_mode"] == "http-fallback"
    assert out["report"]["observer_mode"] == "http-fallback"
