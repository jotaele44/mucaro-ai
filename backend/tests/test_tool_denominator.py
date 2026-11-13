from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from backend.app.tool_denominator import (
    build_tool_denominator,
    load_declared,
    registry_sha256,
    validate_tool_denominator,
)
from backend.app.tools import TOOL_REGISTRY, ToolDefinition

REPO = Path(__file__).resolve().parents[2]


def fake_tool(name: str, *, mutates_state: bool = False, requires_approval: bool = True) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="denominator probe",
        mutates_state=mutates_state,
        requires_approval=requires_approval,
        handler=lambda arguments, cfg: {"status": "OK"},
    )


def test_committed_denominator_closes_against_live_registry() -> None:
    declared = load_declared(REPO)

    assert validate_tool_denominator(declared) == []
    report = build_tool_denominator(REPO)

    assert report["status"] == "DENOMINATOR_CLOSED"
    assert report["errors"] == []
    counts = report["counts"]
    assert counts["present"] + counts["absent"] == counts["required_capabilities"]
    assert counts["registered_tools"] == counts["classified_tools"] == len(TOOL_REGISTRY)
    assert counts["mutating_tools"] + counts["read_only_tools"] == counts["registered_tools"]
    assert counts["exercised_tools"] + counts["unexercised_tools"] == counts["registered_tools"]
    assert [row["name"] for row in report["tools"]] == sorted(TOOL_REGISTRY)
    assert len(report["report_sha256"]) == 64
    assert len(report["registry_sha256"]) == 64


def test_absent_flagpole_capabilities_keep_certification_open() -> None:
    report = build_tool_denominator(REPO)

    assert report["certification_state"] == "OPEN"
    assert set(report["open_capabilities"]) == {
        "ci.status.read",
        "regression.run",
        "vcs.branch_commit",
        "pull_request.open_update",
    }
    for row in report["required_capabilities"]:
        assert row["clause"], row["id"]


def test_runtime_evidence_is_derived_from_governed_requests() -> None:
    tools = {row["name"]: row for row in build_tool_denominator(REPO)["tools"]}

    assert tools["web.fetch"]["evidence_state"] == "EXERCISED"
    assert "backend/tests/test_tools.py::test_tool_runtime_executes_governed_web_fetch" in tools["web.fetch"]["runtime_evidence"]
    assert tools["source_library.status"]["evidence_state"] == "EXERCISED"
    for name in ("gis.inspect", "ocr.analyze_image"):
        assert tools[name]["evidence_state"] == "EXERCISED", name
        assert tools[name]["classification"] == "SUPPORTING"
    for name in ("coding.repo_status", "coding.read_file", "coding.diff", "coding.patch_check", "coding.patch_apply"):
        assert tools[name]["evidence_state"] == "EXERCISED", name
        assert tools[name]["classification"] == "REQUIRED"
    assert all(row["requires_approval"] for row in tools.values())


def test_unclassified_new_tool_invalidates_denominator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TOOL_REGISTRY, "test.unclassified", fake_tool("test.unclassified"))

    report = build_tool_denominator(REPO)

    assert "UNCLASSIFIED_TOOL:test.unclassified" in report["errors"]
    assert report["status"] == "DENOMINATOR_INVALID"
    assert report["certification_state"] == "FAIL"


def test_removed_tool_and_approval_regressions_fail_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    declared = load_declared(REPO)
    monkeypatch.delitem(TOOL_REGISTRY, "web.render")
    assert "UNKNOWN_TOOL:web.render" in validate_tool_denominator(declared)

    monkeypatch.setitem(TOOL_REGISTRY, "web.render", fake_tool("web.render", mutates_state=True, requires_approval=False))
    assert "MUTATING_TOOL_WITHOUT_APPROVAL:web.render" in validate_tool_denominator(declared)

    monkeypatch.setitem(TOOL_REGISTRY, "memory.list", fake_tool("memory.list", requires_approval=False))
    assert "APPROVAL_NOT_REQUIRED:memory.list" in validate_tool_denominator(declared)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda d: d.update(schema="other"), "SCHEMA_MISMATCH"),
        (lambda d: d["required_capabilities"].append(deepcopy(d["required_capabilities"][0])), "DUPLICATE_CAPABILITY_ID"),
        (lambda d: d["required_capabilities"][0].update(state="MAYBE"), "UNKNOWN_STATE:TOOL-01"),
        (lambda d: d["required_capabilities"][0].update(owner_domain="V99"), "UNKNOWN_DOMAIN:TOOL-01"),
        (lambda d: d["required_capabilities"][1].update(satisfied_by=[]), "PRESENT_WITHOUT_TOOL:TOOL-02"),
        (lambda d: d["required_capabilities"][4].update(satisfied_by=["web.fetch"]), "ABSENT_WITH_TOOL:TOOL-05"),
        (lambda d: d["supporting_tools"].append("coding.diff"), "DOUBLE_CLASSIFIED_TOOL:coding.diff"),
        (lambda d: d["supporting_tools"].append("web.fetch"), "DOUBLE_CLASSIFIED_TOOL:web.fetch"),
        (lambda d: d["supporting_tools"].remove("web.render"), "UNCLASSIFIED_TOOL:web.render"),
        (lambda d: d["supporting_tools"].append("ghost.tool"), "UNKNOWN_TOOL:ghost.tool"),
        (lambda d: d.pop("supporting_tools"), "DENOMINATOR_SHAPE"),
    ],
)
def test_declared_denominator_negative_controls(mutate, error: str) -> None:
    declared = deepcopy(load_declared(REPO))
    mutate(declared)

    assert error in validate_tool_denominator(declared)


def test_registry_digest_tracks_tool_definition_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    before = registry_sha256()
    monkeypatch.setitem(TOOL_REGISTRY, "web.render", fake_tool("web.render", mutates_state=True))

    assert registry_sha256() != before


def test_runtime_evidence_counts_only_the_tools_each_request_names(tmp_path: Path) -> None:
    from backend.app.tool_denominator import runtime_evidence

    tests_dir = tmp_path / "backend" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_probe.py").write_text(
        """
import pytest


def test_mentions_without_requesting():
    request_tool_call("memory.list", {})
    assert "web.render" not in ["coding.diff"]


def test_loop():
    for name, arguments in (("coding.diff", {}), ("coding.read_file", {})):
        request_tool_call(name, arguments)


@pytest.mark.parametrize("tool", ["web.search", "web.fetch"])
def test_parametrized(tool):
    request_tool_call(tool, {})


def test_keyword():
    request_tool_call(tool_name="product.audit")
""",
        encoding="utf-8",
    )
    names = ["memory.list", "web.render", "coding.diff", "coding.read_file", "web.search", "web.fetch", "product.audit"]
    evidence = runtime_evidence(tmp_path, names)

    assert evidence["memory.list"] == ["backend/tests/test_probe.py::test_mentions_without_requesting"]
    assert evidence["web.render"] == []
    assert evidence["coding.diff"] == evidence["coding.read_file"] == ["backend/tests/test_probe.py::test_loop"]
    assert evidence["web.search"] == evidence["web.fetch"] == ["backend/tests/test_probe.py::test_parametrized"]
    assert evidence["product.audit"] == ["backend/tests/test_probe.py::test_keyword"]
