"""契约公共基础：枚举与基类。

所有 ID 采用 <prefix>_<ts><rand>；时间统一 UTC ISO-8601（Z 结尾）。
枚举口径严格对齐资料/项目资料/06 状态机规范与已冻结语义。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class ContractBase(BaseModel):
    """契约基类：禁止额外字段进入权威对象，避免字段语义漂移。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AssertionStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUATED = "NOT_EVALUATED"


class TestVerdict(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUATED = "NOT_EVALUATED"


class ExecutionStatus(str, Enum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class DiagnosisStatus(str, Enum):
    QUEUED = "QUEUED"
    COLLECTING = "COLLECTING"
    TRIAGING = "TRIAGING"
    HYPOTHESIZING = "HYPOTHESIZING"
    PROBING = "PROBING"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DiagnosisOutcome(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    INCONCLUSIVE = "INCONCLUSIVE"


class DispositionKind(str, Enum):
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    TEST_ASSET = "TEST_ASSET"
    TEST_EXECUTION = "TEST_EXECUTION"
    ENVIRONMENT = "ENVIRONMENT"
    AGENT = "AGENT"
    PLATFORM = "PLATFORM"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class RepairStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PROMOTING = "PROMOTING"
    PROMOTED = "PROMOTED"
    PROMOTION_FAILED = "PROMOTION_FAILED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"
