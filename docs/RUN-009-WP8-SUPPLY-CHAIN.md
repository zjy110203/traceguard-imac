# RUN-009 WP8：依赖安全与供应链治理审计

> 审计时间：2026-11-24 CST。审计人：general-purpose-3。
> **只读审计**：未修改任何已有依赖文件、requirements、源码、锁文件、`.env` 或其他 agent 的文件；未执行依赖升级（**BLOCKED**，见 F-14）；未执行 `git commit`；未使用 `--force`。
> 工作区中其他文件（`apps/api/**`、`packages/**`、`.workbuddy-run/**` 等）的改动由并行 agent 产生，与本 WP8 无关。
> 本轮仅新建本报告与 `docs/assets/run009/wp8/**` 证据。`pip-audit` 安装在 `/tmp/traceguard-wp8-pip-audit`（项目 `.venv` 未被改动）。

## 0. 状态总览（IMPLEMENTED / VERIFIED / PARTIAL / BLOCKED / NEEDS_DECISION）

| 编号 | 事项 | 状态 | 结论 |
|---|---|---|---|
| F-01 | npm audit 执行与原始证据留存 | **VERIFIED** | 3 moderate / 0 high / 0 critical，均为 dev-only |
| F-02 | 3 个 moderate 漏洞的实际包与调用路径 | **VERIFIED** | `vue-tsc@2.2.12` → `@vue/language-core@2.2.12` → `minimatch@9.0.9`，advisory `GHSA-g3hv-75wg-m249` |
| F-03 | 是否进入生产制品 | **VERIFIED** | 不进入。`npm audit --omit=dev` = 0 漏洞；dist 无相关字符串；三者 lock 均 `"dev": true` |
| F-04 | 安全升级方案与兼容性 | **NEEDS_DECISION** | 唯一 npm 推荐修复为 `vue-tsc@3.3.14`（semver-major 2→3），必须 `--force`；本轮禁止执行 |
| F-05 | Python 依赖漏洞扫描 | **VERIFIED** | pip-audit 2.10.1 对 lock(32 包) 与已安装(35 包) 均报 `No known vulnerabilities found` |
| F-06 | Python 锁文件完整性 | **PARTIAL** | 32 条锁定版本与已安装 100% 一致；但缺 5 个声明依赖、8 个已安装包，且 CI 完全不使用该 lock |
| F-07 | `pytest-cov` 约束冲突 | **NEEDS_DECISION** | `requirements-dev.txt` 为 `>=5,<7`，lock/实际为 `7.1.0`，CI 安装将被降级 |
| F-08 | `httpx2` 供应链身份 | **VERIFIED** | PyPI 元数据确认为 pydantic 官方/Tom Christie 维护，非 typosquat；但未安装、未锁定 |
| F-09 | npm 许可证 | **VERIFIED** | 全部宽松许可（MIT/ISC/Apache-2.0/BSD），无 GPL/AGPL/SSPL/BUSL，无未声明 |
| F-10 | Python 许可证 | **NEEDS_DECISION** | 无 GPL/AGPL；`psycopg*`（LGPL-3.0-only）与 `certifi`（MPL-2.0）在再分发场景需法务确认 |
| F-11 | 项目自身 LICENSE | **NEEDS_DECISION** | 仓库根目录无 LICENSE 文件，下游无法判定再分发条款 |
| F-12 | `.env` / 密钥入库防护 | **VERIFIED** | `.env` 被忽略且未跟踪；已跟踪文件与 dist 的密钥模式扫描均 0 命中；无私钥/凭据文件入库 |
| F-13 | SBOM 可行性 | **IMPLEMENTED / PARTIAL** | npm SPDX + CycloneDX 已生成；Python CycloneDX 已生成，但受 lock 不完整限制，覆盖 PARTIAL |
| F-14 | 本轮执行任何依赖升级 | **BLOCKED** | 任务禁令：不得 `--force`、不得修改依赖文件/锁文件；仅输出方案与验证命令，未实际执行 |

---

