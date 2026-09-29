from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from backend.app.config import Settings
from backend.app.ocr_analysis import analyze_image_ocr
from backend.app.ocr_structure import reconstruct_form, reconstruct_multicolumn, reconstruct_table

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "_architecture" / "ocr08_structure_benchmark_v1.json"
STATUS_OK = "OCR08_STRUCTURE_LIVE_CERT_OK"
STATUS_FAILED = "OCR08_STRUCTURE_LIVE_CERT_FAILED"


def _settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "ocr08.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def _font(size: int) -> Any:
    from PIL import ImageFont

    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise RuntimeError("No deterministic TrueType font found for OCR08 fixture generation")


def _make_multicolumn(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1800, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = _font(52)
    for y, text in [(140, "LEFTALPHA"), (300, "LEFTBETA"), (460, "LEFTGAMMA")]:
        draw.text((120, y), text, fill="black", font=font)
    for y, text in [(150, "RIGHTALPHA"), (310, "RIGHTBETA"), (470, "RIGHTGAMMA")]:
        draw.text((1050, y), text, fill="black", font=font)
    image.save(path, format="PNG")


def _make_table(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1800, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = _font(52)
    rows = [
        ["ITEM", "QTY", "PRICE"],
        ["APPLE", "2", "5"],
        ["PEAR", "3", "4"],
    ]
    xs = [120, 720, 1250]
    ys = [150, 360, 570]
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            draw.text((xs[column_index], ys[row_index]), text, fill="black", font=font)
    image.save(path, format="PNG")


def _make_form(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1600, 900), "white")
    draw = ImageDraw.Draw(image)
    font = _font(52)
    for y, label, value in [
        (150, "Name:", "Alice"),
        (340, "City:", "Ponce"),
        (530, "Status:", "Active"),
    ]:
        draw.text((120, y), f"{label} {value}", fill="black", font=font)
    image.save(path, format="PNG")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _span(
    index: int,
    text: str,
    *,
    x: int,
    y: int,
    block: int = 1,
    line: int = 1,
    word: int = 1,
) -> dict[str, Any]:
    return {
        "span_index": index,
        "raw_text": text,
        "confidence": 95.0,
        "page_index": 0,
        "block_num": block,
        "paragraph_num": 1,
        "line_num": line,
        "word_num": word,
        "bbox": {"x": x, "y": y, "width": 80, "height": 24},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute OCR-08 bounded structure reconstruction with real Tesseract spans.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    benchmark_bytes = BENCHMARK.read_bytes()
    benchmark = json.loads(benchmark_bytes.decode("utf-8"))
    if benchmark.get("schema_version") != "MUCARO_OCR08_STRUCTURE_BENCHMARK/1.0":
        raise RuntimeError("OCR08 benchmark schema drift")
    if benchmark.get("case_count") != 6 or len(benchmark.get("cases", [])) != 6:
        raise RuntimeError("OCR08 benchmark denominator drift")

    with tempfile.TemporaryDirectory(prefix="mucaro-ocr08-") as temp_dir:
        root = Path(temp_dir)
        cfg = _settings(root)
        fixture_dir = root / "fixtures"
        fixture_dir.mkdir(parents=True)
        multi_path = fixture_dir / "multi-column.png"
        table_path = fixture_dir / "table.png"
        form_path = fixture_dir / "form.png"
        _make_multicolumn(multi_path)
        _make_table(table_path)
        _make_form(form_path)

        multi_ocr = analyze_image_ocr(multi_path, cfg=cfg, language="eng", layout_mode="auto")
        table_ocr = analyze_image_ocr(table_path, cfg=cfg, language="eng", layout_mode="auto")
        form_ocr = analyze_image_ocr(form_path, cfg=cfg, language="eng", layout_mode="auto")

        multi = reconstruct_multicolumn(list(multi_ocr["spans"]))
        table = reconstruct_table(list(table_ocr["spans"]))
        form = reconstruct_form(list(form_ocr["spans"]))

        negative_multi = reconstruct_multicolumn(
            [
                _span(0, "One", x=50, y=50, line=1),
                _span(1, "Two", x=50, y=120, line=2),
                _span(2, "Three", x=50, y=190, line=3),
                _span(3, "Four", x=50, y=260, line=4),
            ]
        )
        negative_table = reconstruct_table(
            [
                _span(0, "A", x=100, y=100, line=1, word=1),
                _span(1, "B", x=300, y=100, line=1, word=2),
                _span(2, "ambiguous", x=200, y=200, line=2, word=1),
                _span(3, "right", x=300, y=200, line=2, word=2),
            ]
        )
        negative_form = reconstruct_form(
            [
                _span(0, "Name:", x=100, y=100, line=1, word=1),
                _span(1, "Alice", x=300, y=100, line=1, word=2),
                _span(2, "Name:", x=100, y=190, line=2, word=1),
                _span(3, "Bob", x=300, y=190, line=2, word=2),
            ]
        )

        case_results = [
            {"id": "OCR08-01", "status": multi.get("status")},
            {"id": "OCR08-02", "status": table.get("status")},
            {"id": "OCR08-03", "status": form.get("status")},
            {"id": "OCR08-N01", "status": negative_multi.get("status")},
            {"id": "OCR08-N02", "status": negative_table.get("status")},
            {"id": "OCR08-N03", "status": negative_form.get("status")},
        ]
        expected_statuses = {
            str(row["id"]): str(row["expected_status"])
            for row in benchmark["cases"]
            if isinstance(row, dict)
        }
        case_matches = {
            row["id"]: row["status"] == expected_statuses.get(row["id"])
            for row in case_results
        }

        invariants = {
            "multi_ocr_ok": multi_ocr["status"] == "OCR_ANALYSIS_OK",
            "multi_structure_ok": multi["status"] == "OCR_MULTI_COLUMN_OK",
            "multi_columns_exact": multi.get("column_count") == 2,
            "multi_span_accounting": multi.get("invariants", {}).get("all_input_spans_classified") is True,
            "table_ocr_ok": table_ocr["status"] == "OCR_ANALYSIS_OK",
            "table_structure_ok": table["status"] == "OCR_TABLE_RECONSTRUCTION_OK",
            "table_shape_exact": table.get("row_count") == 3 and table.get("column_count") == 3,
            "table_headers_exact": table.get("headers_raw") == ["ITEM", "QTY", "PRICE"],
            "table_records_exact": table.get("records_raw")
            == [
                {"ITEM": "APPLE", "QTY": "2", "PRICE": "5"},
                {"ITEM": "PEAR", "QTY": "3", "PRICE": "4"},
            ],
            "table_span_accounting": table.get("invariants", {}).get("all_input_spans_classified") is True,
            "form_ocr_ok": form_ocr["status"] == "OCR_ANALYSIS_OK",
            "form_structure_ok": form["status"] == "OCR_FORM_RECONSTRUCTION_OK",
            "form_field_count_exact": form.get("field_count") == 3,
            "form_values_exact": [
                (row.get("label_raw"), row.get("value_raw"))
                for row in form.get("fields", [])
                if isinstance(row, dict)
            ]
            == [("Name:", "Alice"), ("City:", "Ponce"), ("Status:", "Active")],
            "form_span_accounting": form.get("invariants", {}).get("all_input_spans_classified") is True,
            "positive_structures_are_inference": all(
                row.get("epistemic_classification") == "INFERENCE"
                and row.get("certification_state") == "CANDIDATE_NOT_IDENTITY"
                for row in (multi, table, form)
            ),
            "negative_cases_unresolved": all(
                row.get("status") == "OCR_STRUCTURE_UNRESOLVED"
                and row.get("certification_state") == "UNRESOLVED"
                for row in (negative_multi, negative_table, negative_form)
            ),
            "case_denominator_closed": len(case_results) == 6
            and len({row["id"] for row in case_results}) == 6
            and set(row["id"] for row in case_results) == set(expected_statuses),
            "all_six_expected_states_match": all(case_matches.values()),
        }
        result = {
            "schema_version": "mucaro-ocr08-structure-live-cert/1.0",
            "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
            "scope": "OCR08_BOUNDED_PRINTED_STRUCTURE_RUNTIME",
            "benchmark": {
                "path": str(BENCHMARK.relative_to(ROOT)),
                "sha256": hashlib.sha256(benchmark_bytes).hexdigest(),
                "case_count": benchmark["case_count"],
                "thresholds": benchmark["thresholds"],
            },
            "case_results": case_results,
            "case_matches": case_matches,
            "fixtures": {
                "multi_column_sha256": _sha(multi_path),
                "table_sha256": _sha(table_path),
                "form_sha256": _sha(form_path),
            },
            "engine": {
                "multi": multi_ocr["engine"],
                "table": table_ocr["engine"],
                "form": form_ocr["engine"],
            },
            "ocr_report_sha256": {
                "multi": multi_ocr["report_sha256"],
                "table": table_ocr["report_sha256"],
                "form": form_ocr["report_sha256"],
            },
            "structures": {
                "multi": multi,
                "table": table,
                "form": form,
                "negative_multi": negative_multi,
                "negative_table": negative_table,
                "negative_form": negative_form,
            },
            "invariants": invariants,
            "limitations": [
                "Fixtures are deterministic synthetic printed documents used to prove engine-to-structure mechanics, not representative real-world breadth.",
                "Table support is bounded to rectangular, first-row-header geometry with unique x-anchor assignment.",
                "Form support is bounded to one colon-terminated label sequence and one value sequence per OCR line.",
                "Spanning headings, merged/headerless/rotated/nested tables and non-colon or multiline form semantics remain unresolved.",
                "Handwriting and difficult scans remain OCR-09; representative real-world breadth remains OCR-10.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(result["status"])
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0 if result["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
