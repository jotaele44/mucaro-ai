from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from backend.app.cli import main
from backend.app.main import create_app
from backend.app.skill_runtime import SkillRuntimeError, activate_skill, execute_skill, mount_skill
from backend.tests.test_api import make_settings
from backend.tests.test_web_research import CAPS, SKILL, arguments, capture
from scripts import evaluate_web_research_real as evaluator


@pytest.fixture
def ready(tmp_path):
    cfg = make_settings(tmp_path)
    mount_skill(SKILL, cfg=cfg)
    activate_skill(SKILL, cfg=cfg, allowed_capabilities=CAPS)
    return cfg


def test_frozen_real_sources_preserve_required_semantic_evidence():
    report = evaluator.evaluate()
    assert report["status"] == "WEB_RESEARCH_REAL_EVAL_OK"
    assert report["source_count"] == 3 and report["source_bytes"] == 132376
    assert report["case_count"] == 5
    assert all(value == 1.0 for value in report["metrics"].values())
    assert sum(len(row["anchors"]) for row in report["cases"]) == 6


@pytest.mark.parametrize("name", ["rfc8259.txt", "acquisition.json", "corpus_manifest.json"])
def test_source_or_parent_tampering_is_refused_without_reacquisition(tmp_path, name):
    directory = tmp_path / "fixtures"
    shutil.copytree(evaluator.FIXTURES, directory)
    path = directory / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        evaluator.load_sources(directory)


def test_paragraph_mode_keeps_wrapped_requirements_and_original_offsets(ready):
    text = "  Múcaro names\r\n  SHOULD be unique.\r\n \t\r\n  Next paragraph.\r\n"
    a, b = capture(ready, text, 0), capture(ready, "Names\nMUST be unique.\n\nNext paragraph.", 1)
    report = execute_skill(SKILL, arguments(a, b, question="names unique", passage_mode="paragraph"), cfg=ready)[
        "result"
    ]
    assert report["passage_mode"] == "paragraph"
    assert [source["passage_count"] for source in report["sources"]] == [2, 2]
    citation = next(row for row in report["citations"] if row["source_id"] == a[0])
    assert citation["text"] == "  Múcaro names\r\n  SHOULD be unique."
    assert text[citation["start"] : citation["end"]] == citation["text"]
    assert report["synthesis"]["citation_status"] == "EXACT_EXCERPTS_VERIFIED"


def test_line_mode_retains_its_existing_boundaries_and_rejects_unknown_modes(ready):
    a, b = capture(ready, "Budget 40\nScope local", 0), capture(ready, "Budget 60\nScope local", 1)
    report = execute_skill(SKILL, arguments(a, b), cfg=ready)["result"]
    assert report["passage_mode"] == "line"
    assert [row["passage_count"] for row in report["sources"]] == [2, 2]
    with pytest.raises(SkillRuntimeError) as refused:
        execute_skill(SKILL, arguments(a, b, passage_mode="silently-guess"), cfg=ready)
    assert refused.value.status == "ERROR_SKILL_ARGUMENTS_INVALID"


def test_manifest_inclusion_exclusion_and_parent_bindings_close():
    manifest, sources = evaluator.load_sources()
    assert manifest["included_source_count"] + manifest["excluded_source_count"] == 24
    acquisition = json.loads((evaluator.FIXTURES / "acquisition.json").read_bytes())
    assert set(sources) == {row["source_id"] for row in manifest["included_sources"]}
    assert all(row in acquisition["sources"] for row, raw in sources.values())
    assert manifest["code_association"].startswith("UNRESOLVED")


def test_api_and_cli_apply_explicit_paragraph_boundaries(ready, capsys):
    a, b = capture(ready, "Names\nSHOULD be unique.", 0), capture(ready, "Names\nMUST be unique.", 1)
    client = TestClient(create_app(ready))
    response = client.post(f"/skills/{SKILL}/execute", json={"arguments": arguments(a, b, passage_mode="paragraph")})
    assert response.status_code == 200
    assert [row["passage_count"] for row in response.json()["result"]["sources"]] == [1, 1]
    assert (
        main(
            ["captures", "compare", a[0], b[0], "--a-sha256", a[1], "--b-sha256", b[1], "--passage-mode", "paragraph"],
            cfg=ready,
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["passage_mode"] == "paragraph"
    assert [row["passage_count"] for row in result["sources"]] == [1, 1]
