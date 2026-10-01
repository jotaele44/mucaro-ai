from __future__ import annotations

import re
import struct
from pathlib import Path

from scripts import build_launchers

REPO = Path(__file__).resolve().parents[2]
WINDOWS_SRC = REPO / "packaging" / "launchers" / "windows"
ICO = REPO / "packaging" / "icons" / "mucaro.ico"


# The Windows launcher sources are kept as the base for the queued Windows double-click launcher;
# nothing built from them is committed except the icon.


def test_build_requirements_are_hash_pinned():
    text = (REPO / "packaging" / "launchers" / "build-requirements.txt").read_text(encoding="utf-8")
    requirements = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", text, flags=re.MULTILINE)
    assert {name for name, _ in requirements} == {"ziglang", "pillow"}
    for block in re.split(r"\n(?=[A-Za-z])", text.split("\n", 2)[-1]):
        if "==" in block:
            assert "--hash=sha256:" in block, block


def test_resource_script_embeds_the_committed_icon():
    rc = (WINDOWS_SRC / "mucaro_launcher.rc").read_text(encoding="utf-8")
    icon = re.search(r'^1 ICON "([^"]+)"', rc, flags=re.MULTILINE)
    assert icon is not None
    assert (WINDOWS_SRC / icon.group(1)).resolve() == ICO
    for name in ("mucaro_launcher.c", "mucaro_launcher.manifest"):
        assert (WINDOWS_SRC / name).is_file()


def test_icon_is_a_valid_multi_size_ico():
    data = ICO.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    assert count >= 6


def test_build_writes_the_exe_only_under_build():
    assert build_launchers.EXE.relative_to(REPO).parts[0] == "build"
    assert build_launchers.ICO == ICO


def test_no_launcher_binary_is_committed_at_the_top_level():
    assert not [path.name for path in REPO.iterdir() if path.suffix == ".exe" or path.name.startswith("Start Mucaro AI")]
