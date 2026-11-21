"""TraceGuard C01～C12 契约模型（初始版本 v0.1.0，渐进冻结）。

权威依据：资料/项目资料/05 + 路线图 v1.1 第4章 + 修订摘要 F01～F13/G01～G08。
本包是契约的单一事实来源（Single Source of Truth），JSON Schema 由其导出。
"""

from .common import (  # noqa: F401
    AssertionStatus,
    ContractBase,
    DiagnosisOutcome,
    DiagnosisStatus,
    DispositionKind,
    ExecutionStatus,
    RepairStatus,
    TestVerdict,
)
from .models import *  # noqa: F401,F403
