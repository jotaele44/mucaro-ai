#!/bin/bash
# Stage 0 of the double-click setup (started by "Mucaro AI.command").
#
# Provisions pinned, checksum-verified private runtimes inside .mucaro-runtime/
# (uv, Node, CPython) and the project .venv, then hands off to
# scripts/one_click.py, which installs dependencies, builds the UI, and launches.
#
# Rules this file follows so the user never has to act:
# - Runs on bash 3.2 (the macOS default): no associative arrays, mapfile, ${x,,}.
# - Never runs python/python3/git/node/npm from PATH. On a Mac without the
#   Command Line Tools those are stubs in /usr/bin that open an install dialog.
# - Writes nothing outside the project folder.
# With --ui (passed by Start Mucaro AI.app), every step and failure also updates the browser progress page
# .mucaro-runtime/ui/progress.html; the flag is passed on to scripts/one_click.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
cd "${ROOT}"

RUNTIME="${ROOT}/.mucaro-runtime"
LOG_DIR="${RUNTIME}/logs"
LOCK_DIR="${RUNTIME}/setup.lock"
PINS_FILE="${ROOT}/scripts/one_click_pins.env"
VENV_PY="${ROOT}/.venv/bin/python"
MIN_FREE_KB=2000000
LOG=""
PAGE=""
for arg in ${1+"$@"}; do
  if [ "${arg}" = "--ui" ]; then
    PAGE="${RUNTIME}/ui/progress.html"
  fi
done
# shellcheck source=scripts/progress_page.sh
. "${SCRIPT_DIR}/progress_page.sh"

log_line() {
  printf '%s\n' "$*"
  if [ -n "${LOG}" ]; then
    printf '%s\n' "$*" >>"${LOG}" 2>/dev/null || true
  fi
}

fatal() {
  code="$1"
  shift
  progress_page_write "${PAGE}" error 0 "Mucaro AI cannot start on this computer" "${code}: $*" "${LOG}"
  log_line ""
  log_line "${code} $*"
  log_line ""
  log_line "Mucaro AI setup cannot continue on this computer (see the reason above)."
  if [ -n "${LOG}" ]; then
    log_line "Details: ${LOG}"
  fi
  exit 2
}

if ! mkdir -p "${LOG_DIR}" 2>/dev/null; then
  fatal ERROR_FOLDER_NOT_WRITABLE "cannot write inside ${ROOT}; move the mucaro-ai folder somewhere writable (for example ~/Developer) and double-click again"
fi
LOG="${LOG_DIR}/setup-$(date +%Y%m%d-%H%M%S).log"
: >"${LOG}"

if [ ! -f "${PINS_FILE}" ]; then
  fatal ERROR_PINS_MISSING "${PINS_FILE}"
fi
# shellcheck source=scripts/one_click_pins.env
. "${PINS_FILE}"

# Keep the user's shell customizations from leaking into the private runtimes.
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP VIRTUAL_ENV CONDA_PREFIX CONDA_DEFAULT_ENV NODE_OPTIONS || true
export UV_NO_CONFIG=1
export UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON_INSTALL_DIR="${RUNTIME}/python"
export UV_PYTHON_BIN_DIR="${RUNTIME}/python-bin"
export UV_CACHE_DIR="${RUNTIME}/cache/uv"
export UV_TOOL_DIR="${RUNTIME}/uv-tools"

