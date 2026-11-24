"""RUN-009 WP5：macOS 原生进程资源采样器（不依赖 ps / /proc / psutil）。

安全与合规约束（RUN-008 B-13 / B-17）：
- 不使用 `ps`、`pgrep`、`top` 等外部命令，不读取 `/proc`；
- 仅通过 Apple 系统库 `/usr/lib/libproc.dylib` 的 `proc_listpids` /
  `proc_pidinfo(PROC_PIDTASKINFO=4)` / `proc_pidinfo(PROC_PIDTBSDINFO=3)` 采样；
- 不复制父进程完整环境、不读取 `.env`、不记录命令行（命令行可能含凭据）；
- 进程标识只用 pid、父 pid 与进程名（comm/name），不采集 argv / environ。

CPU 口径：`proc_taskinfo.pti_total_user + pti_total_system` 是任务自启动以来的
累计 CPU 纳秒；两次采样差值 / 墙钟差值 * 100 得到区间 CPU%（多核可 >100%）。
RSS 口径：`pti_resident_size` 字节 → MB（1 MB = 1024*1024 B）。

本文件既可作为库被 perf_run009.py 使用，也可单独自检：
    .venv/bin/python scripts/resource_monitor_run009.py --self-test --seconds 3
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import resource
import sqlite3
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs/assets/run009/wp5/resource-self-test.jsonl"

# --------------------------------------------------------------------------- libproc 绑定

PROC_ALL_PIDS = 1
PROC_PIDTBSDINFO = 3
PROC_PIDTASKINFO = 4
MAXCOMLEN = 16


class ProcTaskInfo(ctypes.Structure):
    """sys/proc_info.h: struct proc_taskinfo（96 字节）。"""

    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("total_user", ctypes.c_uint64),
        ("total_system", ctypes.c_uint64),
        ("threads_user", ctypes.c_uint64),
        ("threads_system", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("faults", ctypes.c_int32),
        ("pageins", ctypes.c_int32),
        ("cow_faults", ctypes.c_int32),
        ("messages_sent", ctypes.c_int32),
        ("messages_received", ctypes.c_int32),
        ("syscalls_mach", ctypes.c_int32),
        ("syscalls_unix", ctypes.c_int32),
        ("csw", ctypes.c_int32),
        ("threadnum", ctypes.c_int32),
        ("numrunning", ctypes.c_int32),
        ("priority", ctypes.c_int32),
    ]


class ProcBsdInfo(ctypes.Structure):
    """sys/proc_info.h: struct proc_bsdinfo（136 字节）。"""

    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("rfu1", ctypes.c_uint32),
        ("comm", ctypes.c_char * MAXCOMLEN),
        ("name", ctypes.c_char * (2 * MAXCOMLEN)),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("tdev", ctypes.c_uint32),
        ("tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64),
        ("start_usec", ctypes.c_uint64),
    ]


class LibProcUnavailable(RuntimeError):
    pass


class LibProc:
    def __init__(self, path: str = "/usr/lib/libproc.dylib") -> None:
        if sys.platform != "darwin":
            raise LibProcUnavailable(f"libproc 采样仅支持 macOS，当前 {sys.platform}")
        try:
            self.lib = ctypes.CDLL(path, use_errno=True)
        except OSError as exc:  # pragma: no cover - 平台异常分支
            raise LibProcUnavailable(f"无法加载 {path}: {exc}") from exc
        self.lib.proc_listpids.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self.lib.proc_listpids.restype = ctypes.c_int
        self.lib.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self.lib.proc_pidinfo.restype = ctypes.c_int
        self.taskinfo_size = ctypes.sizeof(ProcTaskInfo)
        self.bsdinfo_size = ctypes.sizeof(ProcBsdInfo)

    def list_pids(self) -> list[int]:
        capacity = 4096
        while capacity <= 1_000_000:
            buf = (ctypes.c_int * capacity)()
            n = self.lib.proc_listpids(
                PROC_ALL_PIDS, 0, buf, capacity * ctypes.sizeof(ctypes.c_int)
            )
            if n < 0:
                return []
            used = n // ctypes.sizeof(ctypes.c_int)
            if used < capacity:
                return [int(p) for p in buf[:used] if p > 0]
            capacity *= 2
        return []

    def taskinfo(self, pid: int) -> ProcTaskInfo | None:
        info = ProcTaskInfo()
        n = self.lib.proc_pidinfo(pid, PROC_PIDTASKINFO, 0, ctypes.byref(info), self.taskinfo_size)
        return info if n == self.taskinfo_size else None

    def bsdinfo(self, pid: int) -> ProcBsdInfo | None:
        info = ProcBsdInfo()
        n = self.lib.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), self.bsdinfo_size)
        return info if n == self.bsdinfo_size else None

    @staticmethod
    def _cstr(raw: bytes) -> str:
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- 分位数


def percentile(values: list[float], q: float) -> float | None:
    """线性插值分位数，q ∈ [0,1]。空列表返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(ordered[lo], 3)
    val = ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
    return round(val, 3)


