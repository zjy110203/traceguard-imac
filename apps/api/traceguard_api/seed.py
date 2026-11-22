"""TeamFlow 演示资产种子：让平台启动后即可用真实用例跑通闭环（幂等）。

每次写入都是"可执行的真实资产"：已审核用例 + 已审批 Oracle，不做 Mock。
重复调用不会产生重复数据（按 testcase_id 检查）。
"""

from __future__ import annotations

from typing import Any

from traceguard_core.ids import utc_now_iso
from traceguard_core.logging import get_logger

logger = get_logger("traceguard.seed")

BASE = "http://127.0.0.1:8001"


def _step(
    step_id: str, action: str, target: tuple[str, str] | None, value: str | None, desc: str
) -> dict:
    return {
        "step_id": step_id,
        "action": action,
        "target": {"strategy": target[0], "value": target[1]} if target else None,
        "value": value,
        "description": desc,
    }


def _assertion(assertion_id: str, desc: str, kind: str, expected: dict, oracle_id: str) -> dict:
    return {
        "assertion_id": assertion_id,
        "description": desc,
        "kind": kind,
        "required": True,
        "expected": expected,
        "oracle_id": oracle_id,
    }


def _oracle(
    oracle_id: str,
    assertion_id: str,
    expected_behavior: str,
    requirement_ref: str,
    rule: dict[str, Any],
) -> dict:
    return {
        "oracle_id": oracle_id,
        "assertion_id": assertion_id,
        "expected_behavior": expected_behavior,
        "requirement_ref": requirement_ref,
        "expectation_approval": {"source": "requirements/TF-PROJECT-CREATE.md"},
        "evaluation_rule": rule,
        "consistency_window_ms": 0,
        "required": True,
        "version": 1,
    }


def _create_project_steps(prefix: str, name_value: str) -> list[dict]:
    return [
        _step(f"{prefix}_s1", "navigate", None, "/projects", "打开项目列表"),
        _step(f"{prefix}_s2", "click", ("test_id", "nav-new"), None, "点击新建项目"),
        _step(f"{prefix}_s3", "fill", ("test_id", "project-name"), name_value, "填写项目名称"),
        _step(
            f"{prefix}_s4",
            "fill",
            ("test_id", "project-description"),
            "TraceGuard 自动化创建",
            "填写描述",
        ),
        _step(f"{prefix}_s5", "click", ("test_id", "create-submit"), None, "提交创建"),
        _step(f"{prefix}_s6", "wait", None, "1200", "等待跳转与数据刷新"),
        _step(
            f"{prefix}_s7",
            "assert",
            ("test_id", "banner-success"),
            "banner-success",
            "读取成功提示",
        ),
        _step(
            f"{prefix}_s8", "assert", ("test_id", "project-rows"), "project-rows", "读取项目列表"
        ),
    ]


