#!/usr/bin/env bash
# 启动被测应用 TeamFlow（默认 127.0.0.1:8001）
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn traceguard_teamflow.main:app \
  --host "${TG_TEAMFLOW_HOST:-127.0.0.1}" --port "${TG_TEAMFLOW_PORT:-8001}" "$@"
