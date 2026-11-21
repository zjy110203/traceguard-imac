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
]


def create_app() -> FastAPI:
    configure_logging("INFO", True)
    init_db()
    for rec in GT_SEEDS:
        GROUND_TRUTH.put(rec)
    app = FastAPI(title="TeamFlow (TraceGuard test target)", version="1.0.0")
    app.include_router(api_router)
    app.include_router(web_router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "app": "teamflow", "version": "1.0.0"}

    return app


app = create_app()
