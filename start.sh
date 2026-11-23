#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

RUNTIME_REPO_URL="${HERMES_RUNTIME_REPO:-https://github.com/pipi-HST/hermes-agent-main.git}"
SIBLING_RUNTIME="$(dirname "$ROOT")/hermes-agent-main"
LOCAL_RUNTIME="$ROOT/.runtime/hermes-agent-main"

if [ -f "$SIBLING_RUNTIME/hermes_cli/main.py" ]; then
  RUNTIME_DIR="$SIBLING_RUNTIME"
else
  RUNTIME_DIR="$LOCAL_RUNTIME"
fi

find_python() {
  for bin in python3.12 python3 python; do
    if command -v "$bin" >/dev/null 2>&1; then
      if "$bin" -c "import sys; assert sys.version_info >= (3, 12)" >/dev/null 2>&1; then
        echo "$bin"
        return 0
      fi
    fi
  done
  echo "[Hermes] 未找到 Python 3.12+，请先安装 Python 3.12 或以上版本。" >&2
  exit 1
}

echo "[Hermes] 正在检查本地运行环境..."

if [ ! -f "$RUNTIME_DIR/hermes_cli/main.py" ]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "[Hermes] 未找到 Git，请先安装 Git 后重试。" >&2
    exit 1
  fi
  echo "[Hermes] 首次运行：正在下载网页工作台运行时到 .runtime ..."
  mkdir -p "$(dirname "$RUNTIME_DIR")"
  git clone --depth 1 "$RUNTIME_REPO_URL" "$RUNTIME_DIR"
fi

VENV_PY="$RUNTIME_DIR/.venv/bin/python"
DEPS_MARKER="$RUNTIME_DIR/.venv/.hermes_bundle_deps_ok"

if [ ! -x "$VENV_PY" ]; then
  PY_BIN="$(find_python)"
  echo "[Hermes] 首次运行：正在创建 Python 虚拟环境..."
  "$PY_BIN" -m venv "$RUNTIME_DIR/.venv"
fi

if [ ! -f "$DEPS_MARKER" ]; then
  echo "[Hermes] 首次运行：正在安装依赖（可能需要 1-3 分钟，请耐心等待）..."
  "$VENV_PY" -m pip install -q -r "$ROOT/requirements.txt"
  "$VENV_PY" -m pip install -q -e "$RUNTIME_DIR[web]"
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$DEPS_MARKER"
fi

"$VENV_PY" "$ROOT/scripts/bootstrap_env.py"

set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

BASE_URL="${LLM_BASE_URL:-https://api.deepseek.com}"
BASE_URL="${BASE_URL%/}"
case "$BASE_URL" in
  */v1) OPENAI_BASE="$BASE_URL" ;;
  *) OPENAI_BASE="$BASE_URL/v1" ;;
esac

VAULT_CFG="${VAULT_PATH:-./data/vault}"
case "$VAULT_CFG" in
  /*|~*) VAULT_ABS="$VAULT_CFG" ;;
  *) VAULT_ABS="$ROOT/${VAULT_CFG#./}" ;;
esac

export PYTHONPATH="$ROOT"
export HERMES_API_TOKEN
export HERMES_KNOWLEDGE_URL="http://127.0.0.1:8000"
export HERMES_KNOWLEDGE_TOKEN="$HERMES_API_TOKEN"
export HERMES_KNOWLEDGE_VAULT="$VAULT_ABS"
export HERMES_KNOWLEDGE_DB="$ROOT/data/captures.db"
export OPENAI_API_KEY="${DEEPSEEK_API_KEY:-${OPENAI_API_KEY:-}}"
export OPENAI_BASE_URL="$OPENAI_BASE"

echo "[Hermes] 正在启动知识服务 (127.0.0.1:8000)..."
"$VENV_PY" -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000 &
SVC_PID=$!

cleanup() {
  echo ""
  echo "[Hermes] 正在关闭后台知识服务..."
  kill "$SVC_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

READY=0
for _ in $(seq 1 30); do
  sleep 0.5
  if curl -fsS -H "Authorization: Bearer $HERMES_API_TOKEN" "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
done

if [ "$READY" -ne 1 ]; then
  echo "[Hermes] 知识服务启动超时，请检查端口 8000 是否被占用。" >&2
  exit 1
fi

echo "[Hermes] 知识服务已就绪！正在启动网页工作台 (http://127.0.0.1:9119)..."
echo "[Hermes] 按 Ctrl + C 可同时关闭全部服务。"

cd "$RUNTIME_DIR"
"$VENV_PY" -u -m hermes_cli.main dashboard
