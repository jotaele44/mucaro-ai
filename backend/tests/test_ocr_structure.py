from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.ocr_structure import (
    OCRStructureError,
    reconstruct_form,
    reconstruct_multicolumn,
    reconstruct_table,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "_architecture" / "ocr08_structure_benchmark_v1.json"


def span(
    index: int,
    text: str,
    *,
    x: int,
    y: int,
    width: int = 80,
    height: int = 24,
    block: int = 1,
    line: int = 1,
    word: int = 1,
) -> dict:
    return {
        "span_index": index,
        "raw_text": text,
        "confidence": 95.0,
        "page_index": 0,
        "block_num": block,
        "paragraph_num": 1,
        "line_num": line,
        "word_num": word,
        "bbox": {"x": x, "y": y, "width": width, "height": height},
    }


def test_ocr08_manifest_is_frozen_and_closed() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "MUCARO_OCR08_STRUCTURE_BENCHMARK/1.0"
    assert payload["case_count"] == 6 == len(payload["cases"])
    ids = [row["id"] for row in payload["cases"]]
    assert len(ids) == len(set(ids))
    assert payload["thresholds"] == {
        "positive_case_pass_rate": 1.0,
        "negative_fail_closed_rate": 1.0,
        "span_accounting_rate": 1.0,
    }


def test_multicolumn_geometry_closes_span_accounting() -> None:
    spans = [
        span(0, "Left one", x=50, y=50, width=180, block=1, line=1),
        span(1, "Left two", x=55, y=120, width=170, block=1, line=2),
        span(2, "Right one", x=500, y=55, width=190, block=2, line=1),
        span(3, "Right two", x=505, y=125, width=180, block=2, line=2),
    ]
    result = reconstruct_multicolumn(spans)
    assert result["status"] == "OCR_MULTI_COLUMN_OK"
    assert result["column_count"] == 2
    assert result["columns"][0]["raw_lines"] == ["Left one", "Left two"]
    assert result["columns"][1]["raw_lines"] == ["Right one", "Right two"]
    assert result["reading_order_span_indices"] == [0, 1, 2, 3]
    assert all(result["invariants"].values())


def test_single_column_does_not_synthesize_multicolumn() -> None:
    spans = [
        span(0, "One", x=50, y=50, width=180, block=1, line=1),
        span(1, "Two", x=50, y=120, width=180, block=1, line=2),
        span(2, "Three", x=50, y=190, width=180, block=1, line=3),
        span(3, "Four", x=50, y=260, width=180, block=1, line=4),
    ]
    result = reconstruct_multicolumn(spans)
    assert result["status"] == "OCR_STRUCTURE_UNRESOLVED"
    assert "COLUMN_COUNT_BELOW_MINIMUM" in result["residue"]
    assert result["invariants"]["semantic_structure_claimed"] is False


def test_rectangular_table_reconstructs_raw_cells_and_records() -> None:
    rows = [
        ["ITEM", "QTY", "PRICE"],
        ["APPLE", "2", "5"],
        ["PEAR", "3", "4"],
    ]
    spans = []
    index = 0
    for row_index, values in enumerate(rows):
        for column_index, value in enumerate(values):
            spans.append(
                span(
                    index,
                    value,
                    x=100 + column_index * 300,
                    y=100 + row_index * 90,
                    block=1,
                    line=row_index + 1,
                    word=column_index + 1,
                )
            )
            index += 1
    result = reconstruct_table(spans)
    assert result["status"] == "OCR_TABLE_RECONSTRUCTION_OK"
    assert result["headers_raw"] == ["ITEM", "QTY", "PRICE"]
    assert result["grid_raw"] == rows
    assert result["records_raw"] == [
        {"ITEM": "APPLE", "QTY": "2", "PRICE": "5"},
        {"ITEM": "PEAR", "QTY": "3", "PRICE": "4"},
    ]
    assert all(result["invariants"].values())


def test_table_assignment_tie_fails_closed() -> None:
    spans = [
        span(0, "A", x=100, y=100, block=1, line=1),
        span(1, "B", x=300, y=100, block=1, line=1),
        span(2, "ambiguous", x=200, y=200, block=1, line=2),
        span(3, "right", x=300, y=200, block=1, line=2),
    ]
    result = reconstruct_table(spans)
    assert result["status"] == "OCR_STRUCTURE_UNRESOLVED"
    assert any(item.startswith("COLUMN_ASSIGNMENT_TIE") for item in result["residue"])
    assert result["invariants"]["semantic_structure_claimed"] is False


def test_colon_labeled_form_binds_exact_raw_values() -> None:
    spans = [
        span(0, "Name:", x=100, y=100, width=120, block=1, line=1, word=1),
        span(1, "Alice", x=300, y=100, width=130, block=1, line=1, word=2),
        span(2, "City:", x=100, y=190, width=120, block=1, line=2, word=1),
        span(3, "Ponce", x=300, y=190, width=130, block=1, line=2, word=2),
        span(4, "Status:", x=100, y=280, width=150, block=1, line=3, word=1),
        span(5, "Active", x=300, y=280, width=140, block=1, line=3, word=2),
    ]
    result = reconstruct_form(spans)
    assert result["status"] == "OCR_FORM_RECONSTRUCTION_OK"
    assert result["field_count"] == 3
    assert [(row["label_raw"], row["value_raw"]) for row in result["fields"]] == [
        ("Name:", "Alice"),
        ("City:", "Ponce"),
        ("Status:", "Active"),
    ]
    assert all(result["invariants"].values())


def test_duplicate_form_label_is_unresolved() -> None:
    spans = [
        span(0, "Name:", x=100, y=100, block=1, line=1, word=1),
        span(1, "Alice", x=300, y=100, block=1, line=1, word=2),
        span(2, "Name:", x=100, y=190, block=1, line=2, word=1),
        span(3, "Bob", x=300, y=190, block=1, line=2, word=2),
    ]
    result = reconstruct_form(spans)
    assert result["status"] == "OCR_STRUCTURE_UNRESOLVED"
    assert "FORM_LABEL_DUPLICATE:name" in result["residue"]
    assert result["invariants"]["semantic_structure_claimed"] is False


def test_invalid_bbox_and_duplicate_span_identity_fail_before_structure() -> None:
    bad_bbox = span(0, "A", x=0, y=0)
    bad_bbox["bbox"]["width"] = 0
    with pytest.raises(OCRStructureError, match="bbox width and height"):
        reconstruct_table([bad_bbox, span(1, "B", x=100, y=0)])

    duplicate = [span(0, "A", x=0, y=0), span(0, "B", x=100, y=0)]
    with pytest.raises(OCRStructureError) as exc:
        reconstruct_form(duplicate)
    assert exc.value.status == "ERROR_OCR_STRUCTURE_DUPLICATE_SPAN"
