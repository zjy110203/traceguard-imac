#!/usr/bin/env bash
# 启动独立 Worker（建议 API 关闭内嵌 Worker：TG_INLINE_WORKER=0）
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m traceguard_worker.main "$@"
