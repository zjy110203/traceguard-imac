"""TeamFlow 演示资产种子：让平台启动后即可用真实用例跑通闭环（幂等）。

每次写入都是"可执行的真实资产"：已审核用例 + 已审批 Oracle，不做 Mock。
重复调用不会产生重复数据（按 testcase_id 检查）。
"""

from __future__ import annotations

import json
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
        rt.catalog.update_intent_status(it["intent_id"], "REVIEW")
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
        # RUN-006 WP1-01：每条断言绑定"声明用于该断言"的 Oracle，不允许跨断言复用
        _oracle(
            "oracle_tf_biz_created",
            "a_biz_created",
            "创建流程结束后独立业务状态查询的项目数必须 >=1",
            "TF-PROJECT-CREATE#BIZ-2",
            {
                "type": "state_count",
                "path": "/api/state/projects?workspace=default",
                "op": "gte",
                "value": 1,
            },
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
                    "a_biz_created",
                    "接口失败导致功能不可用：项目未创建",
                    "business_state",
                    {
                        "api": "/api/state/projects?workspace=default",
                        "field": "count",
                        "op": "gte",
                        "value": 1,
                    },
                    "oracle_tf_biz_created",
                ),
            ],
        },
    ]

    for tc in testcases:
        existing = rt.catalog.get_testcase(tc["testcase_id"])
        if existing:
            latest = existing.get("latest") or {}
            existing_assertions = {a["assertion_id"] for a in latest.get("assertions", [])}
            desired = {a["assertion_id"] for a in tc["assertions"]}
            if existing_assertions == desired:
                if latest.get("review_status") != "APPROVED":
                    latest_source = str(latest.get("compile_source") or "human")
                    if latest_source != "human":
                        # RUN-007：非人工版本（healed/compiler）受稳定性回放门禁约束，
                        # seed 不能也不应绕过门禁补审批——保持 PENDING 并如实记录。
                        skipped += 1
                        logger.info(
                            "seed_skip_unreviewed_candidate",
                            testcase_id=tc["testcase_id"],
                            version=latest.get("version"),
                            compile_source=latest_source,
                        )
                        continue
                    rt.catalog.review_testcase(
                        tc["testcase_id"],
                        int(latest.get("version", 1)),
                        "APPROVED",
                        reviewer="role:qa-lead",
                        note="用例评审通过",
                    )
                    created += 1
                else:
                    skipped += 1
                continue
            # 契约要求版本不可变：语义升级通过新版本完成，并重新审核
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
        doc = rt.catalog.create_testcase(payload, title=tc["title"], review_status="PENDING")
        rt.catalog.review_testcase(
            tc["testcase_id"],
            doc["version"],
            "APPROVED",
            reviewer="role:qa-lead",
            note="用例评审通过",
        )
        created += 1

    created += _seed_bench_v2(rt, base_url)

    logger.info("seed_done", created=created, skipped=skipped)
    return {"created": created, "skipped": skipped}


# ------------------------------------------------------------------ v2 数据集
# RUN-003-D：30 场景扩充。每条场景 = 故障激活步骤 + 业务流 + 断言，可重复、可清理。


def _fault_step(prefix: str, fault_id: str, idx: str = "f1") -> dict:
    return _step(
        f"{prefix}_{idx}",
        "api",
        None,
        f"POST /api/faults/{fault_id}/activate",
        f"激活故障 {fault_id}",
    )


def _wait_step(prefix: str, ms: str, idx: str, desc: str) -> dict:
    return _step(f"{prefix}_{idx}", "wait", None, ms, desc)


def _create_flow(prefix: str, name_value: str, wait_ms: str = "1200") -> list[dict]:
    """标准创建流：导航 → 填表 → 提交 → 等待 → 观测。"""
    return [
        _step(f"{prefix}_n1", "navigate", None, "/projects", "打开项目列表"),
        _step(f"{prefix}_n2", "click", ("test_id", "nav-new"), None, "点击新建项目"),
        _step(f"{prefix}_n3", "fill", ("test_id", "project-name"), name_value, "填写项目名称"),
        _step(f"{prefix}_n4", "click", ("test_id", "create-submit"), None, "提交创建"),
        _wait_step(prefix, wait_ms, "n5", "等待跳转与提示"),
        _step(
            f"{prefix}_n6",
            "assert",
            ("test_id", "banner-success"),
            "banner-success",
            "读取成功提示",
        ),
        _step(
            f"{prefix}_n7", "assert", ("test_id", "project-rows"), "project-rows", "读取项目列表"
        ),
    ]