## 1. npm 依赖审计（F-01 ~ F-04）

### 1.1 执行与原始证据

```
cd apps/web && npm audit --json
```
退出码 1（表示“发现漏洞”，非工具错误）。网络可用，完整 JSON 已落盘：
`docs/assets/run009/wp8/npm-audit-raw.json`（2,078 B）。

报告原文（dev + prod 合计）：

- `moderate: 3`, `high: 0`, `critical: 0`, `info: 0`, `low: 0`
- 依赖计数：`prod: 30`, `dev: 76`, `optional: 53`, `total: 105`

### 1.2 三个 moderate 漏洞的实际包与调用路径

| 层 | 包 | 安装版本 | 声明范围 | npm 报告中的 range | 角色 |
|---|---|---|---|---|---|
| 直接依赖 | `vue-tsc` | 2.2.12 | `^2.1.0`（devDependency） | `1.7.0-alpha.0 - 3.0.1` | 构建期类型检查器 |
| 传递依赖 | `@vue/language-core` | 2.2.12 | 由 vue-tsc 固定 `2.2.12` | `<=3.0.1` | 语言核心 |
| 根因包 | `minimatch` | 9.0.9 | 由 language-core 固定 `^9.0.3` | `7.0.0 - 11.0.0` | glob 匹配 |

**调用路径**：`apps/web/package.json:22` 的 `devDependencies.vue-tsc` →
`vue-tsc@2.2.12` 依赖 `@vue/language-core@2.2.12` →
`@vue/language-core@2.2.12` 依赖 `minimatch@9.0.9`。

**根因公告**：`GHSA-g3hv-75wg-m249` — "minimatch has potential denial of service via globparts amplification"，CWE-405，moderate，受影响范围 `>=7.0.0 <11.0.1`。
三个 npm 条目中只有 `minimatch` 是直接携带公告的“via”；另外两个是“Depends on vulnerable versions of …”的传递传播项，因此 **3 个 moderate 实为同一条漏洞链**，不是三个独立缺陷。

证据：`docs/assets/run009/wp8/npm-audit-raw.json`、`docs/assets/run009/wp8/npm-dependency-tree-and-dist.txt`。

### 1.3 是否进入生产制品 —— 否（VERIFIED）

三重独立验证：

1. **生产依赖树**：`npm ls --omit=dev --depth=1` 仅含 `@lucide/vue`、`pinia`、`vue-router`、`vue` 及其传递依赖，**无 vue-tsc / language-core / minimatch**。
2. **生产审计**：`npm audit --omit=dev --json` 退出码 0，`vulnerabilities: {}`，`moderate: 0`。证据：`npm-audit-prod-only-raw.json`。
3. **制品扫描**：`apps/web/dist` 全部 26 个文件中对 `minimatch|vue-tsc|language-core` 字符串扫描 0 命中；`package-lock.json` 中三者均标记 `"dev": true`。

补充：`package.json:8` 的构建脚本为 `vue-tsc --noEmit && vite build`。`vue-tsc` 只做类型检查（`--noEmit`），不产出运行时代码；`vite build` 的输出不包含该链。因此该漏洞的可用触发面仅为**开发/CI 主机上的类型检查过程**（处理恶意构造的 glob 模式时可能 CPU/内存放大），不面向最终用户或线上服务。

### 1.4 安全升级方案与兼容性 —— NEEDS_DECISION

npm 文本报告明确写道：`fix available via npm audit fix --force`，将安装 `vue-tsc@3.3.14`，且"which is a breaking change"。

| 方案 | 版本 | semver | 评估 | 状态 |
|---|---|---|---|---|
| A. 升级 `vue-tsc` | `2.2.12 → 3.3.14` | **major** | `@vue/language-core@3.3.14` 已用 `picomatch@^4.0.4` 取代 minimatch，从依赖图层面**根除**该漏洞；peer `typescript >=5.0.0` 与当前 `typescript@5.6.3` 兼容 | **NEEDS_DECISION** |
| B. `overrides.minimatch` | `9.0.9 → ≥11.0.1` | major（传递） | `@vue/language-core@2.2.12` 硬约束 `^9.0.3`；`minimatch@11` 为 ESM-only 且 API 变更，可能破坏 language-core；npm 自身未选择此路径 | **NEEDS_DECISION（不推荐）** |
| C. 接受风险并豁免 | 保持现状 | — | 仅影响 dev-only、0 生产暴露、0 high/critical；但需书面风险接受 | **NEEDS_DECISION** |

