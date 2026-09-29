#!/bin/bash
# On a Mac, open Mucaro AI.app instead: it runs this same setup without a Terminal window and shows
# Mucaro AI in its own window. This file still works on a Mac (setup output appears in Terminal and
# the app opens in the browser). Linux: run it from a terminal. Windows: double-click
# "Mucaro AI.cmd". The first run installs private copies of Python and Node inside this folder;
# later runs start in seconds.
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 2
exec /bin/bash scripts/one_click.sh ${1+"$@"}
