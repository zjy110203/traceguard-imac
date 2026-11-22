"""UI-FaultBench 实际数据集（评测侧私有 GT，不通过 API 暴露）。

与 `apps/teamflow/ground_truth.py` 的 GT 种子保持语义一致：
- 正常场景：预期无失败；
- TF-DB-001：UI 成功但未持久化 → 主因 PERSISTENCE；
- TF-UI-001：持久化成功但提示缺失 → 主因 UI_RENDERING；
- TF-API-001：接口 500 → 主因 API。
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


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="TF-NORMAL-001",
        testcase_id="tcase_tf_create_normal",
        fault_mechanism="NONE",
        gt_mechanism="NONE",
        gt_faulty=False,
        description="正常创建项目：UI 成功且业务持久化",
    ),
    Scenario(
        scenario_id="TF-DB-001",
        testcase_id="tcase_tf_false_pass",
        fault_mechanism="TF-DB-001",
        gt_mechanism="PERSISTENCE",
        gt_faulty=True,
        description="UI 显示成功但事务回滚（False Pass）",
    ),
    Scenario(
        scenario_id="TF-UI-001",
        testcase_id="tcase_tf_ui_missing",
        fault_mechanism="TF-UI-001",
        gt_mechanism="UI_RENDERING",
        gt_faulty=True,
        description="持久化成功但成功提示缺失",
    ),
    Scenario(
        scenario_id="TF-API-001",
        testcase_id="tcase_tf_api_error",
        fault_mechanism="TF-API-001",
        gt_mechanism="API",
        gt_faulty=True,
        description="创建接口返回 500",
    ),
)

DATASET_ID = "teamflow-faultbench-v1"
GT_ISOLATED = True
