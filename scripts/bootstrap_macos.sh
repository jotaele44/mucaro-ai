#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

printf '%s\n' "BOOTSTRAP_START root=${ROOT_DIR}"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  printf '%s\n' "ERROR_PYTHON_MISSING install Python 3.11+ and rerun" >&2
  exit 2
fi

"${PYTHON_BIN}" scripts/preflight.py --create-env || true

"${PYTHON_BIN}" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

if command -v npm >/dev/null 2>&1; then
  if grep -q "applied-caas" frontend/package-lock.json 2>/dev/null; then
    bash scripts/repair_npm_registry.sh
  else
    npm config set registry https://registry.npmjs.org/
    (cd frontend && npm ci --registry=https://registry.npmjs.org/)
  fi
  printf '%s\n' "FRONTEND_DEPS_OK"
else
  printf '%s\n' "WARN_NPM_MISSING frontend deps skipped; install Node 20+ later"
fi

python scripts/preflight.py --create-env --strict-imports
python scripts/release_check.py

printf '%s\n' "BOOTSTRAP_OK"
printf '%s\n' "NEXT_BACKEND: source .venv/bin/activate && uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000"
printf '%s\n' "NEXT_FRONTEND: cd frontend && npm run dev"