detect_platform() {
  os_name="$(uname -s)"
  arch="$(uname -m)"
  case "${os_name}" in
    Darwin)
      # A Terminal running under Rosetta reports x86_64; prefer native arm64 builds.
      if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then
        arch="arm64"
      fi
      mac_version="$(sw_vers -productVersion 2>/dev/null || echo 0)"
      mac_major="${mac_version%%.*}"
      case "${arch}" in
        arm64)
          UV_TARGET="aarch64-apple-darwin"
          NODE_PLAT="darwin-arm64"
          min_major=14
          ;;
        x86_64)
          UV_TARGET="x86_64-apple-darwin"
          NODE_PLAT="darwin-x64"
          min_major=13
          ;;
        *)
          fatal ERROR_PLATFORM_UNSUPPORTED "macOS on ${arch} is not supported"
          ;;
      esac
      case "${mac_major}" in
        '' | *[!0-9]*) mac_major=0 ;;
      esac
      if [ "${mac_major}" -lt "${min_major}" ]; then
        fatal ERROR_MACOS_TOO_OLD "found macOS ${mac_version} on ${arch}; Mucaro AI needs macOS ${min_major} or newer on this Mac because its map libraries (rasterio, pyproj) publish no build for older versions"
      fi
      PLATFORM_LABEL="macOS ${mac_version} ${arch}"
      ;;
    Linux)
      case "${arch}" in
        x86_64 | amd64)
          UV_TARGET="x86_64-unknown-linux-gnu"
          NODE_PLAT="linux-x64"
          ;;
        *)
          fatal ERROR_PLATFORM_UNSUPPORTED "Linux on ${arch} is not supported (the pinned rasterio has no build for it); use x86_64"
          ;;
      esac
      PLATFORM_LABEL="Linux ${arch}"
      ;;
    *)
      fatal ERROR_PLATFORM_UNSUPPORTED "${os_name} is not supported by this launcher; on Windows double-click \"Start Mucaro AI.exe\""
      ;;
  esac
  uv_hash_var="UV_SHA256_$(printf '%s' "${UV_TARGET}" | tr '-' '_')"
  node_hash_var="NODE_SHA256_$(printf '%s' "${NODE_PLAT}" | tr '-' '_')"
  UV_SHA256="${!uv_hash_var:-}"
  NODE_SHA256="${!node_hash_var:-}"
  if [ -z "${UV_SHA256}" ] || [ -z "${NODE_SHA256}" ]; then
    fatal ERROR_PINS_INCOMPLETE "no pinned checksum for ${UV_TARGET} / ${NODE_PLAT} in scripts/one_click_pins.env"
  fi
  UV_HOME="${RUNTIME}/uv/${UV_VERSION}"
  UV_BIN="${UV_HOME}/uv"
  NODE_HOME="${RUNTIME}/node/${NODE_VERSION}"
}

lock_holder_alive() {
  holder="$1"
  [ -n "${holder}" ] || return 1
  kill -0 "${holder}" 2>/dev/null || return 1
  # Guard against a recycled pid: the holder must still be this setup (bash stage or one_click.py).
  command -v ps >/dev/null 2>&1 || return 0
  case "$(ps -p "${holder}" -o command= 2>/dev/null || true)" in
    *one_click*) return 0 ;;
  esac
  return 1
}

acquire_lock() {
  waited=0
  empty_checks=0
  while ! mkdir "${LOCK_DIR}" 2>/dev/null; do
    holder="$(cat "${LOCK_DIR}/pid" 2>/dev/null || true)"
    if [ -z "${holder}" ] && [ "${empty_checks}" -lt 5 ]; then
      # The other window may have created the lock a moment ago; give it time to write its pid.
      empty_checks=$((empty_checks + 1))
      sleep 1
      continue
    fi
    if lock_holder_alive "${holder}"; then
      if [ "${waited}" -eq 0 ]; then
        log_line "SETUP_WAITING another Mucaro AI window (pid ${holder}) is still setting up; this window continues when it finishes"
      fi
      sleep 2
      waited=$((waited + 2))
      if [ "${waited}" -ge 3600 ]; then
        fatal ERROR_SETUP_LOCK_TIMEOUT "another setup (pid ${holder}) has been running for over an hour; close its window and double-click again"
      fi
      continue
    fi
    log_line "SETUP_LOCK_STALE removing lock left by pid ${holder:-unknown}"
    rm -rf "${LOCK_DIR}"
    empty_checks=0
  done
  printf '%s\n' "$$" >"${LOCK_DIR}/pid"
  LOCK_TOKEN="$$"
}

release_lock() {
  if [ -n "${LOCK_TOKEN:-}" ] && [ "$(cat "${LOCK_DIR}/pid" 2>/dev/null || true)" = "${LOCK_TOKEN}" ]; then
    rm -rf "${LOCK_DIR}"
  fi
}

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    openssl dgst -sha256 -r "$1" | awk '{print $1}'
  fi
}

