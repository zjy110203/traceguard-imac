#!/usr/bin/env bash
# RUN-005 一键演示/验收（隔离数据库，不触碰 .traceguard/traceguard.db 与 teamflow.db）
#
# 用法：
#   bash scripts/demo_run005.sh              # 起服务 → 真实执行 → 诊断 → 探针闭环 → UI 验收 → 收尾
#   bash scripts/demo_run005.sh --keep      # 结束后保留服务（可手动打开浏览器操作）
#   bash scripts/demo_run005.sh --with-llm  # 额外跑三类 LLM 顾问真实冒烟（消耗少量额度）
#
# 全部数据落在 .traceguard/run005/，日志在 logs/run005/。
set -euo pipefail
cd "$(dirname "$0")/.."

API_PORT="${TG_API_PORT:-8010}"
TF_PORT="${TG_TEAMFLOW_PORT:-8011}"
RUN_DIR=".traceguard/run005"
OUT_DIR="docs/assets/run005"
KEEP=0
WITH_LLM=0
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    --with-llm) WITH_LLM=1 ;;
    *) echo "未知参数：$arg"; exit 2 ;;
  esac
done

PY=".venv/bin/python"
[ -x "$PY" ] || { echo "缺少 .venv，请先执行 bash scripts/bootstrap.sh"; exit 1; }
mkdir -p "$RUN_DIR" "$OUT_DIR" logs/run005

if lsof -ti tcp:"$API_PORT" >/dev/null 2>&1; then
  echo "端口 $API_PORT 已被占用，请先停止旧进程或改用 TG_API_PORT=xxxx bash scripts/demo_run005.sh"; exit 1
fi
if lsof -ti tcp:"$TF_PORT" >/dev/null 2>&1; then
  echo "端口 $TF_PORT 已被占用，请先停止旧进程或改用 TG_TEAMFLOW_PORT=xxxx"; exit 1
fi

