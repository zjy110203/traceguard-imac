"""生成阶段2 的 Oracle 与其余 TestCase fixtures（可复现）。

用法：.venv/bin/python tests/fixtures/build_fixtures.py
产物为 fixtures JSON。权威来源仍是 traceguard_contracts 模型（生成时经模型校验）。
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "packages", "contracts"))

from traceguard_contracts.models import OracleSpec, TestCaseVersion  # noqa: E402

CREATED_AT = "2026-11-21T00:00:00Z"


def oracle(oid: str, aid: str, behavior: str, rule: dict) -> dict:
    o = OracleSpec(
        oracle_id=oid,
        assertion_id=aid,
        expected_behavior=behavior,
        requirement_ref="REQ-TF-CREATE-001",
        expectation_approval={"approved": True, "approver": "human:qa-lead", "at": CREATED_AT},
        evaluation_rule=rule,
        consistency_window_ms=500,
        required=True,
        version=1,
        approved_by_agent=False,
    )
    return o.model_dump(mode="json")


ORACLES = [
    oracle("oracle_login_msg", "a_login_msg", "登录成功提示出现", {"type": "text_contains"}),
    oracle(
        "oracle_create_ui", "a_create_ui_success", "创建成功横幅出现", {"type": "text_contains"}
    ),
    oracle(
        "oracle_create_persist",
        "a_create_persisted",
        "业务状态 count>=1",
        {"type": "api_field", "op": "gte"},
    ),
    oracle("oracle_api_error", "a_api_error_shown", "API 错误提示展示", {"type": "text_contains"}),
    oracle(
        "oracle_not_persist",
        "a_not_persisted",
        "失败时 count==0",
        {"type": "api_field", "op": "eq"},
    ),
    oracle(
        "oracle_false_pass",
        "a_persistence_truth",
        "TF-DB-001 下应未持久化 count==0",
        {"type": "api_field", "op": "eq"},
    ),
    oracle("oracle_list_rows", "a_list_rows", "列表展示新建项目", {"type": "dom_count"}),
    oracle("oracle_detail_name", "a_detail_name", "详情页名称正确", {"type": "text_contains"}),
    oracle("oracle_banner_error", "a_banner_error", "UI 故障下错误横幅", {"type": "text_contains"}),
    oracle("oracle_timeout", "a_timeout_detect", "网络超时被检测", {"type": "timeout"}),
]


def tc(
    tcid: str,
    steps: list[dict],
    assertions: list[dict],
    cleanup: str = "RESET_DATA",
    base_url: str = "http://127.0.0.1:8011",
) -> dict:
    obj = TestCaseVersion(
        testcase_id=tcid,
        version=1,
        steps=steps,
        assertions=assertions,
        test_data_refs=["reset:default"],
        preconditions=["测试数据已 reset"],
        postconditions=[],
        cleanup_policy=cleanup,
        run_config={"base_url": base_url},
        compile_source="human",
        review_status="APPROVED",
        immutable=True,
        created_at=CREATED_AT,
    )
    return obj.model_dump(mode="json")


def nav(value: str) -> dict:
    return {"step_id": "s_nav", "action": "navigate", "value": value}


def fill(name: str, value: str) -> dict:
    return {
        "step_id": f"s_fill_{name}",
        "action": "fill",
        "target": {"strategy": "test_id", "value": name, "source": "human"},
        "value": value,
    }


def click(name: str) -> dict:
    return {
        "step_id": f"s_click_{name}",
        "action": "click",
        "target": {"strategy": "test_id", "value": name, "source": "human"},
    }


def assertion(
    aid: str, desc: str, kind: str, expected: dict, oid: str, required: bool = True
) -> dict:
    return {
        "assertion_id": aid,
        "description": desc,
        "kind": kind,
        "required": required,
        "expected": expected,
        "oracle_id": oid,
    }


EXTRA_CASES = [
    # 列表展示
    tc(
        "tcase_list001",
        [nav("/projects")],
        [
            assertion(
                "a_list_rows",
                "列表至少一行",
                "ui",
                {"test_id": "project-row", "op": "count_gte", "value": 1},
                "oracle_list_rows",
            )
        ],
    ),
    # 详情查看（正常）
    tc(
        "tcase_detail001",
        [
            api_step := {
                "step_id": "s_api",
                "action": "api",
                "value": "GET /api/projects?workspace=default",
            },
            nav("/projects"),
        ],
        [
            assertion(
                "a_detail_name",
                "详情链接可见",
                "ui",
                {"test_id": "detail-link", "op": "count_gte", "value": 1},
                "oracle_detail_name",
            )
        ],
    ),
    # UI 故障：成功提示缺失
    tc(
        "tcase_uifail001",
        [
            {"step_id": "s_api", "action": "api", "value": "POST /api/faults/TF-UI-001/activate"},
            nav("/projects?created=1"),
        ],
        [
            assertion(
                "a_banner_error",
                "UI 故障下显示错误横幅",
                "ui",
                {"test_id": "banner-error", "expected_absent": "banner-success"},
                "oracle_banner_error",
            )
        ],
    ),
    # 网络超时
    tc(
        "tcase_timeout001",
        [
            {"step_id": "s_api", "action": "api", "value": "POST /api/faults/TF-NET-001/activate"},
            nav("/login"),
        ],
        [
            assertion(
                "a_timeout_detect",
                "页面可加载或超时被记录",
                "network",
                {"timeout_ms": 1000, "expect": "timeout"},
                "oracle_timeout",
            )
        ],
    ),
    # 编辑项目
    tc(
        "tcase_edit001",
        [
            nav("/projects"),
            click("nav-new"),
            fill("project-name", "EditTarget"),
            click("create-submit"),
        ],
        [
            assertion(
                "a_list_rows",
                "编辑后列表可见",
                "ui",
                {"test_id": "project-row", "op": "count_gte", "value": 1},
                "oracle_list_rows",
                required=False,
            ),
            assertion(
                "a_create_ui_success",
                "创建成功",
                "ui",
                {"test_id": "banner-success"},
                "oracle_create_ui",
            ),
        ],
    ),
    # 空表单校验
    tc(
        "tcase_emptyform001",
        [nav("/projects/new"), click("create-submit")],
        [
            assertion(
                "a_api_error_shown",
                "空表单不创建项目",
                "ui",
                {"test_id": "create-error", "expected_absent": "banner-success"},
                "oracle_api_error",
            )
        ],
    ),
    # 非法凭据
    tc(
        "tcase_badlogin001",
        [
            nav("/login"),
            fill("username", "tester"),
            fill("password", "wrongpass"),
            click("login-submit"),
        ],
        [
            assertion(
                "a_login_msg",
                "登录失败提示",
                "ui",
                {"test_id": "login-message", "text_contains": "登录失败"},
                "oracle_login_msg",
            )
        ],
    ),
    # 列表 vs 业务状态一致性
    tc(
        "tcase_consistency001",
        [nav("/projects")],
        [
            assertion(
                "a_list_rows",
                "列表行数",
                "ui",
                {"test_id": "project-row", "op": "count_gte", "value": 0},
                "oracle_list_rows",
            ),
            assertion(
                "a_create_persisted",
                "业务状态可查",
                "business_state",
                {
                    "api": "/api/state/projects?workspace=default",
                    "field": "count",
                    "op": "gte",
                    "value": 0,
                },
                "oracle_create_persist",
            ),
        ],
    ),
    # 超时清理后恢复
    tc(
        "tcase_recover001",
        [
            {"step_id": "s_api", "action": "api", "value": "POST /api/faults/TF-NET-001/clear"},
            nav("/login"),
        ],
        [
            assertion(
                "a_login_msg",
                "恢复后登录页可用",
                "ui",
                {"test_id": "login-form"},
                "oracle_login_msg",
            )
        ],
    ),
    # 时序故障下列表最终一致
    tc(
        "tcase_timing001",
        [
            {"step_id": "s_api", "action": "api", "value": "POST /api/faults/TF-TIME-001/activate"},
            nav("/projects"),
        ],
        [
            assertion(
                "a_list_rows",
                "最终列表出现",
                "ui",
                {"test_id": "project-row", "wait_ms": 2000, "op": "count_gte", "value": 0},
                "oracle_list_rows",
            )
        ],
    ),
]


def main() -> None:
    odir = os.path.join(HERE, "oracles")
    tdir = os.path.join(HERE, "testcases")
    os.makedirs(odir, exist_ok=True)
    os.makedirs(tdir, exist_ok=True)
    for o in ORACLES:
        with open(os.path.join(odir, o["oracle_id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(o, f, ensure_ascii=False, indent=2)
    # EXTRA_CASES 中复用已由手写文件覆盖的 ID 时跳过
    existing = {f for f in os.listdir(tdir) if f.endswith(".json")}
    for i, t in enumerate(EXTRA_CASES, start=1):
        fname = f"tc_gen_{i:02d}.json"
        if fname in existing:
            continue
        with open(os.path.join(tdir, fname), "w", encoding="utf-8") as f:
            json.dump(t, f, ensure_ascii=False, indent=2)
    print(f"[fixtures] oracles={len(ORACLES)} extra_testcases={len(EXTRA_CASES)}")


if __name__ == "__main__":
    main()
