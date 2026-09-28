#!/bin/bash
# Terminal fallback for the double-click setup (the normal way is to double-click "Mucaro AI.app").
# Shows setup progress in a Terminal window; closing the window stops Mucaro AI.
# Linux: run this file from a terminal.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
exec /bin/bash scripts/one_click.sh ${1+"$@"}
