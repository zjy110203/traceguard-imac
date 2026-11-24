"""RUN-007-FINALIZATION-2（WP1-A）：Benchmark Seed 幂等性与基线恢复。

覆盖：
1. 第一次 Seed 建立的版本；
2. 第二次 Seed 不产生新版本（此前动态时间戳会让版本无限增长）；
3. 已存在版本不重复创建；
4. Repair 改了定位器后再次 Seed：通过"新版本 + 人工审核"恢复原始基线，
   已存在的不可变版本内容不得被修改；
5. 多次 Seed 后最新正式版本仍正确。
"""

from __future__ import annotations

import uuid

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.seed import _seed_bench_v2, seed_teamflow_catalog
from traceguard_persistence import Database

BENCH_ID = "tcase_bench_loc002"


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    base = work_tmp / f"seed-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'seed.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _versions(rt: Runtime, testcase_id: str) -> list[dict]:
    return rt.catalog.list_versions(testcase_id)


def _signature(doc: dict) -> tuple:
    return (
        tuple((s.get("action"), str(s.get("value"))) for s in doc.get("steps") or []),
        tuple((a["assertion_id"], a.get("oracle_id")) for a in doc.get("assertions") or []),
        dict(doc["steps"][3].get("target") or {}).get("value"),
    )


def test_seed_is_idempotent(rt: Runtime) -> None:
    seed_teamflow_catalog(rt)
    first = {tc["testcase_id"]: _versions(rt, tc["testcase_id"]) for tc in _bench_ids(rt)}
    assert first, "Seed 没有建立任何 Benchmark 用例"

    seed_teamflow_catalog(rt)
    second = {tc["testcase_id"]: _versions(rt, tc["testcase_id"]) for tc in _bench_ids(rt)}

    assert {k: len(v) for k, v in first.items()} == {k: len(v) for k, v in second.items()}, (
        "重复 Seed 产生了新的用例版本（版本漂移未修复）"
    )
    for tc_id, versions in second.items():
        assert len(versions) == 1, f"{tc_id} 存在多余版本：{[v['version'] for v in versions]}"
        assert versions[0]["review_status"] == "APPROVED"


def _bench_ids(rt: Runtime) -> list[dict]:
    return [
        tc for tc in rt.catalog.list_testcases() if tc["testcase_id"].startswith("tcase_bench_")
    ]


def test_seed_restores_baseline_after_repair(rt: Runtime) -> None:
    seed_teamflow_catalog(rt)
    baseline = rt.catalog.latest_approved_version(BENCH_ID)
    assert baseline is not None
    original_target = dict(baseline["steps"][3]["target"])
    assert original_target["value"] == "project-name"
    baseline_sig = _signature(baseline)

    # 模拟 Repair 晋升：把名称输入框定位器改成"已修复"的 role 定位器
    healed = dict(baseline)
    healed["steps"] = [dict(s) for s in baseline["steps"]]
    healed["steps"][3]["target"] = {
        "strategy": "role",
        "value": "textbox:项目名称",
        "source": "healed",
    }
    created = rt.catalog.promote_version_cas(
        BENCH_ID, base_version=1, candidate=healed, title="healed"
    )
    assert created["version"] == 2
    immutables_before = {v["version"]: dict(v["doc"]) for v in _versions(rt, BENCH_ID)}

    # 再次 Seed：必须以新版本恢复原始基线（不是改历史版本）
    _seed_bench_v2(rt, "http://127.0.0.1:8001")
    versions = _versions(rt, BENCH_ID)
    assert [v["version"] for v in versions] == [3, 2, 1]
    latest = rt.catalog.latest_approved_version(BENCH_ID)
    assert latest["version"] == 3
    assert latest["steps"][3]["target"] == original_target
    assert _signature(latest) == baseline_sig

    # 历史不可变版本内容未被修改
    for v in versions:
        if v["version"] in immutables_before:
            assert dict(v["doc"]) == immutables_before[v["version"]], (
                f"v{v['version']} 的内容被就地修改，违反不可变契约"
            )

    # 再 Seed 一次：基线已一致，不再新增版本
    _seed_bench_v2(rt, "http://127.0.0.1:8001")
    assert [v["version"] for v in _versions(rt, BENCH_ID)] == [3, 2, 1]
    assert rt.catalog.latest_approved_version(BENCH_ID)["version"] == 3
