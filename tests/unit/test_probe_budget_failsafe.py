"""RUN-006 WP1-03：ProbeBudget 故障安全与跨进程原子性测试。

覆盖：
1. 读取失败不得新建预算继续执行（fail closed）；
2. 写入失败不得放行探针（拒绝执行 + 计数回滚）；
3. 跨进程（多 Registry 实例共享同一持久化文档）扣减具备 CAS 原子性，不超发、不丢更新；
4. 重启恢复保留已用次数/成本/起始时间；
5. 已关闭预算不能被"再请求一次"绕过。
"""

from __future__ import annotations

import threading

import pytest
from traceguard_core.errors import AppError, ErrorCode
from traceguard_evidence.probe.planner import (
    BudgetUnavailable,
    ProbeBudget,
    ProbeBudgetRegistry,
    ProbeDefinition,
    ProbeEngine,
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


class VersionedStore:
    """模拟 DocStore 的版本 CAS 语义（只在版本匹配时允许更新）。"""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.versions: dict[str, int] = {}
        self.conflicts = 0

    def loader(self, scope_key: str) -> dict | None:
        doc = self.docs.get(scope_key)
        return dict(doc) if doc else None

    def saver(self, budget: ProbeBudget) -> None:
        current = self.versions.get(budget.scope_key, 0)
        if current == 0:
            self.versions[budget.scope_key] = 1
            budget.doc_version = 1
            self.docs[budget.scope_key] = {
                **budget.snapshot(),
                "doc_version": 1,
            }
            return
        if budget.doc_version != current:
            self.conflicts += 1
            raise AppError(ErrorCode.CONFLICT, "doc version CAS failed")
        self.versions[budget.scope_key] = current + 1
        budget.doc_version = current + 1
        self.docs[budget.scope_key] = {**budget.snapshot(), "doc_version": current + 1}


def _engine() -> ProbeEngine:
    engine = ProbeEngine()
    engine.grant_permission("probe.read")
    engine.register(
        ProbeDefinition(
            "network_trace",
            "network_read",
            "probe.read",
            allowed_templates={"run_network"},
            estimated_cost=1.0,
        ),
        lambda p: {"entries": []},
    )
    return engine


def _registry(store: VersionedStore, **kw) -> ProbeBudgetRegistry:  # type: ignore[no-untyped-def]
    return ProbeBudgetRegistry(
        clock=FakeClock(),
        loader=store.loader,
        saver=store.saver,
        **kw,
    )


# ------------------------------------------------------------------ 读取失败
def test_load_failure_raises_instead_of_creating_fresh_budget() -> None:
    def broken_loader(scope: str) -> dict | None:
        raise RuntimeError("database is locked")

    registry = ProbeBudgetRegistry(loader=broken_loader, saver=lambda b: None)
    with pytest.raises(BudgetUnavailable) as exc:
        registry.get_or_create("run:r_down", run_id="r_down")
    assert exc.value.code is ErrorCode.DEPENDENCY_ERROR
    assert exc.value.message == "PROBE_BUDGET_UNAVAILABLE"


def test_load_failure_is_logged_and_blocks_probe_not_silently_reset() -> None:
    """读取失败后绝不能凭空得到一份"全新配额"并执行探针。"""
    store = VersionedStore()
    registry = _registry(store)

    def broken_loader(scope: str) -> dict | None:
        raise RuntimeError("db unavailable")

    registry.loader = broken_loader
    with pytest.raises(BudgetUnavailable):
        registry.get_or_create("run:r_down2", run_id="r_down2")
    assert registry.get("run:r_down2") is None  # 没有创建内存预算


# ------------------------------------------------------------------ 写入失败
def test_save_failure_during_creation_raises() -> None:
    def broken_saver(budget: ProbeBudget) -> None:
        raise AppError(ErrorCode.DEPENDENCY_ERROR, "db write failed")

    registry = ProbeBudgetRegistry(loader=lambda s: None, saver=broken_saver)
    with pytest.raises(BudgetUnavailable):
        registry.get_or_create("run:r_nosave", run_id="r_nosave")


def test_reserve_write_failure_blocks_probe_and_rolls_back_count() -> None:
    store = VersionedStore()
    registry = _registry(store)
    budget = registry.get_or_create("run:r_write", run_id="r_write")
    engine = _engine()
    used_before = budget.probes_used

    def broken_saver(b: ProbeBudget) -> None:
        raise AppError(ErrorCode.DEPENDENCY_ERROR, "disk full")

    registry.saver = broken_saver
    result = engine.execute(
        "network_trace",
        {"workspace": "ws", "template": "run_network"},
        "ws",
        budget=budget,
        reserve=registry.reserve,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "PROBE_BUDGET_UNAVAILABLE"
    assert budget.probes_used == used_before  # 没有记账（先写库后计入）
    assert any(a["decision"] == "BUDGET_UNAVAILABLE" for a in engine.audit_log())


# --------------------------------------------------------------- 跨进程 CAS
def test_two_processes_share_one_budget_without_overspend() -> None:
    """两个进程各自持有同一 scope 的预算对象：合计不能超过 max_probes。"""
    store = VersionedStore()
    proc_a = _registry(store, max_probes=3, max_total_cost=100)
    budget_a = proc_a.get_or_create("run:r_mp", run_id="r_mp")
    proc_b = _registry(store, max_probes=3, max_total_cost=100)
    budget_b = proc_b.get_or_create("run:r_mp", run_id="r_mp")
    assert budget_a.doc_version == budget_b.doc_version == 1

    engine = _engine()
    results: list[str] = []
    lock = threading.Lock()

    def worker(reg: ProbeBudgetRegistry, budget: ProbeBudget) -> None:
        for _ in range(3):
            r = engine.execute(
                "network_trace",
                {"workspace": "ws", "template": "run_network"},
                "ws",
                budget=budget,
                reserve=reg.reserve,
            )
            with lock:
                results.append(r.status)

    threads = [
        threading.Thread(target=worker, args=(proc_a, budget_a)),
        threading.Thread(target=worker, args=(proc_b, budget_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    persisted = store.docs["run:r_mp"]
    assert persisted["probes_used"] == 3  # 合计只花掉 3 次
    assert results.count("SUCCEEDED") == 3
    assert results.count("BLOCKED") == 3
    assert store.conflicts >= 1  # 确实发生了 CAS 冲突并被正确重试


def test_cas_conflict_reloads_latest_values() -> None:
    store = VersionedStore()
    proc_a = _registry(store, max_probes=5)
    budget_a = proc_a.get_or_create("run:r_cas", run_id="r_cas")
    proc_a.reserve(budget_a, 1.0)

    proc_b = _registry(store, max_probes=5)
    budget_b = proc_b.get_or_create("run:r_cas", run_id="r_cas")
    assert budget_b.probes_used == 1  # 创建时已恢复 A 的用量

    reason = proc_b.reserve(budget_b, 1.0)
    assert reason == ""
    assert budget_b.probes_used == 2
    assert store.docs["run:r_cas"]["probes_used"] == 2


# -------------------------------------------------------------- 重启与关闭
def test_restart_preserves_usage_cost_and_start_time() -> None:
    store = VersionedStore()
    clock = FakeClock()
    reg1 = ProbeBudgetRegistry(clock=clock, loader=store.loader, saver=store.saver)
    budget = reg1.get_or_create("run:r_restart", run_id="r_restart")
    engine = _engine()
    engine.execute(
        "network_trace",
        {"workspace": "ws", "template": "run_network"},
        "ws",
        budget=budget,
        reserve=reg1.reserve,
    )
    started_at = budget.started_at

    clock.advance(5)
    reg2 = ProbeBudgetRegistry(clock=clock, loader=store.loader, saver=store.saver)
    restored = reg2.get_or_create("run:r_restart", run_id="r_restart")
    assert restored.probes_used == 1
    assert restored.cost_used == pytest.approx(1.0)
    assert restored.started_at == pytest.approx(started_at)


def test_closed_budget_cannot_be_bypassed_by_new_process() -> None:
    store = VersionedStore()
    reg1 = _registry(store)
    reg1.get_or_create("diagnosis:d_done", run_id="r", diagnosis_id="d_done")
    reg1.close("diagnosis:d_done", "STOP_CONFIRMED")

    reg2 = _registry(store)
    restored = reg2.get_or_create("diagnosis:d_done", run_id="r", diagnosis_id="d_done")
    assert restored.is_closed is True
    assert restored.exhausted_reason() == "PROBE_BUDGET_CLOSED"
    reason = reg2.reserve(restored, 1.0)
    assert reason == "PROBE_BUDGET_CLOSED"


def test_provisional_values_preserved_after_cas_roundtrip() -> None:
    store = VersionedStore()
    reg = _registry(store)
    budget = reg.get_or_create("run:r_prov", run_id="r_prov")
    reg.reserve(budget, 1.0)
    doc = store.docs["run:r_prov"]
    assert doc["provisional"] is True
    assert doc["max_probes"] == 50  # P06 待确认的 provisional 数值保持原样
    assert "P06" in doc["note"]
