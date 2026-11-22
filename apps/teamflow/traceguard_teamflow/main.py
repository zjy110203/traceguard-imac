"""TeamFlow 应用入口。独立运行于 127.0.0.1:8001。"""

from __future__ import annotations

from fastapi import FastAPI
from traceguard_core.logging import configure_logging, get_logger

from .api import router as api_router
from .ground_truth import GROUND_TRUTH, GroundTruthRecord
from .models import init_db
from .web import router as web_router

logger = get_logger("traceguard.teamflow")

# Ground Truth 种子（评测侧答案，隔离存储）
GT_SEEDS = [
    GroundTruthRecord(
        scenario_id="TF-NORMAL-001",
        fault_mechanism_id="NONE",
        expected_business_state={"project_persisted": True, "ui_success": True},
        expected_failure_class="NONE",
        notes="正常创建项目：UI 成功且业务已持久化",
    ),
    GroundTruthRecord(
        scenario_id="TF-API-001",
        fault_mechanism_id="TF-API-001",
        expected_business_state={"project_persisted": False, "api_status": 500},
        expected_failure_class="PRODUCT_DEFECT / API",
        notes="创建接口返回 500",
    ),
    GroundTruthRecord(
        scenario_id="TF-NET-001",
        fault_mechanism_id="TF-NET-001",
        expected_business_state={"timeout": True},
        expected_failure_class="ENVIRONMENT / NETWORK",
        notes="请求延迟或超时",
    ),
    GroundTruthRecord(
        scenario_id="TF-DB-001",
        fault_mechanism_id="TF-DB-001",
        expected_business_state={"ui_success": True, "project_persisted": False},
        expected_failure_class="PRODUCT_DEFECT / PERSISTENCE",
        notes="UI 显示成功但持久化失败（False Pass 典型场景）",
    ),
    GroundTruthRecord(
        scenario_id="TF-UI-001",
        fault_mechanism_id="TF-UI-001",
        expected_business_state={"project_persisted": True, "ui_success_indicator": False},
        expected_failure_class="UI_RENDERING",
        notes="持久化成功但成功提示缺失",
    ),
    GroundTruthRecord(
        scenario_id="TF-TIME-001",
        fault_mechanism_id="TF-TIME-001",
        expected_business_state={"project_persisted": True, "list_refresh_delayed": True},
        expected_failure_class="TIMING / FLAKY",
        notes="列表刷新延迟/竞态",
    ),
    # ---------------- RUN-003-D 扩充（30 场景数据集 v2） ----------------
    GroundTruthRecord(
        scenario_id="TF-NORMAL-002",
        fault_mechanism_id="NONE",
        expected_business_state={"project_persisted": True, "ui_success": True},
        expected_failure_class="NONE",
        notes="正常创建（重名检测关闭）：与 v1 相同语义的第二可重复样本",
    ),
    GroundTruthRecord(
        scenario_id="TF-API-002",
        fault_mechanism_id="TF-API-002",
        expected_business_state={"project_persisted": False, "api_status": 503},
        expected_failure_class="PRODUCT_DEFECT / API",
        notes="创建接口 503 服务不可用",
    ),
    GroundTruthRecord(
        scenario_id="TF-API-003",
        fault_mechanism_id="TF-API-003",
        expected_business_state={"update_applied": False, "api_status": 500, "ui_feedback": "edit-error 可见"},
        expected_failure_class="NONE",
        notes="更新接口 500：UI 正确展示错误反馈（回归场景，预期通过）",
    ),
    GroundTruthRecord(
        scenario_id="TF-NET-002",
        fault_mechanism_id="TF-NET-002",
        expected_business_state={"timeout": True, "timeout_level": "hard"},
        expected_failure_class="ENVIRONMENT / NETWORK",
        notes="9000ms 长延迟必然超时",
    ),
    GroundTruthRecord(
        scenario_id="TF-DB-002",
        fault_mechanism_id="TF-DB-002",
        expected_business_state={"ui_success": True, "project_persisted": False, "trigger": "duplicate_name"},
        expected_failure_class="PRODUCT_DEFECT / PERSISTENCE",
        notes="重名写入静默失败但 API 谎报成功（False Pass 变体）",
    ),
    GroundTruthRecord(
        scenario_id="TF-DB-003",
        fault_mechanism_id="TF-DB-003",
        expected_business_state={"project_persisted": True, "state_lag_ms": 2000},
        expected_failure_class="TIMING / EVENTUAL_CONSISTENCY",
        notes="状态查询滞后 2000ms：等待后应通过（Flaky 来源）",
    ),
    GroundTruthRecord(
        scenario_id="TF-DB-004",
        fault_mechanism_id="TF-DB-004",
        expected_business_state={"project_persisted": True, "state_lag_ms": 9000},
        expected_failure_class="TIMING / EVENTUAL_CONSISTENCY",
        notes="状态查询滞后 9000ms：断言窗口内必然读到旧值",
    ),
    GroundTruthRecord(
        scenario_id="TF-UI-002",
        fault_mechanism_id="TF-UI-002",
        expected_business_state={"project_persisted": False, "ui_feedback": None},
        expected_failure_class="UI_RENDERING",
        notes="表单提交被静默忽略，无任何反馈",
    ),
    GroundTruthRecord(
        scenario_id="TF-UI-003",
        fault_mechanism_id="TF-UI-003",
        expected_business_state={"project_persisted": True, "ui_shows": "misleading_error_text"},
        expected_failure_class="UI_RENDERING",
        notes="业务已成功但提示被误导为失败文案（testid 变为 banner-error）",
    ),
    GroundTruthRecord(
        scenario_id="TF-LOC-001",
        fault_mechanism_id="TF-LOC-001",
        expected_business_state={"project_persisted": False, "submit_locator": "create-submit-v2"},
        expected_failure_class="TEST_ASSET / LOCATOR",
        notes="提交按钮 test_id 变更导致定位器失效",
    ),
    GroundTruthRecord(
        scenario_id="TF-LOC-002",
        fault_mechanism_id="TF-LOC-002",
        expected_business_state={"project_persisted": False, "name_locator": "project-name-v2"},
        expected_failure_class="TEST_ASSET / LOCATOR",
        notes="名称输入框 test_id 变更",
    ),
    GroundTruthRecord(
        scenario_id="TF-TIME-002",
        fault_mechanism_id="TF-TIME-002",
        expected_business_state={"project_persisted": True, "list_refresh_delayed_ms": 5000},
        expected_failure_class="TIMING / FLAKY",
        notes="强时序延迟 5000ms",
    ),
    GroundTruthRecord(
        scenario_id="TF-TIME-003",
        fault_mechanism_id="TF-TIME-003",
        expected_business_state={"second_submit_interrupted_by_navigation": True},
        expected_failure_class="TIMING / RACE",
        notes="双提交竞态：第二次提交被成功后的导航中断（确定性执行失败）",
    ),
    GroundTruthRecord(
        scenario_id="TF-SEC-001",
        fault_mechanism_id="TF-SEC-001",
        expected_business_state={"project_persisted": False, "api_status": 403},
        expected_failure_class="AUTHORIZATION",
        notes="创建返回 403 权限异常",
    ),
    GroundTruthRecord(
        scenario_id="TF-SEC-002",
        fault_mechanism_id="TF-SEC-002",
        expected_business_state={"list_readable": False, "api_status": 403},
        expected_failure_class="AUTHORIZATION",
        notes="列表读取 403",
    ),
    GroundTruthRecord(
        scenario_id="TF-COMP-001",
        fault_mechanism_id="TF-COMP-001",
        expected_business_state={"project_persisted": False, "api_status": 500, "ui_feedback": "misleading_success"},
        expected_failure_class="PRODUCT_DEFECT / API",
        notes="复合：API 500 + 错误提示被误导为成功（主因 API，次因 UI 掩盖）",
    ),
    GroundTruthRecord(
        scenario_id="TF-COMP-002",
        fault_mechanism_id="TF-COMP-002",
        expected_business_state={"project_persisted": False, "list_delayed": True},
        expected_failure_class="PRODUCT_DEFECT / PERSISTENCE",
        notes="复合：持久化回滚 + 列表延迟（主因 PERSISTENCE）",
    ),
    GroundTruthRecord(
        scenario_id="TF-COMP-003",
        fault_mechanism_id="TF-COMP-003",
        expected_business_state={"project_persisted": False, "submit_locator_changed": True, "ui_feedback": None},
        expected_failure_class="TEST_ASSET / LOCATOR",
        notes="复合：定位器失效 + 静默忽略（主因 LOCATOR）",
    ),
    GroundTruthRecord(
        scenario_id="TF-FLAKY-001",
        fault_mechanism_id="TF-FLAKY-001",
        expected_business_state={"project_persisted": "probabilistic_50pct"},
        expected_failure_class="FLAKY / PERSISTENCE",
        notes="Flaky：持久化按 50% 概率失败，同版本多次执行结论不一致",
    ),
    GroundTruthRecord(
        scenario_id="TF-FLAKY-002",
        fault_mechanism_id="TF-FLAKY-002",
        expected_business_state={"api_500": "probabilistic_50pct"},
        expected_failure_class="FLAKY / API",
        notes="Flaky：API 500 按 50% 概率触发",
    ),
    GroundTruthRecord(
        scenario_id="TF-NET-001-FAST",
        fault_mechanism_id="TF-NET-001",
        expected_business_state={"timeout": False, "delay_ms": 3000, "expected_verdict": "PASSED"},
        expected_failure_class="NONE",
        notes="网络慢但可容忍（3000ms）：功能正常，预期通过（性能感知场景）",
    ),
    GroundTruthRecord(
        scenario_id="TF-DB-005",
        fault_mechanism_id="TF-DB-005",
        expected_business_state={"project_persisted": True, "write_delay_ms": 4000},
        expected_failure_class="TIMING / SLOW_WRITE",
        notes="持久化写入慢 4000ms：数据最终落库，预期通过但耗时增加",
    ),
    GroundTruthRecord(
        scenario_id="TF-API-004",
        fault_mechanism_id="TF-API-004",
        expected_business_state={"update_applied": False, "api_status": 503, "ui_feedback": "edit-error 可见"},
        expected_failure_class="NONE",
        notes="更新接口 503：UI 正确展示错误反馈（回归场景，预期通过）",
    ),
    GroundTruthRecord(
        scenario_id="TF-UI-004",
        fault_mechanism_id="TF-UI-004",
        expected_business_state={"project_persisted": True, "banner_delayed_ms": 3000},
        expected_failure_class="UI_RENDERING / TIMING",
        notes="成功提示延迟渲染 3000ms：数据已落库，提示晚到",
    ),
]


def create_app() -> FastAPI:
    configure_logging("INFO", True)
    init_db()
    for rec in GT_SEEDS:
        GROUND_TRUTH.put(rec)
    app = FastAPI(title="TeamFlow (TraceGuard test target)", version="1.0.0")

    @app.middleware("http")
    async def no_store_cache(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(api_router)
    app.include_router(web_router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "app": "teamflow", "version": "1.0.0"}

    return app


app = create_app()
