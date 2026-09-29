from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="POSIX bootstrap script")

# Stub interpreter: answers the version probes bootstrap_macos.sh makes, then fails anything else
# (so a selected stub stops the script at the venv step instead of installing anything).
STUB_TEMPLATE = """#!/bin/sh
case "$2" in
  *platform*) echo {version}; exit 0 ;;
  *"<= sys.version_info"*) exit {supported_rc} ;;
  *">= (3, 14)"*) exit {too_new_rc} ;;
esac
exit 1
"""


def _stub(bin_dir: Path, name: str, version: str) -> None:
    major, minor = (int(part) for part in version.split(".")[:2])
    supported = (3, 11) <= (major, minor) < (3, 14)
    too_new = (major, minor) >= (3, 14)
    path = bin_dir / name
    path.write_text(
        STUB_TEMPLATE.format(version=version, supported_rc=0 if supported else 1, too_new_rc=0 if too_new else 1),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_bootstrap(tmp_path: Path, interpreters: dict[str, str]) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "bootstrap_macos.sh", project / "scripts" / "bootstrap_macos.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to(shutil.which("dirname"))
    for name, version in interpreters.items():
        _stub(bin_dir, name, version)
    return subprocess.run(
        [shutil.which("bash"), str(project / "scripts" / "bootstrap_macos.sh")],
        cwd=project,
        env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_bootstrap_rejects_python_newer_than_supported(tmp_path):
    proc = _run_bootstrap(tmp_path, {"python3": "3.14.0"})

    assert proc.returncode == 2
    assert "ERROR_PYTHON_UNSUPPORTED found=3.14.0 supported=3.11-3.13" in proc.stderr
    assert not (tmp_path / "project" / ".venv").exists()


def test_bootstrap_rejects_python_older_than_supported(tmp_path):
    proc = _run_bootstrap(tmp_path, {"python3": "3.9.6"})

    assert proc.returncode == 2
    assert "ERROR_PYTHON_TOO_OLD found=3.9.6" in proc.stderr


def test_bootstrap_prefers_supported_versioned_python_over_newer_python3(tmp_path):
    proc = _run_bootstrap(tmp_path, {"python3": "3.14.0", "python3.12": "3.12.8"})

    assert "PYTHON_SELECTED python3.12 3.12.8" in proc.stdout
    assert "ERROR_PYTHON_UNSUPPORTED" not in proc.stderr
