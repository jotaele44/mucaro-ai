from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from backend.app.config import Settings
from backend.app.ocr_analysis import analyze_image_ocr
from backend.app.ocr_layout import calibrate_confidence, derive_layout_receipt
from backend.app.ocr_pdf import analyze_pdf_ocr

STATUS_OK = "OCR_LIVE_RUNTIME_CERT_OK"
STATUS_FAILED = "OCR_LIVE_RUNTIME_CERT_FAILED"


def _settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "ocr-live.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise RuntimeError("No deterministic TrueType font found for OCR fixture generation")


def _text_image(path: Path, lines: list[str], *, font_size: int = 54) -> None:
    image = Image.new("RGB", (1800, 900), "white")
    draw = ImageDraw.Draw(image)
    font = _font(font_size)
    y = 100
    for line in lines:
        draw.text((100, y), line, fill="black", font=font)
        y += font_size + 42
    image.save(path, format="PNG")


def _blank_image(path: Path) -> None:
    Image.new("RGB", (1200, 700), "white").save(path, format="PNG")


def _pdf_from_png(image_path: Path, pdf_path: Path) -> None:
    with Image.open(image_path) as image:
        image.convert("RGB").save(pdf_path, "PDF", resolution=150.0)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contains(text: str, tokens: list[str]) -> bool:
    lowered = text.casefold()
    return all(token.casefold() in lowered for token in tokens)


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute live Tesseract + pdftoppm OCR evidence.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="mucaro-ocr-live-") as tmp:
        root = Path(tmp)
        cfg = _settings(root)
        fixture_dir = root / "fixtures"
        fixture_dir.mkdir(parents=True)

        english = fixture_dir / "clean_english.png"
        spanish = fixture_dir / "clean_spanish.png"
        blank = fixture_dir / "blank.png"
        pdf = fixture_dir / "clean_english.pdf"

        _text_image(english, ["Mucaro OCR certification", "Evidence lineage remains exact"])
        _text_image(spanish, ["Puerto Rico información", "Geografía, océano y análisis"])
        _blank_image(blank)
        _pdf_from_png(english, pdf)

        eng_report = analyze_image_ocr(english, cfg=cfg, language="eng", layout_mode="auto")
        spa_report = analyze_image_ocr(spanish, cfg=cfg, language="spa", layout_mode="auto")
        blank_report = analyze_image_ocr(blank, cfg=cfg, language="eng", layout_mode="auto")
        pdf_report = analyze_pdf_ocr(pdf, cfg=cfg, language="eng", layout_mode="auto", dpi=200)

        eng_layout = derive_layout_receipt(list(eng_report["spans"]))
        eng_confidence = calibrate_confidence(list(eng_report["spans"]))

        invariants = {
            "english_ocr_ok": eng_report["status"] == "OCR_ANALYSIS_OK",
            "english_expected_tokens": _contains(
                str(eng_report["text"]["normalized"]),
                ["Mucaro", "OCR", "Evidence", "lineage"],
            ),
            "span_accounting_closed": eng_layout["invariants"]["span_accounting_closed"] is True,
            "confidence_accounting_closed": eng_confidence["invariants"]["span_accounting_closed"] is True,
            "span_bboxes_present": eng_report["invariants"]["bounding_boxes_have_source_coordinates"] is True,
            "spanish_ocr_ok": spa_report["status"] == "OCR_ANALYSIS_OK",
            "spanish_expected_tokens": _contains(
                str(spa_report["text"]["normalized"]),
                ["Puerto", "Rico", "información", "Geografía", "océano", "análisis"],
            ),
            "blank_no_text_explicit": blank_report["status"] == "OCR_ANALYSIS_NO_TEXT"
            and blank_report["invariants"]["empty_text_state"] is True,
            "pdf_status_terminal": pdf_report["status"] in {"OCR_PDF_OK", "OCR_PDF_WARN"},
            "pdf_page_accounting_closed": pdf_report["invariants"]["page_accounting_closed"] is True,
            "pdf_rendered_hashes_present": pdf_report["invariants"]["all_rendered_page_hashes_present"] is True,
            "pdf_ocr_hashes_present": pdf_report["invariants"]["all_ocr_report_hashes_present"] is True,
            "pdf_single_page": pdf_report["invariants"]["source_page_count"] == 1
            and pdf_report["invariants"]["rendered_page_count"] == 1
            and pdf_report["invariants"]["ocr_page_count"] == 1,
        }

        result: dict[str, Any] = {
            "schema_version": "mucaro-ocr-live-runtime-cert/1.0",
            "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
            "scope": "SYNTHETIC_ENGINE_RUNTIME_CALIBRATION_ONLY",
            "fixtures": {
                "english_png_sha256": _hash(english),
                "spanish_png_sha256": _hash(spanish),
                "blank_png_sha256": _hash(blank),
                "pdf_sha256": _hash(pdf),
            },
            "engine": eng_report["engine"],
            "pdf_renderer": pdf_report["renderer"],
            "reports": {
                "english": {
                    "status": eng_report["status"],
                    "report_sha256": eng_report["report_sha256"],
                    "span_count": eng_report["invariants"]["span_count"],
                    "confidence": eng_confidence,
                    "layout": eng_layout,
                },
                "spanish": {
                    "status": spa_report["status"],
                    "report_sha256": spa_report["report_sha256"],
                    "normalized_text": spa_report["text"]["normalized"],
                },
                "blank": {
                    "status": blank_report["status"],
                    "report_sha256": blank_report["report_sha256"],
                    "span_count": blank_report["invariants"]["span_count"],
                },
                "pdf": {
                    "status": pdf_report["status"],
                    "report_sha256": pdf_report["report_sha256"],
                    "source_sha256": pdf_report["source"]["sha256"],
                    "page_count": pdf_report["source"]["page_count"],
                    "pages": pdf_report["pages"],
                },
            },
            "invariants": invariants,
            "limitations": [
                "Fixtures are deterministic synthetic runtime calibration inputs, not a representative real-world OCR corpus.",
                "This evidence does not certify semantic table/form reconstruction, handwriting recognition or difficult-scan performance.",
                "Confidence thresholds remain calibration values until labeled representative corpus execution exists.",
                "OCR-10 remains OPEN until real fixture bytes, expected observations, execution receipts and mismatch adjudication close.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(result["status"])
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0 if result["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
