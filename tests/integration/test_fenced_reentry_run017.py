"""RUN-017 WP4.1：fenced_session 重入防护（真实 PostgreSQL 16）。

背景（RUN-016 WP1 实测）：同一 Run 的 `fenced_session` 嵌套调用会自锁——
外层事务已经持有 run 行锁，内层"条件空更新"在等同一把锁，最终表现为
无限挂起（直到数据库/连接池超时）。产品路径目前没有这种嵌套，但开发者
误用一次就会挂死。

本文件验证修复后的契约：

1. 正常单次调用仍然可用；
2. 相同 Run 嵌套 → 立即抛 `FENCED_SESSION_REENTRY`（在打开事务之前）；
3. 不同 Run 嵌套 → 允许（两个独立事务，互不阻塞）；
4. 异常退出后上下文清理干净（不会误伤后续调用）；
5. 新线程不继承父线程的持有标记（线程隔离）；
6. 子进程内嵌套调用不会永久挂起（有超时保护，退出码可观测）。

环境：需要 PostgreSQL 16（`TG_TEST_POSTGRES_URL` 或本机 127.0.0.1:55432）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

PG_ADMIN = os.environ.get("TG_RUN015_PG_ADMIN", "postgresql://traceguard@127.0.0.1:55432/postgres")
PG_NAME = os.environ.get("TG_RUN017_PG_DB", "traceguard_run017_fence")
PG_URL = (
    os.environ.get("TG_TEST_POSTGRES_URL")
    or f"postgresql+psycopg://traceguard@127.0.0.1:55432/{PG_NAME}"
)
PG_DSN = PG_URL.replace("postgresql+psycopg://", "postgresql://")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _can_connect(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001 - 环境探测
        return False


pytestmark = pytest.mark.skipif(
    not _can_connect(PG_ADMIN if not os.environ.get("TG_TEST_POSTGRES_URL") else PG_DSN),
    reason="PostgreSQL 16 不可达（需 TG_TEST_POSTGRES_URL 或本机 55432）",
)


@pytest.fixture(scope="module")
def pg_db(tmp_path_factory):  # type: ignore[no-untyped-def]
    if not os.environ.get("TG_TEST_POSTGRES_URL"):
        with psycopg.connect(PG_ADMIN, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{PG_NAME}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{PG_NAME}"')
    os.environ["TG_DATABASE_URL"] = PG_URL
    os.environ["TG_ARTIFACT_DIR"] = str(tmp_path_factory.mktemp("run017-fence-artifacts"))
    os.environ["TG_LLM_PROVIDER"] = ""

    from traceguard_core.config import reset_settings_cache
    from traceguard_persistence import Database

    reset_settings_cache()
    db = Database(PG_URL)
    db.create_schema()
    yield db
    db.dispose()
    reset_settings_cache()


@pytest.fixture()
def rt(pg_db, monkeypatch):  # type: ignore[no-untyped-def]
    from traceguard_api.runtime import Runtime
    from traceguard_core.config import reset_settings_cache

    monkeypatch.setenv("TG_DATABASE_URL", PG_URL)
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    runtime = Runtime(pg_db)
    tc = f"tcase_r17r_{uuid.uuid4().hex[:8]}"
    runtime.catalog.create_testcase(
        {
            "testcase_id": tc,
            "version": 1,
            "workspace_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "steps": [{"step_id": "s1", "action": "navigate", "value": "/projects"}],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "标题存在",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "page-title"},
                }
            ],
            "compile_source": "human",
        },
        title="run017-reentry",
        review_status="APPROVED",
    )
    runtime._r17_tc = tc  # type: ignore[attr-defined]
    yield runtime
    reset_settings_cache()


def _run(rt, key: str, worker: str = "worker-A", ttl: float = 60.0):  # type: ignore[no-untyped-def]
    tc = rt._r17_tc  # type: ignore[attr-defined]
    task = rt.tasks.create_task(
        tc,
        f"r17-{key}-{uuid.uuid4().hex[:6]}",
        testcase_version_id=f"{tc}@1",
        workspace_id="default",
    )
    run_id = rt.tasks.create_run(task["task_id"], f"{tc}@1")["run_id"]
    lease = rt.tasks.acquire_lease(run_id, worker, ttl_seconds=ttl)
    return run_id, int(lease["fencing_token"])


def test_single_session_still_works(rt) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import func, select
    from traceguard_persistence.models import StepRow

    run_id, token = _run(rt, "single")
    with rt.tasks.fenced_session(run_id, "worker-A", token) as s:
        rt.tasks._record_step_in(s, run_id, "s1", 0, "PASSED", None, "navigate", 3, None)
    with rt.db.session() as s:
        count = int(
            s.scalar(select(func.count()).select_from(StepRow).where(StepRow.run_id == run_id))
        )
    assert count == 1


def test_same_run_nested_is_rejected_before_opening_transaction(rt) -> None:  # type: ignore[no-untyped-def]
    """嵌套必须在打开事务之前失败：耗时应远小于任何数据库锁等待。"""
    from traceguard_execution.tasks.service import FencedReentryError

    run_id, token = _run(rt, "nested")
    started = time.perf_counter()
    with rt.tasks.fenced_session(run_id, "worker-A", token):
        with pytest.raises(FencedReentryError) as excinfo:
            with rt.tasks.fenced_session(run_id, "worker-A", token):
                pass  # pragma: no cover - 不应执行到这里
    elapsed = time.perf_counter() - started
    assert excinfo.value.code.value == "CONFLICT"
    assert "FENCED_SESSION_REENTRY" in str(excinfo.value)
    assert elapsed < 2.0, f"重入检测耗时 {elapsed:.2f}s，说明仍在等数据库锁"


def test_different_run_nested_is_allowed(rt) -> None:  # type: ignore[no-untyped-def]
    run_a, token_a = _run(rt, "nest-a")
    run_b, token_b = _run(rt, "nest-b")
    with rt.tasks.fenced_session(run_a, "worker-A", token_a) as sa:
        rt.tasks._record_step_in(sa, run_a, "s1", 0, "PASSED", None, "navigate", 1, None)
        with rt.tasks.fenced_session(run_b, "worker-A", token_b) as sb:
            rt.tasks._record_step_in(sb, run_b, "s1", 0, "PASSED", None, "navigate", 1, None)


def test_context_is_cleaned_after_error(rt) -> None:  # type: ignore[no-untyped-def]
    from traceguard_execution.tasks.service import _FENCED_SCOPE

    run_id, token = _run(rt, "cleanup")
    with pytest.raises(ValueError):
        with rt.tasks.fenced_session(run_id, "worker-A", token):
            raise ValueError("boom")
    assert run_id not in _FENCED_SCOPE.get()
    # 同一个 Run 在异常退出后可以重新进入（例如 Worker 重试同一代次）
    with rt.tasks.fenced_session(run_id, "worker-A", token):
        pass


def test_new_thread_does_not_inherit_scope(rt) -> None:  # type: ignore[no-untyped-def]
    """父线程持有时，子线程开同一 Run 的 session 不应被误判为重入。

    真实含义：ContextVar 是"执行上下文"级而不是进程级；Worker 内部把写操作
    放到工作线程时，只要外层已退出就不会被永久标记。这里验证的是"不继承"，
    因此子线程要么拿到锁（父线程已提交）要么等锁返回失败，但绝不会因为
    上下文脏标记而立即报 FENCED_SESSION_REENTRY。
    """
    from traceguard_execution.tasks.service import FencedReentryError

    run_id, token = _run(rt, "thread")
    result: dict[str, object] = {}

    def worker() -> None:
        from traceguard_execution.tasks.service import _FENCED_SCOPE

        result["scope"] = _FENCED_SCOPE.get()
        try:
            with rt.tasks.fenced_session(run_id, "worker-A", token):
                result["entered"] = True
        except FencedReentryError as exc:  # pragma: no cover - 失败路径
            result["error"] = exc
        except Exception as exc:  # noqa: BLE001 - 记录任何异常用于断言
            result["other_error"] = exc

    with rt.tasks.fenced_session(run_id, "worker-A", token):
        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.3)  # 让子线程先进入等待（父事务尚未提交）
    thread.join(timeout=10)
    assert not thread.is_alive(), "子线程挂死：线程隔离未生效"
    assert result.get("scope") == frozenset(), "子线程不应继承父线程的持有标记"
    assert "error" not in result, f"子线程被误判为重入: {result.get('error')}"


def test_subprocess_nested_call_does_not_hang(rt) -> None:  # type: ignore[no-untyped-def]
    """真实子进程：嵌套调用必须在秒级退出，而不是挂到数据库超时。"""
    run_id, token = _run(rt, "subproc")
    code = (
        "import sys;"
        "sys.path[:0]=['packages/core','packages/contracts','packages/execution',"
        "'packages/agents','packages/evidence','packages/persistence','apps/api'];"
        "from traceguard_persistence import Database;"
        "from traceguard_execution.tasks.service import TaskService, FencedReentryError;"
        "db=Database(sys.argv[1]);"
        "svc=TaskService(db);"
        "entered=False;"
        "\ntry:\n"
        "    with svc.fenced_session(sys.argv[2], 'worker-A', int(sys.argv[3])):\n"
        "        with svc.fenced_session(sys.argv[2], 'worker-A', int(sys.argv[3])):\n"
        "            entered=True\n"
        "except FencedReentryError as exc:\n"
        "    print('REENTRY_REJECTED', flush=True); sys.exit(0)\n"
        "print('UNEXPECTED', entered, flush=True); sys.exit(3)\n"
    )
    started = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 - 受控本地子进程，无 shell
        [sys.executable, "-c", code, PG_URL, run_id, str(token)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    elapsed = time.perf_counter() - started
    assert proc.returncode == 0, f"rc={proc.returncode} out={proc.stdout} err={proc.stderr[-800:]}"
    assert "REENTRY_REJECTED" in proc.stdout
    assert elapsed < 20, f"子进程耗时 {elapsed:.1f}s，仍未及时失败"