TF_PID=""; API_PID=""
cleanup() {
  if [ "$KEEP" = "0" ]; then
    [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
    [ -n "$TF_PID" ] && kill "$TF_PID" 2>/dev/null || true
    echo "服务已停止（日志：logs/run005/）"
  else
    echo "服务保留运行：API http://127.0.0.1:$API_PORT  TeamFlow http://127.0.0.1:$TF_PORT"
    echo "停止：kill \$(lsof -ti tcp:$API_PORT) \$(lsof -ti tcp:$TF_PORT)"
  fi
}
trap cleanup EXIT

echo "==> 启动被测应用 TeamFlow :${TF_PORT}（隔离库 $RUN_DIR/teamflow.db）"
TG_TEAMFLOW_DB="sqlite:///./$RUN_DIR/teamflow.db" \
  "$PY" -m uvicorn traceguard_teamflow.main:app --host 127.0.0.1 --port "$TF_PORT" \
  > logs/run005/teamflow.log 2>&1 &
TF_PID=$!

echo "==> 启动 TraceGuard API :${API_PORT}（隔离库 $RUN_DIR/api.db）"
TG_DATABASE_URL="sqlite:///./$RUN_DIR/api.db" \
TG_ARTIFACT_DIR="$RUN_DIR/artifacts" \
TG_TEAMFLOW_BASE_URL="http://127.0.0.1:$TF_PORT" \
TG_INLINE_WORKER=0 \
  "$PY" -m uvicorn traceguard_api.main:app --host 127.0.0.1 --port "$API_PORT" \
  > logs/run005/api.log 2>&1 &
API_PID=$!

echo "==> 等待健康检查"
for i in $(seq 1 40); do
  api_ok=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$API_PORT/health" || true)
  tf_ok=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$TF_PORT/api/state/projects" || true)
  [ "$api_ok" = "200" ] && [ "$tf_ok" = "200" ] && break
  sleep 0.5
  if [ "$i" = "40" ]; then echo "服务未就绪：api=$api_ok teamflow=$tf_ok（看 logs/run005/*.log）"; exit 1; fi
done
echo "    就绪"

run_case() {
  local tc="$1"
  local run_id
  run_id=$(curl -s -X POST "http://127.0.0.1:$API_PORT/api/v1/testcases/$tc/execute" \
    -H 'Content-Type: application/json' -d '{}' | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["run_id"])')
  curl -s -X POST "http://127.0.0.1:$API_PORT/api/v1/runs/$run_id/execute" \
    | "$PY" -c 'import sys,json;d=json.load(sys.stdin);print("    %s  execution=%s verdict=%s observer=%s" % (sys.argv[1], d.get("execution_status"), d.get("test_verdict"), d.get("observer_mode")), file=sys.stderr)' "$run_id"
  echo "$run_id"
}

echo "==> [1/5] 真实浏览器执行：正常场景"
NORMAL_RUN=$(run_case tcase_tf_create_normal | tail -1)

echo "==> [2/5] 真实浏览器执行：API 500 场景"
FAIL_RUN=$(run_case tcase_tf_api_error | tail -1)

echo "==> [3/5] 诊断（规则 + LLM 顾问）"
curl -s -X POST "http://127.0.0.1:$API_PORT/api/v1/runs/$FAIL_RUN/diagnose" \
  | "$PY" -c 'import sys,json;d=json.load(sys.stdin);print("    outcome=%s disposition=%s engine=%s" % (d.get("outcome"), d.get("disposition"), d.get("engine")))'

echo "==> [4/5] Active Diagnosis 探针闭环（预算按 Run 隔离）"
curl -s -X POST "http://127.0.0.1:$API_PORT/api/v1/runs/$FAIL_RUN/probe-plan?max_iterations=3" \
  | "$PY" -c '
import sys, json
d = json.load(sys.stdin)
print("    stop=%s (%s)" % (d.get("stop_reason"), d.get("stop_detail")))
print("    steps=" + ", ".join("%s:%s" % (s["probe_id"], s["status"]) for s in d.get("steps", [])))
b = d.get("budget", {})
print("    budget scope=%s probes=%s/%s cost=%s left=%ss closed=%s provisional=%s" % (
    b.get("scope_key"), b.get("probes_used"), b.get("max_probes"), b.get("cost_used"), b.get("time_left"), b.get("closed"), b.get("provisional")))
print("    evidence=" + ", ".join(d.get("evidence_ids", [])))
'

echo "==> [5/5] 真实浏览器 UI 验收（11 页面 + SSE + 移动端 + 深色主题）"
"$PY" scripts/ui_acceptance.py --base-url "http://127.0.0.1:$API_PORT" --out "$OUT_DIR" || {
  echo "UI 验收有失败项，详见 $OUT_DIR/ui-acceptance.json"; exit 1; }

if [ "$WITH_LLM" = "1" ]; then
  echo "==> 附加：三类 LLM 顾问真实冒烟"
  TG_API_URL="http://127.0.0.1:$API_PORT" "$PY" scripts/llm_advisor_smoke.py > /tmp/tg_llm_smoke.json || true
  "$PY" - <<'PY'
import json
try:
    d = json.load(open('/tmp/tg_llm_smoke.json'))
except Exception as exc:
    print("    BLOCKED/失败：%s" % exc); raise SystemExit(0)
if d.get("status") == "BLOCKED":
    print("    BLOCKED:", d.get("reason")); raise SystemExit(0)
for k in ("compiler", "probe_planner", "repair_planner"):
    v = d.get(k, {})
    print("    %-14s called=%s fallback=%s %s" % (k, v.get("called"), v.get("fallback"), v.get("error") or "ok"))
PY
fi

echo
echo "完成。证据："
echo "  UI 验收 JSON + 截图：$OUT_DIR/"
echo "  探针计划/证据/事件：$RUN_DIR/api.db（可用 http://127.0.0.1:$API_PORT/docs 查询）"
echo "  正常场景 Run：$NORMAL_RUN"
echo "  API 500 场景 Run：$FAIL_RUN"
