"""Structural gates for the macOS app build (TGT-026, TGT-028); the macOS job builds and runs it."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging" / "macos"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pins(lines: list[str]) -> dict[str, str]:
    pins = {}
    for line in lines:
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", line.strip())
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def test_lock_inputs_match_the_project_pins() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = _pins(project["dependencies"] + project["optional-dependencies"]["desktop"] + project["optional-dependencies"]["build-macos"])
    inputs = _pins((PACKAGING / "requirements-macos-arm64.in").read_text(encoding="utf-8").splitlines())
    assert inputs == declared


def test_lock_is_fully_hash_pinned_and_covers_every_input() -> None:
    text = (PACKAGING / "requirements-macos-arm64.lock").read_text(encoding="utf-8")
    entries = re.split(r"\n(?=[A-Za-z0-9])", text[text.index("\n", text.index("uv pip compile")) + 1 :].strip())
    locked = {}
    for entry in entries:
        match = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)", entry)
        assert match, entry[:80]
        assert "--hash=sha256:" in entry, f"{match.group(1)} is not hash-pinned"
        locked[match.group(1).lower()] = match.group(2)
    inputs = _pins((PACKAGING / "requirements-macos-arm64.in").read_text(encoding="utf-8").splitlines())
    for name, version in inputs.items():
        assert locked.get(name) == version, name


def test_minimum_os_agrees_across_build_spec_and_release_class() -> None:
    build = _load_script("build_macos_app")
    manifest = json.loads((ROOT / "_architecture/release/release_scope_manifest_v1.json").read_text(encoding="utf-8"))
    spec_text = (PACKAGING / "mucaro.spec").read_text(encoding="utf-8")
    assert build.MINIMUM_OS == manifest["release_class"]["minimum_os"] == "14.0"
    assert f'"MUCARO_APP_MINIMUM_OS", "{build.MINIMUM_OS}"' in spec_text
    assert '"NSAllowsLocalNetworking": True' in spec_text and "NSAllowsArbitraryLoads" not in spec_text


def test_spec_bundles_resources_that_exist() -> None:
    spec_text = (PACKAGING / "mucaro.spec").read_text(encoding="utf-8")
    for relative in ("schemas", "pyproject.toml", "backend/app/gis_grids", "packaging/macos/desktop_entry.py"):
        assert (ROOT / relative).exists(), relative
    for fragment in (
        'ROOT / "schemas"',
        'ROOT / "frontend" / "dist"',
        'ROOT / "backend" / "app" / "gis_grids"',
        "desktop_entry.py",
        "MUCARO_BUILD_IDENTITY_FILE",
    ):
        assert fragment in spec_text
    build_text = (ROOT / "scripts" / "build_macos_app.py").read_text(encoding="utf-8")
    assert '"schema_version": "mucaro-build-identity/1.0"' in build_text
    assert '"source_head": head' in build_text
    assert '"MUCARO_BUILD_IDENTITY_FILE": str(build_identity)' in build_text


def test_icon_renderer_emits_a_valid_png() -> None:
    build = _load_script("build_macos_app")
    data = build.render_icon_png(48)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, depth, color_type = struct.unpack(">IIBB", data[16:26])
    assert (width, height, depth, color_type) == (48, 48, 8, 6)


def test_build_refuses_a_non_macos_host_without_structural_mode() -> None:
    if sys.platform == "darwin":
        return
    result = subprocess.run([sys.executable, "scripts/build_macos_app.py"], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 2
    assert "ERROR_MACOS_REQUIRED" in result.stderr


def test_frozen_entry_runs_only_allow_listed_helpers(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MUCARO_AI_")}
    env["PYTHONPATH"] = str(ROOT)
    entry = str(PACKAGING / "desktop_entry.py")
    # An allow-listed helper runs as that module: the local worker rejects an empty request in its protocol.
    worker = subprocess.run([sys.executable, entry, "-m", "backend.app.local_worker_entry"], input=b"{}", cwd=tmp_path, env=env, capture_output=True, timeout=60, check=False)
    assert worker.returncode == 2
    assert b"ERROR_LOCAL_WORKER_PROTOCOL" in worker.stderr
    # Any other module name is not executed; the launch is treated as an app launch.
    other = subprocess.run(
        [sys.executable, entry, "-m", "http.server", "--smoke", "--data-dir", str(tmp_path / "data")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert other.returncode == 0, other.stderr
    assert "DESKTOP_SMOKE_OK" in other.stdout


def test_macos_workflow_installs_from_the_hashed_lock_and_runs_the_e2e() -> None:
    workflow = (ROOT / ".github/workflows/macos-app.yml").read_text(encoding="utf-8")
    assert "runs-on: macos-14" in workflow
    assert "--require-hashes" in workflow and "requirements-macos-arm64.lock" in workflow
    assert "scripts/build_macos_app.py" in workflow and "scripts/verify_macos_app.py" in workflow
