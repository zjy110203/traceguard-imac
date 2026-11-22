"""TeamFlow 业务 API + 独立状态查询 + 故障注入控制 + Ground Truth 隔离入口。"""

from __future__ import annotations

import hashlib
import random
import string
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from traceguard_core.ids import new_id, utc_now_iso

from .faults import FAULT_IDS, FAULT_STATE, apply_network_delay
from .ground_truth import GROUND_TRUTH
from .models import Project, Workspace, get_session


def _pw_hash(password: str) -> str:
    return hashlib.sha256(("tg-test-salt:" + password).encode()).hexdigest()


class LoginReq(BaseModel):
    username: str
    password: str


class ProjectCreateReq(BaseModel):
    name: str
    description: str = ""
    workspace_slug: str = "default"


router = APIRouter()


# ---------------- 认证（最小测试账户） ----------------
@router.post("/api/auth/login")
async def login(req: LoginReq) -> dict[str, Any]:
    await apply_network_delay()
    # 测试账户：tester / tester123（明确为测试数据，非真实凭据）
    if req.username == "tester" and req.password == "tester123":
        return {"ok": True, "token": "tf-test-token", "username": "tester"}
    raise HTTPException(status_code=401, detail="invalid credentials")


# ---------------- 业务 API ----------------
@router.post("/api/projects")
async def create_project(
    req: ProjectCreateReq,
    x_tg_workspace: str | None = Header(default=None),
) -> dict[str, Any]:
    await apply_network_delay()

    # TF-API-001：创建接口返回 500
    if FAULT_STATE.is_active("TF-API-001"):
        raise HTTPException(status_code=500, detail="simulated API failure (TF-API-001)")
    # TF-API-002：创建接口返回 503（服务不可用变体）
    if FAULT_STATE.is_active("TF-API-002"):
        raise HTTPException(status_code=503, detail="simulated service unavailable (TF-API-002)")
    # TF-SEC-001：权限异常
    if FAULT_STATE.is_active("TF-SEC-001"):
        raise HTTPException(status_code=403, detail="workspace quota exceeded (TF-SEC-001)")
    # TF-TIME-003：创建响应延迟 1.2s（让第二次点击落入双提交窗口）
    if FAULT_STATE.is_active("TF-TIME-003"):
        import asyncio

        await asyncio.sleep(1.2)
    # TF-FLAKY-002：按 50% 概率返回 500（Flaky）
    if FAULT_STATE.is_active("TF-FLAKY-002") and random.random() < FAULT_STATE.param(
        "TF-FLAKY-002", "probability", 0.5
    ):
        raise HTTPException(status_code=500, detail="intermittent API failure (TF-FLAKY-002)")

    # 直接调用路由函数时，Header 默认值可能是 Header 对象；统一归一为 str
    ws_raw = x_tg_workspace if isinstance(x_tg_workspace, str) else None
    ws = ws_raw or req.workspace_slug or "default"
    session = get_session()
    try:
        # TF-DB-001：UI 会显示成功，但持久化被跳过（模拟事务回滚 / 写入失败）
        if FAULT_STATE.is_active("TF-DB-001"):
            key = "TF-" + "".join(random.choices(string.digits, k=6))
            return {
                "ok": True,
                "project": {
                    "id": new_id("proj"),
                    "key": key,
                    "name": req.name,
                    "description": req.description,
                    "workspace_slug": ws,
                    "status": "ACTIVE",
                    "created_at": utc_now_iso(),
                },
                "persisted": False,  # 内部标记：UI 可忽略，独立状态查询可核对
            }

        # TF-DB-002：重名项目持久化静默失败（假通过变体：返回 ok 但不落库）
        if FAULT_STATE.is_active("TF-DB-002"):
            dup = (
                session.query(Project)
                .filter_by(workspace_slug=ws, name=req.name)
                .first()
            )
            if dup:
                key = "TF-" + "".join(random.choices(string.digits, k=6))
                return {
                    "ok": True,
                    "project": {
                        "id": new_id("proj"),
                        "key": key,
                        "name": req.name,
                        "description": req.description,
                        "workspace_slug": ws,
                        "status": "ACTIVE",
                        "created_at": utc_now_iso(),
                    },
                    "persisted": False,  # 重名写入失败但 API 谎报成功
                }

        # TF-FLAKY-001：按 50% 概率跳过持久化（Flaky 数据源）
        if FAULT_STATE.is_active("TF-FLAKY-001") and random.random() < FAULT_STATE.param(
            "TF-FLAKY-001", "probability", 0.5
        ):
            key = "TF-" + "".join(random.choices(string.digits, k=6))
            return {
                "ok": True,
                "project": {
                    "id": new_id("proj"),
                    "key": key,
                    "name": req.name,
                    "description": req.description,
                    "workspace_slug": ws,
                    "status": "ACTIVE",
                    "created_at": utc_now_iso(),
                },
                "persisted": False,
            }

        # 正常持久化
        existing = session.query(Workspace).filter_by(slug=ws).first()
        if not existing:
            session.add(Workspace(slug=ws, name=ws, owner_username="tester"))
            session.flush()
        # TF-DB-005：持久化写入慢（4000ms，最终落库）
        if FAULT_STATE.is_active("TF-DB-005"):
            import asyncio

            await asyncio.sleep(4.0)

        key = "TF-" + "".join(random.choices(string.digits, k=6))
        p = Project(
            workspace_slug=ws,
            key=key,
            name=req.name,
            description=req.description,
            status="ACTIVE",
            created_by="tester",
        )
        session.add(p)
        session.commit()
        # 供 TF-DB-003/004 滞后窗口计算
        import time as _t

        for lag_fault in ("TF-DB-003", "TF-DB-004"):
            if FAULT_STATE.is_active(lag_fault):
                FAULT_STATE.activate(lag_fault, {"_last_create_ts": _t.time()})
        return {
            "ok": True,
            "persisted": True,
            "project": {
                "id": str(p.id),
                "key": key,
                "name": p.name,
                "description": p.description,
                "workspace_slug": ws,
                "status": p.status,
                "created_at": utc_now_iso(),
            },
        }
    finally:
        session.close()


