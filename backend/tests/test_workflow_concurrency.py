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


def _trigger_paths(text: str, trigger: str) -> list[str] | None:
    """The `paths:` list under a top-level `on:` trigger, or None when the trigger has no path filter.

    Workflows here use one fixed layout (two-space trigger, four-space `paths:`, six-space list items), so a line
    reader is enough and the test needs no YAML dependency.
    """
    lines = text.splitlines()
    try:
        start = lines.index(f"  {trigger}:")
    except ValueError:
        return None
    paths: list[str] | None = None
    for line in lines[start + 1 :]:
        if line and not line.startswith("   "):
            break
        if line == "    paths:":
            paths = []
        elif paths is not None and line.startswith("      - "):
            paths.append(line[len("      - ") :].strip().strip("\"'"))
        elif paths is not None and line.startswith("    ") and not line.startswith("      "):
            break
    return paths


def test_macos_coverage_moved_off_pull_requests_still_runs_on_main_for_the_same_paths() -> None:
    """A workflow whose macOS work runs only outside pull requests must watch every PR path on main pushes too.

    Otherwise a change limited to a PR-only path gets no macOS run at all: skipped on the PR, never triggered on main.
    """
    checked = 0
    for path in ALL_WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        if "macos-" not in text:
            continue
        pr_paths = _trigger_paths(text, "pull_request")
        push_paths = _trigger_paths(text, "push")
        if not pr_paths or push_paths is None:
            continue
        checked += 1
        missing = sorted(set(pr_paths) - set(push_paths))
        assert not missing, f"{path.name}: pull-request paths with no main-push macOS coverage: {missing}"
    assert checked, "expected at least one workflow with macOS work and path-filtered pull_request and push triggers"
