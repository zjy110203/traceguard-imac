# Playwright 真实浏览器执行说明

## 1. 环境

| 项 | 实测值 |
|---|---|
| playwright | 1.65.2（PyPI，清华镜像） |
| 浏览器 | Chromium **159.0.8154.6**（playwright 内置，headless shell） |
| 备选渠道 | 系统 Edge 158（`channel="msedge"`）已验证可启动 |
| 启动耗时 | ~300–400 ms（进程内复用） |
| 单场景执行 | 4–6 s（8 步，含截图/DOM/ARIA 采集） |

安装：

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
# 镜像加速：PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
```

## 2. 进程与线程模型

- Playwright **同步 API 是线程亲和的**：`sync_playwright()` 必须在同一线程 start / 使用 / stop。
- `BrowserManager` 采用 **thread-local 单例**：一个线程复用一套 Playwright + Browser，
  Run 之间只新建 `BrowserContext`。
- 教训（已修复的真实 Bug）：每次 Run 新建 Manager 并 `stop()`，第二次执行会抛
  `Sync API inside the asyncio loop`。现在由 thread-local 复用解决；
  模块/进程退出时 `close_current_thread_browser()` 释放。
- API 端点内同步执行必须走 `run_in_threadpool`，否则同样触发上述错误。

## 3. 隔离与生命周期

| 层次 | 策略 |
|---|---|
| 进程 | 一个 Worker 进程一个 Playwright 实例 |
| 线程 | thread-local Browser |
| Run | 独立 `BrowserContext`（cookie/storage 隔离） |
| Page | 每 Run 一个 page；弹窗页面也挂 console/pageerror 监听 |
| 清理 | `teardown` 先停 trace、再关 context；异常路径同样执行 |

浏览器启动失败 → `BrowserUnavailableError`，Pipeline 将 Run 标记 FAILED，
**绝不**静默降级成 HTTP 模式。HTTP 降级只能由调用方显式指定 `mode=http`，并在报告中标记
`observer_mode=http-fallback`。

## 4. 采集内容

| 类型 | 来源 | 存储 |
|---|---|---|
| 截图 | 每步 `page.screenshot(full_page=True)` | PNG artifact（内容寻址）+ Evidence(raw, browser) |
| DOM | `page.content()`（截断 400k） | HTML artifact + Evidence |
| ARIA | `body.aria_snapshot()` | text artifact + Evidence |
| 网络 | request / response / requestfailed | 步骤 observation + Evidence(network)（仅错误信号时） |
| 控制台 | console / pageerror / weberror / dialog | 步骤 observation（截断） |
| Trace | `context.tracing`（screenshots+snapshots） | zip artifact（Run 结束上传） |

步骤观测会裁剪（网络/控制台只保留末尾若干条，DOM 只在 artifact 中保留全文），避免证据表膨胀。

## 5. 动作与定位

| 动作 | 说明 | 关键行为 |
|---|---|---|
| `navigate` | goto + `domcontentloaded` + 尝试 networkidle | 记录 status/url/title |
| `click` | 等待 visible → click → 等待网络收敛 | 记录跳转后 url |
| `fill` | `locator.fill` | 记录填充长度（不记录明文值到证据摘要） |
| `select` / `press` | 下拉与按键 | — |
| `wait` | 毫秒或选择器 | 记录等待目标 |
| `assert` | 读取元素/表格/正文快照 | 产出 present/count/text/visible |
| `api` | 执行前预置（故障注入/数据准备） | 不走浏览器 |

定位策略优先级（Compiler 生成时）：`role > test_id > label > placeholder > css > xpath`。
`role` 支持 `button:登录` 形式（角色 + 可访问名）。

## 6. 已修复的时序问题（真实 Bug）

**现象**：接口 500 的测试在诊断中识别成"持久化失败"，因为步骤观测里 `network` 只有
`status=None` 的在途请求，`http_errors` 为空。

**根因**：Playwright 事件在 API 调用期间派发；点击后只等 `load_state(networkidle)`，
fetch 不在页面加载状态里，响应事件尚未派发就抓取了快照。

**修复**：`BrowserSession.wait_network_settle()` 轮询驱动事件循环，直到没有在途请求或超时；
navigate/click/fill/wait 后调用。修复后同一场景 `http_errors` 正确记录
`POST /api/projects → 500`，诊断主因变为 `API`（Benchmark B 的首选根因准确率从 0.667 → 1.000）。

## 7. 验证记录

| 验证 | 结果 |
|---|---|
| 内置 Chromium 启动 + 自检 | PASS（`/system/browser?probe=true`） |
| 系统 Edge 158 启动 | PASS |
| 正常创建项目（8 步） | PASSED，24–25 份证据，5–6 s |
| False Pass（TF-DB-001） | FAILED（业务断言 got=0） |
| UI 提示缺失（TF-UI-001） | FAILED（UI 断言），业务断言 PASS |
| 接口 500（TF-API-001） | FAILED（业务断言），网络证据 500 |
| 连续多次执行（同进程） | PASS（thread-local 复用） |
| 独立 Worker 执行 | PASS（`run_179532554450234b50cc4c5e5`） |

## 8. 未完成

- 视频录制（`record_video`）参数存在但未启用。
- 跨浏览器（Firefox/WebKit）未验证。
- 外部站点未验证（P-EXT 未授权）。