class LatencyTracker:
    """API 客户端侧 wall-clock 延迟记录（含失败请求）。线程安全。"""

    def __init__(self, max_records: int = 20000) -> None:
        self._records: deque[dict[str, Any]] = deque(maxlen=max_records)

    def record(self, method: str, path: str, ms: float, status: int | None = None) -> None:
        self._records.append({"method": method, "path": path, "ms": round(ms, 3), "status": status})

    def summary(self) -> dict[str, Any]:
        all_ms = [r["ms"] for r in self._records]
        by_path: dict[str, list[float]] = {}
        for r in self._records:
            by_path.setdefault(r["path"], []).append(r["ms"])
        return {
            "count": len(all_ms),
            "p50_ms": percentile(all_ms, 0.5),
            "p95_ms": percentile(all_ms, 0.95),
            "p99_ms": percentile(all_ms, 0.99),
            "max_ms": round(max(all_ms), 3) if all_ms else None,
            "by_path": {
                p: {
                    "count": len(v),
                    "p50_ms": percentile(v, 0.5),
                    "p95_ms": percentile(v, 0.95),
                    "p99_ms": percentile(v, 0.99),
                }
                for p, v in sorted(by_path.items())
            },
            "non_2xx_count": sum(
                1
                for r in self._records
                if r["status"] is not None and not (200 <= r["status"] < 300)
            ),
        }

    def records(self) -> list[dict[str, Any]]:
        return list(self._records)


# --------------------------------------------------------------------------- 资源采样器

_CHROME_TOKENS = ("chrom", "chrome", "headless_shell")
_BROWSER_TOKENS = ("chromium", "chrome", "google chrome", "msedge")


