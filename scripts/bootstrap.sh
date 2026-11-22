#!/usr/bin/env bash
# 一次性环境准备：venv + 依赖 + Playwright 浏览器 + 包路径
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pip install playwright

# 包路径（本地开发）；不用绝对路径硬编码到仓库，脚本生成
cat > .venv/lib/python*/site-packages/traceguard.pth <<PTH
$ROOT/packages/core
$ROOT/packages/contracts
$ROOT/packages/execution
$ROOT/packages/agents
$ROOT/packages/evidence
$ROOT/packages/persistence
$ROOT/apps/api
$ROOT/apps/teamflow
$ROOT/apps/workers
$ROOT/benchmark
PTH

# Chromium（镜像可加速：PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright）
.venv/bin/playwright install chromium || echo "浏览器下载失败：可设置镜像后重试，或使用系统 Edge/Chrome（TG_BROWSER_CHANNEL=msedge）"

(cd apps/web && npm install && npm run build)
echo "完成。启动：scripts/run_teamflow.sh & scripts/run_api.sh"