require_disk_space() {
  free_kb="$(df -Pk "${RUNTIME}" 2>/dev/null | awk 'NR==2 {print $4}')"
  case "${free_kb}" in
    '' | *[!0-9]*) return 0 ;;
  esac
  if [ "${free_kb}" -lt "${MIN_FREE_KB}" ]; then
    fail ERROR_DISK_FULL "about 2 GB of free disk space is needed for the first setup; found $((free_kb / 1024)) MB free"
  fi
}

# Inside the logged stage the terminal and the log both receive stdout, so plain printf is enough.
say() {
  printf '%s\n' "$*"
}

fail() {
  code="$1"
  shift
  progress_page_write "${PAGE}" error 0 "Mucaro AI setup did not finish" "${code}: $*" "${LOG}"
  say "${code} $*"
  exit 3
}

page_step() {
  progress_page_write "${PAGE}" running 0 "Preparing Mucaro AI" "$1" "${LOG}"
}

download() {
  url="$1"
  dest="$2"
  attempt=1
  delay=2
  while :; do
    if curl --fail --location --silent --show-error --connect-timeout 20 --max-time 1800 --output "${dest}.part" "${url}"; then
      mv -f "${dest}.part" "${dest}"
      return 0
    fi
    rm -f "${dest}.part"
    if [ "${attempt}" -ge 5 ]; then
      return 1
    fi
    say "WARN_DOWNLOAD_RETRY attempt=${attempt} wait=${delay}s url=${url}"
    sleep "${delay}"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

fetch_verified() {
  url="$1"
  dest="$2"
  expected="$3"
  if ! download "${url}" "${dest}"; then
    fail ERROR_DOWNLOAD_FAILED "could not download ${url}; check the internet connection and double-click again"
  fi
  actual="$(sha256_of "${dest}")"
  if [ "${actual}" != "${expected}" ]; then
    rm -f "${dest}"
    fail ERROR_CHECKSUM_MISMATCH "${url} expected=${expected} actual=${actual}; the download was damaged or tampered with and was deleted"
  fi
  say "CHECKSUM_OK ${url##*/}"
}

ensure_uv() {
  if [ -x "${UV_BIN}" ] && [ "$("${UV_BIN}" --version 2>/dev/null | awk '{print $2}')" = "${UV_VERSION}" ]; then
    say "UV_OK cached ${UV_VERSION}"
    return 0
  fi
  require_disk_space
  say "==> Downloading the Python installer uv ${UV_VERSION} (one time, about 20 MB)"
  page_step "Downloading the Python installer (one time, about 20 MB)"
  work="$(mktemp -d "${RUNTIME}/tmp.XXXXXX")"
  fetch_verified "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_TARGET}.tar.gz" "${work}/uv.tar.gz" "${UV_SHA256}"
  tar -xzf "${work}/uv.tar.gz" -C "${work}" || fail ERROR_EXTRACT_FAILED "uv archive"
  [ -x "${work}/uv-${UV_TARGET}/uv" ] || fail ERROR_EXTRACT_FAILED "uv binary missing from archive"
  rm -rf "${UV_HOME}"
  mkdir -p "${RUNTIME}/uv"
  mv "${work}/uv-${UV_TARGET}" "${UV_HOME}"
  rm -rf "${work}"
  say "UV_OK installed $("${UV_BIN}" --version)"
}

ensure_node() {
  if [ -x "${NODE_HOME}/bin/node" ] && [ "$("${NODE_HOME}/bin/node" --version 2>/dev/null)" = "v${NODE_VERSION}" ]; then
    say "NODE_OK cached ${NODE_VERSION}"
    return 0
  fi
  require_disk_space
  say "==> Downloading private Node.js ${NODE_VERSION} (one time, about 50 MB)"
  page_step "Downloading Node.js (one time, about 50 MB)"
  work="$(mktemp -d "${RUNTIME}/tmp.XXXXXX")"
  archive="node-v${NODE_VERSION}-${NODE_PLAT}"
  fetch_verified "https://nodejs.org/dist/v${NODE_VERSION}/${archive}.tar.gz" "${work}/node.tar.gz" "${NODE_SHA256}"
  tar -xzf "${work}/node.tar.gz" -C "${work}" || fail ERROR_EXTRACT_FAILED "node archive"
  [ -x "${work}/${archive}/bin/node" ] || fail ERROR_EXTRACT_FAILED "node binary missing from archive"
  rm -rf "${NODE_HOME}"
  mkdir -p "${RUNTIME}/node"
  mv "${work}/${archive}" "${NODE_HOME}"
  rm -rf "${work}"
  say "NODE_OK installed $("${NODE_HOME}/bin/node" --version)"
}

venv_is_managed() {
  cfg="${ROOT}/.venv/pyvenv.cfg"
  [ -f "${cfg}" ] || return 1
  home="$(sed -n 's/^home[[:space:]]*=[[:space:]]*//p' "${cfg}" | head -n 1)"
  case "${home}" in
    "${UV_PYTHON_INSTALL_DIR}/"*) ;;
    *) return 1 ;;
  esac
  [ -x "${VENV_PY}" ] || return 1
  found="$("${VENV_PY}" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  [ "${found}" = "${PYTHON_VERSION}" ]
}

