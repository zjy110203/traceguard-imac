"""Dataset Manager + Ground Truth Manager（阶段11，C12）。

隔离规则：
- Trusted Test Set 与 Agent Generated Test Set 分开；
- 按 fault_mechanism / scenario_variant / application_version / seed / batch 隔离；
- Ground Truth 独立受控域，不向 Agent 开放（需评测令牌）；
- 数据集版本不可静默变更。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BenchmarkCase:
    case_id: str
    scenario_id: str
    fault_mechanism_id: str
    application_version: str
    data_seed: str
    split: str  # trusted / agent_generated
    scenario_variant: str = "base"
    batch: str = "batch-1"


@dataclass
class GroundTruth:
    case_id: str
    primary_failure_class: str  # 权威主因分类
    fault_mechanism_id: str
    expected_business_state: dict[str, Any] = field(default_factory=dict)


class GroundTruthManager:
    """独立受控域：Agent 无令牌不可读。"""

    def __init__(self) -> None:
        self._token = secrets.token_urlsafe(24)
        self._records: dict[str, GroundTruth] = {}
        self._lock = threading.RLock()

    @property
    def evaluation_token(self) -> str:
        return self._token

    def check_token(self, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(str(token), self._token)

    def put(self, gt: GroundTruth) -> None:
        with self._lock:
            self._records[gt.case_id] = gt

    def get(self, case_id: str, token: str | None) -> GroundTruth | None:
        if not self.check_token(token):
            return None  # Agent 无令牌 → 不可见
        with self._lock:
            return self._records.get(case_id)

    def visible_refs(self) -> list[str]:
        """只暴露 case_id 引用，不含答案。"""
        with self._lock:
            return sorted(self._records.keys())


class DatasetManager:
    def __init__(self) -> None:
        self._cases: dict[str, BenchmarkCase] = {}
        self._version: str = ""
        self._frozen = False
        self._lock = threading.RLock()

    def add_case(self, case: BenchmarkCase) -> None:
        with self._lock:
            if self._frozen:
                raise ValueError("DATASET_FROZEN: 数据集版本不可静默变更")
            self._cases[case.case_id] = case

    def freeze(self, version: str) -> str:
        """冻结数据集版本，计算内容哈希。"""
        with self._lock:
            self._version = version
            payload = json.dumps(
                [c.__dict__ for c in sorted(self._cases.values(), key=lambda x: x.case_id)],
                sort_keys=True,
                ensure_ascii=False,
            )
            digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
            self._frozen = True
            return f"{version}+{digest}"

    def cases(
        self, *, split: str | None = None, mechanism: str | None = None
    ) -> list[BenchmarkCase]:
        with self._lock:
            out = list(self._cases.values())
            if split:
                out = [c for c in out if c.split == split]
            if mechanism:
                out = [c for c in out if c.fault_mechanism_id == mechanism]
            return out

    def isolation_check(self) -> dict[str, Any]:
        """验证 Trusted 与 Agent Generated 隔离。"""
        trusted = {c.case_id for c in self.cases(split="trusted")}
        agent_gen = {c.case_id for c in self.cases(split="agent_generated")}
        return {
            "trusted_count": len(trusted),
            "agent_generated_count": len(agent_gen),
            "overlap": sorted(trusted & agent_gen),
            "isolated": len(trusted & agent_gen) == 0,
        }
