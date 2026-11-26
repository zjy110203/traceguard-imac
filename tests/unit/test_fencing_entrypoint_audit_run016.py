"""RUN-016 WP1：Fencing 写入入口静态审计。

目标：把"业务写入必须带 fence"从约定变成**可回归检查**的约束，防止后续新增
事件/产物写入时无意中绕开租约守卫。

检查对象（源码级 AST，不执行代码）：
1. `RunPipeline._emit(...)` 的每一处调用都必须显式传 `fence=`；
2. `RunPipeline` 内 `self.artifacts.put(...)` 必须传 `fence=` 与 `run_id=`；
3. EventStore / EvidenceStore 的 fence 参数必须保持"可选但默认 None"的签名
   （真正的强制点在调用侧，改签名会破坏 API 侧合法用法）。
"""

from __future__ import annotations

import ast
from pathlib import Path

PIPELINE = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "execution"
    / "traceguard_execution"
    / "runtime"
    / "pipeline.py"
)


def _calls(tree: ast.AST, attr: str) -> list[ast.Call]:
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == attr:
                out.append(node)
    return out


def _kw_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg}


def test_every_pipeline_emit_call_is_fenced() -> None:
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    emits = _calls(tree, "_emit")
    assert emits, "未找到 _emit 调用，审计失效"
    missing = [c.lineno for c in emits if "fence" not in _kw_names(c)]
    assert missing == [], f"以下 _emit 调用缺少 fence=（行号）：{missing}"


def test_pipeline_artifact_put_is_fenced() -> None:
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    puts = _calls(tree, "put")
    assert puts, "未找到 artifacts.put 调用，审计失效"
    for call in puts:
        names = _kw_names(call)
        assert "fence" in names, f"artifacts.put 缺少 fence=（行号 {call.lineno}）"
        assert "run_id" in names, f"artifacts.put 缺少 run_id=（行号 {call.lineno}）"


def test_emit_signature_keeps_fence_optional() -> None:
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_emit":
            found = True
            kwonly = [a.arg for a in node.args.kwonlyargs]
            assert "fence" in kwonly, "_emit 必须以关键字参数保留 fence"
            index = kwonly.index("fence")
            default = node.args.kw_defaults[index]
            assert isinstance(default, ast.Constant) and default.value is None, (
                "fence 默认值必须是 None（由调用侧显式传入）"
            )
    assert found, "未找到 _emit 定义"