**为何不能在本轮直接修**：升级 2.x→3.x 是 semver-major，npm 只能经由 `--force` 完成，而任务明确禁止 `--force`；且任务禁止修改 `package.json` / `package-lock.json`。因此本轮**不执行任何升级**，仅给出受控验证命令（见第 5 节）。

**兼容性权衡（未实测，仅元数据比对）**：
- `vue-tsc@3.3.14` peer `typescript >=5.0.0`，与 `vue-tsc@2.2.12` 相同；本机 TS 5.6.3 满足。
- `@vue/language-core@3.3.14` 依赖 `@volar/typescript@2.4.30`、`alien-signals@^3.2.1`，相对现有 `2.4.15` / `1.0.13` 有 major 变化；Node v22.22.2 满足 `minimatch@11` 的 `18||20||>=22`（若走方案 B）。
- 实际 `npm run typecheck` / `npm run build` 是否在 3.x 下通过，**本轮未验证**，必须在独立分支执行验证后方可决定。

证据：`docs/assets/run009/wp8/npm-audit-text.txt`、`docs/assets/run009/wp8/npm-view-metadata.txt`、`docs/assets/run009/wp8/npm-dependency-tree-and-dist.txt`。

---

## 2. Python 依赖与锁文件审计（F-05 ~ F-08）

### 2.1 pip-audit 可用性与执行

- 项目 `.venv` 未安装 `pip-audit`（`No module named pip_audit`）。
- `pip install --dry-run pip-audit` 确认 PyPI 网络可用；在隔离目录 `/tmp/traceguard-wp8-pip-audit` 安装 `pip-audit 2.10.1`，**未写入项目 `.venv`**。
- `pip-audit -r requirements.lock.txt` 默认模式会创建临时 venv，在本机因 `ensurepip` `SIGABRT` 失败（工具/环境问题，非漏洞）。改用 `--disable-pip --no-deps` 成功。

| 目标 | 命令 | 结果 |
|---|---|---|
| 已安装环境 | `pip_audit --local --format json` | exit 0，**No known vulnerabilities found**（35 个包，`fixes: []`） |
| lock 文件 | `pip_audit -r requirements.lock.txt --disable-pip --no-deps --format json` | exit 0，**No known vulnerabilities found**（32 个包，`fixes: []`） |

证据：`python-pip-audit-availability.txt`、`pip-audit-local-raw.json`、`pip-audit-local-stderr.txt`、`pip-audit-lock-raw.json`、`pip-audit-lock-stderr.txt`。

结论：**当前 Python 依赖集（含 dev）在 OSV 知识库下无已知漏洞**。注意 pip-audit 依赖联网 OSV 查询，离线环境不可用，届时应如实记为工具不可用而非“无漏洞”。

### 2.2 锁文件完整性 —— PARTIAL

声明文件：

- `requirements.txt`：6 项运行时约束
- `requirements-dev.txt`：`-r requirements.txt` + 7 项 dev 约束
- `requirements-postgres.txt`：`-r requirements.txt` + `psycopg[binary]>=3.1,<4`
- `requirements.lock.txt`：32 个 `==` 固定版本

**一致的部分（VERIFIED）**：lock 中 32 个版本与 `.venv` 已安装版本 **0 处不一致**（逐条比对）。

**缺口（PARTIAL）**：

