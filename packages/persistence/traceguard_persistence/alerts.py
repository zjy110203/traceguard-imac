"""RUN-018 WP8：运行告警引擎（规则评估 + 状态机 + 持久化）。

告警类型（WP8 §8.2 最小集）：
- WORKER_OFFLINE   Worker 心跳超时 / 长时间离线
- QUEUE_BACKLOG    队列积压（数量或最老等待超阈值）
- RUN_FAILURE_RATE 24h Run 失败率异常
- DB_UNREACHABLE   数据库不可达
- ARTIFACT_INTEGRITY 产物缺失 / 哈希不一致
- GC_ANOMALY       GC 回收异常（审计记录出现 error）
- RUN_TIMEOUT      长时间运行任务超时（超过 provisional 上限仍未完成）

状态机：OPEN → ACKNOWLEDGED → RESOLVED（终态）。
- 人工处理：acknowledge（记录处理主体+时间）、resolve（记录处理主体+时间+解决原因）；
- 自动处理：触发条件消失时引擎自动 RESOLVED，解决原因为 condition_cleared；
- RESOLVED 后条件再次出现 → 新建告警（不复活旧记录），历史可追溯。

触发时机（不引入新调度基础设施）：
- API 进程：`/api/v1/monitoring/metrics` 与 `/api/v1/monitoring/alerts` 每次读取前评估；
- Worker 进程：主循环节流评估（默认 ≥30s 一次，失败不影响执行）。

所有阈值取自 `monitoring.effective_thresholds()`，均为 **provisional**，
不是 SLA；告警 detail 中随记录带出触发值便于人工核对。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from traceguard_core.ids import new_id

from .db import Database
from .monitoring import effective_thresholds, note_db_failure
from .models import AlertRow

#: 状态机：允许的迁移
ALERT_TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"ACKNOWLEDGED", "RESOLVED"},
    "ACKNOWLEDGED": {"RESOLVED"},
    "RESOLVED": set(),
}

ALERT_TYPES = (
    "WORKER_OFFLINE",
    "QUEUE_BACKLOG",
    "RUN_FAILURE_RATE",
    "DB_UNREACHABLE",
    "ARTIFACT_INTEGRITY",
    "GC_ANOMALY",
    "RUN_TIMEOUT",
)

SEVERITIES = ("INFO", "WARNING", "CRITICAL")

_ENGINE_ACTOR = "system:alert-engine"


def _unreliable_types(snapshot: dict[str, Any]) -> set[str]:
    """快照中数据源报错/缺数的告警类型：视为"未知"，禁止据此自动解决。"""
    bad: set[str] = set()
    if (snapshot.get("worker") or {}).get("error"):
        bad.add("WORKER_OFFLINE")
    if (snapshot.get("queue") or {}).get("error"):
        bad.update({"QUEUE_BACKLOG", "RUN_TIMEOUT"})
    if (snapshot.get("run") or {}).get("error"):
        bad.add("RUN_FAILURE_RATE")
    if (snapshot.get("artifact") or {}).get("error"):
        bad.update({"ARTIFACT_INTEGRITY", "GC_ANOMALY"})
    return bad


class AlertStateError(ValueError):
    """非法状态迁移（含重复处理）。"""


class AlertEngine:
    """规则评估 + 告警记录生命周期管理。"""

    def __init__(self, db: Database, *, min_evaluate_interval_s: float = 0.0) -> None:
        self.db = db
        self.min_evaluate_interval_s = min_evaluate_interval_s
        self._last_eval = 0.0

    # ------------------------------------------------------------ evaluation
    def evaluate(self, snapshot: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        """按当前指标快照评估全部规则并落库。

        返回：本次触发的告警指纹、自动解决的告警、活跃告警计数。
        幂等：同一持续条件不会重复新建（fingerprint 去重，occurrences 累加）。
        """
        now_ts = time.time() if now is None else now
        if (
            self.min_evaluate_interval_s > 0
            and now_ts - self._last_eval < self.min_evaluate_interval_s
        ):
            return {"skipped": True, "reason": "min_evaluate_interval"}
        self._last_eval = now_ts
        findings = self._rules(snapshot, now=now_ts)
        active_fps = {f["fingerprint"] for f in findings}
        # RUN-018 WP8：自动解决的两道防护（修复快照 TOCTOU 竞态）——
        # 1) 快照 `generated_at` 之后才创建的告警不因这份旧快照被自动解决
        #    （Worker 线程"先采集、后评估"期间，API 侧可能新建告警）；
        # 2) 对应数据源在本次快照中报错/缺数时，视为"未知"而非"已恢复"。
        generated_at = float(snapshot.get("generated_at") or now_ts)
        unreliable = _unreliable_types(snapshot)
        created: list[str] = []
        updated: list[str] = []
        auto_resolved: list[str] = []
        try:
            with self.db.session() as s:
                open_rows = list(
                    s.scalars(select(AlertRow).where(AlertRow.status.in_(["OPEN", "ACKNOWLEDGED"])))
                )
                by_fp = {r.fingerprint: r for r in open_rows}
                for f in findings:
                    row = by_fp.get(f["fingerprint"])
                    if row is None:
                        row = AlertRow(
                            alert_id=new_id("alrt"),
                            fingerprint=f["fingerprint"],
                            alert_type=f["alert_type"],
                            severity=f["severity"],
                            status="OPEN",
                            title=f["title"],
                            message=f["message"],
                            detail=f.get("detail") or {},
                            scope_workspace=f.get("scope_workspace"),
                            ref_run_id=f.get("ref_run_id"),
                            ref_worker_id=f.get("ref_worker_id"),
                            occurrences=1,
                            first_seen=now_ts,
                            last_seen=now_ts,
                        )
                        s.add(row)
                        created.append(f["fingerprint"])
                    else:
                        row.occurrences = int(row.occurrences or 1) + 1
                        row.last_seen = now_ts
                        row.message = f["message"]
                        row.detail = f.get("detail") or row.detail
                        row.severity = f["severity"]
                        updated.append(f["fingerprint"])
                for row in open_rows:
                    if row.fingerprint in active_fps:
                        continue
                    if float(row.created_at or 0.0) > generated_at:
                        continue  # 快照之后才创建：本次评估无权判定其已恢复
                    if row.alert_type in unreliable:
                        continue  # 数据源异常：保持活跃，交由下次健康快照/人工处理
                    row.status = "RESOLVED"
                    row.resolved_by = _ENGINE_ACTOR
                    row.resolved_at = now_ts
                    row.resolution_reason = "condition_cleared"
                    row.updated_at = now_ts
                    auto_resolved.append(row.fingerprint)
        except Exception as exc:  # noqa: BLE001
            note_db_failure("write", str(exc))
            return {
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "findings": [f["fingerprint"] for f in findings],
            }
        return {
            "evaluated_at": now_ts,
            "findings": [f["fingerprint"] for f in findings],
            "created": created,
            "reinforced": updated,
            "auto_resolved": auto_resolved,
            "open": self._count_status("OPEN"),
            "acknowledged": self._count_status("ACKNOWLEDGED"),
        }

    def _count_status(self, status: str) -> int:
        from sqlalchemy import func

        try:
            with self.db.session() as s:
                return int(
                    s.scalar(
                        select(func.count()).select_from(AlertRow).where(AlertRow.status == status)
                    )
                    or 0
                )
        except Exception:  # noqa: BLE001
            return 0

    # ----------------------------------------------------------------- rules
    def _rules(self, snapshot: dict[str, Any], *, now: float) -> list[dict[str, Any]]:
        th = effective_thresholds()
        findings: list[dict[str, Any]] = []

        # 1) DB_UNREACHABLE
        db = snapshot.get("database") or {}
        if db.get("ok") is False:
            findings.append(
                {
                    "fingerprint": "DB_UNREACHABLE",
                    "alert_type": "DB_UNREACHABLE",
                    "severity": "CRITICAL",
                    "title": "数据库不可达",
                    "message": f"数据库探活失败：{str(db.get('error') or '')[:200]}",
                    "detail": {"error": db.get("error"), "thresholds": {"status": "provisional"}},
                }
            )

        # 2) WORKER_OFFLINE（逐 Worker；另：全无在线 Worker 时升级 CRITICAL）
        worker = snapshot.get("worker") or {}
        workers = worker.get("workers") or []
        offline_after = float(th.get("worker_offline_seconds"))
        offline_workers = [w for w in workers if not w.get("online")]
        for w in offline_workers:
            findings.append(
                {
                    "fingerprint": f"WORKER_OFFLINE:{w.get('worker_id')}",
                    "alert_type": "WORKER_OFFLINE",
                    "severity": "CRITICAL" if not worker.get("online") else "WARNING",
                    "title": f"Worker 离线：{w.get('worker_id')}",
                    "message": (
                        f"Worker {w.get('worker_id')} 心跳已 {w.get('heartbeat_age_s')}s 未更新"
                        f"（provisional 阈值 {offline_after}s）"
                    ),
                    "detail": {
                        "worker_id": w.get("worker_id"),
                        "heartbeat_age_s": w.get("heartbeat_age_s"),
                        "last_heartbeat_at": w.get("last_heartbeat_at"),
                        "threshold_s": offline_after,
                        "threshold_status": "provisional",
                    },
                    "ref_worker_id": w.get("worker_id"),
                }
            )

        # 3) QUEUE_BACKLOG（数量或最老等待任一超阈值；同一指纹去重）
        queue = snapshot.get("queue") or {}
        if not queue.get("error"):
            backlog_n = int(queue.get("queued") or 0)
            oldest_wait = float(queue.get("oldest_queued_wait_s") or 0.0)
            n_th = int(th.get("queue_backlog_count"))
            w_th = float(th.get("queue_oldest_wait_seconds"))
            reasons = []
            if backlog_n > n_th:
                reasons.append(f"QUEUED={backlog_n} > {n_th}")
            if oldest_wait > w_th:
                reasons.append(f"最老等待 {oldest_wait:.0f}s > {w_th:.0f}s")
            if reasons:
                findings.append(
                    {
                        "fingerprint": "QUEUE_BACKLOG",
                        "alert_type": "QUEUE_BACKLOG",
                        "severity": "WARNING",
                        "title": "队列积压",
                        "message": "；".join(reasons) + "（阈值 provisional）",
                        "detail": {
                            "queued": backlog_n,
                            "running": queue.get("running"),
                            "oldest_queued_wait_s": oldest_wait,
                            "thresholds": {
                                "queue_backlog_count": n_th,
                                "queue_oldest_wait_seconds": w_th,
                                "status": "provisional",
                            },
                        },
                    }
                )

            # 4) RUN_TIMEOUT：长时间未完成的 Run（逐 Run 报警，便于跳转）
            overdue = queue.get("overdue_running") or []
            for item in overdue[:10]:
                findings.append(
                    {
                        "fingerprint": f"RUN_TIMEOUT:{item.get('run_id')}",
                        "alert_type": "RUN_TIMEOUT",
                        "severity": "WARNING",
                        "title": f"Run 长时间未完成：{item.get('run_id')}",
                        "message": (
                            f"Run {item.get('run_id')} 已等待/运行 {item.get('waited_s')}s "
                            f"仍未完成（provisional 上限 {queue.get('overdue_threshold_s')}s）"
                        ),
                        "detail": {
                            **item,
                            "threshold_s": queue.get("overdue_threshold_s"),
                            "threshold_status": "provisional",
                        },
                        "ref_run_id": item.get("run_id"),
                        "ref_worker_id": item.get("worker_id"),
                    }
                )

        # 5) RUN_FAILURE_RATE（24h，最小样本量保护）
        run = snapshot.get("run") or {}
        last24 = run.get("last_24h") or {}
        if not run.get("error"):
            evaluated = int(last24.get("evaluated") or 0)
            min_samples = int(th.get("run_failure_rate_min_samples"))
            rate_th = float(th.get("run_failure_rate"))
            by_v = last24.get("by_verdict") or {}
            failed = int(by_v.get("FAILED") or 0)
            if evaluated >= min_samples and evaluated > 0:
                failure_rate = failed / evaluated
                if failure_rate >= rate_th:
                    findings.append(
                        {
                            "fingerprint": "RUN_FAILURE_RATE",
                            "alert_type": "RUN_FAILURE_RATE",
                            "severity": "CRITICAL" if failure_rate >= 0.8 else "WARNING",
                            "title": "Run 失败率异常（24h）",
                            "message": (
                                f"24h 失败率 {failure_rate:.0%}（{failed}/{evaluated}）"
                                f" ≥ 阈值 {rate_th:.0%}（provisional）"
                            ),
                            "detail": {
                                "failure_rate": round(failure_rate, 4),
                                "failed": failed,
                                "evaluated": evaluated,
                                "threshold": rate_th,
                                "min_samples": min_samples,
                                "threshold_status": "provisional",
                            },
                        }
                    )

        # 6) ARTIFACT_INTEGRITY（缺失 / 哈希不一致）
        artifact = snapshot.get("artifact") or {}
        if not artifact.get("error"):
            missing = artifact.get("missing_files") or []
            mismatch = artifact.get("hash_mismatch") or []
            if missing:
                findings.append(
                    {
                        "fingerprint": "ARTIFACT_INTEGRITY:missing",
                        "alert_type": "ARTIFACT_INTEGRITY",
                        "severity": "CRITICAL",
                        "title": f"产物文件缺失（{len(missing)}）",
                        "message": f"登记在册但落盘缺失的产物 {len(missing)} 个",
                        "detail": {
                            "missing_files": missing[:20],
                            "threshold_status": "provisional",
                        },
                    }
                )
            if mismatch:
                findings.append(
                    {
                        "fingerprint": "ARTIFACT_INTEGRITY:hash",
                        "alert_type": "ARTIFACT_INTEGRITY",
                        "severity": "CRITICAL",
                        "title": f"产物哈希不一致（{len(mismatch)}）",
                        "message": f"文件内容 SHA-256 与登记不一致的产物 {len(mismatch)} 个",
                        "detail": {
                            "hash_mismatch": mismatch[:20],
                            "hash_check": artifact.get("hash_check"),
                            "threshold_status": "provisional",
                        },
                    }
                )

        # 7) GC_ANOMALY
        gc = (artifact.get("gc") or {}) if isinstance(artifact, dict) else {}
        gc_errors = int(gc.get("errors") or 0)
        if gc_errors > int(th.get("gc_error_max")):
            findings.append(
                {
                    "fingerprint": "GC_ANOMALY",
                    "alert_type": "GC_ANOMALY",
                    "severity": "WARNING",
                    "title": "GC 回收异常",
                    "message": f"GC 审计记录中出现 {gc_errors} 条 error 结果",
                    "detail": {"gc": gc, "threshold_status": "provisional"},
                }
            )
        return findings

    # ------------------------------------------------------------ lifecycle
    def list_alerts(
        self,
        *,
        status: str | None = None,
        alert_type: str | None = None,
        workspace_ids: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """告警列表（新→旧）。

        `workspace_ids=None`（platform-admin）→ 全部；
        否则只返回系统级（scope_workspace 为空）+ 授权 Workspace 的告警。
        """
        with self.db.session() as s:
            q = select(AlertRow)
            if status:
                q = q.where(AlertRow.status == status.upper())
            if alert_type:
                q = q.where(AlertRow.alert_type == alert_type.upper())
            if workspace_ids is not None:
                q = q.where(
                    (AlertRow.scope_workspace.is_(None))
                    | (AlertRow.scope_workspace.in_(list(workspace_ids)))
                )
            rows = s.scalars(q.order_by(AlertRow.last_seen.desc()).limit(max(1, min(limit, 500)))).all()
            return [self._row_dict(r) for r in rows]

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.get(AlertRow, alert_id)
            return self._row_dict(row) if row else None

    def acknowledge(self, alert_id: str, actor: str, *, now: float | None = None) -> dict[str, Any]:
        """OPEN → ACKNOWLEDGED；记录处理主体与处理时间。"""
        now_ts = time.time() if now is None else now
        with self.db.session() as s:
            row = s.get(AlertRow, alert_id)
            if row is None:
                raise KeyError(alert_id)
            self._assert_transition(row.status, "ACKNOWLEDGED")
            row.status = "ACKNOWLEDGED"
            row.acknowledged_by = str(actor or "")[:80] or "unknown"
            row.acknowledged_at = now_ts
            row.updated_at = now_ts
            s.flush()
            return self._row_dict(row)

    def resolve(
        self,
        alert_id: str,
        actor: str,
        reason: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """OPEN/ACKNOWLEDGED → RESOLVED；解决原因必填。"""
        if not (reason or "").strip():
            raise ValueError("RESOLUTION_REASON_REQUIRED")
        now_ts = time.time() if now is None else now
        with self.db.session() as s:
            row = s.get(AlertRow, alert_id)
            if row is None:
                raise KeyError(alert_id)
            self._assert_transition(row.status, "RESOLVED")
            row.status = "RESOLVED"
            row.resolved_by = str(actor or "")[:80] or "unknown"
            row.resolved_at = now_ts
            row.resolution_reason = str(reason)[:2000]
            row.updated_at = now_ts
            s.flush()
            return self._row_dict(row)

    @staticmethod
    def _assert_transition(current: str, target: str) -> None:
        if target not in ALERT_TRANSITIONS.get(current, set()):
            raise AlertStateError(f"illegal alert transition {current}->{target}")

    @staticmethod
    def _row_dict(r: AlertRow) -> dict[str, Any]:
        return {
            "alert_id": r.alert_id,
            "fingerprint": r.fingerprint,
            "alert_type": r.alert_type,
            "severity": r.severity,
            "status": r.status,
            "title": r.title,
            "message": r.message,
            "detail": dict(r.detail or {}),
            "scope_workspace": r.scope_workspace,
            "ref_run_id": r.ref_run_id,
            "ref_worker_id": r.ref_worker_id,
            "occurrences": r.occurrences,
            "first_seen": r.first_seen,
            "last_seen": r.last_seen,
            "acknowledged_by": r.acknowledged_by,
            "acknowledged_at": r.acknowledged_at,
            "resolved_by": r.resolved_by,
            "resolved_at": r.resolved_at,
            "resolution_reason": r.resolution_reason,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }


__all__ = [
    "ALERT_TRANSITIONS",
    "ALERT_TYPES",
    "AlertEngine",
    "AlertStateError",
    "SEVERITIES",
]
