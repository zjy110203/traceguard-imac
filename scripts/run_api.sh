#!/usr/bin/env bash
# 启动 TraceGuard API（默认 127.0.0.1:8000，单机模式自带内嵌 Worker）
set -euo pipefail
cd "$(dirname "$0")/.."
export TG_INLINE_WORKER="${TG_INLINE_WORKER:-1}"
exec .venv/bin/python -m uvicorn traceguard_api.main:app \
  --host "${TG_API_HOST:-127.0.0.1}" --port "${TG_API_PORT:-8000}" "$@"