@router.get("/api/projects")
async def list_projects(workspace: str = "default") -> dict[str, Any]:
    await apply_network_delay()
    # TF-TIME-001：列表刷新延迟（竞态 / 异步时序异常）
    if FAULT_STATE.is_active("TF-TIME-001"):
        delay_ms = FAULT_STATE.param("TF-TIME-001", "delay_ms", 1500)
        import asyncio

        await asyncio.sleep(delay_ms / 1000.0)
    # TF-TIME-002：强时序延迟 5000ms
    if FAULT_STATE.is_active("TF-TIME-002"):
        import asyncio

        await asyncio.sleep(5.0)
    # TF-SEC-002：列表读取权限异常
    if FAULT_STATE.is_active("TF-SEC-002"):
        raise HTTPException(status_code=403, detail="read permission denied (TF-SEC-002)")
    session = get_session()
    try:
        rows = session.query(Project).filter_by(workspace_slug=workspace).all()
        return {
            "ok": True,
            "items": [
                {
                    "id": str(r.id),
                    "key": r.key,
                    "name": r.name,
                    "description": r.description,
                    "status": r.status,
                    "workspace_slug": r.workspace_slug,
                }
                for r in rows
            ],
        }
    finally:
        session.close()


@router.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    session = get_session()
    try:
        r = session.query(Project).filter_by(key=project_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="project not found")
        return {
            "ok": True,
            "project": {
                "id": str(r.id),
                "key": r.key,
                "name": r.name,
                "description": r.description,
                "status": r.status,
                "workspace_slug": r.workspace_slug,
            },
        }
    finally:
        session.close()


@router.put("/api/projects/{project_id}")
async def update_project(project_id: str, req: ProjectCreateReq) -> dict[str, Any]:
    # TF-API-003：更新接口返回 500（编辑链路）
    if FAULT_STATE.is_active("TF-API-003"):
        raise HTTPException(status_code=500, detail="simulated update failure (TF-API-003)")
    # TF-API-004：更新接口返回 503（编辑链路服务不可用）
    if FAULT_STATE.is_active("TF-API-004"):
        raise HTTPException(status_code=503, detail="simulated update unavailable (TF-API-004)")
    session = get_session()
    try:
        r = session.query(Project).filter_by(key=project_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="project not found")
        r.name = req.name
        r.description = req.description
        session.commit()
        return {"ok": True, "project": {"key": r.key, "name": r.name, "description": r.description}}
    finally:
        session.close()


# ---------------- 独立业务状态查询（Trusted Oracle 用，不依赖 UI） ----------------
@router.get("/api/state/projects")
async def state_projects(workspace: str = "default") -> dict[str, Any]:
    """独立权威状态查询：直接读持久层，故障注入 TF-DB-001 时此处反映真实未持久化。

    TF-DB-003 / TF-DB-004：最终一致性滞后——状态查询返回滞后快照（Flaky 来源）。
    滞后语义：本次创建后 count 需要等 lag_ms 才反映真实值。
    """
    import time as _time

    lag_active = FAULT_STATE.is_active("TF-DB-003") or FAULT_STATE.is_active("TF-DB-004")
    if lag_active:
        lag_ms = 2000 if FAULT_STATE.is_active("TF-DB-003") else 9000
        # 记录最近一次创建时间戳：滞后窗口内返回滞后值
        last_create = FAULT_STATE.param("TF-DB-003", "_last_create_ts") or FAULT_STATE.param(
            "TF-DB-004", "_last_create_ts"
        )
        session = get_session()
        try:
            rows = session.query(Project).filter_by(workspace_slug=workspace).all()
            real_count = len(rows)
            within_lag = (
                last_create is not None
                and (_time.time() - float(last_create)) * 1000 < lag_ms
            )
            count = real_count - 1 if (within_lag and real_count > 0) else real_count
            keys = [r.key for r in rows]
            return {
                "ok": True,
                "source": "authoritative-state",
                "workspace": workspace,
                "count": count,
                "keys": keys if not within_lag else keys[:-1],
            }
        finally:
            session.close()
    session = get_session()
    try:
        rows = session.query(Project).filter_by(workspace_slug=workspace).all()
        return {
            "ok": True,
            "source": "authoritative-state",
            "workspace": workspace,
            "count": len(rows),
            "keys": [r.key for r in rows],
        }
    finally:
        session.close()


