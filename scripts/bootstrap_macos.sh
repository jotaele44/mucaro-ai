#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

printf '%s\n' "BOOTSTRAP_START root=${ROOT_DIR}"

# Supported: 3.11-3.13. Pinned rasterio 1.4.3 publishes no wheels for 3.14+, so pip would try a GDAL source build.
SUPPORTED_PYTHON_CHECK='import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)'

# macOS often ships an older python3, so probe versioned interpreters first.
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "${candidate}" >/dev/null 2>&1 \
    && "${candidate}" -c "${SUPPORTED_PYTHON_CHECK}" >/dev/null 2>&1; then
    PYTHON_BIN="${candidate}"
    break
  fi
done

if [ -z "${PYTHON_BIN}" ]; then
  if command -v python3 >/dev/null 2>&1; then
    found="$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)"
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "ERROR_PYTHON_UNSUPPORTED found=${found} supported=3.11-3.13 double-click \"Mucaro AI.command\" (it brings its own Python 3.12) or: brew install python@3.12" >&2
    else
      printf '%s\n' "ERROR_PYTHON_TOO_OLD found=${found} required>=3.11 install with: brew install python@3.12 (or python.org) and rerun" >&2
    fi
  else
    printf '%s\n' "ERROR_PYTHON_MISSING install Python 3.11+ (brew install python@3.12 or python.org) and rerun" >&2
  fi
  exit 2
fi
printf '%s\n' "PYTHON_SELECTED ${PYTHON_BIN} $("${PYTHON_BIN}" -c 'import platform; print(platform.python_version())')"

"${PYTHON_BIN}" scripts/preflight.py --create-env || true

if [ -x ".venv/bin/python" ] && ! .venv/bin/python -c "${SUPPORTED_PYTHON_CHECK}" >/dev/null 2>&1; then
  printf '%s\n' "WARN_VENV_PYTHON_UNSUPPORTED recreating .venv with ${PYTHON_BIN}"
  rm -rf .venv
fi
"${PYTHON_BIN}" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

if command -v npm >/dev/null 2>&1; then
  if grep -q "applied-caas" frontend/package-lock.json 2>/dev/null; then
    bash scripts/repair_npm_registry.sh
  else
    # Registry is pinned per-project (frontend/.npmrc + flag); the user's global npm config is left untouched.
    (cd frontend && npm ci --registry=https://registry.npmjs.org/)
  fi
  printf '%s\n' "FRONTEND_DEPS_OK"
else
  printf '%s\n' "WARN_NPM_MISSING frontend deps skipped; install Node 20+ (brew install node) and rerun"
fi

python scripts/preflight.py --create-env --strict-imports
python scripts/release_check.py

printf '%s\n' "BOOTSTRAP_OK"
printf '%s\n' "NEXT_START: source .venv/bin/activate && python scripts/launch_app.py   (or double-click \"Mucaro AI.command\")"
printf '%s\n' "NEXT_OPEN: http://127.0.0.1:8000"
printf '%s\n' "DEV_BACKEND: source .venv/bin/activate && python scripts/dev_start.py --backend-only"
printf '%s\n' "DEV_FRONTEND: source .venv/bin/activate && python scripts/dev_start.py --frontend-only"
