"""TestCase / TestCaseVersion 加载与校验（阶段2）。

原则：正式版本不可原地覆盖；加载时校验不可变标记与必需断言集（F05）。
测试数据以 JSON 文件为初始载体，后续阶段接入 TestCase Service（DB）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from traceguard_contracts.models import TestCaseVersion


def load_testcase_version(path: str | Path) -> TestCaseVersion:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    tc = TestCaseVersion(**data)
    if not tc.immutable:
        raise ValueError("TestCaseVersion 必须是不可变版本")
    required = [a for a in tc.assertions if a.required]
    if not required:
        raise ValueError("正式 TestCase 至少需要一条必需 Assertion（F05）")
    return tc


def load_all(directory: str | Path) -> list[TestCaseVersion]:
    d = Path(directory)
    if not d.exists():
        return []
    return [load_testcase_version(f) for f in sorted(d.glob("*.json"))]


def to_dict(tc: TestCaseVersion) -> dict[str, Any]:
    return tc.model_dump(mode="json")
