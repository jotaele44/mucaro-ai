"""Negative regressions for dependency versions with known release-blocking advisories."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging"


def test_compromised_certifi_2026_7_22_is_absent_from_all_packaging_locks() -> None:
    locks = sorted(PACKAGING.rglob("*.lock"))
    assert locks, "expected packaging lockfiles"
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in locks
        if "certifi==2026.7.22" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == [], f"known-vulnerable certifi pin remains in: {offenders}"


def test_certifi_security_fix_is_hash_pinned_where_present() -> None:
    locks = sorted(PACKAGING.rglob("*.lock"))
    certifi_locks = []
    for path in locks:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "certifi==" not in text:
            continue
        certifi_locks.append(path.relative_to(ROOT).as_posix())
        stanza = text.split("certifi==", 1)[1].split("\n", 4)[:3]
        joined = "\n".join(stanza)
        assert "certifi==2026.10.19" not in joined  # version prefix was consumed by split
        assert "2026.10.19" in stanza[0]
        assert "--hash=sha256:035c1e85572b141bf1c3a3eff0e33a8eb74917522f2559075ff8d2fea780a1ae" in joined
        assert "--hash=sha256:42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b" in joined
    assert certifi_locks, "expected certifi in at least one packaging lock"


def test_one_click_lock_generator_preserves_bounded_security_exceptions() -> None:
    pins = (ROOT / "scripts" / "one_click_pins.env").read_text(encoding="utf-8")
    generator = (ROOT / "scripts" / "lock_python_deps.py").read_text(encoding="utf-8")
    assert "PY_EXCLUDE_NEWER=2026-09-25T00:00:00Z" in pins
    assert "PY_SECURITY_CERTIFI_VERSION=2026.10.19" in pins
    assert "PY_EXCLUDE_NEWER_CERTIFI=2026-10-21T00:00:00Z" in pins
    assert "PY_SECURITY_PYPDF_VERSION=6.21.2" in pins
    assert "PY_EXCLUDE_NEWER_PYPDF=2026-10-24T23:59:59Z" in pins
    assert "PY_SECURITY_SETUPTOOLS_VERSION=84.1.0" in pins
    assert "PY_EXCLUDE_NEWER_SETUPTOOLS=2026-10-20T00:00:00Z" in pins
    assert '("certifi", "PY_SECURITY_CERTIFI_VERSION", "PY_EXCLUDE_NEWER_CERTIFI")' in generator
    assert '("pypdf", "PY_SECURITY_PYPDF_VERSION", "PY_EXCLUDE_NEWER_PYPDF")' in generator
    assert '("setuptools", "PY_SECURITY_SETUPTOOLS_VERSION", "PY_EXCLUDE_NEWER_SETUPTOOLS")' in generator
    assert "--exclude-newer-package" in generator
    assert "--upgrade-package" in generator


def test_pypdf_security_fix_is_pinned_and_hashed_in_all_governed_locks() -> None:
    expected_hashes = {
        "1d44d1a3a200f25062adff0085b02040e4646431ac2ccac22e42679e91466084",
        "0a3e1c5c886a1be03646c16a04827cc166c2a0575c971b26a4bf89686551c2d4",
    }
    locks = sorted(PACKAGING.rglob("*.lock"))
    pypdf_locks = []
    for path in locks:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "pypdf==" not in text:
            continue
        pypdf_locks.append(path.relative_to(ROOT).as_posix())
        assert "pypdf==6.19.0" not in text
        assert "pypdf==6.21.2" in text
        stanza = text.split("pypdf==6.21.2", 1)[1].split("\n", 4)[:3]
        joined = "\n".join(stanza)
        for digest in expected_hashes:
            assert f"--hash=sha256:{digest}" in joined
    assert pypdf_locks, "expected pypdf in governed packaging locks"


def test_setuptools_84_0_0_security_regression_is_absent_and_fixed_pin_is_hashed() -> None:
    expected_hashes = {
        "23657205b63d4d31395ab917815247f694aafad28cd21316bf5fd45a51048ea0",
        "5231689c6ac8156cd155cfb98c3fd6fb252c3e7e9bd3d78b64d3c55f132624c9",
    }
    locks = sorted(PACKAGING.rglob("*.lock"))
    setuptools_locks = []
    for path in locks:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "setuptools==84.0.0" not in text, f"vulnerable setuptools remains in {path.relative_to(ROOT)}"
        if "setuptools==" not in text:
            continue
        setuptools_locks.append(path.relative_to(ROOT).as_posix())
        assert "setuptools==84.1.0" in text
        stanza = text.split("setuptools==84.1.0", 1)[1].split("\n", 4)[:3]
        joined = "\n".join(stanza)
        for digest in expected_hashes:
            assert f"--hash=sha256:{digest}" in joined
    assert setuptools_locks, "expected setuptools in governed packaging locks"

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    one_click_build = (ROOT / "packaging" / "one_click" / "build-requirements.in").read_text(encoding="utf-8")
    macos_lock = (ROOT / "packaging" / "macos" / "requirements-macos-arm64.lock").read_text(encoding="utf-8")
    assert 'requires = ["setuptools==84.1.0"]' in pyproject
    assert "setuptools==84.1.0" in one_click_build
    assert "--exclude-newer-package setuptools=2026-10-20T00:00:00Z" in macos_lock
    assert "--upgrade-package setuptools==84.1.0" in macos_lock


def test_vite_7_3_6_security_regression_is_absent() -> None:
    package_json = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    package_lock = (ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    assert '"vite": "7.3.6"' not in package_json
    assert '"vite": "7.3.6"' not in package_lock
    assert "vite-7.3.6.tgz" not in package_lock
    assert '"vite": "7.3.7"' in package_json
    assert "vite-7.3.7.tgz" in package_lock
