#!/bin/bash
# Shared Finder entry point. Runs setup without a console or a browser fallback.
set -uo pipefail
ROOT="$1"
shift

# MUCARO_OSASCRIPT lets tests stub the dialog; normal use is always /usr/bin/osascript.
OSASCRIPT="${MUCARO_OSASCRIPT:-/usr/bin/osascript}"

failure_dialog() {
  "${OSASCRIPT}" -e 'on run argv' \
    -e 'display dialog (item 1 of argv) with title "Mucaro AI" buttons {"OK"} default button "OK" with icon caution' \
    -e 'end run' -- "$1" </dev/null >/dev/null 2>&1 || true
}

if ! mkdir -p "${ROOT}/.mucaro-runtime/logs"; then
  failure_dialog "Mucaro AI cannot write to its folder. Move the entire mucaro-ai folder somewhere writable and open Mucaro AI Setup.app again."
  exit 2
fi
LOG="${ROOT}/.mucaro-runtime/logs/launch-$(date +%Y%m%d-%H%M%S)-$$.log"
if ! touch "${LOG}"; then
  failure_dialog "Mucaro AI cannot create its startup log. Check that its folder is writable, then reopen Mucaro AI Setup.app."
  exit 2
fi
exec </dev/null >>"${LOG}" 2>&1
export MUCARO_SILENT_LAUNCH=1
stop_child() {
  trap - TERM INT HUP
  kill -TERM "${child}" 2>/dev/null || true
  wait "${child}" 2>/dev/null || true
  exit 0
}
/bin/bash "${ROOT}/scripts/one_click.sh" --native "$@" &
child=$!
trap stop_child TERM INT HUP
wait "${child}"
code=$?
trap - TERM INT HUP
if [ "${code}" -ne 0 ]; then
  failure_dialog "Mucaro AI could not start (code ${code}). Details are in: ${LOG}. Open Mucaro AI.app again to retry; setup resumes where it stopped."
fi
exit "${code}"
