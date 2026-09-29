"""Mucaro AI.app, the double-clickable macOS app committed in this folder, and its icon and helper files.

Runs anywhere: the launcher is a bash script, the icon is checked with the standard library, and
no dialog is shown (MUCARO_APP_DIALOGS=0; on Linux there is no osascript anyway).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

import scripts.build_app_icon as build_app_icon
import scripts.build_macos_app as build_macos_app

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "Mucaro AI.app"
CONTENTS = BUNDLE / "Contents"
LAUNCHER = CONTENTS / "MacOS" / "Mucaro AI"
ICON = CONTENTS / "Resources" / "AppIcon.icns"
BUNDLE_FILES = ["Contents/Info.plist", "Contents/MacOS/Mucaro AI", "Contents/PkgInfo", "Contents/Resources/AppIcon.icns"]

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    proc = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1) or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    return proc


# --- the bundle ---------------------------------------------------------------------------------


def test_info_plist_names_the_launcher_and_the_icon():
    info = plistlib.loads((CONTENTS / "Info.plist").read_bytes())

    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleExecutable"] == LAUNCHER.name and LAUNCHER.is_file()
    assert info["CFBundleIconFile"] == ICON.stem and ICON.is_file()
    assert info["CFBundleName"] == info["CFBundleDisplayName"] == "Mucaro AI"
    # Its own identity: the packaged release app is ai.mucaro.app.
    assert info["CFBundleIdentifier"] == "ai.mucaro.launcher" != build_macos_app.BUNDLE_ID
    # The oldest macOS the setup supports (Intel); stage 0 checks the Apple silicon minimum itself.
    assert info["LSMinimumSystemVersion"] == "13.0"
    assert info["NSHighResolutionCapable"] is True
    assert info["NSAppTransportSecurity"] == {"NSAllowsLocalNetworking": True}
    assert (CONTENTS / "PkgInfo").read_bytes() == b"APPL????"


def test_bundle_tracks_only_its_four_files():
    proc = _git("ls-files", "--", BUNDLE.name)
    tracked = sorted(Path(line).relative_to(BUNDLE.name).as_posix() for line in proc.stdout.splitlines())
    assert tracked == BUNDLE_FILES


def test_git_keeps_the_launcher_lf_and_the_icon_binary():
    proc = _git("check-attr", "eol", "binary", "--", LAUNCHER.relative_to(REPO).as_posix(), ICON.relative_to(REPO).as_posix())
    assert "Mucaro AI.app/Contents/MacOS/Mucaro AI: eol: lf" in proc.stdout
    assert "Mucaro AI.app/Contents/Resources/AppIcon.icns: binary: set" in proc.stdout
    assert b"\r\n" not in LAUNCHER.read_bytes()


def test_window_state_in_data_is_git_ignored():
    for path in ("data/instance.json", "data/.instance.json.4242.tmp", "data/instance.lock", "data/logs/service.log", "data/webview/cache"):
        assert _git("check-ignore", "-q", path).returncode == 0, path
    # The sample documents stay tracked.
    assert _git("check-ignore", "-q", "data/input/sample_mucaro_ai.txt").returncode == 1


# --- the launcher -------------------------------------------------------------------------------


def _checkout(parent: Path) -> tuple[Path, Path]:
    """A copy of the app next to a stand-in for scripts/one_click.sh that records how it was started."""
    folder = parent / "mucaro-ai"
    (folder / "scripts").mkdir(parents=True)
    shutil.copytree(BUNDLE, folder / BUNDLE.name)
    record = folder / "handover.txt"
    stub = folder / "scripts" / "one_click.sh"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" "$MUCARO_APP_BUNDLE" > "{record}"\n', encoding="utf-8")
    return folder, record


def _open(app: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(app.parent), "MUCARO_APP_DIALOGS": "0"}
    return subprocess.run(["bash", str(app / "Contents" / "MacOS" / "Mucaro AI"), *args], env=env, capture_output=True, text=True, timeout=60, check=False)


@needs_bash
def test_launcher_hands_over_to_the_setup_of_its_own_folder(tmp_path):
    folder, record = _checkout(tmp_path / "my projects")

    proc = _open(folder / BUNDLE.name, "-psn_0_12345")

    assert proc.returncode == 0, proc.stderr
    args, bundle = record.read_text(encoding="utf-8").splitlines()
    # Only desktop mode is asked for; nothing Finder adds (-psn_*) is passed on.
    assert args == "--desktop"
    assert Path(bundle) == (folder / BUNDLE.name).resolve()


@needs_bash
def test_launcher_refuses_a_translocated_copy(tmp_path):
    folder, record = _checkout(tmp_path / "private" / "var" / "folders" / "AppTranslocation" / "7D2F")

    proc = _open(folder / BUNDLE.name)

    assert proc.returncode == 1
    assert not record.exists()


@needs_bash
def test_launcher_refuses_to_run_outside_its_folder(tmp_path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    shutil.copytree(BUNDLE, apps / BUNDLE.name)

    proc = _open(apps / BUNDLE.name)

    assert proc.returncode == 1


def test_launcher_names_the_fixes_for_its_two_failures():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "*/AppTranslocation/*)" in text
    assert "Fix-Gatekeeper.command" in text
    assert 'exec /bin/bash "${ROOT}/scripts/one_click.sh" --desktop' in text
    assert '"$@"' not in text


def test_gatekeeper_fix_only_clears_the_quarantine_of_this_folder():
    text = (REPO / "Fix-Gatekeeper.command").read_text(encoding="utf-8")
    code = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert "/usr/bin/xattr -dr com.apple.quarantine . 2>/dev/null || true" in code
    assert not any(word in line for line in code for word in ("codesign", "sudo", "spctl", "rm "))


# --- the icon -----------------------------------------------------------------------------------


def test_app_icon_is_a_complete_png_icns_under_the_size_gate():
    data = ICON.read_bytes()

    assert build_app_icon.check(data) == []
    assert [kind for kind, _ in build_app_icon.parse_icns(data)] == [kind for kind, _ in build_app_icon.ICNS_TYPES]
    assert len(data) < build_app_icon.MAX_BYTES


def test_icns_writer_round_trips_the_owl_and_detects_damage():
    small = build_app_icon.build_icns(types=(("icp4", 16), ("ic11", 32)))
    entries = build_app_icon.parse_icns(small)

    assert [(kind, build_app_icon.png_size(png)) for kind, png in entries] == [("icp4", (16, 16)), ("ic11", (32, 32))]
    assert all(build_app_icon.png_pixels_ok(png) for _, png in entries)
    assert entries[0][1] == build_macos_app.render_icon_png(16)

    png = entries[1][1]
    damaged = png[:-20] + bytes([png[-20] ^ 0xFF]) + png[-19:]
    assert not build_app_icon.png_pixels_ok(damaged)
    with pytest.raises(ValueError):
        build_app_icon.parse_icns(small[:-5])
    assert any("missing ICNS element ic10" in problem for problem in build_app_icon.check(small))


def test_icon_check_command_accepts_the_committed_icon(capsys):
    assert build_app_icon.main(["--check"]) == 0
    assert "APP_ICON_OK" in capsys.readouterr().out


def test_app_launcher_and_helper_are_not_windows_line_endings():
    for path in (LAUNCHER, REPO / "Fix-Gatekeeper.command"):
        assert b"\r" not in path.read_bytes(), path
        assert os.access(path, os.X_OK), path