#: Benchmark 场景的静态测试数据标记。
#:
#: RUN-007-FINALIZATION-2（WP1-A）：此前这里用的是 `utc_now_iso()[:19]` 当数据后缀，
#: 导致每次 Seed 的步骤内容都不同 → 变更检测必然判"语义变化" → 每次都新建一个版本，
#: 用例版本随时间无限增长。基准用的数据必须是**固定**的；确实需要动态值（时间戳/随机串）
#: 时应在 Run 执行期生成，而不是固化进用例版本。
BENCH_STATIC_DATA_TAG = "基准"


def _bench_signature(doc: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    """用例语义指纹：标题无关，只看步骤/断言（含 Oracle 绑定）。

    步骤按 action/value/target 三元组比较，断言按完整字段比较
    （不只是 assertion_id 集合，避免 Oracle 绑定被改却漏检）。
    """
    steps = [
        (s.get("action"), str(s.get("value")), str((s.get("target") or {}).get("value")))
        for s in (doc.get("steps") or [])
    ]
    assertions = sorted(
        (
            str(a.get("assertion_id")),
            str(a.get("kind")),
            str(a.get("oracle_id")),
            json.dumps(a.get("expected"), sort_keys=True, ensure_ascii=False),
            bool(a.get("required", True)),
        )
        for a in (doc.get("assertions") or [])
    )
    return (steps, assertions)


def _seed_bench_v2(rt: Any, base_url: str) -> int:
    """数据集 v2 的 30 场景资产（幂等）。"""
    created = 0
    stamp = BENCH_STATIC_DATA_TAG

    if not rt.catalog.get_intent("intent_tf_bench_v2"):
        rt.catalog.create_intent(
            {
                "intent_id": "intent_tf_bench_v2",
                "title": "UI-FaultBench v2 数据集（30 场景）",
                "description": "覆盖 API/NETWORK/PERSISTENCE/UI/LOCATOR/TIMING/AUTH/Flaky/复合故障的受控基准数据集。",
                "requirement_refs": ["TF-PROJECT-CREATE", "TF-PROJECT-EDIT"],
                "business_context": {"application": "teamflow", "dataset": "faultbench-v2"},
                "preconditions": ["TeamFlow 可访问", "测试数据已重置"],
                "candidate_assertions": ["按场景 GT 预期"],
                "owner": "qa-team",
            }
        )
        rt.catalog.update_intent_status("intent_tf_bench_v2", "REVIEW")
        rt.catalog.update_intent_status("intent_tf_bench_v2", "APPROVED")
        created += 1

    if not rt.catalog.get_oracle("oracle_tf_edit_error"):
        rt.catalog.create_oracle(
            _oracle(
                "oracle_tf_edit_error",
                "a_edit_error",
                "编辑保存失败时必须出现错误提示（data-testid=edit-error）",
                "TF-PROJECT-EDIT#ERR-1",
                {"type": "presence", "test_id": "edit-error"},
            ),
            approved_by="role:qa-lead",
            approval_note="需求评审通过",
            approved=True,
        )
        created += 1

    if not rt.catalog.get_oracle("oracle_tf_row_render2"):
        # RUN-006 WP1-01：每条断言都必须绑定"声明用于该断言"的 Oracle，
        # 因此 a_row_render2 不能复用 oracle_tf_row_render（其 assertion_id 不同）。
        rt.catalog.create_oracle(
            _oracle(
                "oracle_tf_row_render2",
                "a_row_render2",
                "项目列表必须渲染出数据行（data-testid=project-row，>=1）",
                "TF-PROJECT-CREATE#UI-3",
                {"type": "count", "op": "count_gte", "value": 1},
            ),
            approved_by="role:qa-lead",
            approval_note="需求评审通过",
            approved=True,
        )
        created += 1

    def banner_assert(aid: str = "a_ui_banner") -> dict:
        return _assertion(
            aid, "成功提示出现", "ui", {"test_id": "banner-success"}, "oracle_tf_ui_banner"
        )

    def biz_assert(aid: str = "a_biz_count", op: str = "gte", value: int = 1) -> dict:
        return _assertion(
            aid,
            "独立业务状态计数校验",
            "business_state",
            {
                "api": "/api/state/projects?workspace=default",
                "field": "count",
                "op": op,
                "value": value,
            },
            "oracle_tf_biz_count",
        )

    def error_assert(aid: str = "a_error_banner") -> dict:
        return _assertion(
            aid, "错误提示可见", "ui", {"test_id": "create-error"}, "oracle_tf_error_banner"
        )

    def rows_assert(
        aid: str = "a_row_render",
        test_id: str = "project-rows",
        oracle_id: str = "oracle_tf_row_render",
    ) -> dict:
        return _assertion(
            aid,
            "列表渲染数据行",
            "ui",
            {"test_id": test_id, "op": "count_gte", "value": 1},
            oracle_id,
        )

    def edit_error_assert(aid: str = "a_edit_error") -> dict:
        return _assertion(
            aid, "编辑保存失败提示", "ui", {"test_id": "edit-error"}, "oracle_tf_edit_error"
        )

    def edit_flow(prefix: str, fault_id: str) -> list[dict]:
        """编辑链路：先正常创建一个项目，再触发编辑接口故障。"""
        return [
            _fault_step(prefix, fault_id),
            *_create_flow(prefix, f"Bench 编辑链路-{stamp}"),
            _step(f"{prefix}_e1", "navigate", None, "/projects/first", "打开第一个项目详情"),
            _wait_step(prefix, "800", "e2", "等待详情加载"),
            _step(
                f"{prefix}_e3",
                "fill",
                ("test_id", "edit-name"),
                f"编辑后名称-{stamp}",
                "填写新名称",
            ),
            _step(
                f"{prefix}_e4", "click", ("test_id", "edit-save"), None, "保存编辑（预期接口失败）"
            ),
            _wait_step(prefix, "800", "e6", "等待保存结果"),
            _step(
                f"{prefix}_e7",
                "assert",
                ("test_id", "edit-error"),
                "edit-error",
                "读取编辑错误提示",
            ),
        ]

    def double_submit_flow(prefix: str) -> list[dict]:
        return [
            _step(f"{prefix}_n1", "navigate", None, "/projects", "打开项目列表"),
            _step(f"{prefix}_n2", "click", ("test_id", "nav-new"), None, "点击新建项目"),
            _step(
                f"{prefix}_n3", "fill", ("test_id", "project-name"), f"双提交-{stamp}", "填写名称"
            ),
            _step(f"{prefix}_n4", "click", ("test_id", "create-submit"), None, "第一次提交"),
            _step(
                f"{prefix}_n5",
                "click",
                ("test_id", "create-submit"),
                None,
                "第二次提交（300ms 内）",
            ),
            _wait_step(prefix, "1200", "n6", "等待跳转"),
            _step(
                f"{prefix}_n7",
                "assert",
                ("test_id", "banner-success"),
                "banner-success",
                "成功提示",
            ),
        ]

    bench: list[dict] = [
        {
            "testcase_id": "tcase_bench_normal2",
            "title": "正常创建（第二可重复样本）",
            "steps": [*_create_flow("bn2", f"Bench 正常2-{stamp}")],
            "assertions": [banner_assert("a_b2"), biz_assert("a_b2c")],
        },
        {
            "testcase_id": "tcase_bench_db002",
            "title": "重名持久化静默失败（假通过变体）",
            "steps": [
                # 先正常创建同名项目（fault 未激活，真实落库）
                *_create_flow("db2a", f"Bench 重名静默-{stamp}"),
                # 激活故障后用同名再次创建：API 谎报成功但不落库
                _fault_step("db2", "TF-DB-002"),
                _step("db2_b1", "navigate", None, "/projects", "打开项目列表"),
                _step("db2_b2", "click", ("test_id", "nav-new"), None, "点击新建项目"),
                _step(
                    "db2_b3",
                    "fill",
                    ("test_id", "project-name"),
                    f"Bench 重名静默-{stamp}",
                    "再次填写同名",
                ),
                _step(
                    "db2_b4",
                    "click",
                    ("test_id", "create-submit"),
                    None,
                    "提交创建（预期静默失败）",
                ),
                _wait_step("db2", "1200", "b5", "等待跳转与提示"),
                _step(
                    "db2_b6",
                    "assert",
                    ("test_id", "banner-success"),
                    "banner-success",
                    "读取成功提示",
                ),
            ],
            "assertions": [biz_assert("a_biz_count2", value=2)],
        },
        {
            "testcase_id": "tcase_bench_db003",
            "title": "状态查询滞后 2000ms：等待后通过",
            "steps": [
                _fault_step("db3", "TF-DB-003"),
                *_create_flow("db3", f"Bench 滞后2s-{stamp}"),
                _wait_step("db3", "3000", "n8", "等待一致性窗口结束"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_db004",
            "title": "状态查询滞后 9000ms：断言窗口内读旧值",
            "steps": [
                _fault_step("db4", "TF-DB-004"),
                *_create_flow("db4", f"Bench 滞后9s-{stamp}"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_db005",
            "title": "持久化写入慢 4000ms：最终落库",
            "steps": [
                _fault_step("db5", "TF-DB-005"),
                *_create_flow("db5", f"Bench 写入慢-{stamp}", wait_ms="6000"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_ui002",
            "title": "表单提交被静默忽略",
            "steps": [
                _fault_step("ui2", "TF-UI-002"),
                *_create_flow("ui2", f"Bench 静默-{stamp}"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_ui003",
            "title": "业务成功但提示被误导为失败文案",
            "steps": [
                _fault_step("ui3", "TF-UI-003"),
                *_create_flow("ui3", f"Bench 误导-{stamp}"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_ui004",
            "title": "成功提示延迟渲染 3000ms",
            "steps": [
                _fault_step("ui4", "TF-UI-004"),
                *_create_flow("ui4", f"Bench 提示晚到-{stamp}", wait_ms="4000"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_api002",
            "title": "创建接口 503",
            "steps": [
                _fault_step("api503", "TF-API-002"),
                *_create_flow("api503", f"Bench 503-{stamp}"),
            ],
            "assertions": [error_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_api003",
            "title": "更新接口 500（编辑链路）",
            "steps": [*edit_flow("api003", "TF-API-003")],
            "assertions": [edit_error_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_api004",
            "title": "更新接口 503（编辑链路）",
            "steps": [*edit_flow("api004", "TF-API-004")],
            "assertions": [edit_error_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_net001",
            "title": "请求延迟 3000ms：可容忍",
            "steps": [
                _fault_step("net1", "TF-NET-001"),
                *_create_flow("net1", f"Bench 网络慢-{stamp}", wait_ms="5000"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_net001fast",
            "title": "网络慢 3000ms（性能感知样本）",
            "steps": [
                _fault_step("net1f", "TF-NET-001", idx="f1"),
                *_create_flow("net1f", f"Bench 性能感知-{stamp}", wait_ms="5000"),
            ],
            "assertions": [banner_assert("a_b2"), biz_assert("a_b2c")],
        },
        {
            "testcase_id": "tcase_bench_net002",
            "title": "9000ms 长延迟必然超时",
            "steps": [
                _fault_step("net2", "TF-NET-002"),
                *_create_flow("net2", f"Bench 超时-{stamp}", wait_ms="1500"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_loc001",
            "title": "提交按钮定位器失效",
            "steps": [
                _fault_step("loc1", "TF-LOC-001"),
                *_create_flow("loc1", f"Bench 定位失效-{stamp}"),
            ],
            "assertions": [banner_assert()],
        },
        {
            "testcase_id": "tcase_bench_loc002",
            "title": "名称输入框定位器失效",
            "steps": [
                _fault_step("loc2", "TF-LOC-002"),
                *_create_flow("loc2", f"Bench 输入失效-{stamp}"),
            ],
            "assertions": [banner_assert()],
        },
        {
            "testcase_id": "tcase_bench_time001",
            "title": "列表刷新延迟 1500ms（预期通过）",
            "steps": [
                _fault_step("tm1", "TF-TIME-001"),
                *_create_flow("tm1", f"Bench 列表延迟-{stamp}", wait_ms="3000"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_time002",
            "title": "列表刷新延迟 5000ms（预期通过）",
            "steps": [
                _fault_step("tm2", "TF-TIME-002"),
                *_create_flow("tm2", f"Bench 强延迟-{stamp}", wait_ms="1000"),
            ],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_time003",
            "title": "双提交竞态：第二次被丢弃",
            "steps": [*double_submit_flow("tm3")],
            "assertions": [banner_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_sec001",
            "title": "创建返回 403 权限异常",
            "steps": [
                _fault_step("sec1", "TF-SEC-001"),
                *_create_flow("sec1", f"Bench 权限-{stamp}"),
            ],
            "assertions": [error_assert(), biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_sec002",
            "title": "列表读取 403",
            "steps": [
                # 创建流（不含 rows 观测，避免故障前观测污染断言）
                _step("sec2_n1", "navigate", None, "/projects", "打开项目列表"),
                _step("sec2_n2", "click", ("test_id", "nav-new"), None, "点击新建项目"),
                _step(
                    "sec2_n3",
                    "fill",
                    ("test_id", "project-name"),
                    f"Bench 读权限-{stamp}",
                    "填写名称",
                ),
                _step("sec2_n4", "click", ("test_id", "create-submit"), None, "提交创建"),
                _wait_step("sec2", "1200", "n5", "等待创建完成"),
                _fault_step("sec2", "TF-SEC-002", idx="f2"),
                _step("sec2_r1", "navigate", None, "/projects", "再次打开列表（预期读取失败）"),
                _wait_step("sec2", "1000", "r2", "等待列表加载失败"),
                _step(
                    "sec2_r3",
                    "assert",
                    ("test_id", "project-row"),
                    "project-row",
                    "观测故障后的数据行数量",
                ),
            ],
            "assertions": [
                rows_assert(
                    "a_row_render2", test_id="project-row", oracle_id="oracle_tf_row_render2"
                )
            ],
        },
        {
            "testcase_id": "tcase_bench_comp001",
            "title": "复合：API 500 + 错误提示被误导",
            "steps": [
                _fault_step("cp1", "TF-COMP-001"),
                *_create_flow("cp1", f"Bench 复合API-{stamp}"),
            ],
            "assertions": [biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_comp002",
            "title": "复合：持久化回滚 + 列表延迟",
            "steps": [
                _fault_step("cp2", "TF-COMP-002"),
                *_create_flow("cp2", f"Bench 复合DB-{stamp}", wait_ms="2500"),
            ],
            "assertions": [biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_comp003",
            "title": "复合：定位器失效 + 静默忽略",
            "steps": [
                _fault_step("cp3", "TF-COMP-003"),
                *_create_flow("cp3", f"Bench 复合LOC-{stamp}"),
            ],
            "assertions": [banner_assert()],
        },
        {
            "testcase_id": "tcase_bench_flaky001",
            "title": "Flaky：持久化 50% 概率失败",
            "steps": [
                _fault_step("fl1", "TF-FLAKY-001"),
                *_create_flow("fl1", f"Bench FlakyDB-{stamp}"),
            ],
            "assertions": [biz_assert()],
        },
        {
            "testcase_id": "tcase_bench_flaky002",
            "title": "Flaky：API 500 概率触发",
            "steps": [
                _fault_step("fl2", "TF-FLAKY-002"),
                *_create_flow("fl2", f"Bench FlakyAPI-{stamp}"),
                _step(
                    "fl2_n8",
                    "assert",
                    ("test_id", "create-error"),
                    "create-error",
                    "读取错误提示（可能不存在）",
                ),
            ],
            "assertions": [error_assert(), biz_assert()],
        },
    ]

    for tc in bench:
        existing = rt.catalog.get_testcase(tc["testcase_id"])
        if existing:
            approved = rt.catalog.latest_approved_version(tc["testcase_id"])
            latest_version = int(existing.get("latest_version") or 0)
            approved_version = int((approved or {}).get("version") or 0)
            # 幂等判定必须同时满足：
            #   1) 当前最新版本就是正式版本（没有遗留的 PENDING/DRAFT 影子版本）；
            #   2) 正式版本内容与静态基线语义一致。
            # 只有 1) 2) 同时成立才跳过；否则走"新版本 + 人工审核"的正常流程恢复基线
            # （Repair 改了定位器后再次 Seed 属于这种情况），绝不修改已存在的不可变版本。
            if (
                approved is not None
                and approved_version == latest_version
                and _bench_signature(approved) == _bench_signature(tc)
            ):
                continue
        payload = {
            "testcase_id": tc["testcase_id"],
            "intent_ref": "intent_tf_bench_v2",
            "steps": tc["steps"],
            "assertions": tc["assertions"],
            "version": 1,
            "test_data_refs": [],
            "preconditions": ["TeamFlow 可访问", "测试数据已重置", "对应故障经控制 API 激活"],
            "postconditions": ["清理注入故障"],
            "cleanup_policy": "RESET_DATA",
            "run_config": {"base_url": base_url, "fail_fast": False, "trace": True},
            "compile_source": "human",
            "created_at": utc_now_iso(),
        }
        doc = rt.catalog.create_testcase(payload, title=tc["title"], review_status="PENDING")
        rt.catalog.review_testcase(
            tc["testcase_id"],
            doc["version"],
            "APPROVED",
            reviewer="role:qa-lead",
            note="Benchmark 数据集 v2 评审通过",
        )
        created += 1
    return created
