"""RUN-008 WP1：长稳脚本守卫的自动化回归测试。

覆盖任务书 1.2 要求：`teamflow_ok` 不得恒真、`_get`/`_post` 异常分类、
SQLite 只读失败不得当作有效计数、采样文件续跑、验收条件无恒真项。
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import sqlite3
import urllib.error
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "soak_run007.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("soak_run007_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


soak = _load_module()


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"ok": True, "count": 7}, True),
        ({"ok": False, "count": 7}, False),
        ({"ok": True}, False),
        ({"__error__": "TimeoutError: timed out"}, False),
        (None, False),
        ([], False),
    ],
)
def test_teamflow_health_requires_real_ok_and_count(state: Any, expected: bool) -> None:
    result = soak._teamflow_health(state)
    assert result["ok"] is expected
    if not expected:
        assert result["error"]
        assert result["count"] is None


def test_get_returns_error_payload_on_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(soak.urllib.request, "urlopen", boom)
    result = soak._get("http://127.0.0.1:1/api/state/projects", timeout=1)
    assert soak._is_transport_error(result) is True
    assert "URLError" in str(result["__error__"])


def test_post_separates_http_error_from_business_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def http_500(*_args: Any, **_kwargs: Any) -> None:
        raise urllib.error.HTTPError("http://127.0.0.1:1/api", 500, "server error", {}, None)

    monkeypatch.setattr(soak.urllib.request, "urlopen", http_500)
    error = soak._post("http://127.0.0.1:1/api", {}, timeout=1)
    assert soak._is_transport_error(error) is True
    assert "HTTPError" in error["__error__"]

    monkeypatch.setattr(
        soak.urllib.request,
        "urlopen",
        lambda *_a, **_k: _Response(b'{"status":"FAILED","reason":"X"}'),
    )
    business = soak._post("http://127.0.0.1:1/api", {}, timeout=1)
    assert soak._is_transport_error(business) is False
    assert business["status"] == "FAILED"


def test_api_health_detects_transport_error_and_inline_worker() -> None:
    ok = soak._api_health({"status": "ok", "database": "ok", "worker": {"inline_running": False}})
    assert ok["ok"] is True
    assert ok["inline_running"] is False

    inline = soak._api_health({"status": "ok", "worker": {"inline_running": True}})
    assert inline["ok"] is False
    assert inline["inline_running"] is True

    broken = soak._api_health({"__error__": "TimeoutError: no response"})
    assert broken["ok"] is False
    assert broken["status"] is None


def test_db_snapshot_never_treats_read_error_as_zero(tmp_path: Path) -> None:
    bad_db = tmp_path / "bad.db"
    bad_db.write_text("not a sqlite database", encoding="utf-8")
    snap = soak._db_snapshot(bad_db)
    assert soak._has_db_errors(snap) is True
    assert any(str(v).startswith("ERR:") for v in snap.values())

    good_db = tmp_path / "api.db"
    with sqlite3.connect(good_db) as con:
        for table in ("tg_run", "tg_task", "tg_event", "tg_evidence", "tg_step", "tg_doc"):
            con.execute(f"CREATE TABLE {table} (id TEXT)")
    good = soak._db_snapshot(good_db)
    assert soak._has_db_errors(good) is False
    assert good["db_path"] == str(good_db)
    assert good["runs"] == 0


def _healthy_inputs(db_path: Path) -> dict[str, Any]:
    return {
        "duration_s": 3660.0,
        "minutes": 60.0,
        "cycle": 60,
        "target_cycles": 60,
        "counts": {"ok": 60, "failed": 0, "not_evaluated": 0, "api_error": 0, "timeout": 0},
        "errors": [],
        "worker_deaths": [],
        "integrity": {
            "runs_without_report": 0,
            "duplicate_event_keys": 0,
            "orphan_steps": 0,
            "runs_without_evidence": 0,
            "unrecovered_leases": 0,
            "quick_check": "ok",
            "db_path": str(db_path),
            "final_counts": {"runs": 60},
        },
        "sse_stats": {
            "cycles_with_events": 60,
            "cycles_without_events": 0,
            "resume_ok": 60,
            "resume_no_new": 0,
            "heartbeats": 100,
            "duplicate_ids": 0,
        },
        "api_alive": True,
        "api_health_failures": 0,
        "inline_worker_seen": 0,
        "metric_sources": {"ps"},
        "db_path": db_path,
    }


def test_build_checks_is_not_tautological(tmp_path: Path) -> None:
    healthy = _healthy_inputs(tmp_path / "api.db")
    checks = soak.build_checks(**healthy)
    assert all(checks.values()) is True

    broken = {**healthy, "api_health_failures": 1, "inline_worker_seen": 1}
    broken["errors"] = ["cycle 1: TeamFlow 状态接口异常"]
    broken["integrity"] = {
        **healthy["integrity"],
        "final_counts": {"runs": "ERR: unable to open database file"},
        "db_path": "/other/instance.db",
    }
    broken["metric_sources"] = {"unknown"}
    failed = soak.build_checks(**broken)
    assert failed["api_health_ok_all_cycles"] is False
    assert failed["api_inline_worker_off"] is False
    assert failed["teamflow_healthy_all_cycles"] is False
    assert failed["db_counts_readable"] is False
    assert failed["db_counts_from_current_instance"] is False
    assert failed["process_metrics_source_recorded"] is False


def test_samples_tail_resume_and_corruption(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"
    assert soak.read_samples_tail(missing) == (0, 0.0, 0)

    samples = tmp_path / "soak-samples.jsonl"
    samples.write_text(
        json.dumps({"cycle": 0, "since_start_s": 0.0})
        + "\n"
        + json.dumps({"cycle": 1, "since_start_s": 60.1})
        + "\n",
        encoding="utf-8",
    )
    assert soak.read_samples_tail(samples) == (2, 60.1, 2)

    samples.write_text(samples.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        soak.read_samples_tail(samples)


def test_no_constant_true_acceptance_conditions() -> None:
    source = inspect.getsource(soak.build_checks)
    assert '"api_inline_worker_off": True' not in source

    # 只看 AST 里的可执行表达式；文档字符串中提到旧版 `or True` 是必要的历史说明。
    tree = ast.parse(inspect.getsource(soak))
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            assert not any(
                isinstance(value, ast.Constant) and value.value is True for value in node.values
            ), ast.dump(node)
