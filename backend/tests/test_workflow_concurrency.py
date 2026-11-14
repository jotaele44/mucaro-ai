import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/macos-app.yml",
    ".github/workflows/rendered-qa.yml",
    ".github/workflows/arm64-portable-lifecycle-cert.yml",
)

GROUP = r"group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
CANCEL = r"cancel-in-progress: ${{ github.event_name == 'pull_request' }}"

# Manual-only helpers with no pull-request or main-push work to bound.
UNMANAGED = {"ci-startup-probe.yml", "v16-main-reconciliation-exact-head.yml"}
ALL_WORKFLOWS = sorted(path for path in WORKFLOW_DIR.glob("*.yml") if path.name not in UNMANAGED)
PULL_REQUEST_TRIGGER = re.compile(r"^  pull_request:", re.MULTILINE)


def _pull_request_workflows() -> list[Path]:
    return [path for path in ALL_WORKFLOWS if PULL_REQUEST_TRIGGER.search(path.read_text(encoding="utf-8"))]


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


def test_every_pull_request_workflow_cancels_superseded_heads() -> None:
    workflows = _pull_request_workflows()
    assert workflows, "no pull-request workflows found"
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        assert GROUP in text, path.name
        assert CANCEL in text, path.name
        assert "cancel-in-progress: true" not in text, path.name


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda path: path.name)
def test_every_job_is_time_bounded_and_token_scoped(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert any(line == "permissions:" for line in lines), f"{path.name} has no top-level permissions block"
    for index, line in enumerate(lines):
        if line.startswith("    runs-on:"):
            following = lines[index + 1 : index + 4]
            assert any(item.startswith("    timeout-minutes:") for item in following), (
                f"{path.name}:{index + 1} job has no timeout-minutes"
            )


def test_macos_runners_are_not_requested_for_pull_requests() -> None:
    """macOS runners are not available to pull requests in this repository; those jobs were only ever cancelled."""
    for path in _pull_request_workflows():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^    runs-on: macos", text, re.MULTILINE), path.name
        assert not re.search(r"^\s*-? ?os: macos", text, re.MULTILINE), path.name
        if '"os":"macos-' in text:
            # A matrix may still name macOS, but only on the branch of an expression taken outside pull requests.
            assert "github.event_name == 'pull_request'\n          && '[{\"os\":\"ubuntu-latest\"" in text, path.name
