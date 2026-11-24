"""RUN-009 WP2：本地认证管理员初始化（不把凭据写入仓库）。

用法：
    TG_BOOTSTRAP_USERNAME=admin TG_BOOTSTRAP_PASSWORD='...' .venv/bin/python scripts/auth_bootstrap.py

只在首次创建用户；已存在时拒绝覆盖。密码与令牌不会打印到日志或报告。
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "persistence"))

from traceguard_persistence.auth import create_user, get_user_by_username  # noqa: E402
from traceguard_persistence.db import Database  # noqa: E402


def main() -> int:
    username = os.getenv("TG_BOOTSTRAP_USERNAME", "").strip()
    password = os.getenv("TG_BOOTSTRAP_PASSWORD", "")
    if not username:
        print("TG_BOOTSTRAP_USERNAME is required", file=sys.stderr)
        return 2
    if not password:
        password = getpass.getpass("Bootstrap password: ")
    db = Database()
    db.create_schema()
    if get_user_by_username(db, username):
        print("USER_ALREADY_EXISTS; no change made", file=sys.stderr)
        return 3
    user = create_user(
        db,
        username=username,
        password=password,
        roles=["platform-admin", "qa-lead", "qa-engineer", "viewer"],
    )
    print(f"created user_id={user['user_id']} username={user['username']} roles={user['roles']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
