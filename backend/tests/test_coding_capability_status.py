from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_coding_status_document_does_not_overclaim_execution_or_certification() -> None:
    text = (ROOT / "docs" / "CODING_CAPABILITY_STATUS.md").read_text(encoding="utf-8")
    assert "CANDIDATE / PROVISIONAL" in text
    assert "Source presence alone is not execution evidence." in text
    assert "not certified completion" in text
    assert "Live execution with a frozen pinned coding image" in text
    assert "historical `ABSENT` implementation label" in text