ensure_venv() {
  if venv_is_managed; then
    say "VENV_OK cached .venv python ${PYTHON_VERSION}"
    return 0
  fi
  if [ -e "${ROOT}/.venv" ]; then
    say "VENV_RECREATE .venv does not use the private Python ${PYTHON_VERSION}; rebuilding it"
  fi
  require_disk_space
  say "==> Installing private Python ${PYTHON_VERSION} (one time, about 30 MB)"
  page_step "Installing a private Python ${PYTHON_VERSION} (one time, about 30 MB)"
  "${UV_BIN}" python install --no-bin --no-registry "${PYTHON_VERSION}" || fail ERROR_PYTHON_INSTALL_FAILED "uv python install ${PYTHON_VERSION}"
  rm -rf "${ROOT}/.venv"
  "${UV_BIN}" venv --seed --no-project --python "${PYTHON_VERSION}" "${ROOT}/.venv" || fail ERROR_VENV_FAILED "uv venv"
  venv_is_managed || fail ERROR_VENV_FAILED ".venv was created but does not run the private Python ${PYTHON_VERSION}"
  say "VENV_OK created .venv python $("${VENV_PY}" -c 'import platform; print(platform.python_version())')"
}

stage0() {
  set -euo pipefail
  rm -rf "${RUNTIME}"/tmp.* 2>/dev/null || true
  say "==> [0/6] Preparing private runtimes in .mucaro-runtime"
  page_step "Checking the private Python and Node.js"
  ensure_uv
  ensure_node
  ensure_venv
}

log_line "Mucaro AI - setup and launch"
log_line "No input is needed. The first run downloads everything it needs (2-5 minutes); later runs start in seconds."
log_line "ONE_CLICK_STAGE0_START root=${ROOT} log=${LOG}"
detect_platform
log_line "PLATFORM ${PLATFORM_LABEL} uv_target=${UV_TARGET} node=${NODE_PLAT}"
acquire_lock
trap release_lock EXIT

set +e
(stage0) 2>&1 | tee -a "${LOG}"
stage0_rc="${PIPESTATUS[0]}"
set -e
if [ "${stage0_rc}" -ne 0 ]; then
  log_line ""
  # fail() already put its reason on the page; this covers anything that stopped stage 0 without it.
  if [ -n "${PAGE}" ] && ! grep -q 'class="state-error"' "${PAGE}" 2>/dev/null; then
    progress_page_write "${PAGE}" error 0 "Mucaro AI setup did not finish" "ERROR_STAGE0_FAILED: step failed with code ${stage0_rc}" "${LOG}"
  fi
  log_line "Mucaro AI setup did not finish (step failed with code ${stage0_rc})."
  log_line "Details: ${LOG}"
  log_line "Nothing outside this folder was changed. Fix the problem named above (usually the internet connection), then double-click again; setup resumes where it stopped."
  exit "${stage0_rc}"
fi

export MUCARO_RUNTIME_DIR="${RUNTIME}"
export MUCARO_UV_BIN="${UV_BIN}"
export MUCARO_NODE_DIR="${NODE_HOME}"
export MUCARO_SETUP_LOCK="${LOCK_DIR}"
export MUCARO_SETUP_LOCK_TOKEN="${LOCK_TOKEN}"
export MUCARO_SETUP_LOG="${LOG}"
# exec keeps this process id, so the lock token stays valid; one_click.py releases the lock.
trap - EXIT
exec "${VENV_PY}" "${ROOT}/scripts/one_click.py" ${1+"$@"}