@router.get("/api/state/project/{project_key}")
async def state_project(project_key: str) -> dict[str, Any]:
    session = get_session()
    try:
        r = session.query(Project).filter_by(key=project_key).first()
        return {
            "ok": r is not None,
            "source": "authoritative-state",
            "project": None if not r else {"key": r.key, "name": r.name, "status": r.status},
        }
    finally:
        session.close()


# ---------------- 测试数据重置 ----------------
@router.post("/api/test/reset")
async def reset_test_data() -> dict[str, Any]:
    """可重复的测试数据初始化/清理。仅清空 TeamFlow 测试表。"""
    session = get_session()
    try:
        n = session.query(Project).delete()
        session.query(Workspace).delete()
        session.commit()
        FAULT_STATE.clear()
        return {"ok": True, "cleared_projects": n, "faults_cleared": True}
    finally:
        session.close()


@router.post("/api/test/seed")
async def seed_test_data() -> dict[str, Any]:
    """初始化测试种子：workspace + 测试账户数据。"""
    session = get_session()
    try:
        ws = session.query(Workspace).filter_by(slug="default").first()
        if not ws:
            session.add(
                Workspace(slug="default", name="Default Workspace", owner_username="tester")
            )
            session.commit()
        return {"ok": True, "seeded": ["workspace:default", "account:tester"]}
    finally:
        session.close()


# ---------------- 故障注入控制（仅测试侧） ----------------
@router.get("/api/faults")
async def list_faults() -> dict[str, Any]:
    return {"available": FAULT_IDS, "state": FAULT_STATE.snapshot()}


@router.post("/api/faults/{fault_id}/activate")
async def activate_fault(fault_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if fault_id not in FAULT_IDS:
        raise HTTPException(status_code=404, detail="unknown fault")
    FAULT_STATE.activate(fault_id, params or {})
    # 复合故障：同时激活底层机制（各自保持独立语义，便于证据归属）
    composite_map = {
        "TF-COMP-001": ["TF-API-001", "TF-UI-003"],  # API 失败 + 错误提示被误导
        "TF-COMP-002": ["TF-DB-001", "TF-TIME-001"],  # 持久化回滚 + 列表延迟
        "TF-COMP-003": ["TF-LOC-001", "TF-UI-002"],  # 定位器失效 + 静默忽略
    }
    for underlying in composite_map.get(fault_id, []):
        FAULT_STATE.activate(underlying)
    return {"ok": True, "activated": fault_id, "state": FAULT_STATE.snapshot()}


@router.post("/api/faults/{fault_id}/clear")
async def clear_fault(fault_id: str | None = None) -> dict[str, Any]:
    FAULT_STATE.clear(fault_id)
    return {"ok": True, "cleared": fault_id or "all", "state": FAULT_STATE.snapshot()}


# ---------------- Ground Truth：仅评测侧令牌可读 ----------------
@router.get("/api/ground-truth/{scenario_id}")
async def get_ground_truth(
    scenario_id: str,
    x_eval_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """评测侧专用。Agent 的工具网关禁止调用此路由；令牌不出现在任何业务响应中。"""
    if not GROUND_TRUTH.check_token(x_eval_token):
        raise HTTPException(status_code=403, detail="evaluation token required")
    rec = GROUND_TRUTH.get(scenario_id)
    if not rec:
        raise HTTPException(status_code=404, detail="ground truth not found")
    return {
        "scenario_id": rec.scenario_id,
        "fault_mechanism_id": rec.fault_mechanism_id,
        "expected_business_state": rec.expected_business_state,
        "expected_failure_class": rec.expected_failure_class,
        "notes": rec.notes,
    }


# ---------------- Benchmark 场景元数据（阶段1开始积累） ----------------
@router.get("/api/scenarios")
async def list_scenarios() -> dict[str, Any]:
    return {
        "scenario_refs": GROUND_TRUTH.all_public_refs(),
        "note": "仅引用，不含 Ground Truth 内容",
    }
