from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend.app.coherent_architecture import (
    CATEGORIES,
    MATRIX_PATH,
    STATUS_FAILED,
    STATUS_OK,
    validate_coherent_architecture,
)
from backend.app.tools import TOOL_REGISTRY, ToolDefinition

ROOT = Path(__file__).resolve().parents[2]


def _matrix() -> dict:
    return json.loads((ROOT / MATRIX_PATH).read_text(encoding="utf-8"))


def _row(matrix: dict, category: str) -> dict:
    return next(row for row in matrix["categories"] if row["category"] == category)


def test_declared_architecture_matches_the_running_code() -> None:
    result = validate_coherent_architecture(ROOT)

    assert result["status"] == STATUS_OK, result["errors"]
    assert result["category_count"] == len(CATEGORIES) == 12
    assert result["unmapped_tools"] == []
    assert result["mapped_tool_count"] == result["registered_tool_count"] == len(TOOL_REGISTRY)


def test_coding_bridges_include_execution_and_repository_mutation() -> None:
    bridges = set(_row(_matrix(), "CODE")["tool_registry_bridges"])
    assert {"coding.exec", "coding.commit", "coding.push", "coding.patch_apply"} <= bridges


def test_a_tool_outside_the_matrix_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        TOOL_REGISTRY, "bypass.direct", ToolDefinition("bypass.direct", "not in the matrix", False, True, lambda a, c: {})
    )

    result = validate_coherent_architecture(ROOT)

    assert result["status"] == STATUS_FAILED
    assert result["unmapped_tools"] == ["bypass.direct"]


@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        (lambda m: _row(m, "CODE")["tool_registry_bridges"].append("coding.no_such_tool"), "CODE:unknown_tool:coding.no_such_tool"),
        (lambda m: _row(m, "MEMORY")["shared_contracts"].append("TELEPATHY"), "MEMORY:unknown_contract_family:TELEPATHY"),
        (lambda m: _row(m, "GIS")["evidence_refs"].append("backend/tests/test_missing.py"), "GIS:missing_ref:backend/tests/test_missing.py"),
        (lambda m: _row(m, "AI").update(canonical_surface="backend/app/services.py:no_such_function"), "AI:canonical_surface_unresolved:backend/app/services.py:no_such_function"),
        (lambda m: _row(m, "OCR")["tool_registry_bridges"].append("gis.inspect"), "OCR:expected_exactly_one_bridge:['ocr.analyze_image', 'gis.inspect']"),
        (lambda m: m["categories"].pop(), "categories:"),
    ],
    ids=["unknown-tool", "unknown-contract", "missing-ref", "broken-surface", "second-ocr-bridge", "missing-category"],
)
def test_a_drifted_matrix_fails(change, expected_error: str) -> None:
    matrix = copy.deepcopy(_matrix())
    change(matrix)

    result = validate_coherent_architecture(ROOT, matrix)

    assert result["status"] == STATUS_FAILED
    assert any(error.startswith(expected_error) for error in result["errors"]), result["errors"]