| 缺口 | 明细 | 影响 |
|---|---|---|
| 声明但未锁定 | `httpx`、`httpx2`、`mypy`、`playwright`、`psycopg`（5 个） | `requirements.lock.txt` 不能重建完整环境 |
| 已安装但未锁定 | `certifi`、`greenlet`、`httpcore`、`httpx`、`pip`、`playwright`、`psycopg`、`psycopg-binary`（8 个） | 传递依赖与驱动二进制无锁定记录 |
| CI 不使用 lock | `pr-gate.yml:34`、`nightly-gate.yml:31/97/98/233`、`release-gate.yml:45` 均 `pip install -r requirements-dev.txt` / `requirements.txt` / `requirements-postgres.txt`；无一处引用 `requirements.lock.txt` | **Python 依赖在 CI 中实际是未固定的范围解析**，与 npm 强制 `npm ci` 形成对比，存在供应链漂移风险 |
| 元数据双源 | `pyproject.toml:9` 声明 `dependencies = []`，真实运行时依赖只在 `requirements.txt` | 打包/工具链可能读到空依赖集 |

证据：`python-lock-completeness.txt`、`python-installed-versions.txt`、`gitignore-and-artifacts.txt`。

### 2.3 `pytest-cov` 约束冲突 —— NEEDS_DECISION

`requirements-dev.txt:3` 为 `pytest-cov>=5,<7`，但 `requirements.lock.txt:19` 与实际安装均为 `pytest-cov==7.1.0`（违反 `<7`）。
`docs/技术兼容矩阵.md:31` 也把 `>=5,<7` 与 `7.1.0` 同时标为 ✅，属记录错误。

后果：CI 执行 `pip install -r requirements-dev.txt` 时会把 `pytest-cov` **降级到 6.x**，与本地 lock 环境不一致，可能导致覆盖率行为差异。
建议（需授权改文件）：将约束更新为 `pytest-cov>=7,<8` 并再生 lock；本轮不改。

### 2.4 `httpx2` 身份核验 —— VERIFIED（非 typosquat）

`requirements-dev.txt:7` 的 `httpx2>=2,<3` 未安装、未锁定，名称与 `httpx` 高度相似，故做供应链身份核验：

- PyPI: `httpx2 2.16.2`，summary "The next generation HTTP client."，author_email `Tom Christie <tom@tomchristie.com>`，project_urls 指向 `https://github.com/pydantic/httpx2`。
- 对照 `httpx 0.28.1` 同作者，但 source 为 `github.com/encode/httpx`。
- `docs/技术兼容矩阵.md:29` 记载 starlette 的 `testclient` 需 `httpx2` 而非 `httpx`；`tests/teamflow/test_teamflow.py:3` 说明当前用直接路由调用绕开未安装的 httpx2。

结论：名称可信、归属 pydantic 官方，不是仿冒包；但**环境漂移真实存在**（声明了却未安装/未锁定），应补齐 lock 或在文档中明确其用途与安装状态。

证据：`python-httpx2-investigation.txt`。

---

## 3. 许可证合规（F-09 ~ F-11）

### 3.1 npm —— VERIFIED

按 `package-lock.json` 的 `license` 字段聚合（prod 29 条 + dev 76 条）：

- MIT：99；ISC：3；Apache-2.0：1（`typescript@5.6.3`）；BSD-2-Clause：1；BSD-3-Clause：1。
- 无 GPL / AGPL / LGPL / SSPL / BUSL / Elastic / CC-BY-NC 命中；无未声明许可的包。

### 3.2 Python —— NEEDS_DECISION（仅再分发场景）

40 个已安装发行包：

- MIT 系 21（MIT 19 + `MIT AND PSF-2.0` 1 + `MIT License` 1），Apache-2.0 4（另 1 个 `Apache-2.0 OR BSD-2-Clause` 双许可），BSD 系 10（BSD-2 1 + BSD-3 9），PSF-2.0 1。
- **LGPL-3.0-only 2**：`psycopg==3.3.9`、`psycopg-binary==3.3.9`。
- **MPL-2.0 1**：`certifi==2026.11.20`。
- 无 GPL / AGPL。

