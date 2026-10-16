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


def test_multicolumn_splits_engine_line_when_columns_share_one_tesseract_line() -> None:
    spans = [
        span(0, "LEFTALPHA", x=120, y=140, width=310, height=41, block=1, line=1, word=1),
        span(1, "RIGHTALPHA", x=1050, y=150, width=340, height=41, block=1, line=1, word=2),
        span(2, "LEFTBETA", x=120, y=300, width=280, height=41, block=1, line=2, word=1),
        span(3, "RIGHTBETA", x=1050, y=310, width=310, height=41, block=1, line=2, word=2),
        span(4, "LEFTGAMMA", x=120, y=460, width=330, height=41, block=1, line=3, word=1),
        span(5, "RIGHTGAMMA", x=1050, y=470, width=360, height=41, block=1, line=3, word=2),
    ]

    result = reconstruct_multicolumn(spans)

    assert result["status"] == "OCR_MULTI_COLUMN_OK"
    assert result["column_count"] == 2
    assert result["columns"][0]["raw_lines"] == ["LEFTALPHA", "LEFTBETA", "LEFTGAMMA"]
    assert result["columns"][1]["raw_lines"] == ["RIGHTALPHA", "RIGHTBETA", "RIGHTGAMMA"]
    assert result["reading_order_span_indices"] == [0, 2, 4, 1, 3, 5]
    assert result["invariants"]["all_input_spans_classified"] is True
    assert result["invariants"]["span_assignment_unique"] is True


def test_multicolumn_does_not_split_normal_multiword_line() -> None:
    spans = [
        span(0, "Left", x=50, y=50, width=80, block=1, line=1, word=1),
        span(1, "one", x=150, y=50, width=70, block=1, line=1, word=2),
        span(2, "Left", x=50, y=120, width=80, block=1, line=2, word=1),
        span(3, "two", x=150, y=120, width=70, block=1, line=2, word=2),
        span(4, "Right", x=500, y=55, width=90, block=2, line=1, word=1),
        span(5, "one", x=610, y=55, width=70, block=2, line=1, word=2),
        span(6, "Right", x=500, y=125, width=90, block=2, line=2, word=1),
        span(7, "two", x=610, y=125, width=70, block=2, line=2, word=2),
    ]

    result = reconstruct_multicolumn(spans)

    assert result["status"] == "OCR_MULTI_COLUMN_OK"
    assert result["columns"][0]["raw_lines"] == ["Left one", "Left two"]
    assert result["columns"][1]["raw_lines"] == ["Right one", "Right two"]


def test_single_wide_gapped_row_still_cannot_synthesize_multicolumn() -> None:
    spans = [
        span(0, "LEFT", x=50, y=50, width=100, block=1, line=1, word=1),
        span(1, "RIGHT", x=700, y=50, width=120, block=1, line=1, word=2),
    ]

    result = reconstruct_multicolumn(spans)

    assert result["status"] == "OCR_STRUCTURE_UNRESOLVED"
    assert "INSUFFICIENT_LINE_DENOMINATOR" in result["residue"]
    assert result["invariants"]["semantic_structure_claimed"] is False


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


def test_multicolumn_requires_vertical_overlap_not_just_x_separation() -> None:
    spans = [
        span(0, "Top left one", x=50, y=50, width=180, block=1, line=1),
        span(1, "Top left two", x=50, y=110, width=180, block=1, line=2),
        span(2, "Bottom right one", x=500, y=600, width=180, block=2, line=1),
        span(3, "Bottom right two", x=500, y=660, width=180, block=2, line=2),
    ]
    result = reconstruct_multicolumn(spans)
    assert result["status"] == "OCR_STRUCTURE_UNRESOLVED"
    assert "COLUMN_VERTICAL_OVERLAP_BELOW_MINIMUM" in result["residue"]


