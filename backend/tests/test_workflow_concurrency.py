from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/macos-app.yml",
    ".github/workflows/rendered-qa.yml",
    ".github/workflows/arm64-portable-lifecycle-cert.yml",
)

GROUP = r"group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
CANCEL = r"cancel-in-progress: ${{ github.event_name == 'pull_request' }}"


def test_expensive_pr_workflows_cancel_superseded_heads() -> None:
    for relative in WORKFLOWS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "\nconcurrency:\n" in text, relative
        assert GROUP in text, relative
        assert CANCEL in text, relative


def test_main_pushes_are_not_cancelled_by_pr_freshness_policy() -> None:
    for relative in WORKFLOWS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "cancel-in-progress: true" not in text, relative
        assert "github.event_name == 'pull_request'" in text, relative