LGPL 与 MPL 属弱 copyleft：作为动态链接库/按文件 copyleft 使用通常不触发源码开放义务，但**若对外再分发制品（尤其静态打包 psycopg-binary），需法务确认义务边界**。本项标 NEEDS_DECISION，非阻塞开发。

### 3.3 项目自身 LICENSE —— NEEDS_DECISION

仓库根目录**无 LICENSE / LICENSE.md / COPYING 文件**（`ls LICENSE*` 无结果）。下游使用者无法确定本项目的授权与再分发条款。需项目所有者决定许可证类型后新增。

证据：`licenses-npm.txt`、`licenses-python.txt`、`gitignore-and-artifacts.txt`。

---

## 4. 构建制品与密钥扫描（F-12）

### 4.1 `.gitignore` 与真实凭据隔离 —— VERIFIED

- `.gitignore:2` `.env`、`:3` `.env.*`、`:4` `!.env.example`、`:5-9` `*.key|*.pem|*.p12|*_rsa|*_dsa`、`:10-11` `secrets/|credentials/`。
- `git check-ignore -v .env` → `.gitignore:2:.env`；`git ls-files --error-unmatch .env` → 未跟踪。
- `.env.example` 已跟踪，作为占位符模板。
- `git ls-files` 中无 `*.key|*.pem|*.p12`、`*_rsa`、`*_dsa`、`secrets/`、`credentials/` 文件。

**本轮未读取、未输出 `.env` 的任何真实值**；仅核对 git 跟踪与忽略状态。

### 4.2 模式扫描 —— VERIFIED

复用 `nightly-gate.yml:210` 的 CI 同款模式：

```
(sk-[A-Za-z0-9]{20,}|DEEPSEEK_API_KEY=[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY)
```

使用 `git grep -IlE`（**只输出命中文件名，不输出内容**，避免任何潜在凭据外泄）：

- 已跟踪文件（CI 排除 `.github` 口径）：退出码 1，**无命中**。
- 不排除 `.github`：同样无命中。
- `apps/web/dist` 全量文件：无命中。

### 4.3 构建制品 —— VERIFIED

- `apps/web/dist` 共 26 个文件（25 个 assets + index.html），未在 git 中跟踪（`.gitignore:30`）。
- 无 `*.map` source map、无 `*.bak` / `*.orig` 备份文件。
- 无漏洞链字符串（见 1.3）、无密钥模式命中。

**扫描范围说明（PARTIAL）**：CI 扫描仅覆盖指定扩展名且排除 `.github`；`.env` 因被 git 忽略而不在扫描范围（设计如此）。该门禁可防止“已跟踪文件误提交密钥”，但不能替代运行时的密钥管理与轮换审计。

证据：`gitignore-and-artifacts.txt`、`secret-pattern-scan.txt`、`build-artifact-check.txt`、`npm-dependency-tree-and-dist.txt`。

---

## 5. 明确处置与验证命令（未执行，需授权）

> 以下命令**本轮均未执行**。任何改动都应先在独立分支进行，并保留本轮证据作为改造前基线。**不得使用 `npm audit fix --force` 或 `npm install --force`。**

### 5.1 npm 三漏洞 —— NEEDS_DECISION

推荐方案 A（显式指定 semver-major 版本，避免 `--force`）：

```bash
cd apps/web
# 1) 显式升级（非 --force）；此步会修改 package.json 与 package-lock.json，需先授权
npm install --save-dev vue-tsc@3.3.14

# 2) 审计复验：应变为 0 moderate / 0 high / 0 critical
npm audit --json
npm audit --omit=dev --json

# 3) 功能回归：类型检查 + 构建必须通过
npm run typecheck
npm run build

# 4) 确认新 language-core 已不再依赖 minimatch
npm ls vue-tsc @vue/language-core minimatch
```

若验证失败或不想跨 major，则走方案 C：形成书面风险接受（dev-only、0 生产暴露、无 high/critical），并在 CI 中保留现有“仅 high/critical 阻断”规则，同时跟踪 `GHSA-g3hv-75wg-m249` 的上游修复进展。