class ResourceSampler:
    """按固定间隔采样指定 pid 组，并自动发现浏览器后代进程。"""

    def __init__(self) -> None:
        self.proc = LibProc()
        self._prev: dict[int, tuple[float, int]] = {}  # pid -> (monotonic, cumulative ns)
        self._t0: float | None = None
        self._first_rss: dict[str, float] = {}
        self.samples: list[dict[str, Any]] = []

    @staticmethod
    def _is_chrome(name: str) -> bool:
        n = name.lower()
        return any(tok in n for tok in _CHROME_TOKENS)

    @staticmethod
    def _is_browser_main(name: str) -> bool:
        n = name.lower()
        return any(tok in n for tok in _BROWSER_TOKENS) and "helper" not in n

    def sample(self, groups: dict[str, list[int]]) -> dict[str, Any]:
        """采集一帧。groups 形如 {"api":[pid], "worker":[p1,p2], ...}。"""
        now = time.monotonic()
        if self._t0 is None:
            self._t0 = now
        pids = self.proc.list_pids()
        meta: dict[int, dict[str, Any]] = {}
        parent: dict[int, int] = {}
        children: dict[int, list[int]] = {}
        for pid in pids:
            bi = self.proc.bsdinfo(pid)
            if bi is None:
                continue
            name = LibProc._cstr(bi.comm) or LibProc._cstr(bi.name)
            meta[pid] = {"name": name, "ppid": int(bi.ppid)}
            parent[pid] = int(bi.ppid)
            children.setdefault(int(bi.ppid), []).append(pid)

        roots = [p for ps in groups.values() for p in ps]
        root_set = set(roots)
        descendants: set[int] = set()
        stack = list(root_set)
        while stack:
            cur = stack.pop()
            for child in children.get(cur, []):
                if child not in descendants:
                    descendants.add(child)
                    stack.append(child)

        chrome_pids = sorted(
            p for p in descendants if self._is_chrome(meta.get(p, {}).get("name", ""))
        )
        # 浏览器实例近似：Chrome 进程中父进程不是 Chrome 的视为一个实例主进程。
        browser_roots: list[int] = []
        for p in chrome_pids:
            pp = parent.get(p, -1)
            if pp not in chrome_pids:
                browser_roots.append(p)

        group_stats: dict[str, Any] = {}
        for group, pids_in_group in groups.items():
            entries: list[dict[str, Any]] = []
            cpu_total = 0.0
            rss_total = 0.0
            alive = 0
            for pid in pids_in_group:
                ti = self.proc.taskinfo(pid)
                if ti is None:
                    entries.append({"pid": pid, "alive": False})
                    continue
                alive += 1
                rss_mb = round(ti.resident_size / (1024 * 1024), 2)
                cum_ns = int(ti.total_user) + int(ti.total_system)
                prev = self._prev.get(pid)
                cpu_pct: float | None = None
                if prev is not None:
                    dt = now - prev[0]
                    if dt > 0:
                        cpu_pct = round(max(0.0, (cum_ns - prev[1]) / 1e9 / dt * 100), 2)
                self._prev[pid] = (now, cum_ns)
                cpu_total += cpu_pct or 0.0
                rss_total += rss_mb
                entries.append(
                    {
                        "pid": pid,
                        "alive": True,
                        "name": meta.get(pid, {}).get("name", "?"),
                        "cpu_pct": cpu_pct,
                        "rss_mb": rss_mb,
                        "threads": int(ti.threadnum),
                        "total_cpu_s": round(cum_ns / 1e9, 3),
                    }
                )
            if group not in self._first_rss and alive:
                self._first_rss[group] = rss_total
            growth = None
            if group in self._first_rss and now - self._t0 > 0:  # type: ignore[operator]
                growth = round(
                    (rss_total - self._first_rss[group]) / (now - self._t0),
                    4,  # type: ignore[operator]
                )
            group_stats[group] = {
                "alive": alive,
                "expected": len(pids_in_group),
                "cpu_pct": round(cpu_total, 2),
                "rss_mb": round(rss_total, 2),
                "rss_growth_mb_per_s": growth,
                "rss_growth_pct": (
                    round((rss_total / self._first_rss[group] - 1) * 100, 2)
                    if self._first_rss.get(group)
                    else None
                ),
                "processes": entries,
            }

        chrome_stats: list[dict[str, Any]] = []
        chrome_rss = 0.0
        chrome_cpu = 0.0
        for pid in chrome_pids:
            ti = self.proc.taskinfo(pid)
            if ti is None:
                continue
            rss_mb = round(ti.resident_size / (1024 * 1024), 2)
            cum_ns = int(ti.total_user) + int(ti.total_system)
            prev = self._prev.get(pid)
            cpu_pct = None
            if prev is not None:
                dt = now - prev[0]
                if dt > 0:
                    cpu_pct = round(max(0.0, (cum_ns - prev[1]) / 1e9 / dt * 100), 2)
            self._prev[pid] = (now, cum_ns)
            chrome_rss += rss_mb
            chrome_cpu += cpu_pct or 0.0
            chrome_stats.append(
                {
                    "pid": pid,
                    "name": meta.get(pid, {}).get("name", "?"),
                    "ppid": parent.get(pid),
                    "cpu_pct": cpu_pct,
                    "rss_mb": rss_mb,
                    "is_browser_root": pid in browser_roots,
                }
            )

        frame = {
            "t_monotonic": round(now, 4),
            "elapsed_s": round(now - self._t0, 4),  # type: ignore[operator]
            "source": "libproc.dylib:proc_pidinfo(PROC_PIDTASKINFO/PROC_PIDTBSDINFO)",
            "groups": group_stats,
            "chromium": {
                "process_count": len(chrome_stats),
                "browser_instance_count": len(browser_roots),
                "cpu_pct": round(chrome_cpu, 2),
                "rss_mb": round(chrome_rss, 2),
                "processes": chrome_stats,
            },
            "rusage_self": {
                "maxrss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
                "note": "macOS ru_maxrss 单位为字节（此处已换算为 MB）；仅采样进程自身",
            },
        }
        self.samples.append(frame)
        return frame


# --------------------------------------------------------------------------- 数据库/证据探针


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


