#!/bin/bash
# Double-click this file (macOS) to set up and start Mucaro AI. No other steps are needed:
# the first run installs private copies of Python and Node inside this folder, later runs
# start in seconds. Linux: run it from a terminal. Windows: double-click "Mucaro AI.cmd".
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 2
exec /bin/bash scripts/one_click.sh ${1+"$@"}
