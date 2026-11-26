"""RUN-015 WP6：Benchmark 真实故障状态字段可穿透记录与历史归档冻结。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bench_v5_run011_r1 import enrich_record  # noqa: E402

#: RUN-014 原始归档 SHA-256：本轮脚本增强不得回改历史数据
HISTORICAL_HASHES = {
    "docs/assets/run014/wp5/v5-2rounds.json": (
        "f3c3c6b86235019d5f12bf2dcc78e390381f10f184fcf0582e1f95c981bb976e"
    ),
    "docs/assets/run014/wp5/ai-quality-n32.json": (
        "299172cfef833a3820b5e72b33788902ba3002c8a041de859a1c76e83f00932a"
    ),
}


def test_fault_state_fields_survive_enrichment() -> None:
    sample = {
        "scenario": "AUTH_RESET_STICKY",
        "round": 1,
        "run_id": "run_15_fault",
        "verdict": "FAILED",
        "gt_mechanism": "sticky_fault_not_cleared",
        "fault": "TF-API-001",
        "diagnosis_id": "diag_15_fault",
        "fault_activation_status": "CONFIRMED",
        "fault_effect_status": "CONFIRMED",
        "fault_activation_timestamp": 1795670000.1,
        "fault_effect_timestamp": 1795670002.9,
        "fault_state_snapshot": {"active": ["TF-API-001"], "sticky": True},
        "evidence_refs": ["evd_1", "evd_2"],
    }
    enriched = enrich_record(sample)
    for key in (
        "run_id",
        "diagnosis_id",
        "fault_activation_status",
        "fault_effect_status",
        "fault_activation_timestamp",
        "fault_effect_timestamp",
        "fault_state_snapshot",
        "evidence_refs",
    ):
        assert enriched[key] == sample[key], key
    assert enriched["classification_confidence"] == "STRUCTURED_EVIDENCE"


def test_run014_archives_are_unchanged() -> None:
    for rel, expected in HISTORICAL_HASHES.items():
        digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert digest == expected, f"{rel} 历史归档被修改"
