"""RUN-009 WP2 / RUN-010 WP1：本地认证用户初始化（不把凭据写入仓库）。

用法（环境变量方式，向后兼容）：
    TG_BOOTSTRAP_USERNAME=admin TG_BOOTSTRAP_PASSWORD='...' .venv/bin/python scripts/auth_bootstrap.py

用法（CLI 参数方式，RUN-010 E2E / 部署脚本用）：
    .venv/bin/python scripts/auth_bootstrap.py \
      --db "sqlite:///./.traceguard/traceguard.db" \
      --username admin --password '...' \
      --roles platform-admin,qa-lead --workspaces '["*"]'

只在首次创建用户；已存在时拒绝覆盖（退出码 3）。密码与令牌不会打印到日志或报告。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "persistence"))

from traceguard_persistence.auth import create_user, get_user_by_username, set_user_workspaces  # noqa: E402
from traceguard_persistence.db import Database  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TraceGuard 认证用户初始化")
    parser.add_argument("--db", default="", help="数据库 DSN（默认读 TG_DATABASE_URL / 项目配置）")
    parser.add_argument("--username", default="", help="用户名")
    parser.add_argument(
        "--password", default="", help="密码（不传则读取 TG_BOOTSTRAP_PASSWORD 或交互输入）"
    )
    parser.add_argument(
        "--roles",
        default="",
        help="逗号分隔角色；默认 platform-admin,qa-lead,qa-engineer,viewer",
    )
    parser.add_argument(
        "--workspaces", default="", help='workspace 范围 JSON，如 ["*"] 或 ["ws-a"]'
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    username = (args.username or os.getenv("TG_BOOTSTRAP_USERNAME", "")).strip()
    password = args.password or os.getenv("TG_BOOTSTRAP_PASSWORD", "")
    if not username:
        print("username is required (--username or TG_BOOTSTRAP_USERNAME)", file=sys.stderr)
        return 2
    if not password:
        password = getpass.getpass("Bootstrap password: ")
    if len(password) < 8:
        print("password must be at least 8 characters", file=sys.stderr)
        return 2
    roles = (
        [r.strip() for r in args.roles.split(",") if r.strip()]
        if args.roles
        else ["platform-admin", "qa-lead", "qa-engineer", "viewer"]
    )
    db = Database(args.db or None)
    db.create_schema()
    if get_user_by_username(db, username):
        print("USER_ALREADY_EXISTS; no change made", file=sys.stderr)
        return 3
    user = create_user(db, username=username, password=password, roles=roles)
    if args.workspaces:
        try:
            scope = json.loads(args.workspaces)
        except json.JSONDecodeError:
            print("invalid --workspaces JSON", file=sys.stderr)
            return 2
        set_user_workspaces(db, user["user_id"], scope)
    print(f"created user_id={user['user_id']} username={user['username']} roles={user['roles']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
