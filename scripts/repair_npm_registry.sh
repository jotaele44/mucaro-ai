#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

printf '%s\n' "NPM_REGISTRY_REPAIR_START root=${ROOT_DIR}"

if [ ! -f "frontend/package-lock.json" ]; then
  printf '%s\n' "ERROR_PACKAGE_LOCK_MISSING frontend/package-lock.json" >&2
  exit 2
fi

python3 - <<'PY'
from pathlib import Path
lock = Path('frontend/package-lock.json')
text = lock.read_text(encoding='utf-8')
old = 'https://packages.applied-caas-gateway1.internal.api.openai.org/artifactory/api/npm/npm-public/'
new = 'https://registry.npmjs.org/'
count = text.count(old)
if count:
    lock.write_text(text.replace(old, new), encoding='utf-8')
print(f'PACKAGE_LOCK_REGISTRY_REWRITTEN replacements={count}')
PY

cat > frontend/.npmrc <<'NPMRC'
registry=https://registry.npmjs.org/
fund=false
audit=true
NPMRC

# The registry is pinned by frontend/.npmrc and the --registry flag; the user's global npm config is left untouched.
npm cache verify
cd frontend
rm -rf node_modules
npm ci --registry=https://registry.npmjs.org/
npm run build
printf '%s\n' "NPM_REGISTRY_REPAIR_OK"
