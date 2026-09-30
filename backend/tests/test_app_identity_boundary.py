from __future__ import annotations

import plistlib
from pathlib import Path

from backend.app.packaging import MACOS_APP_BUNDLE_NAME

ROOT = Path(__file__).resolve().parents[2]


def test_source_bootstrap_and_release_app_have_distinct_canonical_identities() -> None:
    setup_app = ROOT / "Mucaro AI Setup.app"
    assert setup_app.is_dir()
    assert not (ROOT / "Mucaro AI.app").exists()

    setup_info = plistlib.loads((setup_app / "Contents" / "Info.plist").read_bytes())
    assert setup_info["CFBundleName"] == "Mucaro AI Setup"
    assert setup_info["CFBundleDisplayName"] == "Mucaro AI Setup"
    assert setup_info["CFBundleIdentifier"] == "ai.mucaro.source-launcher"
    assert MACOS_APP_BUNDLE_NAME == "Mucaro AI Setup.app"

    release_spec = (ROOT / "packaging" / "macos" / "mucaro.spec").read_text(encoding="utf-8")
    assert 'name="Mucaro AI.app"' in release_spec
    assert '"ai.mucaro.app"' in release_spec

    assert MACOS_APP_BUNDLE_NAME != "Mucaro AI.app"
    assert setup_info["CFBundleIdentifier"] != "ai.mucaro.app"
