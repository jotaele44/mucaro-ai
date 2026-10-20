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
