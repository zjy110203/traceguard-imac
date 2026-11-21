"""从 Pydantic 模型导出 C01～C12 JSON Schema（draft 2020-12）到 schemas/。

用法：PYTHONPATH=packages/contracts .venv/bin/python packages/contracts/export_schemas.py
生成物是派生产物，权威来源是 traceguard_contracts 模型。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "contracts"))

from pydantic import TypeAdapter  # noqa: E402
from traceguard_contracts import models as m  # noqa: E402

# 契约编号 -> 导出对象集合
CONTRACTS: dict[str, dict[str, type]] = {
    "C01_TestIntent": {"TestIntent": m.TestIntent},
    "C02_TestCase": {
        "LocatorSpec": m.LocatorSpec,
        "TestStep": m.TestStep,
        "AssertionSpec": m.AssertionSpec,
        "TestCaseVersion": m.TestCaseVersion,
    },
    "C03_Oracle": {"OracleSpec": m.OracleSpec},
    "C04_Execution": {
        "Task": m.Task,
        "Run": m.Run,
        "Step": m.Step,
        "AssertionResult": m.AssertionResult,
    },
    "C05_Event": {"EventEnvelope": m.EventEnvelope},
    "C06_Evidence": {"Evidence": m.Evidence},
    "C07_EvidenceGraph": {"GraphNode": m.GraphNode, "GraphEdge": m.GraphEdge},
    "C08_Diagnosis": {"Hypothesis": m.Hypothesis, "Diagnosis": m.Diagnosis},
    "C09_Probe": {"ProbeDefinition": m.ProbeDefinition, "ProbeExecution": m.ProbeExecution},
    "C10_Disposition": {"Disposition": m.Disposition},
    "C11_Repair": {"RepairProposal": m.RepairProposal},
    "C12_Benchmark": {"BenchmarkCase": m.BenchmarkCase, "EvaluationResult": m.EvaluationResult},
}

OUT = os.path.join(os.path.dirname(__file__), "schemas")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    manifest: dict[str, list[str]] = {}
    for contract, objs in CONTRACTS.items():
        files: list[str] = []
        for name, model in objs.items():
            schema = TypeAdapter(model).json_schema()
            schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
            schema["title"] = name
            fname = f"{contract}.{name}.schema.json"
            with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
                json.dump(schema, f, ensure_ascii=False, indent=2)
                f.write("\n")
            files.append(fname)
        manifest[contract] = files
        print(f"[export] {contract}: {len(files)} schema(s)")
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(
        f"[export] total contracts={len(CONTRACTS)} schemas={sum(len(v) for v in manifest.values())}"
    )


if __name__ == "__main__":
    main()
