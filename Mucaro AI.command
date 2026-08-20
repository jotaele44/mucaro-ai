#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"
if [ ! -d ".venv" ]; then
  printf '%s\n' "ERROR_VENV_MISSING run: bash scripts/bootstrap_macos.sh" >&2
  exit 2
fi
source .venv/bin/activate
python scripts/launch_app.py
