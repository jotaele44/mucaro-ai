from __future__ import annotations

import subprocess
import sys


def test_launch_app_self_test():
    proc = subprocess.run(
        [sys.executable, "scripts/launch_app.py", "--self-test"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0
    assert "LAUNCH_APP_SELF_TEST_OK" in proc.stdout
