"""The identity table in docs/release/RELEASE_APP_IDENTITIES.md must match the code."""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

from backend.app import packaging, portable_runtime

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "release" / "RELEASE_APP_IDENTITIES.md"


def _documented() -> dict[str, str]:
    rows = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| [^|]+ \| [^|]+ \| `([^`]+\.app)` \| `([^`]+)` \|", line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def _constant(relative: str, name: str) -> str:
    text = (ROOT / relative).read_text(encoding="utf-8")
    match = re.search(rf'^{name} = "([^"]+)"$', text, re.MULTILINE)
    assert match, f"{name} not found in {relative}"
    return match.group(1)


def test_documented_identities_match_code_and_are_disjoint() -> None:
    documented = _documented()
    release_name = _constant("scripts/build_macos_app.py", "APP_NAME") + ".app"
    release_id = _constant("scripts/build_macos_app.py", "BUNDLE_ID")
    portable_plist = re.search(
        r"<key>CFBundleIdentifier</key><string>([^<]+)</string>",
        (ROOT / "backend" / "app" / "portable_runtime.py").read_text(encoding="utf-8"),
    )
    assert portable_plist
    expected = {
        release_name: release_id,
        packaging.MACOS_APP_BUNDLE_NAME: packaging.MACOS_APP_BUNDLE_ID,
        portable_runtime.PORTABLE_APP_NAME: portable_plist.group(1),
    }
    assert documented == expected
    assert len(set(expected.values())) == len(expected) == 3
    assert f'"{release_id}"' in (ROOT / "packaging" / "macos" / "mucaro.spec").read_text(encoding="utf-8")


def test_tracked_setup_launcher_uses_the_documented_identity() -> None:
    plist = plistlib.loads((ROOT / packaging.MACOS_APP_BUNDLE_NAME / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleIdentifier"] == _documented()[packaging.MACOS_APP_BUNDLE_NAME]
