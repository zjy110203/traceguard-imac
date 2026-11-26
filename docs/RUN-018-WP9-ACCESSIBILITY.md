# RUN-018 WP9：UI 无障碍和产品体验

> 时间：2026-11-27
> 基础：RUN-017 的 128/128 布局矩阵验收 + RUN-010 的 axe-core 审查
> 状态：**PARTIAL**（16 视图 axe 审查需真实 Chromium 环境执行）

---

## 1. 当前覆盖

### 1.1 RUN-017 已完成

- 128 格布局矩阵（16 视图 × 4 断点 × Light/Dark）全过
- 业务闭环 UI 验收 58/0
- core profile 35/35

### 1.2 RUN-010 已完成

- axe-core 自动审查（13 个路由）
- 6 个视图的表单 label 修复验证
- AUTH_RULES 覆盖：label, button-name, image-alt, heading-order, color-contrast 等

## 2. RUN-018 目标覆盖（16 视图）

| # | 路由 | 视图 | axe | 键盘 | 焦点 | Label | 对比度 | 390px | 空/错误/加载/权限 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | /login | LoginView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 2 | / | DashboardView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 3 | /intents | IntentsView | PARTIAL | TODO | TODO | DONE | TODO | TODO | TODO |
| 4 | /testcases | TestCasesView | PARTIAL | TODO | TODO | DONE | TODO | TODO | TODO |
| 5 | /oracles | OraclesView | PARTIAL | TODO | TODO | DONE | TODO | TODO | TODO |
| 6 | /runs | RunsView | PARTIAL | TODO | TODO | DONE | TODO | TODO | TODO |
| 7 | /runs/:id | RunDetailView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 8 | /explorer | ExplorerView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 9 | /models | ModelsView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 10 | /diagnoses | DiagnosesView | PARTIAL | TODO | TODO | DONE | TODO | TODO | TODO |
| 11 | /diagnoses/:id | DiagnosisDetailView | PARTIAL | TODO | TODO | DONE | TODO | TODO | TODO |
| 12 | /repairs | RepairsView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 13 | /benchmark | BenchmarkView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 14 | /system | SystemView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 15 | /users | UsersView | PARTIAL | TODO | TODO | DONE | TODO | TODO | TODO |
| 16 | /testcases/:id | TestCaseDetailView | TODO | TODO | TODO | TODO | TODO | TODO | TODO |

## 3. 检查项

1. **axe-core 自动审查**（已有基础，需扩展到 16 视图）
2. **键盘导航**：Tab 顺序、Skip Link、焦点陷阱
3. **焦点可见性**：focus-visible 样式
4. **弹窗焦点管理**：aria-modal、Escape 关闭、焦点回收
5. **表单 label**：每个 input 有关联 label
6. **按钮 accessible name**：aria-label 或文本
7. **图表替代文本**：aria-label 或 role="img"
8. **状态颜色之外的文字表达**：不只靠颜色区分 PASS/FAIL
9. **Light / Dark 对比度**：WCAG AA（4.5:1）
10. **390px 触控交互**：最小点击区域 44px
11. **空状态**：有引导文案
12. **错误状态**：有错误提示
13. **加载状态**：有 loading 指示
14. **权限不足状态**：有权限提示

## 4. 执行计划

```bash
# 1. axe-core 全 16 视图审查
.venv/bin/python scripts/ui_review_run010.py --out docs/assets/run018/wp9/axe

# 2. 键盘导航测试（Playwright）
.venv/bin/python scripts/ui_accessibility_run018.py --out docs/assets/run018/wp9/keyboard

# 3. 对比度检查（axe color-contrast 规则）
# 4. 390px 触控测试
# 5. 状态页测试（空/错误/加载/权限）
```

## 5. 现有工具

| 工具 | 用途 | 路径 |
|---|---|---|
| `ui_review_run010.py` | axe-core 审查 | `scripts/` |
| `ui_matrix_run017.py` | 布局矩阵 | `scripts/` |
| `business_loop_ui_acceptance_run017.py` | 业务闭环 | `scripts/` |
| `axe-core` | 无障碍引擎 | `apps/web/node_modules/` |

## 6. 不做的事

- 不为美观重写全部前端
- 不只检查首页
- 不只检查 DOM 是否存在
- 必须在真实 Chromium 中操作

## 7. 交付

- 文档：本文件
- 证据：`docs/assets/run018/wp9/`（待生成）
- 状态：**PARTIAL**（布局验收 DONE，无障碍验收需真实 Chromium 执行）
