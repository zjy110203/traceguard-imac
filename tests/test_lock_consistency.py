"""RUN-010 WP7：Python 依赖 lock 完整性与一致性测试。

覆盖指令 §7.1：
- requirements.txt / requirements-dev.txt 声明的每个直接依赖都出现在 lock 中；
- lock 中直接依赖的锁定版本必须满足声明的范围（防 pytest-cov 式约束冲突）；
- lock 自身无重复条目、全部为 name==version 形式。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_REQ_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")


def _direct_requirements(path: Path) -> list[tuple[str, str]]:
    """解析 range 声明文件：返回 (name, constraint) 列表，跳过 -r 引用与注释。"""
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        m = _REQ_NAME.match(line)
        assert m, f"无法解析依赖行: {path.name}: {line}"
        name = m.group(1).lower().replace("_", "-")
        constraint = line[m.end() :].strip()
        out.append((name, constraint))
    return out


def _lock() -> dict[str, str]:
    lock_path = ROOT / "requirements.lock.txt"
    out: dict[str, str] = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"lock 行必须是 name==version: {line}"
        name, version = line.split("==", 1)
        key = name.lower().replace("_", "-")
        assert key not in out, f"lock 重复条目: {key}"
        out[key] = version
    return out


def _version_satisfies(version: str, constraint: str) -> bool:
    """最小可用的 PEP 440 范围判定（支持 >=, <=, ==, <, >, 逗号多段）。"""

    def parse(v: str) -> tuple[int, ...]:
        nums = re.findall(r"\d+", v)
        return tuple(int(x) for x in nums) if nums else (0,)

    v = parse(version)
    for part in [c.strip() for c in constraint.split(",") if c.strip()]:
        m = re.match(r"^(>=|<=|==|!=|<|>)\s*(.+)$", part)
        if not m:
            continue
        op, target = m.group(1), parse(m.group(2))
        if op == ">=" and not v >= target:
            return False
        if op == "<=" and not v <= target:
            return False
        if op == "==" and v != target:
            return False
        if op == "!=" and v == target:
            return False
        if op == "<" and not v < target:
            return False
        if op == ">" and not v > target:
            return False
    return True


def test_lock_contains_all_runtime_deps() -> None:
    lock = _lock()
    for name, _constraint in _direct_requirements(ROOT / "requirements.txt"):
        assert name in lock, f"运行时依赖 {name} 未锁定"


def test_lock_contains_all_dev_deps() -> None:
    lock = _lock()
    for name, _constraint in _direct_requirements(ROOT / "requirements-dev.txt"):
        assert name in lock, f"开发依赖 {name} 未锁定"


def test_locked_versions_satisfy_declared_ranges() -> None:
    """约束冲突防护：lock 版本必须满足声明范围（如 pytest-cov <7 vs ==7.1.0 类冲突）。"""
    lock = _lock()
    conflicts: list[str] = []
    for req_file in ("requirements.txt", "requirements-dev.txt"):
        for name, constraint in _direct_requirements(ROOT / req_file):
            version = lock.get(name)
            if version and not _version_satisfies(version, constraint):
                conflicts.append(f"{name}: lock={version} 不满足 {req_file}:{constraint}")
    assert not conflicts, "lock 与声明范围冲突:\n" + "\n".join(conflicts)


def test_lock_entries_are_pinned() -> None:
    lock_path = ROOT / "requirements.lock.txt"
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        assert "==" in line, f"lock 必须完全钉住: {line}"
        assert ">=" not in line and "~=" not in line and ">" not in line.split("==")[1], line