### 5.2 Python 约束与锁 —— NEEDS_DECISION

```bash
# 1) 修正 pytest-cov 约束冲突（需授权改 requirements-dev.txt：>=7,<8）
# 2) 用 pip-tools 从 requirements*.txt 重建完整 lock（含 httpx/httpx2/mypy/playwright/psycopg 及传递依赖）
#    pip-compile 推荐加 --generate-hashes
# 3) 让 CI 改为从 lock 安装，消除范围漂移
# 4) 复验：
PYTHONPATH=/tmp/traceguard-wp8-pip-audit .venv/bin/python -m pip_audit \
  -r requirements.lock.txt --disable-pip --no-deps --format json
.venv/bin/python -m pip check
```

### 5.3 许可证与治理 —— NEEDS_DECISION

- 由项目所有者选择并新增根目录 LICENSE（影响再分发合规性）。
- 若计划对外分发包含 `psycopg-binary` 的制品，先完成 LGPL-3.0 / MPL-2.0 法务评估。
- 将 SBOM 生成纳入 CI（npm 现有工具即可；Python 可引入 `cyclonedx-py` 产出纯组件 SBOM）。

---

## 6. 证据索引（全部位于 `docs/assets/run009/wp8/`）

| 文件 | 内容 |
|---|---|
| `npm-audit-raw.json` | `npm audit --json` 原始报告（3 moderate） |
| `npm-audit-prod-only-raw.json` | `npm audit --omit=dev --json`（0 漏洞） |
| `npm-audit-text.txt` | npm 文本报告（含 `--force` 与 breaking change 提示） |
| `npm-audit-stderr.txt` | audit 标准错误（空） |
| `npm-dependency-tree-and-dist.txt` | 依赖树、仅生产依赖树、dist 清单与关键字扫描 |
| `npm-view-metadata.txt` | npm registry 升级目标兼容性元数据 |
| `python-installed-versions.txt` | `.venv` `pip freeze --all` 与 pip-audit 可用性 |
| `python-pip-audit-availability.txt` | pip-audit 探测、`httpx2` 版本查询 |
| `pip-audit-local-raw.json` / `pip-audit-local-stderr.txt` | 已安装环境的 OSV 审计结果 |
| `pip-audit-lock-raw.json` / `pip-audit-lock-stderr.txt` | lock 文件的 OSV 审计结果 |
| `python-lock-completeness.txt` | 声明/lock/安装三方覆盖矩阵与缺口 |
| `python-httpx2-investigation.txt` | `httpx2` PyPI 身份核验、lock 与安装一致性 |
| `licenses-npm.txt` / `licenses-python.txt` | 依赖许可证聚合 |
| `gitignore-and-artifacts.txt` | `.env` 忽略/跟踪状态、制品目录 |
| `secret-pattern-scan.txt` | CI 同款密钥模式扫描（仅文件名） |
| `build-artifact-check.txt` | dist 密钥扫描、source map、pyproject 依赖段 |
| `sbom-tooling.txt` | SBOM 工具可用性与生成命令记录 |
| `sbom-npm-spdx.json` / `sbom-npm-cyclonedx.json` | npm SPDX（56 packages）/ CycloneDX 1.5（55 components） |
| `sbom-python-cyclonedx.json` | Python CycloneDX JSON（32 components, 0 vulnerabilities） |
| `sbom-npm-spdx.err` / `sbom-npm-cyclonedx.err` / `sbom-python.err` | SBOM 生成 stderr |

---

## 7. 一句话结论

**当前代码库没有高危 Python/npm 漏洞、没有密钥入库、没有复制性许可证污染；npm 的 3 个 moderate 全部是 dev-only 的同一条 `minimatch` DoS 链，不进入生产制品，但唯一修复路径是 semver-major 的 `vue-tsc@3.3.14`（npm 只能靠 `--force` 完成），本轮按禁令未执行，标记为 NEEDS_DECISION；Python lock 不完整且 CI 未使用它、`pytest-cov` 约束冲突，同样列入 NEEDS_DECISION。**
