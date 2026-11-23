"""RUN-005 WP1.1：ProbeBudget 生命周期与并发隔离测试。

覆盖需求 4.1 的 9 条验收点：
- 每 Run / Diagnosis 独立预算与独立生命周期；
- 记录探针数量、累计成本、耗时、剩余预算；
- 并发诊断互不影响（含同一预算并发不超发）；
- 诊断完成后预算关闭，关闭后拒绝执行且原因明确；
- 预算使用量持久化与审计；
- 预算耗尽原因明确；
- 服务运行超过 60 秒不会让后续诊断探针失效；
- 重启后不同诊断预算不混用；
- 超时与异常事实保留。
"""

from __future__ import annotations

import threading

import pytest
from traceguard_evidence.probe.planner import (
    ProbeBudget,
    ProbeBudgetRegistry,
    ProbeDefinition,
    ProbeEngine,
    budget_doc_id,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.t = start
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.t

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.t += seconds


def _engine(budget: ProbeBudget | None = None) -> ProbeEngine:
    e = ProbeEngine(budget or ProbeBudget())
    e.grant_permission("probe.read")
    e.register(
        ProbeDefinition(
            "network_trace",
            "network_read",
            "probe.read",
            allowed_templates={"run_network"},
            estimated_cost=1.0,
        ),
        lambda p: {"entries": []},
    )
    e.register(
        ProbeDefinition(
            "slow_probe",
            "api_read",
            "probe.read",
            allowed_templates={"slow"},
            estimated_cost=1.0,
        ),
        lambda p: (_ for _ in ()).throw(TimeoutError("PROBE_TIMEOUT")),
    )
    e.register(
        ProbeDefinition(
            "broken_probe",
            "api_read",
            "probe.read",
            allowed_templates={"broken"},
            estimated_cost=1.0,
        ),
        lambda p: (_ for _ in ()).throw(RuntimeError("EXECUTOR_CRASH")),
    )
    return e


# --------------------------------------------------------------- 服务长期运行
def test_service_uptime_does_not_expire_new_run_budget() -> None:
    """服务已运行 10 分钟，新 Run 的预算仍从自己的首次使用开始计时。"""
    clock = FakeClock()
    registry = ProbeBudgetRegistry(
        max_probes=50, max_total_cost=100, max_duration_seconds=60, clock=clock
    )
    clock.advance(600)  # 进程启动后已过去 10 分钟
    budget = registry.get_or_create("run:run_new", run_id="run_new")
    assert budget.started_at == pytest.approx(clock())
    assert budget.time_left() == pytest.approx(60.0)
    assert budget.exhausted_reason() == ""


def test_old_run_budget_expires_independently() -> None:
    clock = FakeClock()
    registry = ProbeBudgetRegistry(max_duration_seconds=60, clock=clock)
    first = registry.get_or_create("run:run_old", run_id="run_old")
    clock.advance(90)
    second = registry.get_or_create("run:run_new", run_id="run_new")
    assert first.exhausted_reason() == "PROBE_TIME_BUDGET_EXHAUSTED"
    assert second.exhausted_reason() == ""
    assert second.time_left() == pytest.approx(60.0)


# ----------------------------------------------------------------- 计数隔离
def test_multiple_runs_have_independent_counters() -> None:
    registry = ProbeBudgetRegistry(max_probes=5, max_total_cost=100, clock=FakeClock())
    engine = _engine()
    b1 = registry.get_or_create("run:r1", run_id="r1")
    b2 = registry.get_or_create("run:r2", run_id="r2")
    for _ in range(3):
        engine.execute(
            "network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b1
        )
    assert b1.probes_used == 3
    assert b2.probes_used == 0
    r = engine.execute(
        "network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b2
    )
    assert r.status == "SUCCEEDED" and b2.probes_used == 1


def test_budget_exhaustion_by_count_has_explicit_reason() -> None:
    registry = ProbeBudgetRegistry(max_probes=2, max_total_cost=100, clock=FakeClock())
    engine = _engine()
    b = registry.get_or_create("run:r_limit", run_id="r_limit")
    for _ in range(2):
        assert (
            engine.execute(
                "network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b
            ).status
            == "SUCCEEDED"
        )
    blocked = engine.execute(
        "network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b
    )
    assert blocked.status == "BLOCKED"
    assert blocked.reason == "PROBE_COUNT_BUDGET_EXHAUSTED"
    assert b.exhausted_reason() == "PROBE_COUNT_BUDGET_EXHAUSTED"


def test_budget_exhaustion_by_cost() -> None:
    registry = ProbeBudgetRegistry(max_probes=10, max_total_cost=1.5, clock=FakeClock())
    engine = _engine()
    b = registry.get_or_create("run:r_cost", run_id="r_cost")
    engine.execute("network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b)
    blocked = engine.execute(
        "network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b
    )
    assert blocked.status == "BLOCKED" and blocked.reason == "PROBE_COST_BUDGET_EXHAUSTED"


def test_budget_exhaustion_by_time() -> None:
    clock = FakeClock()
    registry = ProbeBudgetRegistry(max_duration_seconds=5, clock=clock)
    engine = _engine()
    b = registry.get_or_create("run:r_time", run_id="r_time")
    clock.advance(6)
    blocked = engine.execute(
        "network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b
    )
    assert blocked.status == "BLOCKED" and blocked.reason == "PROBE_TIME_BUDGET_EXHAUSTED"


# ------------------------------------------------------------------ 并发
def test_concurrent_diagnoses_do_not_interfere() -> None:
    registry = ProbeBudgetRegistry(max_probes=100, max_total_cost=1000, clock=FakeClock())
    engine = _engine()
    budgets = [
        registry.get_or_create(f"diagnosis:d{i}", run_id="r", diagnosis_id=f"d{i}")
        for i in range(6)
    ]
    errors: list[BaseException] = []

    def worker(b: ProbeBudget) -> None:
        try:
            for _ in range(10):
                engine.execute(
                    "network_trace",
                    {"workspace": "ws", "template": "run_network"},
                    "ws",
                    budget=b,
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(b,)) for b in budgets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert [b.probes_used for b in budgets] == [10] * 6
    assert all(b.cost_used == pytest.approx(10.0) for b in budgets)


def test_same_budget_concurrent_calls_never_overspend() -> None:
    registry = ProbeBudgetRegistry(max_probes=10, max_total_cost=100, clock=FakeClock())
    engine = _engine()
    budget = registry.get_or_create("run:r_shared", run_id="r_shared")
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(5):
            r = engine.execute(
                "network_trace",
                {"workspace": "ws", "template": "run_network"},
                "ws",
                budget=budget,
            )
            with lock:
                results.append(r.status)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert budget.probes_used == 10  # 不超发
    assert results.count("SUCCEEDED") == 10
    assert results.count("BLOCKED") == 10


# ------------------------------------------------------------- 关闭与审计
def test_closed_budget_blocks_with_explicit_reason() -> None:
    registry = ProbeBudgetRegistry(clock=FakeClock())
    engine = _engine()
    b = registry.get_or_create("diagnosis:d_done", run_id="r", diagnosis_id="d_done")
    registry.close("diagnosis:d_done", "STOP_CONFIRMED")
    assert b.is_closed and b.close_reason == "STOP_CONFIRMED"
    r = engine.execute(
        "network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b
    )
    assert r.status == "BLOCKED" and r.reason == "PROBE_BUDGET_CLOSED"


def test_budget_snapshot_records_usage_and_remaining() -> None:
    clock = FakeClock()
    registry = ProbeBudgetRegistry(
        max_probes=5, max_total_cost=10, max_duration_seconds=60, clock=clock
    )
    engine = _engine()
    b = registry.get_or_create("run:r_snap", run_id="r_snap", workspace_id="ws1")
    engine.execute("network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b)
    clock.advance(12)
    snap = b.snapshot()
    assert snap["probes_used"] == 1 and snap["probes_left"] == 4
    assert snap["cost_used"] == pytest.approx(1.0) and snap["cost_left"] == pytest.approx(9.0)
    assert snap["elapsed_seconds"] == pytest.approx(12.0)
    assert snap["time_left"] == pytest.approx(48.0)
    assert snap["scope_key"] == "run:r_snap" and snap["workspace_id"] == "ws1"
    assert snap["provisional"] is True  # P06 未确认，必须标注


# ------------------------------------------------------------- 持久化/重启
def test_budget_usage_survives_restart_without_mixing_scopes() -> None:
    saved: dict[str, dict] = {}

    def saver(b: ProbeBudget) -> None:
        saved[b.budget_id] = b.to_dict()

    clock = FakeClock()
    reg1 = ProbeBudgetRegistry(clock=clock, saver=saver)
    engine = _engine()
    b1 = reg1.get_or_create("run:r_persist", run_id="r_persist")
    engine.execute("network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b1)
    reg1.save(b1)  # 服务层每次执行后持久化（ProbeService.execute 的职责）

    # 进程重启：新注册表 + 持久化恢复
    reg2 = ProbeBudgetRegistry(
        clock=clock, loader=lambda k: saved.get(budget_doc_id(k)), saver=saver
    )
    restored = reg2.get_or_create("run:r_persist", run_id="r_persist")
    assert restored.probes_used == 1 and restored.cost_used == pytest.approx(1.0)

    other = reg2.get_or_create("run:r_other", run_id="r_other")
    assert other.probes_used == 0  # 不混为一谈


def test_closed_budget_stays_closed_after_restart() -> None:
    """关闭是显式状态：重启后仍为 CLOSED，不会被静默重置成新窗口。"""
    saved: dict[str, dict] = {}

    def saver(b: ProbeBudget) -> None:
        saved[b.budget_id] = b.to_dict()

    clock = FakeClock()
    reg1 = ProbeBudgetRegistry(clock=clock, saver=saver)
    b = reg1.get_or_create("diagnosis:d_closed", run_id="r", diagnosis_id="d_closed")
    b.close("STOP_CONFIRMED")
    saver(b)

    reg2 = ProbeBudgetRegistry(
        clock=clock, loader=lambda k: saved.get(budget_doc_id(k)), saver=saver
    )
    restored = reg2.get_or_create("diagnosis:d_closed", run_id="r", diagnosis_id="d_closed")
    assert restored.is_closed is True
    assert restored.close_reason == "STOP_CONFIRMED"
    assert restored.exhausted_reason() == "PROBE_BUDGET_CLOSED"


def test_persisted_budget_from_other_owner_is_rejected() -> None:
    """scope 相同但 owner 不一致（人为篡改/错配）时必须拒绝恢复。"""
    doc = {
        "scope_key": "run:r_owner",
        "owner_run_id": "r_someone_else",
        "probes_used": 9,
        "max_probes": 50,
        "max_total_cost": 100,
        "max_duration_seconds": 60,
    }
    reg = ProbeBudgetRegistry(clock=FakeClock(), loader=lambda k: doc)
    budget = reg.get_or_create("run:r_owner", run_id="r_owner")
    assert budget.probes_used == 0


# ---------------------------------------------------------------- 异常事实
def test_timeout_and_failure_are_preserved_and_counted() -> None:
    registry = ProbeBudgetRegistry(clock=FakeClock())
    engine = _engine()
    b = registry.get_or_create("run:r_fail", run_id="r_fail")
    timed = engine.execute("slow_probe", {"workspace": "ws", "template": "slow"}, "ws", budget=b)
    failed = engine.execute(
        "broken_probe", {"workspace": "ws", "template": "broken"}, "ws", budget=b
    )
    assert timed.status == "TIMED_OUT" and "PROBE_TIMEOUT" in timed.reason
    assert failed.status == "FAILED" and "EXECUTOR_CRASH" in failed.reason
    assert b.probes_used == 2  # 失败尝试同样计入预算，避免无限重试
    audit = engine.audit_log()
    assert {a["decision"] for a in audit} >= {"TIMEOUT", "EXEC_ERROR"}


def test_audit_log_records_budget_scope() -> None:
    registry = ProbeBudgetRegistry(clock=FakeClock())
    engine = _engine()
    b = registry.get_or_create("run:r_audit", run_id="r_audit")
    engine.execute("network_trace", {"workspace": "ws", "template": "run_network"}, "ws", budget=b)
    assert engine.audit_log()[-1]["budget_scope"] == "run:r_audit"
