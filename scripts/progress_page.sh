#!/bin/bash
# Bash renderer for the setup progress page (see scripts/progress_page.py, the reference copy).
# Sourced by "Start Mucaro AI.app/Contents/MacOS/mucaro-launcher" and scripts/one_click.sh.
# Usage: progress_page_write <page> <state> <step> <title> <detail> [log] [url]
# Bash 3.2 compatible. Never fails the caller: rendering problems are ignored.

# bash 5.2 expands "&" in ${var//pat/rep} replacements to the matched text, which would corrupt
# the HTML escapes below. Older bash versions do not have the option.
shopt -u patsub_replacement 2>/dev/null || true

PROGRESS_TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/one_click_progress.html"
PROGRESS_TOTAL_STEPS=6

progress_html_escape() {
  local s="$1"
  s="${s//&/&amp;}"
  s="${s//</&lt;}"
  s="${s//>/&gt;}"
  s="${s//\"/&quot;}"
  s="${s//\'/&#x27;}"
  printf '%s' "${s}"
}

progress_page_render() {
  local state="$1" step="$2" title="$3" detail="$4" log="${5:-}" url="${6:-}"
  local total="${PROGRESS_TOTAL_STEPS}" pct=0 refresh="" page
  case "${state}" in
    running) refresh='<meta http-equiv="refresh" content="1">' ;;
    ready) [ -n "${url}" ] && refresh="<meta http-equiv=\"refresh\" content=\"0; url=$(progress_html_escape "${url}")\">" ;;
  esac
  case "${state}" in
    ready | stopped) pct=100 ;;
    *) pct=$((step * 100 / total)) ;;
  esac
  [ "${pct}" -gt 100 ] && pct=100
  [ "${pct}" -lt 0 ] && pct=0
  page="$(cat "${PROGRESS_TEMPLATE}")" || return 1
  page="${page//\{\{REFRESH\}\}/${refresh}}"
  page="${page//\{\{STATE\}\}/${state}}"
  page="${page//\{\{STEP\}\}/${step}}"
  page="${page//\{\{TOTAL\}\}/${total}}"
  page="${page//\{\{PERCENT\}\}/${pct}}"
  page="${page//\{\{TITLE\}\}/$(progress_html_escape "${title}")}"
  page="${page//\{\{DETAIL\}\}/$(progress_html_escape "${detail}")}"
  page="${page//\{\{LOG\}\}/$(progress_html_escape "${log}")}"
  page="${page//\{\{URL\}\}/$(progress_html_escape "${url}")}"
  printf '%s\n' "${page}"
}

progress_page_write() {
  local target="$1"
  shift
  [ -n "${target}" ] || return 0
  mkdir -p "$(dirname "${target}")" 2>/dev/null || return 0
  if progress_page_render "$@" >"${target}.tmp" 2>/dev/null; then
    mv -f "${target}.tmp" "${target}" 2>/dev/null || rm -f "${target}.tmp"
  else
    rm -f "${target}.tmp"
  fi
  return 0
}
