from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.app.portable_runtime import (
    PortableRuntimeError,
    build_portable_macos_app,
    verify_portable_macos_app,
)


def git(root: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode == 0, proc.stderr


def fixture_project(root: Path) -> tuple[Path, Path]:
    (root / "backend").mkdir()
    (root / "backend" / "marker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "launch_app.py").write_text("print('launch')\n", encoding="utf-8")
    (root / "frontend" / "dist").mkdir(parents=True)
    (root / "frontend" / "dist" / "index.html").write_text("<html>Mucaro</html>\n", encoding="utf-8")
    git(root, "init")
    git(root, "config", "user.email", "mucaro-tests@example.invalid")
    git(root, "config", "user.name", "Mucaro Tests")
    git(root, "add", "backend/marker.py", "scripts/launch_app.py")
    git(root, "commit", "-m", "fixture")

    runtime = root / "runtime-input"
    (runtime / "bin").mkdir(parents=True)
    py = runtime / "bin" / "python3"
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py.chmod(py.stat().st_mode | 0o755)
    (runtime / "lib").mkdir()
    (runtime / "lib" / "runtime.txt").write_text("runtime\n", encoding="utf-8")
    return root, runtime


def test_portable_bundle_is_bundle_relative_and_hash_verified(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    report = build_portable_macos_app(root, runtime, output_dir=root / "out")
    assert report["status"] == "PORTABLE_APP_VERIFIED"
    assert all(row["status"] == "PASS" for row in report["checks"])
    state = report["distribution_state"]
    assert state["embedded_runtime"] is True
    assert state["bundle_relative_launch"] is True
    assert state["signed"] is False
    assert state["clean_machine_executed"] is False

    app = Path(report["app_path"])
    launcher = (app / "Contents" / "MacOS" / "mucaro-ai").read_text(encoding="utf-8")
    assert str(root) not in launcher
    assert "${RESOURCES}/runtime/bin/python3" in launcher
    assert verify_portable_macos_app(app)["status"] == "PORTABLE_APP_VERIFIED"


def test_runtime_symlink_fails_closed(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    target = runtime / "lib" / "runtime.txt"
    link = runtime / "lib" / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(PortableRuntimeError) as exc:
        build_portable_macos_app(root, runtime, output_dir=root / "out")
    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_SYMLINK"


def test_runtime_python_must_be_executable(tmp_path: Path) -> None:
    root, runtime = fixture_project(tmp_path)
    py = runtime / "bin" / "python3"
    py.chmod(0o644)
    with pytest.raises(PortableRuntimeError) as exc:
        build_portable_macos_app(root, runtime, output_dir=root / "out")
    assert exc.value.status == "ERROR_PORTABLE_RUNTIME_PYTHON"
