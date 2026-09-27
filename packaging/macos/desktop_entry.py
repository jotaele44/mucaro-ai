"""Entry point of the frozen Mucaro AI app.

In a frozen app ``sys.executable`` is the app itself, so helper subprocesses started
as ``sys.executable -m <module>`` arrive here. Only allow-listed helper modules are
dispatched; every other launch opens the desktop shell.
"""

from __future__ import annotations

import runpy
import sys

HELPER_MODULES = frozenset({"backend.app.local_worker_entry"})


def _entry() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "-m" and sys.argv[2] in HELPER_MODULES:
        module = sys.argv[2]
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return
    from backend.app.desktop_shell import main

    main()


if __name__ == "__main__":
    _entry()
