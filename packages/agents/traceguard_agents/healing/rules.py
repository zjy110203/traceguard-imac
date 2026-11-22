"""Repair 安全规则（纯函数，可被状态机与持久化服务共用）。

规则来源：阶段10 设计（不得削弱正式断言、不得修改 Oracle、Diff 白名单）。
抽成纯函数是为了让"内存服务"与"持久化服务"共用同一套判定，避免规则漂移。
"""

from __future__ import annotations

from typing import Any

from traceguard_contracts.models import TestCaseVersion

# 允许修改的字段白名单（非业务语义）
ALLOWED_DIFF_FIELDS = {
    "steps[].target",
    "steps[].value",
    "run_config.wait_timeout",
    "test_data_refs",
    "steps[].action_config",
}

FORBIDDEN_DIFF_KEYWORDS = {
    "assertion",
    "oracle",
    "expected_behavior",
    "required",
    "product_code",
    "business",
}


def assertion_preserved(base: TestCaseVersion, candidate: TestCaseVersion) -> bool:
    """必需 Assertion 不得删除，也不得改变 kind / required / expected（严格度不可降）。"""
    base_req = [a for a in base.assertions if a.required]
    cand_req = [a for a in candidate.assertions if a.required]
    if len(cand_req) < len(base_req):
        return False
    base_map = {a.assertion_id: a for a in base_req}
    for a in cand_req:
        b = base_map.get(a.assertion_id)
        if not b:
            continue  # 新增必需断言允许（更严格）
        if a.kind != b.kind or not a.required:
            return False
        if a.expected != b.expected:
            return False
    return True


def oracle_integrity_ok(base: TestCaseVersion, candidate: TestCaseVersion) -> bool:
    """Oracle 引用不得变更（断言 → Oracle 的绑定关系保持）。"""
    base_oracles = {a.assertion_id: a.oracle_id for a in base.assertions}
    cand_oracles = {a.assertion_id: a.oracle_id for a in candidate.assertions}
    return base_oracles == cand_oracles


def diff_allowed(diff: list[dict[str, Any]]) -> bool:
    """Diff 只允许非业务语义字段；禁止触及 assertion / oracle / business。"""
    for d in diff:
        path = str(d.get("path", ""))
        low = path.lower()
        if any(k in low for k in FORBIDDEN_DIFF_KEYWORDS):
            return False
        if path in ALLOWED_DIFF_FIELDS:
            continue
        if any(path.startswith(a.replace("[]", "")) for a in ALLOWED_DIFF_FIELDS):
            continue
        return False
    return True
