"""RUN-009 WP2：本地真实身份、会话、RBAC 与审计日志持久化。

安全口径：
- 密码只存 PBKDF2-HMAC-SHA256 摘要，不存明文；
- 会话令牌安全随机生成，库里只存 SHA-256 摘要；
- 审批主体由服务端 Principal 注入，不信任请求体声明；
- 审计记录带前序哈希链，便于发现删改；不记录密码/令牌/密钥等敏感内容。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any

from sqlalchemy import text
from traceguard_core.ids import utc_now_iso

from .db import Database

# 角色 → 允许动作。Agent / LLM 永远不在角色集合中。
ROLE_VIEWER = "viewer"
ROLE_QA_ENGINEER = "qa-engineer"
ROLE_QA_LEAD = "qa-lead"
ROLE_PLATFORM_ADMIN = "platform-admin"
ALL_ROLES = {ROLE_VIEWER, ROLE_QA_ENGINEER, ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN}

# 审计写入的乐观分配重试上限（跨进程 seq 冲突）。
_AUDIT_APPEND_RETRIES = 6

PERMISSIONS: dict[str, set[str]] = {
    "read": {ROLE_VIEWER, ROLE_QA_ENGINEER, ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "testcase.create": {ROLE_QA_ENGINEER, ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "testcase.review": {ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "oracle.create": {ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "oracle.bind": {ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "run.execute": {ROLE_QA_ENGINEER, ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "diagnosis.operate": {ROLE_QA_ENGINEER, ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "probe.execute": {ROLE_QA_ENGINEER, ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "repair.submit": {ROLE_QA_ENGINEER, ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "repair.approve": {ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN},
    "repair.promote": {ROLE_PLATFORM_ADMIN},
    "repair.rollback": {ROLE_PLATFORM_ADMIN},
    "system.configure": {ROLE_PLATFORM_ADMIN},
    # RUN-011-R1.2：跨 Workspace 的全局运维观测（审计/冲突/Inbox 汇总）独立权限，
    # 不得通过普通 read 暴露给 viewer / qa-engineer / qa-lead。
    "system.observe": {ROLE_PLATFORM_ADMIN},
    "user.manage": {ROLE_PLATFORM_ADMIN},
}

#: 角色展示矩阵（前端用户管理页只做展示，服务端始终是最终权限判定者）。
PERMISSION_MATRIX: dict[str, list[str]] = {
    role: sorted(action for action, roles in PERMISSIONS.items() if role in roles)
    for role in sorted(ALL_ROLES)
}

_PBKDF2_ROUNDS = 200_000
_audit_lock = threading.Lock()


def has_permission(roles: list[str] | set[str], action: str) -> bool:
    allowed = PERMISSIONS.get(action)
    if not allowed:
        return False
    return bool(set(roles) & allowed)


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("PASSWORD_TOO_SHORT")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_roles(roles: list[str]) -> list[str]:
    cleaned = sorted({str(r).strip() for r in roles if str(r).strip()})
    unknown = set(cleaned) - ALL_ROLES
    if unknown:
        raise ValueError(f"UNKNOWN_ROLE: {sorted(unknown)}")
    if not cleaned:
        raise ValueError("ROLE_REQUIRED")
    return cleaned


def create_user(
    db: Database, *, username: str, password: str, roles: list[str], user_id: str = ""
) -> dict[str, Any]:
    name = (username or "").strip().lower()
    if not name:
        raise ValueError("USERNAME_REQUIRED")
    cleaned_roles = _validate_roles(roles)
    now = utc_now_iso()
    uid = user_id or f"usr_{secrets.token_hex(10)}"
    with db.session() as s:
        existing = s.execute(
            text("SELECT user_id FROM tg_user WHERE username = :u"), {"u": name}
        ).first()
        if existing:
            raise ValueError("USER_ALREADY_EXISTS")
        s.execute(
            text(
                "INSERT INTO tg_user (user_id, username, password_hash, roles, active, "
                "created_at, updated_at) VALUES (:id, :u, :p, :r, 1, :now, :now)"
            ),
            {
                "id": uid,
                "u": name,
                "p": hash_password(password),
                "r": json.dumps(cleaned_roles),
                "now": now,
            },
        )
    return {"user_id": uid, "username": name, "roles": cleaned_roles}


def get_user_by_username(db: Database, username: str) -> dict[str, Any] | None:
    with db.session() as s:
        row = (
            s.execute(
                text("SELECT * FROM tg_user WHERE username = :u"), {"u": (username or "").lower()}
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def get_user(db: Database, user_id: str) -> dict[str, Any] | None:
    with db.session() as s:
        row = (
            s.execute(text("SELECT * FROM tg_user WHERE user_id = :id"), {"id": user_id})
            .mappings()
            .first()
        )
    return dict(row) if row else None


def list_users(db: Database) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.execute(text("SELECT * FROM tg_user ORDER BY username")).mappings().all()
    return [dict(r) for r in rows]


def set_user_active(db: Database, user_id: str, active: bool) -> bool:
    with db.session() as s:
        result = s.execute(
            text("UPDATE tg_user SET active = :a, updated_at = :now WHERE user_id = :id"),
            {"a": 1 if active else 0, "now": utc_now_iso(), "id": user_id},
        )
    return result.rowcount == 1


def change_password(db: Database, user_id: str, old_password: str, new_password: str) -> bool:
    user = get_user(db, user_id)
    if not user or not verify_password(old_password, user["password_hash"]):
        return False
    with db.session() as s:
        s.execute(
            text("UPDATE tg_user SET password_hash = :p, updated_at = :now WHERE user_id = :id"),
            {"p": hash_password(new_password), "now": utc_now_iso(), "id": user_id},
        )
    revoke_all_sessions(db, user_id)
    return True


def create_session(db: Database, user_id: str, ttl_s: int) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    with db.session() as s:
        s.execute(
            text(
                "INSERT INTO tg_session (token_hash, user_id, csrf_token, expires_at, revoked, "
                "created_at) VALUES (:h, :uid, :csrf, :exp, 0, :now)"
            ),
            {
                "h": _token_hash(token),
                "uid": user_id,
                "csrf": csrf,
                "exp": time.time() + ttl_s,
                "now": utc_now_iso(),
            },
        )
    return {"token": token, "csrf_token": csrf, "expires_in_s": ttl_s}


def get_session(db: Database, token: str) -> dict[str, Any] | None:
    if not token:
        return None
    with db.session() as s:
        row = (
            s.execute(
                text(
                    "SELECT s.*, u.username, u.roles, u.active, u.workspaces FROM tg_session s "
                    "LEFT JOIN tg_user u ON u.user_id = s.user_id "
                    "WHERE s.token_hash = :h"
                ),
                {"h": _token_hash(token)},
            )
            .mappings()
            .first()
        )
    if not row:
        return None
    item = dict(row)
    if item["revoked"] or not item["active"] or float(item["expires_at"]) <= time.time():
        return None
    try:
        item["role_list"] = json.loads(item["roles"] or "[]")
    except (TypeError, ValueError):
        item["role_list"] = []
    return item


def revoke_session(db: Database, token: str) -> bool:
    with db.session() as s:
        result = s.execute(
            text("UPDATE tg_session SET revoked = 1 WHERE token_hash = :h"),
            {"h": _token_hash(token)},
        )
    return result.rowcount == 1


def revoke_all_sessions(db: Database, user_id: str) -> int:
    with db.session() as s:
        result = s.execute(
            text("UPDATE tg_session SET revoked = 1 WHERE user_id = :uid AND revoked = 0"),
            {"uid": user_id},
        )
    return int(result.rowcount or 0)


def purge_expired_sessions(db: Database) -> int:
    with db.session() as s:
        result = s.execute(
            text("DELETE FROM tg_session WHERE expires_at < :now"), {"now": time.time()}
        )
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# RUN-010 WP1：数据库级登录失败计数（多进程共享，不可被单进程重启绕过）。
# ---------------------------------------------------------------------------


def record_login_attempt(db: Database, *, username: str, client: str, ok: bool) -> dict[str, Any]:
    """原子追加一次登录尝试记录。失败计数以本表为准，不依赖进程内状态。"""
    name = (username or "").strip().lower()[:120]
    entry = {
        "attempt_id": f"la_{secrets.token_hex(12)}",
        "username": name,
        "client": (client or "")[:120],
        "ok": 1 if ok else 0,
        "created_at": utc_now_iso(),
    }
    with db.session() as s:
        s.execute(
            text(
                "INSERT INTO tg_login_attempt (attempt_id, username, client, ok, created_at) "
                "VALUES (:attempt_id, :username, :client, :ok, :created_at)"
            ),
            entry,
        )
    return entry


def count_failed_logins(db: Database, *, username: str, client: str, window_s: int) -> int:
    """统计窗口期内的失败次数（username + client 维度，跨进程一致）。"""
    name = (username or "").strip().lower()[:120]
    cutoff = _iso_at(time.time() - window_s)
    with db.session() as s:
        row = s.execute(
            text(
                "SELECT COUNT(*) FROM tg_login_attempt "
                "WHERE username = :u AND client = :c AND ok = 0 AND created_at >= :cutoff"
            ),
            {"u": name, "c": (client or "")[:120], "cutoff": cutoff},
        ).first()
    return int(row[0]) if row else 0


def prune_login_attempts(db: Database, *, older_than_s: int) -> int:
    """清理过期尝试记录，防止表无限增长（由登录流程周期触发）。"""
    cutoff = _iso_at(time.time() - older_than_s)
    with db.session() as s:
        result = s.execute(
            text("DELETE FROM tg_login_attempt WHERE created_at < :cutoff"), {"cutoff": cutoff}
        )
    return int(result.rowcount or 0)


def _iso_at(ts: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# RUN-010 WP1：workspace 数据访问范围（对象级授权）。
# ---------------------------------------------------------------------------


def parse_workspaces(raw: Any) -> list[str]:
    """解析用户 workspace 范围。空 / 解析失败一律按仅 default 处理（fail-closed）。"""
    if raw is None:
        return ["default"]
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(str(raw) or "[]")
        except (TypeError, ValueError):
            return ["default"]
    cleaned = sorted({str(w).strip() for w in items if str(w).strip()})
    return cleaned or ["default"]


def workspace_in_scope(scope: list[str], workspace_id: str) -> bool:
    """`*` 表示全部 workspace；其余为显式白名单。"""
    if "*" in scope:
        return True
    return workspace_id in scope


def user_workspace_scope(user: dict[str, Any]) -> list[str]:
    return parse_workspaces(user.get("workspaces"))


def set_user_workspaces(db: Database, user_id: str, workspaces: list[str]) -> bool:
    cleaned = parse_workspaces(list(workspaces) or ["default"])
    with db.session() as s:
        result = s.execute(
            text("UPDATE tg_user SET workspaces = :w, updated_at = :now WHERE user_id = :id"),
            {"w": json.dumps(cleaned), "now": utc_now_iso(), "id": user_id},
        )
    return result.rowcount == 1


def _canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _audit_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()


def record_audit(
    db: Database,
    *,
    actor_id: str,
    action: str,
    role: str = "",
    resource_type: str = "",
    resource_id: str = "",
    result: str = "SUCCESS",
    request_id: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """追加一条带前序哈希的审计记录。调用方负责不传入敏感字段。

    RUN-010 WP1：跨进程安全。seq 通过「读最大值 + 唯一索引冲突重试」乐观分配；
    同一 seq 不可能被两个进程同时写入（ux_tg_audit_seq 唯一索引兜底），
    重试时重新读取 prev_hash，保证哈希链前驱永远指向已提交的真实末尾。
    """

    def _dump(value: dict[str, Any] | None) -> Any:
        # raw text() SQL 不经过 SQLAlchemy 类型绑定：必须显式序列化为 JSON 字符串。
        # （真实缺陷修复：RUN-009 的 set_user_active(after={...}) 在 SQLite 下 500，
        #   因 dict 无法直接绑定；PostgreSQL 同样需要文本参数。）
        if not value:
            return None
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    with _audit_lock:
        last_exc: Exception | None = None
        for _ in range(_AUDIT_APPEND_RETRIES):
            try:
                with db.session() as s:
                    row = s.execute(
                        text("SELECT seq, entry_hash FROM tg_audit_log ORDER BY seq DESC LIMIT 1")
                    ).first()
                    seq = int(row[0]) + 1 if row and row[0] is not None else 1
                    prev = str(row[1]) if row else ""
                    now = utc_now_iso()
                    entry = {
                        "audit_id": f"aud_{secrets.token_hex(12)}",
                        "actor_id": str(actor_id),
                        "role": str(role),
                        "action": str(action),
                        "resource_type": str(resource_type),
                        "resource_id": str(resource_id),
                        "timestamp": now,
                        "result": str(result),
                        "request_id": str(request_id),
                        "before_json": _dump(before),
                        "after_json": _dump(after),
                        "prev_hash": prev,
                        "seq": seq,
                    }
                    entry["entry_hash"] = _audit_hash(_entry_without_seq(entry))
                    s.execute(
                        text(
                            "INSERT INTO tg_audit_log (audit_id, actor_id, role, action, "
                            "resource_type, resource_id, timestamp, result, request_id, "
                            "before_json, after_json, prev_hash, entry_hash, seq) "
                            "VALUES (:audit_id, :actor_id, :role, :action, "
                            ":resource_type, :resource_id, :timestamp, :result, :request_id, "
                            ":before_json, :after_json, :prev_hash, :entry_hash, :seq)"
                        ),
                        entry,
                    )
                return entry
            except Exception as exc:  # noqa: BLE001 - seq 唯一冲突 → 重新读取后重试
                if _is_seq_conflict(exc):
                    last_exc = exc
                    continue
                raise
        raise last_exc  # pragma: no cover - 重试耗尽（理论上极小概率）


def _entry_without_seq(entry: dict[str, Any]) -> dict[str, Any]:
    """entry_hash 不包含 seq：seq 只是物理排序保证，逻辑链仍由 prev_hash 串联。"""
    return {k: v for k, v in entry.items() if k != "seq"}


def _is_seq_conflict(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "unique constraint" in msg
        or "duplicate key" in msg
        or "unique index" in msg
        or "database is locked" in msg
    )


def _normalize_json_columns(entry: dict[str, Any]) -> dict[str, Any]:
    """把 JSON 列还原成写入时的规范化字符串（供审计链校验/复算使用）。

    PostgreSQL 的 JSON 列读回是 dict/list；SQLite 读回是原字符串。
    写入哈希时用的是 `_dump()` 产生的字符串，因此校验前必须统一成同一形态，
    否则同一份未被篡改的数据在 PostgreSQL 上会被误判为链断裂。
    """
    for key in ("before_json", "after_json"):
        value = entry.get(key)
        if value is not None and not isinstance(value, str):
            entry[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return entry


def verify_audit_chain(db: Database) -> dict[str, Any]:
    with db.session() as s:
        rows = [dict(r) for r in s.execute(text("SELECT * FROM tg_audit_log")).mappings().all()]
    ordered = sorted(
        rows,
        key=lambda r: (r["seq"] is None, r["seq"] or 0, r["timestamp"] or "", r["audit_id"] or ""),
    )
    prev = ""
    broken: list[str] = []
    for row in ordered:
        entry = dict(row)
        entry.pop("seq", None)
        expected = entry.pop("entry_hash", "")
        # RUN-011 WP1：写入时 before_json/after_json 是 JSON 字符串（hash 基于该字符串）。
        # PostgreSQL 的 JSON 列读回是 dict，直接参与规范化会得到不同摘要 →
        # 所有带 before/after 的条目都被误判为链断裂（真实闭环里 audit verify 报 broken）。
        # 校验前把 JSON 列还原成写入时的规范化字符串，其余字段保持原值。
        _normalize_json_columns(entry)
        if entry.get("prev_hash") != prev or _audit_hash(entry) != expected:
            broken.append(str(row.get("audit_id")))
        prev = str(expected)
    return {
        "entries": len(ordered),
        "broken": broken,
        "intact": not broken,
        "ordering": "seq ASC（v12 后单调分配；历史行按 timestamp, audit_id 回填）",
    }
