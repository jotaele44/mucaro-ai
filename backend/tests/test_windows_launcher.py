from __future__ import annotations

import hashlib
import re
import shutil
import struct
import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts import build_launchers, launch_app

REPO = Path(__file__).resolve().parents[2]
EXE = REPO / "Mucaro AI.exe"
WINDOWS_SRC = REPO / "packaging" / "launchers" / "windows"
ICO = REPO / "packaging" / "icons" / "mucaro.ico"
NATIVE_START = REPO / "scripts" / "native_start.ps1"
PWSH = shutil.which("pwsh")


def _project_version() -> str:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def _pe_header(data: bytes) -> tuple[int, int]:
    assert data[:2] == b"MZ"
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe : pe + 4] == b"PE\0\0"
    machine = struct.unpack_from("<H", data, pe + 4)[0]
    subsystem = struct.unpack_from("<H", data, pe + 24 + 68)[0]
    return machine, subsystem


def test_top_folder_has_the_windows_launcher_next_to_the_mac_app():
    assert EXE.is_file()
    assert (REPO / "Mucaro AI Setup.app").is_dir()
    assert (REPO / "Mucaro AI.cmd").is_file()
    assert not [path.name for path in REPO.iterdir() if path.name.startswith("Start Mucaro AI")]
    assert build_launchers.EXE == EXE


def test_launcher_is_a_small_x64_gui_executable():
    data = EXE.read_bytes()
    machine, subsystem = _pe_header(data)
    assert machine == 0x8664  # x86-64; Windows 11 on ARM runs it under emulation
    assert subsystem == 2  # GUI: no console window flashes on double-click
    assert len(data) < 1_000_000  # certify_release large_file_scan limit


def test_launcher_matches_its_recorded_build_hash():
    recorded = (WINDOWS_SRC / "BUILD_SHA256").read_text(encoding="utf-8").split()
    assert recorded[1:] == ["Mucaro", "AI.exe"]
    assert hashlib.sha256(EXE.read_bytes()).hexdigest() == recorded[0]


def test_launcher_source_starts_the_silent_windows_entry_point():
    source = (WINDOWS_SRC / "mucaro_launcher.c").read_text(encoding="utf-8")
    assert "scripts\\\\native_start.ps1" in source
    assert "CREATE_NO_WINDOW" in source
    assert "-WindowStyle" not in source  # a process flag hides the console, not a PowerShell switch
    assert "--ui" not in source
    version = _project_version()
    rc = (WINDOWS_SRC / "mucaro_launcher.rc").read_text(encoding="utf-8")
    assert f'VALUE "ProductVersion", "{version}"' in rc
    assert 'VALUE "OriginalFilename", "Mucaro AI.exe"' in rc
    numeric_version = ",".join(version.split(".")) + ",0"
    assert f"FILEVERSION {numeric_version}" in rc
    assert f"PRODUCTVERSION {numeric_version}" in rc
    manifest = (WINDOWS_SRC / "mucaro_launcher.manifest").read_text(encoding="utf-8")
    assert f'version="{version}.0"' in manifest
    icon = re.search(r'^1 ICON "([^"]+)"', rc, flags=re.MULTILINE)
    assert icon is not None and (WINDOWS_SRC / icon.group(1)).resolve() == ICO


def test_icon_is_a_valid_multi_size_ico_made_from_the_mac_owl():
    reserved, kind, count = struct.unpack_from("<HHH", ICO.read_bytes(), 0)
    assert (reserved, kind) == (0, 1) and count == len(build_launchers.ICO_SIZES)
    assert build_launchers.ICON_SOURCE.is_file()


def test_build_requirements_are_hash_pinned():
    text = (REPO / "packaging" / "launchers" / "build-requirements.txt").read_text(encoding="utf-8")
    requirements = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", text, flags=re.MULTILINE)
    assert {name for name, _ in requirements} == {"ziglang", "pillow"}
    for block in re.split(r"\n(?=[A-Za-z])", text.split("\n", 2)[-1]):
        if "==" in block:
            assert "--hash=sha256:" in block, block


def test_windows_entry_point_runs_setup_silently_and_owns_the_failure_dialog():
    data = NATIVE_START.read_bytes()
    data.decode("ascii")  # Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page
    text = data.decode("ascii")
    code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not any("2>&1" in line for line in code_lines)
    assert 'scripts\\one_click_windows.ps1' in text and '"--native"' in text
    assert '$env:MUCARO_SILENT_LAUNCH = "1"' in text
    assert "-RedirectStandardOutput" in text and "-RedirectStandardError" in text
    assert "WScript.Shell" in text
    # launch_app returns this code when WebView2 is missing; the dialog then names the download.
    assert "$WebView2MissingCode = 11" in text
    assert launch_app.native_window.WEBVIEW2_DOWNLOAD in text


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 not installed")
def test_windows_entry_point_parses():
    command = (
        "$errors = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{NATIVE_START}', [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
    )
    proc = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
