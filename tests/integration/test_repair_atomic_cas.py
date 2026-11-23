"""RUN-006 WP1-04：Self-Healing 晋升/回滚的原子 CAS 集成测试。

重点验证"比较 + 写入必须在同一事务"：
- 两个基于同一 base_version 的并发晋升只允许一个成功，另一个 VERSION_CONFLICT；
- 回滚必须校验"当前正式版本仍是本提案晋升的版本"；
- 幂等键重放不产生额外版本；
- 沙箱版本不改变正式（APPROVED）版本状态；
- 晋升不改变断言与 Oracle 绑定。
"""

from __future__ import annotations

import threading

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.repair_service import RepairService
from traceguard_core.errors import AppError, ErrorCode
from traceguard_persistence import Database


def _payload() -> dict:
    return {
        "testcase_id": "tcase_cas",
        "version": 1,
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/projects"},
            {
                "step_id": "s2",
                "action": "click",
                "target": {"strategy": "test_id", "value": "nav-new"},
            },
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "标题存在",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "page-title"},
                "oracle_id": "oracle_cas_a1",
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"cas-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'cas.db'}")
    runtime = Runtime(db)
    runtime.catalog.create_oracle(
        {
            "oracle_id": "oracle_cas_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-CAS-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    runtime.catalog.create_testcase(_payload(), title="cas", review_status="PENDING")
    runtime.catalog.review_testcase("tcase_cas", 1, "APPROVED", reviewer="role:qa-lead")
    yield runtime
    db.dispose()


def _candidate(rt: Runtime, version: int, locator: str) -> dict:
    doc = dict(rt.catalog.get_version("tcase_cas", version) or {})
    doc["steps"] = [dict(s) for s in doc["steps"]]
    doc["steps"][1]["target"] = {"strategy": "test_id", "value": locator, "source": "healed"}
    return doc


# ------------------------------------------------------ 目录层原子 CAS 语义
def test_promote_version_cas_rejects_stale_base(rt: Runtime) -> None:
    created = rt.catalog.promote_version_cas(
        "tcase_cas", base_version=1, candidate=_candidate(rt, 1, "nav-new-2"), title="healed"
    )
    assert created["version"] == 2
    with pytest.raises(AppError) as exc:
        rt.catalog.promote_version_cas(
            "tcase_cas", base_version=1, candidate=_candidate(rt, 1, "nav-new-3")
        )
    assert exc.value.code is ErrorCode.CONFLICT
    assert rt.catalog.approved_version_number("tcase_cas") == 2


def test_concurrent_promotion_only_one_succeeds(rt: Runtime) -> None:
    results: list[str] = []
    lock = threading.Lock()

    def worker(locator: str) -> None:
        try:
            rt.catalog.promote_version_cas(
                "tcase_cas",
                base_version=1,
                candidate=_candidate(rt, 1, locator),
                title="healed",
            )
            with lock:
                results.append("OK")
        except AppError as exc:
            with lock:
                results.append(exc.code.value)

    threads = [threading.Thread(target=worker, args=(f"nav-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("OK") == 1
    assert results.count("CONFLICT") == 1
    versions = {v["version"] for v in rt.catalog.list_versions("tcase_cas")}
    assert versions == {1, 2}  # 只多出一个正式版本，没有互相覆盖


def test_promotion_preserves_assertions_and_oracle(rt: Runtime) -> None:
    before = rt.catalog.latest_approved_version("tcase_cas")
    created = rt.catalog.promote_version_cas(
        "tcase_cas", base_version=1, candidate=_candidate(rt, 1, "nav-new-2")
    )
    assert created["assertions"] == before["assertions"]
    assert created["oracle_bindings"] == before["oracle_bindings"]
    assert created["oracle_binding_frozen"] is True


# ------------------------------------------------- 服务层：晋升 / 回滚 / 幂等
def _approved_proposal(rt: Runtime, *, base_version: int = 1, locator: str = "nav-v2") -> str:
    svc = RepairService(rt)
    prop = svc.propose(
        testcase_id="tcase_cas",
        base_version=base_version,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": locator},
            }
        ],
        reason="locator 失效修复",
    )
    # 跳过真实沙箱执行（本测试关注 CAS 层），显式构造"已审批待晋升"状态
    doc = svc.get(prop["proposal_id"])
    doc["status"] = "APPROVED"
    doc["sandbox_status"] = "PASSED"
    doc["approved_by"] = "role:qa-lead"
    rt.docs.put(
        doc_id=doc["proposal_id"],
        kind="repair",
        doc=doc,
        workspace_id=doc["workspace_id"],
        ref_id="tcase_cas",
        status="APPROVED",
    )
    return doc["proposal_id"]


def test_service_promotion_is_atomic_and_idempotent(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    promoted = svc.promote(pid, idempotency_key="idem-1")
    assert promoted["status"] == "PROMOTED"
    assert promoted["promotion_record"]["atomic"] is True
    assert promoted["promoted_version"] == 2

    replay = svc.promote(pid, idempotency_key="idem-1")
    assert replay["promoted_version"] == 2  # 幂等重放不产生新版本
    assert rt.catalog.approved_version_number("tcase_cas") == 2


def test_service_promotion_conflict_does_not_overwrite(rt: Runtime) -> None:
    svc = RepairService(rt)
    first = _approved_proposal(rt, locator="nav-a")
    second = _approved_proposal(rt, locator="nav-b")
    svc.promote(first, idempotency_key="idem-a")
    stale = svc.promote(second, idempotency_key="idem-b")
    assert stale["status"] == "PROMOTION_FAILED"
    assert stale["failure_reason"] == "VERSION_CONFLICT"
    assert stale["promotion_record"]["current_version"] == 2
    doc = rt.catalog.latest_approved_version("tcase_cas")
    assert doc["steps"][1]["target"]["value"] == "nav-a"  # 新版本没有被旧提案覆盖


def test_rollback_requires_current_promoted_version(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    svc.promote(pid, idempotency_key="idem-r")
    rolled = svc.rollback(pid, operator="human:ops", reason="回归失败")
    assert rolled["status"] == "ROLLED_BACK"
    assert rolled["rollback_record"]["target_version"] == "tcase_cas:v2"
    # 回滚产生新的正式版本（不删除已发布版本）
    assert rt.catalog.approved_version_number("tcase_cas") == 3
    assert rt.catalog.get_version("tcase_cas", 2)["version"] == 2


def test_rollback_blocked_when_others_promoted(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt, locator="nav-a")
    svc.promote(pid, idempotency_key="idem-a")
    # 其他提案基于 v2 继续晋升到 v3
    other = _approved_proposal(rt, base_version=2, locator="nav-b")
    svc.promote(other, idempotency_key="idem-b")
    with pytest.raises(AppError) as exc:
        svc.rollback(pid, operator="human:ops", reason="试图覆盖后来发布的版本")
    assert exc.value.code is ErrorCode.CONFLICT
    assert "ROLLBACK_CONFLICT" in exc.value.message


def test_rollback_rejects_agent_operator(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    svc.promote(pid, idempotency_key="idem-r")
    with pytest.raises(AppError) as exc:
        svc.rollback(pid, operator="agent:repair", reason="x")
    assert exc.value.code is ErrorCode.AUTHORIZATION_ERROR


def test_sandbox_version_does_not_become_formal(rt: Runtime) -> None:
    """沙箱版本（PENDING）不改变正式版本状态与断言绑定。"""
    before = rt.catalog.latest_approved_version("tcase_cas")
    sandbox = dict(before)
    sandbox["run_config"] = {**(sandbox.get("run_config") or {}), "sandbox_only": True}
    created = rt.catalog.create_testcase(
        sandbox, title="sandbox", workspace_id="default", review_status="PENDING"
    )
    assert created["review_status"] == "PENDING"
    assert rt.catalog.approved_version_number("tcase_cas") == 1
    assert rt.catalog.latest_approved_version("tcase_cas")["assertions"] == before["assertions"]