class DbProbe:
    """只读 SQLite 文件探针 + 独立连接的写锁等待探测。"""

    def __init__(self, db_path: Path, artifact_dir: Path) -> None:
        self.db_path = db_path
        self.artifact_dir = artifact_dir
        self.lock_waits_ms: list[float] = []
        self.lock_failures = 0

    def _connect_ro(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=0.5)
        conn.execute("PRAGMA busy_timeout=500")
        return conn

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source": "sqlite3 只读连接 + os.stat（真实文件/表统计）",
            "db_path": str(self.db_path),
            "file_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "wal_bytes": Path(str(self.db_path) + "-wal").stat().st_size
            if Path(str(self.db_path) + "-wal").exists()
            else 0,
            "shm_bytes": Path(str(self.db_path) + "-shm").stat().st_size
            if Path(str(self.db_path) + "-shm").exists()
            else 0,
            "evidence_dir_bytes": _dir_size(self.artifact_dir),
        }
        try:
            conn = self._connect_ro()
            try:
                out["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
                out["busy_timeout_ms"] = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                out["synchronous"] = conn.execute("PRAGMA synchronous").fetchone()[0]
                out["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
                out["freelist_count"] = conn.execute("PRAGMA freelist_count").fetchone()[0]
                out["migrations"] = [
                    {"version": r[0], "name": r[1], "applied_at": r[2]}
                    for r in conn.execute(
                        "SELECT version, name, applied_at FROM tg_schema_migration ORDER BY version"
                    ).fetchall()
                ]
                out["row_counts"] = {}
                for table in ("tg_run", "tg_event", "tg_evidence", "tg_step"):
                    try:
                        out["row_counts"][table] = conn.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                    except sqlite3.Error:
                        out["row_counts"][table] = None
                out["indexes"] = [
                    {"name": r[0], "table": r[1]}
                    for r in conn.execute(
                        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY tbl_name, name"
                    ).fetchall()
                ]
                # 大 Event/Evidence 查询：真实执行，记录耗时与返回行数
                for label, sql in (
                    (
                        "events_limit500",
                        "SELECT * FROM tg_event ORDER BY recorded_at DESC LIMIT 500",
                    ),
                    (
                        "evidence_limit500",
                        "SELECT * FROM tg_evidence ORDER BY created_at DESC LIMIT 500",
                    ),
                ):
                    t0 = time.perf_counter()
                    try:
                        rows = conn.execute(sql).fetchall()
                        out.setdefault("query_probes", {})[label] = {
                            "ms": round((time.perf_counter() - t0) * 1000, 2),
                            "rows": len(rows),
                        }
                    except sqlite3.Error as exc:
                        out.setdefault("query_probes", {})[label] = {
                            "ms": round((time.perf_counter() - t0) * 1000, 2),
                            "error": str(exc)[:200],
                        }
            finally:
                conn.close()
        except sqlite3.Error as exc:
            out["probe_error"] = str(exc)[:300]
        return out

    def probe_write_lock(self, busy_ms: float = 500.0) -> dict[str, Any]:
        """独立连接 BEGIN IMMEDIATE 的获取耗时 = 该库写锁等待的真实样本。

        探测事务立即 ROLLBACK，不写入任何数据；busy_timeout 有限，失败只记数。
        """
        t0 = time.perf_counter()
        result: dict[str, Any] = {"ok": False, "ms": None}
        if not self.db_path.exists():
            return result
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=busy_ms / 1000)
            try:
                conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
                conn.execute("BEGIN IMMEDIATE")
                ms = (time.perf_counter() - t0) * 1000
                conn.rollback()
                self.lock_waits_ms.append(round(ms, 2))
                result = {"ok": True, "ms": round(ms, 2)}
            finally:
                conn.close()
        except sqlite3.Error:
            self.lock_failures += 1
            result = {"ok": False, "ms": round((time.perf_counter() - t0) * 1000, 2)}
        return result

    def lock_summary(self) -> dict[str, Any]:
        return {
            "source": "独立连接 BEGIN IMMEDIATE 获取耗时（真实锁竞争样本；非应用内部等待）",
            "samples": len(self.lock_waits_ms),
            "busy_failures": self.lock_failures,
            "p50_ms": percentile(self.lock_waits_ms, 0.5),
            "p95_ms": percentile(self.lock_waits_ms, 0.95),
            "max_ms": max(self.lock_waits_ms) if self.lock_waits_ms else None,
            "all_ms": self.lock_waits_ms,
        }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RUN-009 WP5 资源采样器自检")
    ap.add_argument("--self-test", action="store_true", help="兼容用法标记（默认即自检）")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    sampler = ResourceSampler()
    groups = {"monitor": [os.getpid()]}
    frames: list[dict[str, Any]] = []
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        frame = sampler.sample(groups)
        frames.append(frame)
        print(
            f"t={frame['elapsed_s']:5.2f}s monitor RSS={frame['groups']['monitor']['rss_mb']}MB "
            f"CPU={frame['groups']['monitor']['cpu_pct']}%"
        )
        time.sleep(args.interval)
    path = Path(args.out)
    write_jsonl(path, frames)
    print(f"采样 {len(frames)} 帧 -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
