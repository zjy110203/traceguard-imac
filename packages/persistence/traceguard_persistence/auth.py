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
    "user.manage": {ROLE_PLATFORM_ADMIN},
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
                    "SELECT s.*, u.username, u.roles, u.active FROM tg_session s "
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
    """追加一条带前序哈希的审计记录。调用方负责不传入敏感字段。"""

    def _dump(value: dict[str, Any] | None) -> dict[str, Any] | None:
        return value if value else None

    with _audit_lock:
        with db.session() as s:
            last = s.execute(
                text(
                    "SELECT entry_hash FROM tg_audit_log ORDER BY timestamp DESC, audit_id DESC LIMIT 1"
                )
            ).first()
            prev = str(last[0]) if last else ""
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
            }
            entry["entry_hash"] = _audit_hash(entry)
            s.execute(
                text(
                    "INSERT INTO tg_audit_log (audit_id, actor_id, role, action, resource_type, "
                    "resource_id, timestamp, result, request_id, before_json, after_json, "
                    "prev_hash, entry_hash) VALUES (:audit_id, :actor_id, :role, :action, "
                    ":resource_type, :resource_id, :timestamp, :result, :request_id, :before_json, "
                    ":after_json, :prev_hash, :entry_hash)"
                ),
                entry,
            )
    return entry


def verify_audit_chain(db: Database) -> dict[str, Any]:
    with db.session() as s:
        rows = [dict(r) for r in s.execute(text("SELECT * FROM tg_audit_log")).mappings().all()]
    ordered = sorted(rows, key=lambda r: (r["timestamp"] or "", r["audit_id"] or ""))
    prev = ""
    broken: list[str] = []
    for row in ordered:
        entry = dict(row)
        expected = entry.pop("entry_hash", "")
        if entry.get("prev_hash") != prev or _audit_hash(entry) != expected:
            broken.append(str(row.get("audit_id")))
        prev = str(expected)
    return {
        "entries": len(ordered),
        "broken": broken,
        "intact": not broken,
        "ordering": "timestamp ASC, audit_id ASC",
    }
