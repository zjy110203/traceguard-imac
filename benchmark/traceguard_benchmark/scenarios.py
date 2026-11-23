"""UI-FaultBench 实际数据集 v2（30 场景，评测侧私有 GT，不通过 API 暴露）。

RUN-003-D 扩充：覆盖 API / NETWORK / PERSISTENCE / UI / LOCATOR / TIMING /
AUTHORIZATION / Flaky / 复合故障 十类机制。
每个场景与 `apps/teamflow/traceguard_teamflow/main.py` 的 GT 种子一一对应；
每条场景有独立 testcase（seed.py），故障经控制 API 激活，可重复、可清理。

统计口径提醒：诊断实验仅评估"失败样本"；PASSED 预期场景用于回归与性能感知验证。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    testcase_id: str
    fault_mechanism: str
    gt_mechanism: str
    gt_faulty: bool
    description: str
    expected_verdict: str = "FAILED"  # PASSED 预期场景不参与诊断对照计分


SCENARIOS: tuple[Scenario, ...] = (
    # ---- 正常（回归 + 性能感知） ----
    Scenario(
        "TF-NORMAL-001",
        "tcase_tf_create_normal",
        "NONE",
        "NONE",
        False,
        "正常创建项目：UI 成功且业务持久化",
        "PASSED",
    ),
    Scenario(
        "TF-NORMAL-002",
        "tcase_bench_normal2",
        "NONE",
        "NONE",
        False,
        "正常创建（第二可重复样本）",
        "PASSED",
    ),
    # ---- 数据持久化故障 ----
    Scenario(
        "TF-DB-001",
        "tcase_tf_false_pass",
        "TF-DB-001",
        "PERSISTENCE",
        True,
        "UI 成功但事务回滚（False Pass）",
    ),
    Scenario(
        "TF-DB-002",
        "tcase_bench_db002",
        "TF-DB-002",
        "PERSISTENCE",
        True,
        "重名持久化静默失败，API 谎报成功",
    ),
    Scenario(
        "TF-DB-003",
        "tcase_bench_db003",
        "TF-DB-003",
        "TIMING",
        False,
        "状态查询滞后 2000ms：等待后通过",
        "PASSED",
    ),
    Scenario(
        "TF-DB-004",
        "tcase_bench_db004",
        "TF-DB-004",
        "TIMING",
        True,
        "状态查询滞后 9000ms：断言窗口内必然读旧值",
    ),
    Scenario(
        "TF-DB-005",
        "tcase_bench_db005",
        "TF-DB-005",
        "TIMING",
        False,
        "写入慢 4000ms：最终落库，预期通过",
        "PASSED",
    ),
    # ---- UI 渲染故障 ----
    Scenario(
        "TF-UI-001",
        "tcase_tf_ui_missing",
        "TF-UI-001",
        "UI_RENDERING",
        True,
        "持久化成功但成功提示缺失",
    ),
    Scenario(
        "TF-UI-002",
        "tcase_bench_ui002",
        "TF-UI-002",
        "UI_RENDERING",
        True,
        "提交被静默忽略，无任何反馈",
    ),
    Scenario(
        "TF-UI-003",
        "tcase_bench_ui003",
        "TF-UI-003",
        "UI_RENDERING",
        True,
        "业务成功但提示被误导为失败文案",
    ),
    Scenario(
        "TF-UI-004",
        "tcase_bench_ui004",
        "TF-UI-004",
        "TIMING",
        False,
        "成功提示延迟 3000ms 渲染",
        "PASSED",
    ),
    # ---- API 故障 ----
    Scenario("TF-API-001", "tcase_tf_api_error", "TF-API-001", "API", True, "创建接口返回 500"),
    Scenario("TF-API-002", "tcase_bench_api002", "TF-API-002", "API", True, "创建接口返回 503"),
    Scenario(
        "TF-API-003",
        "tcase_bench_api003",
        "TF-API-003",
        "API",
        False,
        "更新接口 500：UI 正确反馈错误（回归）",
        "PASSED",
    ),
    Scenario(
        "TF-API-004",
        "tcase_bench_api004",
        "TF-API-004",
        "API",
        False,
        "更新接口 503：UI 正确反馈错误（回归）",
        "PASSED",
    ),
    # ---- 网络故障 ----
    Scenario(
        "TF-NET-001",
        "tcase_bench_net001",
        "TF-NET-001",
        "NETWORK",
        False,
        "请求延迟 3000ms：可容忍，预期通过",
        "PASSED",
    ),
    Scenario(
        "TF-NET-001-FAST",
        "tcase_bench_net001fast",
        "TF-NET-001",
        "NETWORK",
        False,
        "网络慢 3000ms（性能感知样本）",
        "PASSED",
    ),
    Scenario(
        "TF-NET-002", "tcase_bench_net002", "TF-NET-002", "NETWORK", True, "9000ms 长延迟必然超时"
    ),
    # ---- 定位器失效 ----
    Scenario(
        "TF-LOC-001", "tcase_bench_loc001", "TF-LOC-001", "LOCATOR", True, "提交按钮 test_id 变更"
    ),
    Scenario(
        "TF-LOC-002", "tcase_bench_loc002", "TF-LOC-002", "LOCATOR", True, "名称输入框 test_id 变更"
    ),
    # ---- 时序 / 并发 ----
    Scenario(
        "TF-TIME-001",
        "tcase_bench_time001",
        "TF-TIME-001",
        "TIMING",
        False,
        "列表刷新延迟 1500ms：预期通过",
        "PASSED",
    ),
    Scenario(
        "TF-TIME-002",
        "tcase_bench_time002",
        "TF-TIME-002",
        "TIMING",
        False,
        "列表刷新延迟 5000ms：预期通过",
        "PASSED",
    ),
    Scenario(
        "TF-TIME-003",
        "tcase_bench_time003",
        "TF-TIME-003",
        "TIMING",
        True,
        "双提交竞态：第二次提交被导航中断（真实竞态表现）",
        "EXEC_FAILED",
    ),
    # ---- 权限异常 ----
    Scenario(
        "TF-SEC-001", "tcase_bench_sec001", "TF-SEC-001", "AUTHORIZATION", True, "创建返回 403"
    ),
    Scenario(
        "TF-SEC-002", "tcase_bench_sec002", "TF-SEC-002", "AUTHORIZATION", True, "列表读取 403"
    ),
    # ---- 多源证据冲突 / 复合故障 ----
    Scenario(
        "TF-COMP-001",
        "tcase_bench_comp001",
        "TF-COMP-001",
        "API",
        True,
        "复合：API 500 + 错误提示被误导为成功",
    ),
    Scenario(
        "TF-COMP-002",
        "tcase_bench_comp002",
        "TF-COMP-002",
        "PERSISTENCE",
        True,
        "复合：持久化回滚 + 列表延迟",
    ),
    Scenario(
        "TF-COMP-003",
        "tcase_bench_comp003",
        "TF-COMP-003",
        "LOCATOR",
        True,
        "复合：定位器失效 + 静默忽略",
    ),
    # ---- Flaky ----
    Scenario(
        "TF-FLAKY-001",
        "tcase_bench_flaky001",
        "TF-FLAKY-001",
        "PERSISTENCE",
        True,
        "Flaky：持久化按 50% 概率失败",
    ),
    Scenario(
        "TF-FLAKY-002",
        "tcase_bench_flaky002",
        "TF-FLAKY-002",
        "API",
        True,
        "Flaky：API 500 按 50% 概率触发",
    ),
)

assert len(SCENARIOS) == 30, f"数据集必须为 30 场景，当前 {len(SCENARIOS)}"

DATASET_ID = "teamflow-faultbench-v2"
DATASET_VERSION = "v2.0"
GT_ISOLATED = True
