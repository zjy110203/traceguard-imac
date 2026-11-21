"""TraceGuard 确定性执行内核（阶段2）。

边界：加载正式 TestCaseVersion + OracleSpec → 确定性执行 → Assertion 评估 → Verdict 聚合。
不由 Agent 决定测试结果；Oracle 不可用时不得 PASSED。
"""

__version__ = "0.2.0"
