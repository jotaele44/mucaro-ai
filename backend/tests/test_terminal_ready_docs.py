from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


REQUIRED = [
    "TERMINAL_READY.txt",
    "terminal_ready/01_DEVELOPMENT.txt",
    "terminal_ready/02_SECURITY.txt",
    "terminal_ready/03_RELEASE.txt",
    "terminal_ready/04_RECOVERY.txt",
]


def test_terminal_ready_document_set_is_present_and_nonempty() -> None:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    assert missing == []
    empty = [path for path in REQUIRED if not (ROOT / path).read_text(encoding="utf-8").strip()]
    assert empty == []


def test_terminal_ready_root_is_a_complete_index() -> None:
    text = (ROOT / "TERMINAL_READY.txt").read_text(encoding="utf-8")
    assert "TERMINAL_READY_VERSION: 1" in text
    assert "Artifact execution: NOT_APPLICABLE" in text
    assert "MACOS / LINUX — QUICK START" in text
    assert "WINDOWS POWERSHELL — QUICK START" in text
    assert "ARTIFACT PATH / SHA-256 CHECKS" in text
    assert "FAILURE HANDLING" in text
    assert "SMOKE TESTS" in text
    assert "python3 scripts/certify_mucaro_100.py --strict --format text" in text
    for path in REQUIRED[1:]:
        assert path in text


def test_release_terminal_instructions_preserve_exact_artifact_identity() -> None:
    text = (ROOT / "terminal_ready/03_RELEASE.txt").read_text(encoding="utf-8")
    assert "shasum -a 256 <artifact-path>" in text
    assert "certutil -hashfile <artifact-path> SHA256" in text
    assert "final source HEAD" in text
    assert "MUCARO_100 denominators agree" in text
