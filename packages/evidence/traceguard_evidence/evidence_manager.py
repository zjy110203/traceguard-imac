"""Evidence Manager（阶段4，C06）。

关键语义：
- Evidence 分 raw / normalized / derived / diagnostic；
- 派生证据必须记录来源 raw_ref、规则 derivation_rule、模型版本 model_version；
- integrity_check 与 source_trust 是两个独立维度，不混为一谈；
- SHA-256 内容完整性；原始证据不可被派生结果冒充；
- 脱敏（redacted）标记；附件引用（大文件在对象存储，库内只存引用）。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import utc_now_iso
from traceguard_core.logging import redact


@dataclass
class EvidenceRecord:
    evidence_id: str
    kind: str  # raw / normalized / derived / diagnostic
    source_type: str  # browser / network / api / backend_log / database / agent / probe
    source_ref: str
    sha256: str  # 原始来源 payload（脱敏前）的摘要，历史字段名，保持兼容
    workspace_id: str
    content: dict[str, Any] = field(default_factory=dict)
    #: 实际持久化内容（脱敏后）的摘要；服务端完整性校验以它为准
    stored_content_sha256: str = ""
    #: 附件（ArtifactStore 落盘字节）的摘要
    attachment_sha256: str = ""
    #: 完整性与来源比对的明细（分开记录，不合并成一个 PASS）
    integrity_detail: dict[str, Any] = field(default_factory=dict)
    raw_ref: str | None = None
    derivation_rule: str | None = None
    model_version: str | None = None
    attachment_ref: str | None = None
    integrity_check: str = "NOT_RUN"  # PASS / FAIL / NOT_RUN
    source_trust: str = "UNKNOWN"  # TRUSTED / UNTRUSTED / UNKNOWN（与完整性独立）
    redacted: bool = False
    captured_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "sha256": self.sha256,
            "stored_content_sha256": self.stored_content_sha256,
            "attachment_sha256": self.attachment_sha256,
            "integrity_detail": dict(self.integrity_detail),
            "workspace_id": self.workspace_id,
            "content": dict(self.content),
            "raw_ref": self.raw_ref,
            "derivation_rule": self.derivation_rule,
            "model_version": self.model_version,
            "attachment_ref": self.attachment_ref,
            "integrity_check": self.integrity_check,
            "source_trust": self.source_trust,
            "redacted": self.redacted,
            "captured_at": self.captured_at,
            # RUN-011-R1 WP1 §1.4：暴露证据来源 Run（DB 列 run_id），
            # 供血缘校验「source_run_id 属于失败 Run」；向后兼容的新增字段。
            "run_id": getattr(self, "run_id", "") or "",
        }


def _content_hash(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class EvidenceManager:
    def __init__(self) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._lock = threading.RLock()

    def add_raw(
        self,
        evidence_id: str,
        source_type: str,
        source_ref: str,
        workspace_id: str,
        content: dict[str, Any],
        *,
        source_trust: str = "UNKNOWN",
        do_redact: bool = True,
        captured_at: str = "",
    ) -> EvidenceRecord:
        """采集原始证据。

        摘要语义（RUN-007-FINALIZATION-2 / WP2）：
        - `sha256` = 脱敏前**原始来源 payload** 的摘要（兼容历史字段名）；
        - `stored_content_sha256` = 实际持久化内容（脱敏后）的摘要。
        二者不可混用：库里保存的是脱敏后内容，只有后者能通过"读回内容重算"来验证。
        """
        raw_hash = _content_hash(content)  # 基于原始内容
        stored_content = redact(content) if do_redact else content
        rec = EvidenceRecord(
            evidence_id=evidence_id,
            kind="raw",
            source_type=source_type,
            source_ref=source_ref,
            sha256=raw_hash,
            workspace_id=workspace_id,
            content=stored_content,
            stored_content_sha256=_content_hash(stored_content),
            source_trust=source_trust,
            redacted=do_redact,
            captured_at=captured_at or utc_now_iso(),
        )
        with self._lock:
            if evidence_id in self._records:
                raise AppError(ErrorCode.CONFLICT, "evidence_id exists (immutable)")
            self._records[evidence_id] = rec
        return rec

    def add_derived(
        self,
        evidence_id: str,
        raw_ref: str,
        workspace_id: str,
        content: dict[str, Any],
        *,
        derivation_rule: str,
        model_version: str | None = None,
        source_trust: str = "UNKNOWN",
    ) -> EvidenceRecord:
        """派生证据：必须指向 raw_ref 并记录规则/模型版本，不得冒充原始事实。"""
        with self._lock:
            raw = self._records.get(raw_ref)
            if not raw:
                raise AppError(ErrorCode.NOT_FOUND, "raw evidence not found")
            if raw.kind != "raw":
                raise AppError(
                    ErrorCode.PRECONDITION_FAILED, "derivation source must be raw evidence"
                )
        rec = EvidenceRecord(
            evidence_id=evidence_id,
            kind="derived",
            source_type="agent",
            source_ref=raw_ref,
            sha256=_content_hash(content),
            workspace_id=workspace_id,
            content=content,
            stored_content_sha256=_content_hash(content),
            raw_ref=raw_ref,
            derivation_rule=derivation_rule,
            model_version=model_version,
            source_trust=source_trust,
            integrity_check="NOT_RUN",
        )
        with self._lock:
            self._records[evidence_id] = rec
        return rec

    def verify_integrity(self, evidence_id: str, original_content: dict[str, Any]) -> bool:
        """**旧语义**：调用者重新提交"原始内容"做来源摘要比对。

        这不是存储完整性校验：它只能证明"调用者手上的内容与采集时的原始 payload 一致"，
        不能证明库里持久化的内容未被篡改。旧的 `integrity_check` 字段不再由它写入，
        以免把来源比对冒充成持久化完整性（RUN-007-FINALIZATION-2 / C3）。
        """
        with self._lock:
            rec = self._records.get(evidence_id)
            if not rec:
                return False
            ok = rec.sha256 == _content_hash(original_content)
            rec.integrity_detail = {
                **rec.integrity_detail,
                "source_payload_match": "PASS" if ok else "FAIL",
                "source_payload_checked_with": "caller_supplied_content",
            }
            return ok

    def verify_storage_integrity(
        self, evidence_id: str, *, expected_workspace: str | None = None
    ) -> dict[str, Any]:
        """服务端存储完整性：读持久化内容 → 重算摘要 → 与保存时摘要比较。

        返回 {status: PASS|FAIL|NOT_RUN, checks: {...}, reason}，并且**不接收**调用者内容。
        """
        with self._lock:
            rec = self._records.get(evidence_id)
            if not rec:
                return {"status": "FAIL", "reason": "EVIDENCE_NOT_FOUND", "checks": {}}
            checks: dict[str, Any] = {"record_found": True}
            reasons: list[str] = []
            if expected_workspace:
                checks["workspace_match"] = rec.workspace_id == expected_workspace
                if not checks["workspace_match"]:
                    reasons.append("WORKSPACE_MISMATCH")
            recomputed = _content_hash(rec.content)
            if not rec.stored_content_sha256:
                checks["stored_content_match"] = None
                reasons.append("STORED_DIGEST_NOT_RECORDED_AT_CAPTURE")
            else:
                checks["stored_content_match"] = recomputed == rec.stored_content_sha256
                if not checks["stored_content_match"]:
                    reasons.append("STORED_CONTENT_MISMATCH")
            if rec.kind == "derived":
                raw = self._records.get(rec.raw_ref or "")
                checks["derived_source_exists"] = raw is not None
                checks["derived_source_is_raw"] = bool(raw and raw.kind == "raw")
                checks["derivation_rule_recorded"] = bool(rec.derivation_rule)
                for key in (
                    "derived_source_exists",
                    "derived_source_is_raw",
                    "derivation_rule_recorded",
                ):
                    if checks[key] is False:
                        reasons.append(key.upper())
            if any(v is False for v in checks.values()):
                status = "FAIL"
            elif checks.get("stored_content_match") is None:
                status = "NOT_RUN"
            else:
                status = "PASS"
            rec.integrity_check = status
            rec.integrity_detail = {**rec.integrity_detail, "storage": checks, "reasons": reasons}
            return {
                "status": status,
                "reason": reasons[0] if reasons else "",
                "reasons": reasons,
                "checks": checks,
                "stored_content_sha256": rec.stored_content_sha256,
                "recomputed_sha256": recomputed,
            }

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        with self._lock:
            return self._records.get(evidence_id)

    def list_by_workspace(self, workspace_id: str) -> list[EvidenceRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.workspace_id == workspace_id]
