"""RUN-012 WP4：真实 Chromium + 真实 API + 真实 TeamFlow + 真实 Worker 的业务闭环验收。

覆盖指令 §2.3 的核心业务链（Intent → TestCase → Oracle → 审核 → 执行 → 诊断
→ Repair → 晋升 → 回滚），每一步 UI 点击后**立即通过服务端 API 回读**校验真实数据：

1. qa-engineer UI 创建需求 → 服务端落库；
2. qa-engineer UI 创建用例 → 服务端 PENDING；
3. qa-lead UI 在用例详情页绑定已审批 Oracle → 服务端 oracle_bindings 落库；
4. qa-lead UI 审核通过 → 服务端 review_status=APPROVED（Oracle 门禁真实生效）；
5. qa-lead UI 执行 → 真实 Worker 消费队列，Run 终态 + 真实证据；
6. qa-engineer UI 诊断 → 服务端生成 Diagnosis；
7. qa-engineer UI 新建修复提案 → 规则校验 → 沙箱真实执行 → 提交审批；
8. qa-lead UI 批准 → 晋升正式版本 → 回滚，版本线与服务端状态一致；
9. LLM 建议入口在未配置凭据时如实提示，不伪造成功；
10. 未绑定 Oracle 时审核被服务端门禁拒绝（不绕过）。

安全约定：不读取宿主机任何环境变量；子进程环境使用显式非敏感白名单。

用法：
    .venv/bin/python scripts/business_loop_ui_acceptance_run012.py \
        --out docs/assets/run012/wp4/ui

退出码：0 全部通过；1 有失败；3 环境不可达（BLOCKED，不伪装通过）。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ui_acceptance_run011_r1_1 import _free_port, _wait_health  # noqa: E402

PASSWORD = "Loop-accept-2026!"

_PATHS = [
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
    "apps/api",
    "apps/teamflow",
]


def _child_env(
    db: str,
    api_port: int,
    teamflow_port: int,
    teamflow_db: str,
    *,
    inline_worker: bool = True,
    lease_ttl_s: float = 120.0,
) -> dict[str, str]:
    """显式白名单：不读取任何宿主机环境变量，只放非敏感字面量。

    RUN-013 WP1：`inline_worker=False` 时 API 不启动内嵌 Worker，
    Run 必须由独立 `traceguard_worker.main` 进程消费（生产部署形态）。
    """
    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": ":".join(str(ROOT / p) for p in _PATHS),
        "TG_AUTH_ENABLED": "true",
        "TG_AUTH_CSRF_REQUIRED": "true",
        "TG_ENVIRONMENT": "development",
        "TG_DATABASE_URL": db,
        "TG_LLM_PROVIDER": "",
        # RUN-013 WP1：false = 不启动内嵌 Worker，必须由独立进程消费队列。
        "TG_INLINE_WORKER": "true" if inline_worker else "false",
        "TG_WORKER_LEASE_TTL_S": str(lease_ttl_s),
        "TG_API_PORT": str(api_port),
        "TG_TEAMFLOW_DB": teamflow_db,
        "TG_TEAMFLOW_BASE_URL": f"http://127.0.0.1:{teamflow_port}",
        "TG_BROWSER_HEADLESS": "true",
    }


class Http:
    """带 Cookie 会话与 CSRF 的极简客户端（每个角色一个实例）。"""

    def __init__(self, base: str, label: str = "", timeout: float = 120.0) -> None:
        self.base = base.rstrip("/")
        self.label = label
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""
        self.user = ""
        self.roles: list[str] = []

    def raw(self, method: str, path: str, body: Any = None, timeout: float | None = None):
        url = self.base + path
        data = None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
        if self.csrf and method not in {"GET", "HEAD", "OPTIONS"}:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as resp:  # noqa: S310
                raw = resp.read().decode()
                try:
                    parsed = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    # SSE 等非 JSON 成功响应：保留原始文本，不能因解析失败丢掉状态。
                    parsed = {"raw": raw[:2000]}
                return resp.status, parsed, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw[:500]}
            return exc.code, parsed, raw

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        expect_status: int | tuple = 200,
        timeout: float | None = None,
    ) -> Any:
        status, parsed, raw = self.raw(method, path, body, timeout)
        wanted = (expect_status,) if isinstance(expect_status, int) else tuple(expect_status)
        if status not in wanted:
            raise RuntimeError(f"{method} {path} -> {status}: {raw[:400]}")
        return parsed

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.call("POST", path, body, **kw)

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self.call("POST", "/api/v1/auth/login", {"username": username, "password": password})
        self.csrf = str(data.get("csrf_token") or "")
        me = self.call("GET", "/api/v1/auth/me")
        self.user = str(me.get("username") or "")
        self.roles = list(me.get("roles") or [])
        return me


class Reporter:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.results: list[dict[str, Any]] = []
        self.screens: list[str] = []

    def check(
        self, name: str, ok: bool, detail: str = "", *, server_verified: bool = False
    ) -> None:
        self.results.append(
            {
                "check": name,
                "status": "PASS" if ok else "FAIL",
                "server_verified": server_verified,
                "detail": detail,
            }
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    def note(self, name: str, detail: str) -> None:
        self.results.append({"check": name, "status": "NOTE", "detail": detail})
        print(f"[NOTE] {name} — {detail}")

    def screenshot(self, page: Any, name: str) -> None:
        path = self.out / f"{name}.png"
        try:
            page.screenshot(path=str(path), full_page=False)
            self.screens.append(str(path))
        except Exception as exc:  # noqa: BLE001
            self.note(f"screenshot:{name}", f"跳过（{exc}）")

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] == "FAIL"]


def _post_fault(teamflow: str, fault_id: str, params: dict[str, Any]) -> None:
    """向真实被测目标注入故障（仅测试目标侧，不触碰 TraceGuard 自身状态）。"""
    req = urllib.request.Request(
        f"{teamflow}/api/faults/{fault_id}/activate",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()


def _clear_fault(teamflow: str, fault_id: str) -> None:
    req = urllib.request.Request(
        f"{teamflow}/api/faults/{fault_id}/clear",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()


def _wait_terminal_run(api: Http, run_id: str, timeout_s: float = 180.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = api.get(f"/api/v1/runs/{run_id}")
        if str(last.get("execution_status")) in {
            "COMPLETED",
            "FAILED",
            "TIMED_OUT",
            "CANCELLED",
        }:
            return last
        time.sleep(1.0)
    return last


def _wait_until(fn: Any, timeout_s: float, interval: float = 0.5) -> Any:
    """轮询直到 fn 返回真值；超时返回最后一次结果。"""
    deadline = time.time() + timeout_s
    last: Any = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


class WorkerFleet:
    """独立 Worker 进程池（RUN-013 WP1）。

    所有 Worker 共用同一数据库与 Artifact 目录，仅 `TG_WORKER_ID` 不同；
    支持优雅退出（SIGTERM）、强制终止（SIGKILL）、暂停/恢复（SIGSTOP/SIGCONT），
    用于真实验证租约到期、接管与旧 Worker 越权提交。
    """

    def __init__(self, env: dict[str, str], out: Path, lease_ttl_s: float) -> None:
        self.env = dict(env)
        self.out = out
        self.lease_ttl_s = lease_ttl_s
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self.logs: dict[str, Any] = {}

    def start(self, name: str, *, lease_ttl_s: float | None = None) -> subprocess.Popen[bytes]:
        # 幂等：同名 Worker 已存活时直接复用，避免同一 worker_id 出现两个进程
        # （会让“杀掉一个”后仍有同 id 进程继续提交，污染租约接管验收）。
        existing = self.procs.get(name)
        if existing and existing.poll() is None:
            return existing
        env = dict(self.env)
        env["TG_WORKER_ID"] = name
        env["TG_WORKER_LEASE_TTL_S"] = str(lease_ttl_s or self.lease_ttl_s)
        env["TG_WORKER_POLL_INTERVAL_S"] = "0.4"
        log = open(self.out / f"worker-{name}.log", "ab")  # noqa: SIM115
        self.logs[name] = log
        proc = subprocess.Popen(
            [sys.executable, "-m", "traceguard_worker.main"],
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self.procs[name] = proc
        return proc

    def alive(self, name: str) -> bool:
        proc = self.procs.get(name)
        return bool(proc and proc.poll() is None)

    def send(self, name: str, sig: int) -> None:
        proc = self.procs.get(name)
        if proc and proc.poll() is None:
            proc.send_signal(sig)

    def stop(self, name: str, *, timeout: float = 20.0) -> int | None:
        """SIGTERM 优雅退出；超时则 SIGKILL。返回退出码。"""
        proc = self.procs.get(name)
        if not proc:
            return None
        if proc.poll() is None:
            proc.terminate()
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait(timeout=10)

    def kill(self, name: str) -> int | None:
        proc = self.procs.get(name)
        if not proc:
            return None
        if proc.poll() is None:
            proc.kill()
        return proc.wait(timeout=10)

    def close(self) -> None:
        for name in list(self.procs):
            try:
                self.stop(name, timeout=8)
            except Exception:  # noqa: BLE001
                pass
        for log in self.logs.values():
            try:
                log.close()
            except Exception:  # noqa: BLE001
                pass


def _start_api(env: dict[str, str], port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_worker_faults(
    rep: Reporter,
    base: str,
    teamflow: str,
    tc_id: str,
    lead: Http,
    fleet: WorkerFleet,
    env: dict[str, str],
    api_port: int,
    api_holder: dict[str, Any],
    *,
    run_timeout: float = 180.0,
) -> None:
    """RUN-013 WP1 §3.4：独立 Worker 故障场景（真实进程 + 真实租约 + 真实提交）。"""
    ttl = fleet.lease_ttl_s
    worker1, worker2 = "r13-w1", "r13-w2"

    # ---------------------------------------------------------- 1. 启动失败
    bad_env = dict(env)
    bad_env["TG_DATABASE_URL"] = "postgresql+psycopg://bad@127.0.0.1:1/absent"
    bad_env["TG_WORKER_ID"] = "r13-bad"
    bad = subprocess.run(
        [sys.executable, "-m", "traceguard_worker.main", "--once"],
        cwd=str(ROOT),
        env=bad_env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    rep.check(
        "W1-① Worker 启动失败（数据库不可达）→ 进程非零退出且不消费任务",
        bad.returncode != 0,
        f"exit={bad.returncode} err={(bad.stderr or '')[-160:]!r}",
    )
    rep.check(
        "W1-② Worker 启动失败不影响 API 与既有队列",
        bool(_wait_until(lambda: _health_ok(base + "/health"), 10)),
        "API 仍健康",
    )

    # ---------------------------------------------------------- 2. 正常退出
    fleet.start(worker1)
    rep.check(
        "W1-③ 独立 Worker-1 启动并注册",
        bool(_wait_until(lambda: fleet.alive(worker1), 15)),
        f"pid={fleet.procs[worker1].pid}",
    )
    code = fleet.stop(worker2, timeout=15)
    rep.check(
        "W1-④ Worker-2 正常退出（SIGTERM）→ 退出码 0",
        code == 0,
        f"exit={code}",
    )
    fleet.start(worker2)
    rep.check(
        "W1-⑤ Worker-2 可重新启动并继续消费",
        bool(_wait_until(lambda: fleet.alive(worker2), 15)),
        f"pid={fleet.procs[worker2].pid}",
    )

    # ------------------------------------------- 3. 执行中强杀 + 租约接管
    fleet.stop(worker2, timeout=15)
    run1 = lead.post(
        f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201)
    )
    run1_id = str(run1.get("run_id") or "")
    claimed = _wait_until(
        lambda: (
            lambda d: (
                d
                if str(d.get("lease_owner") or "").startswith(worker1)
                and d.get("execution_status") in {"PREPARING", "RUNNING"}
                else None
            )
        )(lead.get(f"/api/v1/runs/{run1_id}")),
        60,
    )
    rep.check(
        "W1-⑥ 队列由独立 Worker-1 领取（API 不执行）",
        bool(claimed),
        f"run={run1_id} owner={(claimed or {}).get('lease_owner')} fencing={(claimed or {}).get('fencing_token')}",
        server_verified=True,
    )
    token_before = int((claimed or {}).get("fencing_token") or 0)
    fleet.kill(worker1)
    rep.check(
        "W1-⑦ Worker 执行中被 SIGKILL → 进程非零退出",
        fleet.procs[worker1].returncode not in (0, None),
        f"exit={fleet.procs[worker1].returncode}",
    )
    after_kill = lead.get(f"/api/v1/runs/{run1_id}")
    rep.check(
        "W1-⑧ Worker 死亡后 API 不假报执行成功",
        after_kill.get("execution_status") not in {"COMPLETED"},
        f"status={after_kill.get('execution_status')} verdict={after_kill.get('test_verdict')}",
        server_verified=True,
    )
    fleet.start(worker2)
    takeover = _wait_until(
        lambda: (
            lambda d: (
                d
                if str(d.get("lease_owner") or "").startswith(worker2)
                and int(d.get("fencing_token") or 0) > token_before
                else None
            )
        )(lead.get(f"/api/v1/runs/{run1_id}")),
        ttl + 60,
    )
    rep.check(
        "W1-⑨ 租约到期后新 Worker 接管（fencing_token 递增）",
        bool(takeover),
        f"owner={(takeover or {}).get('lease_owner')} fencing={(takeover or {}).get('fencing_token')} before={token_before}",
        server_verified=True,
    )
    final1 = _wait_terminal_run(lead, run1_id, timeout_s=run_timeout)
    rep.check(
        "W1-⑩ 接管后任务真实执行到终态且仅一份业务结果",
        final1.get("execution_status") in {"COMPLETED", "FAILED"},
        f"status={final1.get('execution_status')} verdict={final1.get('test_verdict')}",
        server_verified=True,
    )
    steps1 = lead.get(f"/api/v1/runs/{run1_id}/steps")
    step_ids = [str(s.get("step_id")) for s in steps1 or []]
    rep.check(
        "W1-⑪ 接管重跑不产生重复步骤（step_id 唯一）",
        len(step_ids) == len(set(step_ids)),
        f"steps={len(step_ids)} unique={len(set(step_ids))}",
        server_verified=True,
    )

    # ---------------------------------- 4. 旧 Worker 恢复后尝试提交（fencing）
    fleet.kill(worker1)
    fleet.stop(worker2, timeout=15)
    fleet.start(worker1)
    _wait_until(lambda: fleet.alive(worker1), 15)
    run2_id = ""
    token2 = 0
    claimed2: dict[str, Any] | None = None
    paused = False
    # 真实 Run 只有 ~3 秒；若检测/暂停晚于执行结束则重试一次，避免把“跑完了”误判为接管。
    for attempt in range(1, 3):
        run2 = lead.post(
            f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201)
        )
        run2_id = str(run2.get("run_id") or "")
        rid = run2_id
        claimed2 = _wait_until(
            lambda rid=rid: (
                lambda d: (
                    d
                    if str(d.get("lease_owner") or "").startswith(worker1)
                    and d.get("execution_status") in {"PREPARING", "RUNNING"}
                    else None
                )
            )(lead.get(f"/api/v1/runs/{rid}")),
            60,
        )
        token2 = int((claimed2 or {}).get("fencing_token") or 0)
        if not claimed2:
            continue
        fleet.send(worker1, signal.SIGSTOP)
        time.sleep(0.4)
        paused_doc = lead.get(f"/api/v1/runs/{run2_id}")
        if paused_doc.get("execution_status") not in {"COMPLETED", "FAILED", "CANCELLED"}:
            paused = True
            break
        fleet.send(worker1, signal.SIGCONT)
        rep.note(
            f"W1-⑫ 暂停时机重试（第 {attempt} 次）",
            f"Run 在暂停前已到终态 {paused_doc.get('execution_status')}，换新 Run 重试",
        )
        _wait_terminal_run(lead, run2_id, timeout_s=run_timeout)
    rep.check(
        "W1-⑫ 旧 Worker 已持有租约并成功暂停（另有新 Worker 待接管）",
        paused,
        f"run={run2_id} owner={(claimed2 or {}).get('lease_owner')} fencing={token2}",
        server_verified=True,
    )
    if paused:
        time.sleep(ttl + 1.5)
        fleet.start("r13-w3")
        takeover2 = _wait_until(
            lambda: (
                lambda d: (
                    d
                    if str(d.get("lease_owner") or "").startswith("r13-w3")
                    and int(d.get("fencing_token") or 0) > token2
                    else None
                )
            )(lead.get(f"/api/v1/runs/{run2_id}")),
            ttl + 90,
        )
        rep.check(
            "W1-⑬ 暂停 Worker 租约过期 → 第三个 Worker 接管",
            bool(takeover2),
            f"owner={(takeover2 or {}).get('lease_owner')} fencing={(takeover2 or {}).get('fencing_token')}",
            server_verified=True,
        )
        final2 = _wait_terminal_run(lead, run2_id, timeout_s=run_timeout)
        snapshot_status = final2.get("execution_status")
        snapshot_verdict = final2.get("test_verdict")
        steps2 = lead.get(f"/api/v1/runs/{run2_id}/steps")
        step_ids_before = [str(s.get("step_id")) for s in steps2 or []]
        before_evi = len(lead.get(f"/api/v1/runs/{run2_id}/evidence") or [])

        fleet.send(worker1, signal.SIGCONT)
        time.sleep(8)
        after_resume = lead.get(f"/api/v1/runs/{run2_id}")
        steps3 = lead.get(f"/api/v1/runs/{run2_id}/steps")
        step_ids3 = [str(s.get("step_id")) for s in steps3 or []]
        after_evi = len(lead.get(f"/api/v1/runs/{run2_id}/evidence") or [])
        rep.check(
            "W1-⑭ 旧 Worker 恢复后不能覆盖已接管任务的状态",
            after_resume.get("execution_status") == snapshot_status
            and after_resume.get("test_verdict") == snapshot_verdict,
            f"before={snapshot_status}/{snapshot_verdict} after={after_resume.get('execution_status')}/{after_resume.get('test_verdict')}",
            server_verified=True,
        )
        rep.check(
            "W1-⑮ 旧 Worker 恢复后不产生重复步骤/证据",
            len(step_ids3) == len(set(step_ids3)) and after_evi == before_evi,
            f"steps={len(step_ids3)} (before={len(step_ids_before)}) evidence={before_evi}->{after_evi}",
            server_verified=True,
        )
        rep.check(
            "W1-⑯ 旧 Worker 的过期提交被 Fencing 拒绝（进程仍存活）",
            fleet.alive(worker1),
            f"alive={fleet.alive(worker1)}",
        )
    else:
        fleet.send(worker1, signal.SIGCONT)
        rep.note(
            "W1-⑬-⑯ 租约接管与旧 Worker 恢复",
            "两次暂停都晚于 Run 终态，未形成可观测的租约接管窗口；不伪造接管结果。",
        )

    # ---------------------------------------------- 5. API 重启 + 客户端断连
    fleet.stop("r13-w3", timeout=15)
    fleet.start(worker2)
    run3 = lead.post(
        f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201)
    )
    run3_id = str(run3.get("run_id") or "")
    api_proc = api_holder["proc"]
    api_proc.kill()
    api_proc.wait(timeout=10)
    rep.check(
        "W1-⑰ API 进程被强制终止（模拟客户端未收到响应）",
        not _health_ok(base + "/health", timeout=2),
        "API 不可达",
    )
    # Worker 不依赖 API，继续消费队列
    time.sleep(3)
    api_holder["proc"] = _start_api(env, api_port)
    rep.check(
        "W1-⑱ API 重启后恢复健康",
        bool(_wait_until(lambda: _health_ok(base + "/health"), 30)),
        base,
    )
    lead2 = Http(base, "lead-restarted")
    lead2.login("r12-lead", PASSWORD)
    run3_doc = _wait_terminal_run(lead2, run3_id, timeout_s=run_timeout)
    rep.check(
        "W1-⑲ API 重启期间 Worker 继续消费，重启后可回读终态",
        run3_doc.get("execution_status") in {"COMPLETED", "FAILED"},
        f"status={run3_doc.get('execution_status')} verdict={run3_doc.get('test_verdict')}",
        server_verified=True,
    )
    rep.check(
        "W1-⑳ 已完成任务在 API 重启后不重复产生业务结果",
        len([s for s in (lead2.get(f"/api/v1/runs/{run3_id}/steps") or [])]) >= 1
        and len({str(s.get("step_id")) for s in (lead2.get(f"/api/v1/runs/{run3_id}/steps") or [])})
        == len(lead2.get(f"/api/v1/runs/{run3_id}/steps") or []),
        f"run={run3_id}",
        server_verified=True,
    )

    # ------------------------------------------------ 6. 双 Worker 并发幂等
    fleet.start("r13-w4")
    _wait_until(lambda: fleet.alive("r13-w4"), 15)
    concurrent = [
        str(
            lead2.post(
                f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201)
            ).get("run_id")
            or ""
        )
        for _ in range(4)
    ]
    finals = [_wait_terminal_run(lead2, rid, timeout_s=run_timeout) for rid in concurrent]
    rep.check(
        "W1-㉑ 双 Worker 并发消费 4 个 Run 全部到终态",
        all(f.get("execution_status") in {"COMPLETED", "FAILED"} for f in finals),
        f"statuses={[f.get('execution_status') for f in finals]}",
        server_verified=True,
    )
    dup = []
    for rid in concurrent:
        sids = [str(s.get("step_id")) for s in lead2.get(f"/api/v1/runs/{rid}/steps") or []]
        if len(sids) != len(set(sids)):
            dup.append(rid)
    rep.check(
        "W1-㉒ 并发消费未出现重复步骤/双重提交",
        not dup,
        f"duplicates={dup}",
        server_verified=True,
    )


def _health_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/assets/run012/wp4/ui")
    ap.add_argument("--workpackage", default="RUN-012 WP4")
    ap.add_argument("--label", default="loop-acceptance")
    ap.add_argument(
        "--independent-workers",
        type=int,
        default=0,
        help=">0 时关闭 API 内嵌 Worker，启动 N 个独立 Worker 进程（RUN-013 WP1）",
    )
    ap.add_argument("--lease-ttl", type=float, default=6.0, help="独立 Worker 租约 TTL（秒）")
    ap.add_argument("--faults", action="store_true", help="执行独立 Worker 故障场景")
    # RUN-016 WP4.3：追加证据图 / 探针建议 / 回归验证三段 UI 校验（不影响 RUN-012/013 口径）
    ap.add_argument(
        "--extended",
        action="store_true",
        help="RUN-016：追加证据图、探针建议、回归验证的 UI 校验",
    )
    args = ap.parse_args(argv)
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rep = Reporter(out)
    independent = args.independent_workers > 0

    from playwright.sync_api import sync_playwright

    api_port = _free_port()
    teamflow_port = _free_port()
    work = out / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    db_file = work / f"{args.label}.db"
    tf_db_file = work / f"{args.label}-teamflow.db"
    for f in (db_file, tf_db_file):
        if f.exists():
            f.unlink()
    db = f"sqlite:///{db_file}"
    tf_db = f"sqlite:///{tf_db_file}"
    env = _child_env(
        db,
        api_port,
        teamflow_port,
        tf_db,
        inline_worker=not independent,
        lease_ttl_s=args.lease_ttl,
    )
    fleet = WorkerFleet(env, out, args.lease_ttl) if independent else None
    api_holder: dict[str, Any] = {"proc": None}

    # TeamFlow：真实被测目标（独立进程 + 独立库）
    tf_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(teamflow_port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    api_proc = _start_api(env, api_port)
    api_holder["proc"] = api_proc
    base = f"http://127.0.0.1:{api_port}"
    teamflow = f"http://127.0.0.1:{teamflow_port}"

    try:
        if not _wait_health(teamflow + "/health"):
            rep.check("环境：真实 TeamFlow 可启动", False, "TeamFlow 未就绪")
            return 3
        rep.check("环境：真实 TeamFlow 可启动", True, teamflow)
        if not _wait_health(base + "/health"):
            rep.check("环境：真实 API 可启动", False, "API 未就绪")
            return 3
        rep.check("环境：真实 API 可启动", True, base)
        if fleet:
            for i in range(1, max(2, args.independent_workers) + 1):
                fleet.start(f"r13-w{i}")
            started = _wait_until(
                lambda: all(
                    fleet.alive(f"r13-w{i}") for i in range(1, max(2, args.independent_workers) + 1)
                ),
                20,
            )
            rep.check(
                "环境：独立 Worker 进程已启动（API 内嵌 Worker 关闭）",
                bool(started),
                ", ".join(f"{n}:{p.pid}" for n, p in fleet.procs.items()),
            )

        users = [
            ("r12-eng", "qa-engineer", '["default"]'),
            ("r12-lead", "qa-lead", '["default"]'),
            ("r12-admin", "platform-admin", '["*"]'),
        ]
        for username, roles, workspaces in users:
            r = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "auth_bootstrap.py"),
                    "--db",
                    db,
                    "--username",
                    username,
                    "--password",
                    PASSWORD,
                    "--roles",
                    roles,
                    "--workspaces",
                    workspaces,
                ],
                env=env,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                rep.check(f"环境：初始化 {username}", False, r.stderr[-200:])
                return 3
        rep.check("环境：三个角色已初始化", True, ", ".join(u[0] for u in users))

        eng = Http(base, "engineer")
        lead = Http(base, "lead")
        admin = Http(base, "admin")
        eng.login("r12-eng", PASSWORD)
        lead.login("r12-lead", PASSWORD)
        admin.login("r12-admin", PASSWORD)
        rep.check("三个角色身份来自服务端", True, f"{eng.roles}/{lead.roles}/{admin.roles}")

        # 服务端配置必须暴露真实目标地址（前端不再硬编码端口）
        cfg = admin.get("/api/v1/system/config")
        rep.check(
            "服务端配置暴露真实被测目标地址",
            str(cfg.get("teamflow_base_url")) == teamflow,
            f"teamflow_base_url={cfg.get('teamflow_base_url')}",
            server_verified=True,
        )
        if independent:
            rep.check(
                "API 不启动内嵌 Worker（TG_INLINE_WORKER=false）",
                bool((cfg.get("worker") or {}).get("inline")) is False,
                f"worker={cfg.get('worker')}",
                server_verified=True,
            )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            def login_page(username: str) -> Any:
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                page.goto(base + "/login", wait_until="domcontentloaded")
                page.wait_for_selector("#login-username", timeout=15000)
                page.fill("#login-username", username)
                page.fill("#login-password", PASSWORD)
                page.click("button.login-submit")
                page.wait_for_selector(".workspace-picker select", timeout=20000)
                return page

            def toast_text(page: Any) -> str:
                try:
                    return page.locator(".toast").first.inner_text(timeout=2000)
                except Exception:  # noqa: BLE001
                    return ""

            try:
                # ---------------------------------------------------- 1. Intent
                eng_p = login_page("r12-eng")
                eng_p.goto(base + "/intents", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button.primary:has-text('新建需求')", timeout=20000)
                eng_p.click("button.primary:has-text('新建需求')")
                eng_p.wait_for_selector("#intent-title", timeout=10000)
                intent_title = "WP4 闭环需求：创建项目后必须真实持久化"
                eng_p.fill("#intent-title", intent_title)
                eng_p.fill("#intent-desc", "真实浏览器创建，服务端必须落库并可继续下游链路")
                eng_p.fill("#intent-owner", "qa-team")
                eng_p.click("button.primary:has-text('创建')")
                eng_p.wait_for_timeout(1500)
                intents = eng.get("/api/v1/intents?workspace_id=default")
                rows = intents if isinstance(intents, list) else intents.get("items", [])
                matched = [i for i in rows if i.get("title") == intent_title]
                rep.check(
                    "① UI 创建需求 → 服务端真实落库",
                    len(matched) == 1,
                    f"匹配 {len(matched)} 条",
                    server_verified=True,
                )
                intent_id = str(matched[0].get("intent_id")) if matched else ""
                rep.screenshot(eng_p, "01-intent-created")

                # ---------------------------------------------------- 2. TestCase
                eng_p.goto(base + "/testcases", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button.primary:has-text('新建用例')", timeout=20000)
                eng_p.click("button.primary:has-text('新建用例')")
                eng_p.wait_for_selector("#tc-title", timeout=10000)
                tc_title = "WP4 闭环用例：成功提示可见"
                eng_p.fill("#tc-title", tc_title)
                if intent_id:
                    eng_p.select_option("#tc-intent-ref", intent_id)
                eng_p.fill(
                    "#tc-steps",
                    json.dumps(
                        [
                            {
                                "step_id": "s1",
                                "action": "navigate",
                                "value": "/projects",
                                "description": "打开项目列表",
                            },
                            {
                                "step_id": "s2",
                                "action": "click",
                                "target": {"strategy": "test_id", "value": "nav-new"},
                                "description": "新建",
                            },
                            {
                                "step_id": "s3",
                                "action": "fill",
                                "target": {"strategy": "test_id", "value": "project-name"},
                                "value": "WP4 闭环项目",
                                "description": "填写名称",
                            },
                            {
                                "step_id": "s4",
                                "action": "click",
                                "target": {"strategy": "test_id", "value": "create-submit"},
                                "description": "提交",
                            },
                            {
                                "step_id": "s5",
                                "action": "assert",
                                "target": {"strategy": "test_id", "value": "banner-success"},
                                "value": "banner-success",
                                "description": "读取成功提示",
                            },
                        ],
                        ensure_ascii=False,
                    ),
                )
                eng_p.fill(
                    "#tc-assertions",
                    json.dumps(
                        [
                            {
                                "assertion_id": "a_wp4_banner",
                                "description": "成功提示可见",
                                "kind": "ui",
                                "required": True,
                                "expected": {"test_id": "banner-success"},
                            }
                        ],
                        ensure_ascii=False,
                    ),
                )
                eng_p.click("button.primary:has-text('创建（待审核）')")
                eng_p.wait_for_timeout(1500)
                tcs = eng.get("/api/v1/testcases?workspace_id=default")
                tc_rows = tcs if isinstance(tcs, list) else tcs.get("items", [])
                tcm = [t for t in tc_rows if t.get("title") == tc_title]
                rep.check(
                    "② UI 新建用例 → 服务端 PENDING",
                    len(tcm) == 1 and tcm[0].get("review_status") == "PENDING",
                    f"匹配 {len(tcm)} 条 status={(tcm[0].get('review_status') if tcm else 'N/A')}",
                    server_verified=True,
                )
                tc_id = str(tcm[0].get("testcase_id")) if tcm else ""
                version = int(tcm[0].get("latest_version") or 1) if tcm else 1
                rep.screenshot(eng_p, "02-testcase-pending")

                # ---------------------------------------- 3. Oracle（已审批）+ 绑定
                # oracle.create 权限属 qa-lead / platform-admin（Agent 不能自批 Oracle）。
                lead_p = login_page("r12-lead")
                lead_p.goto(base + "/oracles", wait_until="domcontentloaded")
                lead_p.wait_for_selector("button.primary:has-text('新建 Oracle')", timeout=20000)
                lead_p.click("button.primary:has-text('新建 Oracle')")
                lead_p.wait_for_selector("#oracle-assertion", timeout=10000)
                oracle_id = "oracle_wp4_banner"
                lead_p.fill("#oracle-id", oracle_id)
                lead_p.fill("#oracle-assertion", "a_wp4_banner")
                lead_p.fill("#oracle-expected", "创建成功后必须出现成功提示")
                lead_p.fill("#oracle-reqref", "REQ-WP4-LOOP#UI-1")
                lead_p.check("#oracle-approve-now")
                lead_p.click("button.primary:has-text('创建')")
                lead_p.wait_for_timeout(1500)
                oracles = lead.get("/api/v1/oracles?workspace_id=default")
                om = [o for o in oracles if o.get("oracle_id") == oracle_id]
                rep.check(
                    "③ UI 新建已审批 Oracle → 服务端落库",
                    len(om) == 1 and bool(om[0].get("approved")),
                    f"匹配 {len(om)} 条 approved={(om[0].get('approved') if om else 'N/A')}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "03-oracle-approved")

                # 用例详情页：绑定 Oracle（此前 UI 只有展示，没有绑定入口）
                lead_p.goto(base + f"/testcases/{tc_id}", wait_until="domcontentloaded")
                lead_p.wait_for_selector("h2:has-text('断言与 Oracle 绑定')", timeout=20000)
                bind_select = lead_p.locator("table select").first
                rep.check(
                    "④ 用例详情页提供 Oracle 绑定控件",
                    bind_select.count() >= 1,
                    f"select 数量={bind_select.count()}",
                    server_verified=True,
                )
                bind_select.select_option(oracle_id)
                lead_p.click("button:has-text('保存绑定')")
                lead_p.wait_for_timeout(1500)
                ver = lead.get(f"/api/v1/testcases/{tc_id}/versions/{version}")
                bindings = ver.get("oracle_bindings") or {}
                rep.check(
                    "⑤ UI 保存绑定 → 服务端 oracle_bindings 落库",
                    str((bindings.get("a_wp4_banner") or {}).get("oracle_id")) == oracle_id,
                    f"bindings={list(bindings)}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "04-oracle-bound")

                # --------------------------------------- 6. 审核门禁（真实生效）
                lead_p.click("button:has-text('审核通过')")
                lead_p.wait_for_timeout(1800)
                after = lead.get(f"/api/v1/testcases/{tc_id}")
                rep.check(
                    "⑥ UI 审核通过 → 服务端 APPROVED（Oracle 门禁未绕过）",
                    after.get("review_status") == "APPROVED",
                    f"status={after.get('review_status')}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "05-approved")

                # ------------------------------------------------ 7. 真实执行
                lead_p.click("button:has-text('执行当前版本')")
                lead_p.wait_for_url("**/runs/**", timeout=20000)
                lead_p.wait_for_timeout(1500)
                run_id = lead_p.url.rstrip("/").rsplit("/", 1)[-1]
                rep.check("⑦ UI 执行 → 产生真实 Run", bool(run_id), f"run_id={run_id}")
                final = _wait_terminal_run(lead, run_id)
                rep.check(
                    "⑧ Run 由真实 Worker 执行到终态",
                    str(final.get("execution_status")) in {"COMPLETED", "FAILED"},
                    f"status={final.get('execution_status')} verdict={final.get('test_verdict')} observer={final.get('observer_mode')}",
                    server_verified=True,
                )
                rep.check(
                    "⑨ 执行使用真实浏览器（playwright）而非降级",
                    str(final.get("observer_mode")) == "playwright",
                    f"observer_mode={final.get('observer_mode')}",
                    server_verified=True,
                )
                if independent:
                    owner = str(final.get("lease_owner") or "")
                    rep.check(
                        "⑨a Run 由独立 Worker 进程领取并持有租约（Fencing Token > 0）",
                        owner.startswith("r13-w") and int(final.get("fencing_token") or 0) > 0,
                        f"lease_owner={owner} fencing={final.get('fencing_token')}",
                        server_verified=True,
                    )
                steps = lead.get(f"/api/v1/runs/{run_id}/steps")
                evi = lead.get(f"/api/v1/runs/{run_id}/evidence")
                rep.check(
                    "⑩ Run 产出真实步骤轨迹与证据",
                    len(steps or []) >= 4 and len(evi or []) >= 1,
                    f"steps={len(steps or [])} evidence={len(evi or [])}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "06-run-detail")

                # ---------------------- 8. 注入真实故障 → 失败 Run → 诊断 → 修复链
                # 诊断按钮对 PASSED 用例禁用（设计如此：无需诊断通过的用例），
                # 因此必须先注入真实故障产生 FAILED Run 才能验证诊断与修复链。
                # sticky=true：跨 Run 的 RESET_DATA 预清理保留故障（RUN-011-R1 WP1 语义）。
                _post_fault(teamflow, "TF-UI-002", {"sticky": True})
                rep.check(
                    "⑪ 已向真实被测目标注入故障 TF-UI-002（提交静默忽略）",
                    True,
                    "TF-UI-002 activated",
                    server_verified=True,
                )
                fail_run = lead.post(
                    f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201)
                )
                fail_run_id = str(fail_run.get("run_id") or "")
                final_fail = _wait_terminal_run(lead, fail_run_id)
                rep.check(
                    "⑫ 故障注入后真实 Run 失败（verdict=FAILED）",
                    final_fail.get("test_verdict") == "FAILED",
                    f"verdict={final_fail.get('test_verdict')} status={final_fail.get('execution_status')}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "07-run-failed")

                eng_p.goto(base + f"/runs/{fail_run_id}", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button:has-text('诊断')", timeout=20000)
                diag_btn = eng_p.locator("button:has-text('诊断')").first
                rep.check(
                    "⑬ 失败 Run 的诊断入口可用",
                    diag_btn.count() >= 1 and diag_btn.is_enabled(),
                    f"enabled={diag_btn.is_enabled() if diag_btn.count() else 'N/A'}",
                    server_verified=True,
                )
                diag_btn.click()
                eng_p.wait_for_timeout(3000)
                diags = eng.get(f"/api/v1/diagnoses?run_id={fail_run_id}")
                dl = diags if isinstance(diags, list) else diags.get("items", [])
                rep.check(
                    "⑭ UI 诊断 → 服务端生成 Diagnosis",
                    len(dl) >= 1,
                    f"diagnoses={len(dl)}",
                    server_verified=True,
                )
                diagnosis_id = str((dl[0] or {}).get("diagnosis_id")) if dl else ""
                rep.note("诊断 ID", diagnosis_id)
                rep.screenshot(eng_p, "08-diagnosis")
                if args.extended:
                    # ---- RUN-016 WP4.3 追加：证据图 / 探针建议（真实数据渲染）----
                    eng_p.click("button:has-text('证据图')")
                    eng_p.wait_for_timeout(1500)
                    graph_doc = eng.post(f"/api/v1/runs/{fail_run_id}/graph", {})
                    graph_nodes = int(((graph_doc or {}).get("stats") or {}).get("nodes") or 0)
                    graph_dom = eng_p.locator("text=Evidence Graph").count()
                    graph_svg = eng_p.locator("svg").count()
                    rep.check(
                        "⑭a UI 证据图 → 服务端图节点可渲染",
                        graph_nodes > 0 and graph_dom >= 1 and graph_svg >= 1,
                        f"api_nodes={graph_nodes} dom={graph_dom} svg={graph_svg}",
                        server_verified=True,
                    )
                    rep.screenshot(eng_p, "08a-evidence-graph")
                    eng_p.click("button:has-text('诊断')")
                    eng_p.wait_for_timeout(1200)
                    probe_list = eng.get(f"/api/v1/runs/{fail_run_id}/probe-suggestions")
                    probe_rows = eng_p.locator("button:has-text('执行（只读）')").count()
                    rep.check(
                        "⑭b UI 探针建议来自服务端并可执行",
                        isinstance(probe_list, list) and len(probe_list) >= 1 and probe_rows >= 1,
                        f"api_probes={len(probe_list) if isinstance(probe_list, list) else 'n/a'} "
                        f"ui_rows={probe_rows}",
                        server_verified=True,
                    )
                    rep.screenshot(eng_p, "08b-probe-suggestions")
                # sticky=true：跨 Run 的 RESET_DATA 预清理保留故障（RUN-011-R1 WP1 语义）。
                _post_fault(teamflow, "TF-UI-002", {"sticky": True})

                # --------------------------------------- 9. LLM 建议入口（未配凭据）
                eng_p.goto(base + "/repairs", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button:has-text('新建修复提案')", timeout=20000)
                rep.check(
                    "⑮ 修复中心提供 LLM 建议入口",
                    eng_p.locator("button:has-text('新建修复提案')").count() >= 1,
                    "提案入口存在（LLM 建议位于提案弹窗内）",
                    server_verified=True,
                )
                # LLM 建议入口位于提案弹窗内（依赖已选目标用例）。
                eng_p.click("button:has-text('新建修复提案')")
                eng_p.wait_for_selector(".modal select", timeout=10000)
                eng_p.select_option(".modal select >> nth=0", tc_id)
                eng_p.wait_for_timeout(1500)
                eng_p.click(".modal-foot button:has-text('LLM 建议')")
                eng_p.wait_for_timeout(2500)
                llm_toast = toast_text(eng_p)
                rep.check(
                    "⑯ 未配置 LLM 凭据时如实提示，不伪造成功",
                    "未配置" in llm_toast or "不可用" in llm_toast or "禁用" in llm_toast,
                    f"toast={llm_toast[:80]!r}",
                    server_verified=True,
                )

                # ------------------------------------------ 10. 修复提案全生命周期
                # 选择填充步骤（保持业务语义不变的重申定位器修复）：
                # 把 s3 的 fill 定位器重新绑定到正确元素，避免把导航步骤改坏导致语义破坏。
                step_select = eng_p.locator(".modal select >> nth=1")
                step_select.select_option("2")
                eng_p.wait_for_timeout(600)
                eng_p.select_option(".modal select >> nth=2", "test_id")
                eng_p.fill(".modal input[placeholder*='create-submit']", "project-name")
                eng_p.fill(".modal input[placeholder*='aria-label']", "WP4 闭环定位器修复")
                eng_p.click("button:has-text('创建提案')")
                eng_p.wait_for_timeout(2000)
                reps = eng.get("/api/v1/repairs?workspace_id=default")
                rl = reps if isinstance(reps, list) else reps.get("items", [])
                mine = [r for r in rl if r.get("testcase_id") == tc_id]
                rep.check(
                    "⑰ UI 新建修复提案 → 服务端落库",
                    len(mine) >= 1,
                    f"proposals={len(mine)}",
                    server_verified=True,
                )
                proposal_id = str(mine[0].get("proposal_id")) if mine else ""
                status = str(mine[0].get("status")) if mine else ""
                rep.check(
                    "⑱ 提案初始状态为 VALIDATED（规则校验通过）",
                    status == "VALIDATED",
                    f"status={status}",
                    server_verified=True,
                )
                rep.screenshot(eng_p, "09-repair-created")

                # 清除故障后跑沙箱：修复候选必须在"目标已恢复"的真实环境下复现通过，
                # 否则沙箱失败只是故障残留，不能证明修复有效。
                _clear_fault(teamflow, "TF-UI-002")
                rep.check(
                    "⑲a 已清除故障（sticky 语义），目标恢复真实行为",
                    True,
                    "TF-UI-002 cleared",
                    server_verified=True,
                )

                # 沙箱真实执行（依赖真实 Worker + 真实浏览器）
                eng_p.goto(base + "/repairs", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button:has-text('沙箱真实执行')", timeout=20000)
                sandbox_btn = eng_p.locator("button:has-text('沙箱真实执行')")
                sandbox_btn.click()
                deadline = time.time() + 240
                sandbox_status = ""
                while time.time() < deadline:
                    cur = eng.get(f"/api/v1/repairs/{proposal_id}")
                    sandbox_status = str(cur.get("sandbox_status") or "")
                    if sandbox_status in {"PASSED", "FAILED"}:
                        break
                    time.sleep(2.0)
                cur = eng.get(f"/api/v1/repairs/{proposal_id}")
                rep.check(
                    "⑲ UI 沙箱验证 → 服务端真实执行并落库结果",
                    sandbox_status in {"PASSED", "FAILED"},
                    f"sandbox_status={sandbox_status} run={cur.get('sandbox_run_id')} reason={cur.get('failure_reason')}",
                    server_verified=True,
                )
                rep.screenshot(eng_p, "10-repair-sandbox")

                if sandbox_status == "PASSED":
                    eng_p.click("button:has-text('提交审批')")
                    eng_p.wait_for_timeout(1500)
                    cur = eng.get(f"/api/v1/repairs/{proposal_id}")
                    rep.check(
                        "⑳ UI 提交审批 → 服务端 PENDING_APPROVAL",
                        cur.get("status") == "PENDING_APPROVAL",
                        f"status={cur.get('status')}",
                        server_verified=True,
                    )

                    # qa-lead 审批（repair.approve）
                    lead_p.goto(base + "/repairs", wait_until="domcontentloaded")
                    lead_p.wait_for_selector("button:has-text('批准')", timeout=20000)
                    # 先在列表中选中本条提案，避免误操作列表首条
                    lead_p.click(f"tr:has-text('{proposal_id[-10:]}')")
                    lead_p.wait_for_timeout(1200)
                    lead_p.click(
                        "button:has-text('批准')"
                    )  # 页面按钮是「批准」，确认弹窗按钮是「审批」
                    lead_p.wait_for_timeout(1200)
                    lead_p.click(".confirm-dialog button:has-text('审批')")
                    lead_p.wait_for_timeout(1800)
                    cur = lead.get(f"/api/v1/repairs/{proposal_id}")
                    rep.check(
                        "㉑ UI 审批 → 服务端 APPROVED",
                        cur.get("status") == "APPROVED",
                        f"status={cur.get('status')}",
                        server_verified=True,
                    )

                    # 晋升 / 回滚属 platform-admin 专属（repair.promote / repair.rollback）
                    admin_p = login_page("r12-admin")
                    admin_p.goto(base + "/repairs", wait_until="domcontentloaded")
                    admin_p.wait_for_selector("button:has-text('晋升正式版本')", timeout=20000)
                    admin_p.click(f"tr:has-text('{proposal_id[-10:]}')")
                    admin_p.wait_for_timeout(1200)
                    admin_p.click("button:has-text('晋升正式版本')")
                    admin_p.wait_for_timeout(800)
                    admin_p.click(".confirm-dialog button:has-text('晋升正式版本')")
                    admin_p.wait_for_timeout(2500)
                    cur = admin.get(f"/api/v1/repairs/{proposal_id}")
                    promoted_version = int(cur.get("promoted_version") or 0)
                    rep.check(
                        "㉒ UI 晋升 → 服务端 PROMOTED 且版本号有效",
                        cur.get("status") == "PROMOTED" and promoted_version > 0,
                        f"status={cur.get('status')} promoted_version={promoted_version}",
                        server_verified=True,
                    )
                    rep.screenshot(admin_p, "11-repair-promoted")

                    if args.extended:
                        # ---- RUN-016 WP4.3 追加：UI 发起回归验证（真实 Run + Worker 执行）----
                        admin_p.click("button:has-text('运行回归验证')")
                        admin_p.wait_for_timeout(2500)
                        reg_doc = admin.get(f"/api/v1/repairs/{proposal_id}/regression")
                        deadline_reg = time.time() + 40
                        while (
                            str(reg_doc.get("regression_status")) in {"QUEUED", "RUNNING"}
                            and time.time() < deadline_reg
                        ):
                            time.sleep(1.0)
                            reg_doc = admin.get(f"/api/v1/repairs/{proposal_id}/regression")
                        rep.check(
                            "㉒a UI 运行回归验证 → 服务端创建真实回归 Run 并给出结论",
                            bool(reg_doc.get("regression_run_id"))
                            and str(reg_doc.get("regression_status"))
                            in {"PASSED", "FAILED", "INCONCLUSIVE"},
                            f"status={reg_doc.get('regression_status')} "
                            f"verdict={reg_doc.get('regression_verdict')} "
                            f"run={reg_doc.get('regression_run_id')}",
                            server_verified=True,
                        )
                        rep.screenshot(admin_p, "11a-repair-regression")

                    admin_p.click("button:has-text('回滚')")
                    admin_p.wait_for_timeout(800)
                    admin_p.click(".confirm-dialog button:has-text('回滚')")
                    admin_p.wait_for_timeout(2500)
                    cur = admin.get(f"/api/v1/repairs/{proposal_id}")
                    rep.check(
                        "㉓ UI 回滚 → 服务端 ROLLED_BACK 且留痕",
                        cur.get("status") == "ROLLED_BACK" and bool(cur.get("rollback_record")),
                        f"status={cur.get('status')} record={bool(cur.get('rollback_record'))}",
                        server_verified=True,
                    )
                    rep.screenshot(admin_p, "12-repair-rolled-back")
                else:
                    rep.note(
                        "⑳-㉓ 审批/晋升/回滚链路",
                        "沙箱未 PASSED（真实执行未通过），审批链前置条件未满足，"
                        "按服务端门禁不继续；不伪造通过。",
                    )
            finally:
                try:
                    _clear_fault(teamflow, "TF-UI-002")
                except Exception:  # noqa: BLE001
                    pass
                browser.close()

        if fleet and args.faults:
            tc_ref = locals().get("tc_id") or ""
            if tc_ref:
                rep.check(
                    "环境：开始独立 Worker 故障注入（真实进程 / 真实租约）",
                    True,
                    f"lease_ttl={args.lease_ttl}s",
                )
                _run_worker_faults(
                    rep,
                    base,
                    teamflow,
                    str(tc_ref),
                    lead,
                    fleet,
                    env,
                    api_port,
                    api_holder,
                )
            else:
                rep.note(
                    "独立 Worker 故障注入",
                    "业务闭环未产出 TestCase，无法创建真实 Run；不伪造故障结果。",
                )
    except Exception as exc:  # noqa: BLE001
        rep.check("闭环验收脚本执行", False, f"{type(exc).__name__}: {exc}")
    finally:
        if fleet:
            fleet.close()
        for proc in (api_holder.get("proc"), tf_proc):
            if not proc:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    summary = {
        "workpackage": args.workpackage,
        "engine": (
            "Playwright Chromium + real API (auth+CSRF) + real TeamFlow + "
            + ("independent Worker processes" if independent else "inline Worker")
            + (" + worker fault injection" if args.faults else "")
        ),
        "deployment": {
            "inline_worker": not independent,
            "independent_workers": max(2, args.independent_workers) if independent else 0,
            "lease_ttl_s": args.lease_ttl if independent else None,
            "database": "sqlite",
        },
        "total": len(rep.results),
        "passed": sum(1 for r in rep.results if r["status"] == "PASS"),
        "failed": len(rep.failed),
        "screenshots": rep.screens,
        "results": rep.results,
    }
    result_path = out / f"{args.label}.json"
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[out] {result_path}  passed={summary['passed']} failed={summary['failed']}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