def seed_teamflow_catalog(rt: Any, base_url: str = BASE) -> dict[str, Any]:
    """写入 TeamFlow 场景资产。返回 created/skipped 计数。"""
    created, skipped = 0, 0

    intents = [
        {
            "intent_id": "intent_tf_create_normal",
            "title": "正常创建项目并验证持久化",
            "description": "用户在项目列表点击新建，填写名称后提交，应看到成功提示且数据真实持久化。",
            "requirement_refs": ["TF-PROJECT-CREATE"],
            "business_context": {"application": "teamflow", "workspace": "default"},
            "preconditions": ["已登录（当前版本登录为非强制）"],
            "candidate_assertions": ["成功提示可见", "项目数 +1"],
            "owner": "qa-team",
        },
        {
            "intent_id": "intent_tf_false_pass",
            "title": "UI 成功但持久化失败（False Pass 场景）",
            "description": "接口返回成功并跳转，但后端事务被回滚，页面显示成功而数据未落库。",
            "requirement_refs": ["TF-PROJECT-CREATE"],
            "business_context": {"application": "teamflow", "fault": "TF-DB-001"},
            "preconditions": ["故障 TF-DB-001 激活"],
            "candidate_assertions": ["UI 成功提示", "业务状态必须含新增项目"],
            "owner": "qa-team",
        },
        {
            "intent_id": "intent_tf_ui_render",
            "title": "持久化成功但成功提示缺失",
            "description": "数据已落库，但页面未渲染成功提示，用户无法确认操作结果。",
            "requirement_refs": ["TF-PROJECT-CREATE"],
            "business_context": {"application": "teamflow", "fault": "TF-UI-001"},
            "preconditions": ["故障 TF-UI-001 激活"],
            "candidate_assertions": ["成功提示可见"],
            "owner": "qa-team",
        },
        {
            "intent_id": "intent_tf_api_error",
            "title": "创建接口 500 时页面给出错误提示",
            "description": "后端接口异常时，前端必须展示可理解的错误信息，而不是静默失败。",
            "requirement_refs": ["TF-PROJECT-CREATE"],
            "business_context": {"application": "teamflow", "fault": "TF-API-001"},
            "preconditions": ["故障 TF-API-001 激活"],
            "candidate_assertions": ["错误提示可见", "项目数不变"],
            "owner": "qa-team",
        },
    ]
    for it in intents:
        if rt.catalog.get_intent(it["intent_id"]):
            skipped += 1
            continue
        rt.catalog.create_intent(it)
        rt.catalog.update_intent_status(it["intent_id"], "APPROVED")
        created += 1

    oracles = [
        _oracle(
            "oracle_tf_ui_banner",
            "a_ui_banner",
            "创建成功后必须出现成功提示（data-testid=banner-success）",
            "TF-PROJECT-CREATE#UI-1",
            {"type": "presence", "test_id": "banner-success"},
        ),
        _oracle(
            "oracle_tf_biz_count",
            "a_biz_count",
            "创建成功后独立业务状态查询的项目数必须增加（>=1）",
            "TF-PROJECT-CREATE#BIZ-1",
            {
                "type": "state_count",
                "path": "/api/state/projects?workspace=default",
                "op": "gte",
                "value": 1,
            },
        ),
        _oracle(
            "oracle_tf_error_banner",
            "a_error_banner",
            "接口失败时必须出现错误提示（data-testid=create-error）",
            "TF-PROJECT-CREATE#ERR-1",
            {"type": "presence", "test_id": "create-error"},
        ),
        _oracle(
            "oracle_tf_biz_unchanged",
            "a_biz_unchanged",
            "接口失败时不得产生新的项目数据（count == 0）",
            "TF-PROJECT-CREATE#ERR-2",
            {
                "type": "state_count",
                "path": "/api/state/projects?workspace=default",
                "op": "eq",
                "value": 0,
            },
        ),
        _oracle(
            "oracle_tf_row_render",
            "a_row_render",
            "项目列表必须渲染出数据行（>=1）",
            "TF-PROJECT-CREATE#UI-2",
            {"type": "count", "op": "count_gte", "value": 1},
        ),
    ]
    for o in oracles:
        if rt.catalog.get_oracle(o["oracle_id"]):
            skipped += 1
            continue
        rt.catalog.create_oracle(
            o, approved_by="role:qa-lead", approval_note="需求评审通过", approved=True
        )
        created += 1

    testcases = [
        {
            "testcase_id": "tcase_tf_create_normal",
            "title": "正常创建项目（UI + 业务状态）",
            "intent_ref": "intent_tf_create_normal",
            "steps": _create_project_steps("normal", f"TraceGuard 正常流-{utc_now_iso()[:19]}"),
            "assertions": [
                _assertion(
                    "a_ui_banner",
                    "成功提示出现",
                    "ui",
                    {"test_id": "banner-success"},
                    "oracle_tf_ui_banner",
                ),
                _assertion(
                    "a_biz_count",
                    "项目数增加",
                    "business_state",
                    {
                        "api": "/api/state/projects?workspace=default",
                        "field": "count",
                        "op": "gte",
                        "value": 1,
                    },
                    "oracle_tf_biz_count",
                ),
                _assertion(
                    "a_row_render",
                    "列表渲染数据行",
                    "ui",
                    {"test_id": "project-rows", "op": "count_gte", "value": 1},
                    "oracle_tf_row_render",
                ),
            ],
        },
        {
            "testcase_id": "tcase_tf_false_pass",
            "title": "False Pass 检测：UI 成功但数据未持久化",
            "intent_ref": "intent_tf_false_pass",
            "steps": [
                _step(
                    "fp_s1", "api", None, "POST /api/faults/TF-DB-001/activate", "激活持久化故障"
                ),
                *_create_project_steps("fp", f"TraceGuard 假通过-{utc_now_iso()[:19]}"),
            ],
            "assertions": [
                _assertion(
                    "a_biz_count",
                    "业务状态必须有新增项目（识破假通过）",
                    "business_state",
                    {
                        "api": "/api/state/projects?workspace=default",
                        "field": "count",
                        "op": "gte",
                        "value": 1,
                    },
                    "oracle_tf_biz_count",
                ),
            ],
        },
        {
            "testcase_id": "tcase_tf_ui_missing",
            "title": "UI 提示缺失检测",
            "intent_ref": "intent_tf_ui_render",
            "steps": [
                _step("ui_s1", "api", None, "POST /api/faults/TF-UI-001/activate", "激活 UI 故障"),
                *_create_project_steps("ui", f"TraceGuard 提示缺失-{utc_now_iso()[:19]}"),
            ],
            "assertions": [
                _assertion(
                    "a_ui_banner",
                    "成功提示必须出现",
                    "ui",
                    {"test_id": "banner-success"},
                    "oracle_tf_ui_banner",
                ),
                _assertion(
                    "a_biz_count",
                    "数据仍应落库",
                    "business_state",
                    {
                        "api": "/api/state/projects?workspace=default",
                        "field": "count",
                        "op": "gte",
                        "value": 1,
                    },
                    "oracle_tf_biz_count",
                ),
            ],
        },
        {
            "testcase_id": "tcase_tf_api_error",
            "title": "创建接口 500 的错误提示",
            "intent_ref": "intent_tf_api_error",
            "steps": [
                _step(
                    "api500_s1",
                    "api",
                    None,
                    "POST /api/faults/TF-API-001/activate",
                    "激活 API 故障",
                ),
                _step("api500_s2", "navigate", None, "/projects/new", "打开新建页"),
                _step(
                    "api500_s3",
                    "fill",
                    ("test_id", "project-name"),
                    f"TraceGuard 接口失败-{utc_now_iso()[:19]}",
                    "填写名称",
                ),
                _step("api500_s4", "click", ("test_id", "create-submit"), None, "提交（预期 500）"),
                _step("api500_s5", "wait", None, "800", "等待错误提示"),
                _step(
                    "api500_s6",
                    "assert",
                    ("test_id", "create-error"),
                    "create-error",
                    "读取错误提示",
                ),
            ],
            "assertions": [
                _assertion(
                    "a_error_banner",
                    "错误提示可见",
                    "ui",
                    {"test_id": "create-error"},
                    "oracle_tf_error_banner",
                ),
                _assertion(
                    "a_biz_unchanged",
                    "失败不得产生数据",
                    "business_state",
                    {
                        "api": "/api/state/projects?workspace=default",
                        "field": "count",
                        "op": "eq",
                        "value": 0,
                    },
                    "oracle_tf_biz_unchanged",
                ),
            ],
        },
    ]

    for tc in testcases:
        if rt.catalog.get_testcase(tc["testcase_id"]):
            skipped += 1
            continue
        payload = {
            **{k: v for k, v in tc.items() if k != "title"},
            "version": 1,
            "test_data_refs": [],
            "preconditions": ["TeamFlow 可访问", "测试数据已重置"],
            "postconditions": ["清理注入故障"],
            "cleanup_policy": "RESET_DATA",
            "run_config": {"base_url": base_url, "fail_fast": False, "trace": True},
            "compile_source": "human",
            "created_at": utc_now_iso(),
        }
        rt.catalog.create_testcase(payload, title=tc["title"], review_status="PENDING")
        rt.catalog.review_testcase(
            tc["testcase_id"], 1, "APPROVED", reviewer="role:qa-lead", note="用例评审通过"
        )
        created += 1

    logger.info("seed_done", created=created, skipped=skipped)
    return {"created": created, "skipped": skipped}
