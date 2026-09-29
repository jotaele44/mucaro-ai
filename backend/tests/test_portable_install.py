from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.app.portable_install import PortableInstallError, install_portable_macos_app, rollback_portable_macos_app
from backend.app.portable_runtime import build_portable_macos_app


def git(root: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode == 0, proc.stderr


def make_source(root: Path, marker: str) -> Path:
    project = root / f"project-{marker}"
    (project / "backend").mkdir(parents=True)
    (project / "backend" / "marker.py").write_text(f"VALUE = {marker!r}\n", encoding="utf-8")
    (project / "scripts").mkdir()
    (project / "scripts" / "launch_app.py").write_text("print('launch')\n", encoding="utf-8")
    (project / "frontend" / "dist").mkdir(parents=True)
    (project / "frontend" / "dist" / "index.html").write_text(f"<html>{marker}</html>\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        '[project]\\nname = "mucaro-ai"\\nversion = "0.1.23"\\n', encoding="utf-8"
    )
    git(project, "init")
    git(project, "config", "user.email", "mucaro-tests@example.invalid")
    git(project, "config", "user.name", "Mucaro Tests")
    git(project, "add", "backend/marker.py", "scripts/launch_app.py", "pyproject.toml")
    git(project, "commit", "-m", marker)

    runtime = project / "runtime"
    (runtime / "bin").mkdir(parents=True)
    py = runtime / "bin" / "python3"
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py.chmod(py.stat().st_mode | 0o755)
    return Path(build_portable_macos_app(project, runtime, output_dir=project / "out")["app_path"])


def test_install_reinstall_and_rollback_are_hash_verified(tmp_path: Path) -> None:
    v1 = make_source(tmp_path, "v1")
    v2 = make_source(tmp_path, "v2")
    install_root = tmp_path / "Applications"

    first = install_portable_macos_app(v1, install_root=install_root)
    assert first["status"] == "PORTABLE_INSTALL_OK"
    assert first["rollback"]["available"] is False
    assert all(first["invariants"].values())

    second = install_portable_macos_app(v2, install_root=install_root)
    assert second["status"] == "PORTABLE_INSTALL_OK"
    assert second["rollback"]["available"] is True
    assert second["previous_installation"]["head_sha"] != second["installation"]["head_sha"]
    assert all(second["invariants"].values())

    rolled = rollback_portable_macos_app(install_root=install_root)
    assert rolled["status"] == "PORTABLE_ROLLBACK_OK"
    assert rolled["installation"]["head_sha"] == first["installation"]["head_sha"]
    assert all(rolled["invariants"].values())


def test_rollback_without_backup_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PortableInstallError) as exc:
        rollback_portable_macos_app(install_root=tmp_path / "Applications")
    assert exc.value.status == "ERROR_PORTABLE_ROLLBACK_UNAVAILABLE"
