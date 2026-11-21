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
    sha256: str
    workspace_id: str
    content: dict[str, Any] = field(default_factory=dict)
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
        """采集原始证据。脱敏后存 content，但 sha256 基于脱敏前原始内容记录。"""
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
        """校验内容完整性：基于原始内容重算 SHA-256。"""
        with self._lock:
            rec = self._records.get(evidence_id)
            if not rec:
                return False
            ok = rec.sha256 == _content_hash(original_content)
            rec.integrity_check = "PASS" if ok else "FAIL"
            return ok

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        with self._lock:
            return self._records.get(evidence_id)

    def list_by_workspace(self, workspace_id: str) -> list[EvidenceRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.workspace_id == workspace_id]
