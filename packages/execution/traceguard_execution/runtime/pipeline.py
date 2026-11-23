"""Run 执行 Pipeline：把 TaskService / TestCase / Oracle / Observer / Evidence 串成真实链路。

链路：
    Run(QUEUED) → 领取租约 → 加载用例版本与 Oracle → 浏览器执行步骤（逐步落库 + 事件 + 证据）
    → Trusted Oracle 独立状态查询 → Assertion 判定 → Verdict 聚合 → 事件收尾 → 提交结果

设计原则：
- 执行器不做"是否通过"的推断，只用确定性规则；
- 任何步骤异常都保留已采集证据，Run 标记 FAILED / verdict NOT_EVALUATED；
- 真实浏览器与 HTTP 降级严格区分（observer_mode 写入 Run 与报告）；
- 证据通过 artifact_sink 上传并登记为不可变 Evidence（raw）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from traceguard_contracts.common import ExecutionStatus
from traceguard_contracts.models import AssertionSpec, OracleSpec, TestCaseVersion, TestStep
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_core.logging import get_logger, redact
from traceguard_persistence.catalog import CatalogService
from traceguard_persistence.db import Database
from traceguard_persistence.stores import (
    ArtifactStore,
    PersistentEventStore,
    PersistentEvidenceManager,
)

from ..browser.manager import BrowserConfig, get_browser_manager
from ..tasks.service import LeaseHeartbeat, TaskService
from ..verdict import VerdictInput, aggregate
from .assertions import evaluate_assertion
from .http_observer import HttpObserver
from .observer import ObservationRecord, Observer
from .playwright_observer import PlaywrightObserver

logger = get_logger("traceguard.pipeline")

DEFAULT_STATE_PATH = "/api/state/projects?workspace=default"


@dataclass
class ExecutionOptions:
    headless: bool = True
    channel: str = ""
    capture_screenshot: bool = True
    capture_dom: bool = True
    capture_aria: bool = True
    trace: bool = True
    max_duration_s: int = 180
    state_path: str = DEFAULT_STATE_PATH


@dataclass
class RunOutcome:
    run_id: str
    execution_status: str
    test_verdict: str
    observer_mode: str
    report: dict[str, Any] = field(default_factory=dict)


class RunPipeline:
    def __init__(
        self,
        db: Database,
        tasks: TaskService,
        catalog: CatalogService,
        events: PersistentEventStore,
        evidence: PersistentEvidenceManager,
        artifacts: ArtifactStore,
        *,
        worker_id: str = "inline-worker",
        options: ExecutionOptions | None = None,
    ) -> None:
        self.db = db
        self.tasks = tasks
        self.catalog = catalog
        self.events = events
        self.evidence = evidence
        self.artifacts = artifacts
        self.worker_id = worker_id
        self.options = options or ExecutionOptions()
        self._producer_instance = events.new_producer_instance(worker_id)
        #: 单次 Run 内的业务状态读取缓存（避免同一 Run 重复打被测应用状态接口）。
        #: **必须每次 execute 清空**：常驻 Worker 复用同一 pipeline 实例跨 Run 使用，
        #: 若跨 Run 复用缓存，断言会读到上一次 Run 的业务状态（RUN-007-RESUME 实测缺陷：
        #: 假通过检测因此在长驻 Worker 上被判为 PASSED）。
        self._state_cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ public
    def execute(self, run_id: str, *, lease: dict[str, Any] | None = None) -> RunOutcome:
        self._state_cache.clear()  # 状态缓存的生存期严格等于一次 Run
        run = self.tasks.get_run(run_id)
        if not run:
            raise AppError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
        if lease is None:
            lease = self.tasks.acquire_lease(run_id, self.worker_id, ttl_seconds=600)
        token = int(lease["fencing_token"])
        heartbeat = LeaseHeartbeat(
            self.tasks, run_id, self.worker_id, token, ttl_s=float(lease.get("ttl_s", 600))
        ).start()
        task = self.tasks.get_task(run["task_id"]) or {}
        workspace_id = run.get("workspace_id") or "default"

        self._emit(
            "run.started",
            workspace_id,
            {"run_id": run_id, "task_id": run["task_id"], "attempt": run["attempt"]},
            idem=f"{run_id}:started",
        )

        started = time.time()
        report: dict[str, Any] = {}
        observer_mode = ""
        try:
            tc = self._load_testcase(run)
            if tc is None:
                raise AppError(ErrorCode.PRECONDITION_FAILED, "TESTCASE_VERSION_NOT_FOUND")
            self._guard_reviewed(tc, task)
            oracles = self._load_oracles(tc)
            observer, observer_mode = self._build_observer(task, run, workspace_id)
            self.tasks.mark_running(run_id, self.worker_id, token, observer_mode)

            report = self._run_with_observer(
                run=run,
                task=task,
                tc=tc,
                oracles=oracles,
                observer=observer,
                workspace_id=workspace_id,
                token=token,
                started=started,
            )
            verdict = report["test_verdict"]
            execution_status = report["execution_status"]
        except AppError as exc:
            execution_status, verdict, observer_mode = "FAILED", "NOT_EVALUATED", observer_mode
            report = self._failure_report(run, str(exc.message), started, observer_mode)
            self._emit(
                "run.failed",
                workspace_id,
                {"run_id": run_id, "error": str(exc.message), "code": exc.code.value},
                idem=f"{run_id}:failed",
            )
        except Exception as exc:  # noqa: BLE001 - 执行器必须收敛所有异常
            execution_status, verdict = "FAILED", "NOT_EVALUATED"
            logger.exception("run_crashed", run_id=run_id)
            report = self._failure_report(
                run, f"{type(exc).__name__}: {exc}", started, observer_mode
            )
            self._emit(
                "run.failed",
                workspace_id,
                {"run_id": run_id, "error": str(exc)[:1000]},
                idem=f"{run_id}:failed",
            )

        heartbeat.stop()
        self.tasks.submit_result(
            run_id,
            self.worker_id,
            token,
            execution_status=execution_status,
            test_verdict=verdict,
            report=report,
            observer_mode=observer_mode or None,
            error=report.get("error"),
        )
        self._emit(
            "run.completed",
            workspace_id,
            {
                "run_id": run_id,
                "execution_status": execution_status,
                "test_verdict": verdict,
                "observer_mode": observer_mode,
                "duration_ms": report.get("duration_ms", 0),
            },
            idem=f"{run_id}:completed",
        )
        return RunOutcome(
            run_id=run_id,
            execution_status=execution_status,
            test_verdict=verdict,
            observer_mode=observer_mode,
            report=report,
        )

    # --------------------------------------------------------------- internals
    def _load_testcase(self, run: dict[str, Any]) -> TestCaseVersion | None:
        ref = run.get("testcase_version_id") or ""
        tc_id, version = _split_version_ref(ref)
        if tc_id and version:
            doc = self.catalog.get_version(tc_id, version)
        elif tc_id:
            doc = self.catalog.latest_approved_version(tc_id)
        else:
            task = self.tasks.get_task(run["task_id"]) or {}
            tc_id = task.get("testcase_ref", "")
            doc = self.catalog.latest_approved_version(tc_id) if tc_id else None
        if not doc:
            return None
        # 文档里带有审核/绑定等元数据（review_history / oracle_bindings / review_revision），
        # 契约模型是 extra=forbid，必须按模型字段过滤而不是只删一个键
        allowed = set(TestCaseVersion.model_fields)
        return TestCaseVersion(**{k: v for k, v in doc.items() if k in allowed})

    def _guard_reviewed(self, tc: TestCaseVersion, task: dict[str, Any] | None = None) -> None:
        # 沙箱验证允许执行 PENDING 候选版本（显式 sandbox 标记，不是普通入口）
        # 两个来源：版本自身 run_config.sandbox_only，或 Task.mode == "sandbox"（候选回放）
        if tc.review_status == "PENDING" and (
            (tc.run_config or {}).get("sandbox_only") is True
            or (task or {}).get("mode") == "sandbox"
        ):
            return
        if tc.review_status != "APPROVED":
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"TESTCASE_NOT_APPROVED: review_status={tc.review_status}",
            )

    def _load_oracles(self, tc: TestCaseVersion) -> dict[str, OracleSpec]:
        """加载 Oracle：审核时固定的版本优先，绝不静默改用"最新版本"。"""
        out: dict[str, OracleSpec] = {}
        for spec in tc.assertions:
            if not spec.oracle_id:
                continue
            pinned = getattr(spec, "oracle_version", None)
            doc = self.catalog.get_oracle(spec.oracle_id, int(pinned) if pinned else None)
            if not doc:
                if pinned:
                    # 固定版本被删除/不可读 → 该断言没有可信 Oracle（不得回退到最新版本）
                    logger.warning(
                        "pinned_oracle_missing",
                        oracle_id=spec.oracle_id,
                        pinned_version=pinned,
                    )
                continue
            if not doc.get("approved", False):
                # 未审批的 Oracle 不作为判定依据（避免 Agent 自批自用）
                continue
            try:
                out[spec.oracle_id] = OracleSpec(
                    **{k: v for k, v in doc.items() if k in OracleSpec.model_fields}
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("oracle_parse_failed", oracle_id=spec.oracle_id, error=str(exc))
        return out

    def _build_observer(
        self, task: dict[str, Any], run: dict[str, Any], workspace_id: str
    ) -> tuple[Observer, str]:
        mode = (task.get("mode") or "playwright").lower()
        sink = self._artifact_sink(workspace_id, run["run_id"])

        def artifact_sink(data: bytes, media_type: str, name: str) -> dict[str, Any]:
            return sink(data, media_type, name)

        if mode in {"http", "http-fallback", "fallback"}:
            return HttpObserver(), "http-fallback"
        # 同一线程复用同一个 Playwright/Browser：sync API 不能在同线程反复 start/stop
        manager = get_browser_manager(
            BrowserConfig(headless=self.options.headless, channel=self.options.channel)
        )
        observer = PlaywrightObserver(
            manager,
            artifact_sink=artifact_sink,
            capture_screenshot=self.options.capture_screenshot,
            capture_dom=self.options.capture_dom,
            capture_aria=self.options.capture_aria,
        )
        return observer, "playwright"

    def _artifact_sink(self, workspace_id: str, run_id: str):
        def sink(data: bytes, media_type: str, name: str) -> dict[str, Any]:
            meta = self.artifacts.put(
                data, workspace_id=workspace_id, media_type=media_type, original_name=name
            )
            return meta

        return sink

    def _run_with_observer(
        self,
        *,
        run: dict[str, Any],
        task: dict[str, Any],
        tc: TestCaseVersion,
        oracles: dict[str, OracleSpec],
        observer: Observer,
        workspace_id: str,
        token: int,
        started: float,
        heartbeat: LeaseHeartbeat | None = None,
    ) -> dict[str, Any]:
        default_base = (
            "http://127.0.0.1:8001" if task.get("application") == "teamflow" else base_url_default()
        )
        base_url = str(tc.run_config.get("base_url", default_base)).rstrip("/")
        run_config = dict(tc.run_config or {})
        run_config.setdefault("base_url", base_url)
        run_config["run_id"] = run["run_id"]

        observer.setup(run_config)
        observer_mode = observer.mode

        # 执行前清理，保证可重复
        if tc.cleanup_policy == "RESET_DATA":
            _http_post(base_url, "/api/test/reset", {})

        # api 预置步骤（故障注入 / 数据准备）
        for step in tc.steps:
            if step.action == "api":
                _run_api_step(base_url, step)

        records: list[ObservationRecord] = []
        evidence_ids: list[str] = []
        errors: list[str] = []
        interrupted = False
        deadline = started + self.options.max_duration_s
        step_events = 0

        for idx, step in enumerate(tc.steps):
            if heartbeat is not None and heartbeat.lost:
                raise AppError(ErrorCode.CONFLICT, "LEASE_LOST: 租约已被回收，停止写入证据")
            if step.action == "api":
                rec = ObservationRecord(
                    step_id=step.step_id, action="api", observation={"preset": True}
                )
                self.tasks.record_step(
                    run["run_id"],
                    step.step_id,
                    idx,
                    "PASSED",
                    action="api",
                    observation=rec.observation,
                )
                records.append(rec)
                continue
            if time.time() > deadline:
                interrupted = True
                errors.append("RUN_TIMEOUT")
                self.tasks.record_step(
                    run["run_id"], step.step_id, idx, "TIMED_OUT", "RUN_TIMEOUT", action=step.action
                )
                break

            rec = observer.observe(step, {"index": idx})
            records.append(rec)
            status = "FAILED" if rec.error else "PASSED"
            self.tasks.record_step(
                run["run_id"],
                step.step_id,
                idx,
                status,
                rec.error,
                action=step.action,
                duration_ms=rec.duration_ms,
                observation=_compact_observation(rec.observation),
            )
            ev_ids = self._record_step_evidence(
                workspace_id, run["run_id"], rec, step, observer_mode
            )
            evidence_ids.extend(ev_ids)
            step_events += 1
            self._emit(
                "step.completed",
                workspace_id,
                {
                    "run_id": run["run_id"],
                    "step_id": step.step_id,
                    "index": idx,
                    "action": step.action,
                    "status": status,
                    "error": rec.error,
                    "duration_ms": rec.duration_ms,
                    "evidence_ids": ev_ids,
                },
                idem=f"{run['run_id']}:step:{step.step_id}",
            )
            if rec.error:
                interrupted = True
                errors.append(f"{step.step_id}: {rec.error}")
                # 失败即停：后续步骤不再执行，避免证据噪声（可在 run_config.fail_fast=false 关闭）
                if run_config.get("fail_fast", True):
                    break

        # 收尾：trace 证据
        trace_ref = None
        drain = getattr(observer, "drain_trace", None)
        try:
            observer.teardown()
        except Exception:  # noqa: BLE001
            logger.warning("observer_teardown_failed", run_id=run["run_id"])
        if callable(drain):
            try:
                trace_ref = drain()
            except Exception:  # noqa: BLE001
                trace_ref = None

        # Trusted Oracle：独立业务状态查询（页面文本不算业务事实）
        state_ctx = self._query_states(tc, base_url)
        state_evidence = self._record_state_evidence(
            workspace_id, run["run_id"], tc, base_url, state_ctx
        )
        evidence_ids.extend(state_evidence)

        outcomes = []
        for spec in tc.assertions:
            oracle = oracles.get(spec.oracle_id or "")
            observation = _observation_for(spec, records, state_ctx)
            outcome = evaluate_assertion(
                spec, oracle, observation, execution_interrupted=interrupted
            )
            outcomes.append(outcome)
            self._emit(
                "assertion.evaluated",
                workspace_id,
                {
                    "run_id": run["run_id"],
                    "assertion_id": outcome.assertion_id,
                    "status": outcome.status.value,
                    "reason": outcome.reason,
                },
                idem=f"{run['run_id']}:assert:{spec.assertion_id}",
            )

        verdict = aggregate(
            VerdictInput(
                assertion_outcomes=outcomes,
                execution_status=ExecutionStatus.COMPLETED
                if not interrupted
                else ExecutionStatus.FAILED,
                execution_completed=not interrupted,
            )
        )
        ended = time.time()
        summary = _summarize(outcomes)
        report = {
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "workspace_id": workspace_id,
            "testcase_id": tc.testcase_id,
            "testcase_version": tc.version,
            "observer_mode": observer_mode,
            "browser": getattr(observer, "browser_info", {}),
            "execution_status": "COMPLETED" if not interrupted else "FAILED",
            "test_verdict": verdict.value,
            "started_at": utc_now_iso(),
            "ended_at": utc_now_iso(),
            "duration_ms": int((ended - started) * 1000),
            "steps": [
                {
                    "step_id": r.step_id,
                    "action": r.action,
                    "status": "FAILED" if r.error else "PASSED",
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                    "observation": _compact_observation(r.observation),
                    "artifacts": r.artifacts,
                    "evidence_ids": r.evidence_refs,
                }
                for r in records
            ],
            "assertion_outcomes": [
                {
                    "assertion_id": o.assertion_id,
                    "status": o.status.value,
                    "required": o.required,
                    "kind": next(
                        (a.kind for a in tc.assertions if a.assertion_id == o.assertion_id), "ui"
                    ),
                    "oracle_available": o.oracle_available,
                    "reason": o.reason,
                }
                for o in outcomes
            ],
            "summary": summary,
            "oracle_state": state_ctx,
            "evidence_ids": evidence_ids,
            "trace_artifact": trace_ref,
            "errors": errors,
            "error": errors[0] if errors else None,
            "step_event_count": step_events,
        }
        return report

    # --------------------------------------------------------------- evidence
    def _record_state_evidence(
        self,
        workspace_id: str,
        run_id: str,
        tc: TestCaseVersion,
        base_url: str,
        state_ctx: dict[str, Any],
    ) -> list[str]:
        """把 Trusted Oracle 的独立状态查询结果登记为证据（可追溯、可诊断引用）。"""
        paths: set[str] = set()
        for spec in tc.assertions:
            api_path = (spec.expected or {}).get("api")
            if isinstance(api_path, str) and api_path.startswith("/"):
                paths.add(api_path)
        if not paths:
            paths.add(self.options.state_path)
        ids: list[str] = []
        for path in sorted(paths):
            key = path.split("?")[0].strip("/").replace("/", "_")
            payload = state_ctx.get(key)
            if payload is None:
                continue
            evd_id = new_id("evd")
            try:
                self.evidence.add_raw(
                    evd_id,
                    "api",
                    f"state_query:{path}",
                    workspace_id,
                    {
                        "run_id": run_id,
                        "path": path,
                        "response": payload,
                        "source": "independent_state_query",
                        "base_url": base_url,
                    },
                    source_trust="TRUSTED",
                    do_redact=True,
                    run_id=run_id,
                )
                ids.append(evd_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("state_evidence_failed", error=str(exc))
        return ids

    def _record_step_evidence(
        self,
        workspace_id: str,
        run_id: str,
        rec: ObservationRecord,
        step: TestStep,
        observer_mode: str,
    ) -> list[str]:
        ids: list[str] = []
        # 网络/控制台信号单独登记为证据（诊断需要可引用的权威来源）
        obs = rec.observation or {}
        signals = {
            "http_errors": obs.get("http_errors") or [],
            "network": obs.get("network") or [],
            "page_errors": obs.get("page_errors") or [],
            "console_errors": [c for c in (obs.get("console") or []) if c.get("type") == "error"],
        }
        if any(signals.values()):
            evd_id = new_id("evd")
            try:
                self.evidence.add_raw(
                    evd_id,
                    "network" if observer_mode == "playwright" else "network",
                    f"step_signals:{step.step_id}",
                    workspace_id,
                    {
                        "run_id": run_id,
                        "step_id": step.step_id,
                        "action": step.action,
                        "signals": signals,
                        "page_url": obs.get("page_url", ""),
                    },
                    source_trust="TRUSTED",
                    do_redact=True,
                    run_id=run_id,
                )
                ids.append(evd_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("signal_evidence_failed", error=str(exc))
        if not rec.artifacts:
            return ids
        for kind, art in rec.artifacts.items():
            evd_id = new_id("evd")
            content = {
                "run_id": run_id,
                "step_id": step.step_id,
                "action": step.action,
                "artifact": art,
                "kind": kind,
                "captured_at": utc_now_iso(),
            }
            try:
                self.evidence.add_raw(
                    evd_id,
                    "browser" if observer_mode == "playwright" else "network",
                    f"{step.action}:{step.step_id}",
                    workspace_id,
                    content,
                    source_trust="TRUSTED",
                    do_redact=True,
                    run_id=run_id,
                    attachment_ref=art.get("artifact_id"),
                    attachment_sha256=str(art.get("sha256") or ""),
                )
                ids.append(evd_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("evidence_write_failed", error=str(exc))
        rec.evidence_refs = ids
        return ids

    # ---------------------------------------------------------------- oracle
    def _query_states(self, tc: TestCaseVersion, base_url: str) -> dict[str, Any]:
        paths: set[str] = set()
        for spec in tc.assertions:
            expected = spec.expected or {}
            api_path = expected.get("api")
            if isinstance(api_path, str) and api_path.startswith("/"):
                paths.add(api_path)
        if not paths:
            paths.add(self.options.state_path)
        state: dict[str, Any] = {}
        for p in paths:
            key = p.split("?")[0].strip("/").replace("/", "_")
            data = self._cached_get(base_url, p)
            state[key] = data
            if isinstance(data, dict):
                for k, v in data.items():
                    state.setdefault(k, v)
        # 默认业务状态（TeamFlow 项目数）
        if "count" not in state:
            data = self._cached_get(base_url, self.options.state_path)
            if isinstance(data, dict) and "count" in data:
                state["count"] = data["count"]
        return state

    def _cached_get(self, base_url: str, path: str) -> dict[str, Any]:
        if path in self._state_cache:
            return self._state_cache[path]
        data = _http_get_json(base_url + path)
        self._state_cache[path] = data
        return data

    # ----------------------------------------------------------------- events
    def _emit(
        self, event_type: str, workspace_id: str, payload: dict[str, Any], *, idem: str
    ) -> None:
        try:
            self.events.append(
                event_type=event_type,
                server_workspace_id=workspace_id,
                producer_id=self.worker_id,
                producer_instance_id=self._producer_instance,
                idempotency_key=idem,
                payload=payload,
                context_refs={"run": str(payload.get("run_id", ""))},
            )
        except Exception as exc:  # noqa: BLE001 - 事件写入失败不得静默丢失
            logger.error("event_append_failed", event_type=event_type, error=str(exc))
            raise

    def _failure_report(
        self, run: dict[str, Any], error: str, started: float, observer_mode: str
    ) -> dict[str, Any]:
        return {
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "workspace_id": run.get("workspace_id", "default"),
            "observer_mode": observer_mode,
            "execution_status": "FAILED",
            "test_verdict": "NOT_EVALUATED",
            "started_at": utc_now_iso(),
            "ended_at": utc_now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "steps": [],
            "assertion_outcomes": [],
            "summary": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "not_evaluated": 0,
                "inconclusive": 0,
            },
            "evidence_ids": [],
            "errors": [error],
            "error": error,
        }


# ----------------------------------------------------------------- helpers
def base_url_default() -> str:
    from traceguard_core.config import get_settings

    return get_settings().teamflow_base_url


def _split_version_ref(ref: str) -> tuple[str, int | None]:
    if not ref:
        return "", None
    if "@" in ref:
        tc, _, v = ref.partition("@")
        return tc, int(v) if v.isdigit() else None
    if ":" in ref:
        tc, _, v = ref.partition(":")
        v = v.lstrip("v")
        return tc, int(v) if v.isdigit() else None
    return ref, None


def _http_get_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310 - 受控本地目标
            data = resp.read().decode()
        try:
            obj = json.loads(data)
            return obj if isinstance(obj, dict) else {"data": obj}
        except json.JSONDecodeError:
            return {"raw": data[:2000]}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"error": str(exc)}


def _http_post(base_url: str, path: str, body: dict[str, Any]) -> None:
    try:
        req = urllib.request.Request(
            base_url + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).close()  # noqa: S310
    except Exception:  # noqa: BLE001
        pass


def _run_api_step(base_url: str, step: TestStep) -> None:
    value = step.value or ""
    parts = value.split(" ", 1)
    if len(parts) != 2:
        return
    method, path = parts[0].upper(), parts[1]
    if method == "POST":
        _http_post(base_url, path, {})
    elif method == "GET":
        try:
            urllib.request.urlopen(base_url + path, timeout=10).close()  # noqa: S310
        except Exception:  # noqa: BLE001
            pass


def _compact_observation(obs: dict[str, Any]) -> dict[str, Any]:
    """落库前裁剪：DOM 文本与网络细节可能很大，保留可判定的关键字段。"""
    out: dict[str, Any] = {}
    for k, v in obs.items():
        if k in {"html", "content"} and isinstance(v, str):
            out[k] = v[:4000]
        elif k == "network" and isinstance(v, list):
            out[k] = v[-10:]
        elif k == "console" and isinstance(v, list):
            out[k] = v[-10:]
        elif isinstance(v, str) and len(v) > 8000:
            out[k] = v[:8000]
        else:
            out[k] = v
    return redact(out)


def _observation_for(
    spec: AssertionSpec,
    records: list[ObservationRecord],
    state: dict[str, Any],
) -> dict[str, Any]:
    """为断言挑选观测：优先匹配 test_id 的 assert 记录，否则最后一条 assert，再合并全部。"""
    asserts = [r for r in records if r.action == "assert" and not r.error]
    expected = spec.expected or {}
    chosen: ObservationRecord | None = None
    want_tid = expected.get("test_id") or spec.description
    for r in asserts:
        tid = str(r.observation.get("test_id", ""))
        if want_tid and tid and tid in str(want_tid):
            chosen = r
            break
    if chosen is None and asserts:
        chosen = asserts[-1]
    merged: dict[str, Any] = {}
    for r in records:
        for k, v in r.observation.items():
            if k not in {"network", "console", "page_errors", "http_errors"}:
                merged[k] = v
    # UI 断言（expected 含 test_id）以所选 DOM 观测为准；否则保持既有合并顺序
    ui_dom = expected.get("test_id") is not None and chosen is not None
    if chosen is not None:
        for k, v in chosen.observation.items():
            if k in {"network", "console", "page_errors", "http_errors"}:
                continue
            if ui_dom or k not in merged:
                merged[k] = v
    if ui_dom:
        # UI 断言：状态查询只补充 DOM 观测中缺失的键，不用业务 count 覆盖 DOM count
        for k, v in state.items():
            if k not in {"error", "raw"} and k not in merged:
                merged[k] = v
    else:
        merged.update({k: v for k, v in state.items() if k not in {"error", "raw"}})
    if "api" in expected:
        api = str(expected["api"])
        key = api.split("?")[0].strip("/").replace("/", "_")
        payload = state.get(key)
        if isinstance(payload, dict):
            merged["json"] = payload
            merged.update(payload)
    if "text_contains" in expected and "text" not in merged:
        merged["text"] = ""
    return merged


def _summarize(outcomes: list[Any]) -> dict[str, int]:
    summary = {
        "total": len(outcomes),
        "passed": 0,
        "failed": 0,
        "not_evaluated": 0,
        "inconclusive": 0,
    }
    for o in outcomes:
        key = o.status.value.lower()
        if key in summary:
            summary[key] += 1
    return summary


__all__ = ["ExecutionOptions", "RunOutcome", "RunPipeline"]