def test_supported_structure_is_explicitly_inference_not_identity() -> None:
    spans = [
        span(0, "Name:", x=100, y=100, block=1, line=1, word=1),
        span(1, "Alice", x=300, y=100, block=1, line=1, word=2),
        span(2, "City:", x=100, y=190, block=1, line=2, word=1),
        span(3, "Ponce", x=300, y=190, block=1, line=2, word=2),
    ]
    result = reconstruct_form(spans)
    assert result["epistemic_classification"] == "INFERENCE"
    assert result["certification_state"] == "CANDIDATE_NOT_IDENTITY"


def test_malformed_engine_hierarchy_fails_closed() -> None:
    malformed = span(0, "Name:", x=100, y=100)
    malformed["line_num"] = "not-an-integer"
    with pytest.raises(OCRStructureError) as exc:
        reconstruct_form([malformed, span(1, "Alice", x=300, y=100)])
    assert exc.value.status == "ERROR_OCR_STRUCTURE_HIERARCHY"


def test_saved_ocr_report_structure_receipt_binds_input_hash(tmp_path: Path) -> None:
    from backend.app.config import Settings
    from backend.app.ocr_structure import reconstruct_ocr_report

    cfg = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        input_dir=tmp_path / "data/input",
        processed_dir=tmp_path / "data/processed",
        export_dir=tmp_path / "data/exports",
        index_dir=tmp_path / "data/indexes",
        sqlite_path=tmp_path / "data/processed/test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    report_path = tmp_path / "form-report.json"
    payload = {
        "schema_version": "mucaro_ocr_report_v1",
        "report_id": "ocrreport_fixture",
        "source": {"sha256": "a" * 64},
        "engine": {"name": "tesseract", "version": "fixture"},
        "spans": [
            span(0, "Name:", x=100, y=100, block=1, line=1, word=1),
            span(1, "Alice", x=300, y=100, block=1, line=1, word=2),
            span(2, "City:", x=100, y=190, block=1, line=2, word=1),
            span(3, "Ponce", x=300, y=190, block=1, line=2, word=2),
        ],
    }
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    result = reconstruct_ocr_report(report_path, kind="form", cfg=cfg)
    assert result["status"] == "OCR_FORM_RECONSTRUCTION_OK"
    assert result["input_ocr_report"]["sha256"] == __import__("hashlib").sha256(report_path.read_bytes()).hexdigest()
    assert result["structure"]["certification_state"] == "CANDIDATE_NOT_IDENTITY"
    assert result["invariants"]["structure_span_count_matches_input"] is True
    assert Path(result["receipt_path"]).is_file()
    assert len(result["receipt_sha256"]) == 64


def test_saved_ocr_report_rejects_wrong_schema_and_unresolved_returns_receipt(tmp_path: Path) -> None:
    from backend.app.config import Settings
    from backend.app.ocr_structure import OCRStructureError, reconstruct_ocr_report

    cfg = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        input_dir=tmp_path / "data/input",
        processed_dir=tmp_path / "data/processed",
        export_dir=tmp_path / "data/exports",
        index_dir=tmp_path / "data/indexes",
        sqlite_path=tmp_path / "data/processed/test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "wrong", "spans": []}), encoding="utf-8")
    with pytest.raises(OCRStructureError) as exc:
        reconstruct_ocr_report(bad, kind="form", cfg=cfg)
    assert exc.value.status == "ERROR_OCR_STRUCTURE_REPORT_SCHEMA"

    single = tmp_path / "single.json"
    single.write_text(
        json.dumps(
            {
                "schema_version": "mucaro_ocr_report_v1",
                "report_id": "ocrreport_single",
                "source": {"sha256": "b" * 64},
                "engine": {"name": "tesseract"},
                "spans": [
                    span(0, "One", x=50, y=50, line=1),
                    span(1, "Two", x=50, y=120, line=2),
                    span(2, "Three", x=50, y=190, line=3),
                    span(3, "Four", x=50, y=260, line=4),
                ],
            }
        ),
        encoding="utf-8",
    )
    unresolved = reconstruct_ocr_report(single, kind="multi_column", cfg=cfg)
    assert unresolved["status"] == "OCR_STRUCTURE_UNRESOLVED"
    assert unresolved["structure"]["certification_state"] == "UNRESOLVED"
    assert Path(unresolved["receipt_path"]).is_file()
