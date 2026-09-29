#!/bin/bash
# One-time macOS fix, needed only when this mucaro-ai folder came from a downloaded ZIP file.
#
# macOS marks everything downloaded from the internet as quarantined. For a quarantined folder it
# refuses to open Mucaro AI.app ("cannot be opened because Apple cannot check it for malicious
# software"), or runs a read-only copy of the app that cannot reach this folder. This removes that
# mark from the files in this folder and changes nothing else. A folder made with git clone or
# GitHub Desktop is never quarantined and does not need it.
#
# Double-click this file once, then open Mucaro AI.app.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

echo "Removing the macOS download quarantine from:"
echo "  $(pwd -P)"
/usr/bin/xattr -dr com.apple.quarantine . 2>/dev/null || true
echo ""
echo "Done. Open Mucaro AI.app in this folder."
if [ -t 0 ]; then
  read -r -p "Press Enter to close this window..." _ || true
fi
