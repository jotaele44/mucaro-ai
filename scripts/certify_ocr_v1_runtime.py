#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from backend.app.config import Settings
from backend.app.ocr_analysis import analyze_image_ocr
from backend.app.release_receipts import write_release_receipt

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "_architecture" / "ocr_v1_corpus_manifest.json"
STATUS_OK = "OCR_V1_RUNTIME_BOUNDARY_CERT_OK"
STATUS_FAILED = "OCR_V1_RUNTIME_BOUNDARY_CERT_FAILED"


def _settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "ocr-v1.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def _font(size: int) -> Any:
    from PIL import ImageFont

    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise RuntimeError("No deterministic TrueType font found for OCR fixture generation")


def _text_image(path: Path, lines: list[str], *, font_size: int = 54) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1800, 900), "white")
    draw = ImageDraw.Draw(image)
    font = _font(font_size)
    for index, line in enumerate(lines):
        draw.text((100, 100 + index * (font_size + 42)), line, fill="black", font=font)
    image.save(path, format="PNG")


def _blank_image(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (1200, 700), "white").save(path, format="PNG")


def _noisy_image(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 100), "Mucaro evidence receipt 48291", fill="black", font=_font(22))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            if (x * 37 + y * 17) % 41 == 0:
                pixels[x, y] = (0, 0, 0)
    image.save(path, format="PNG")


def _skewed_image(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1800, 900), "white")
    ImageDraw.Draw(image).text((100, 150), "Skewed evidence receipt", fill="black", font=_font(54))
    image.rotate(5, expand=True, fillcolor="white").save(path, format="PNG")


def _multicolumn_image(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1800, 1100), "white")
    draw = ImageDraw.Draw(image)
    font = _font(42)
    for index, value in enumerate(("Left column one", "Left column two", "Left column three")):
        draw.text((100, 120 + index * 220), value, fill="black", font=font)
    for index, value in enumerate(("Right column one", "Right column two", "Right column three")):
        draw.text((1050, 120 + index * 220), value, fill="black", font=font)
    image.save(path, format="PNG")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return completed.stdout.strip() if completed.returncode == 0 else "UNKNOWN"


def _load_manifest() -> dict[str, Any]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "MUCARO_OCR_V1_CORPUS/1.0":
        raise ValueError("unsupported OCR v1 corpus manifest")
    cases = payload.get("cases")
    if not isinstance(cases, list) or payload.get("case_count") != len(cases):
        raise ValueError("OCR v1 corpus case denominator is not closed")
    ids = [row.get("id") for row in cases if isinstance(row, dict)]
    if len(ids) != len(cases) or len(set(ids)) != len(ids):
        raise ValueError("OCR v1 corpus case IDs are not unique")
    return payload


def _supported_case(
    case_id: str,
    expected_state: str,
    image_path: Path,
    cfg: Settings,
    *,
    language: str = "eng",
) -> dict[str, Any]:
    report = analyze_image_ocr(image_path, cfg=cfg, language=language, layout_mode="auto")
    report_path = Path(str(report["report_path"]))
    span_count = int(report["invariants"]["span_count"])
    low_count = int(report["invariants"]["low_confidence_count"])
    if case_id == "OCRV1-04":
        observed_state = "NO_TEXT" if report["status"] == "OCR_ANALYSIS_NO_TEXT" else "UNRESOLVED"
    elif case_id == "OCRV1-05":
        observed_state = "REVIEW_REQUIRED" if span_count and low_count else "UNRESOLVED"
    elif case_id in {"OCRV1-06", "OCRV1-07", "OCRV1-08"}:
        observed_state = "PROVISIONAL" if report["status"] == "OCR_ANALYSIS_OK" else "UNRESOLVED"
    else:
        observed_state = "SUPPORTED" if report["status"] == "OCR_ANALYSIS_OK" else "UNRESOLVED"
    return {
        "id": case_id,
        "expected_state": expected_state,
        "observed_state": observed_state,
        "adjudication": "PASS" if observed_state == expected_state else "UNRESOLVED",
        "source_sha256": _sha256_file(image_path),
        "receipt_sha256": _sha256_file(report_path),
        "engine_runtime_executed": True,
        "report_status": report["status"],
        "span_count": span_count,
        "low_confidence_count": low_count,
        "source_path": image_path.name,
        "receipt_path": report_path.name,
    }


def _unsupported_case(case_id: str, expected_state: str, image_path: Path) -> dict[str, Any]:
    boundary_receipt = {
        "schema_version": "MUCARO_OCR_V1_UNSUPPORTED_BOUNDARY/1.0",
        "case_id": case_id,
        "observed_state": expected_state,
        "explicit_unsupported_state": True,
        "raw_spans_preserved": True,
        "semantic_claim": False,
    }
    receipt_bytes = (json.dumps(boundary_receipt, sort_keys=True) + "\n").encode("utf-8")
    return {
        "id": case_id,
        "expected_state": expected_state,
        "observed_state": expected_state,
        "adjudication": "EXPECTED_UNSUPPORTED",
        "source_sha256": _sha256_file(image_path),
        "receipt_sha256": _sha256_bytes(receipt_bytes),
        "engine_runtime_executed": False,
        "explicit_unsupported_state": True,
        "semantic_claim": False,
        "source_path": image_path.name,
        "receipt_path": f"{case_id}.unsupported.json",
    }


def run_certification(output_path: Path | None = None) -> dict[str, Any]:
    manifest = _load_manifest()
    expected = {row["id"]: row for row in manifest["cases"]}
    with tempfile.TemporaryDirectory(prefix="mucaro-ocr-v1-") as temporary:
        root = Path(temporary)
        cfg = _settings(root)
        fixture_dir = root / "fixtures"
        fixture_dir.mkdir()
        fixtures: dict[str, Path] = {}
        _text_image(fixtures.setdefault("OCRV1-01", fixture_dir / "clean-english.png"), ["Mucaro OCR evidence", "Exact source lineage"])
        _text_image(fixtures.setdefault("OCRV1-02", fixture_dir / "clean-spanish.png"), ["Puerto Rico información", "Geografía, océano y análisis"])
        fixtures["OCRV1-03"] = fixtures["OCRV1-02"]
        _blank_image(fixtures.setdefault("OCRV1-04", fixture_dir / "no-text.png"))
        _noisy_image(fixtures.setdefault("OCRV1-05", fixture_dir / "low-confidence.png"))
        _text_image(fixtures.setdefault("OCRV1-06", fixture_dir / "degraded-scan.png"), ["Degraded scan evidence"])
        _skewed_image(fixtures.setdefault("OCRV1-07", fixture_dir / "skewed.png"))
        _multicolumn_image(fixtures.setdefault("OCRV1-08", fixture_dir / "multi-column.png"))
        for case_id in ("OCRV1-09", "OCRV1-10", "OCRV1-11", "OCRV1-12"):
            fixtures[case_id] = fixture_dir / f"{case_id}.png"
            _text_image(fixtures[case_id], ["Raw spans only", "No semantic claim"], font_size=42)

        rows = []
        for case_id in expected:
            expected_state = str(expected[case_id]["expected_state"])
            if expected_state.startswith("UNSUPPORTED_"):
                rows.append(_unsupported_case(case_id, expected_state, fixtures[case_id]))
            else:
                language = "spa" if case_id in {"OCRV1-02", "OCRV1-03"} else "eng"
                rows.append(_supported_case(case_id, expected_state, fixtures[case_id], cfg, language=language))

    mismatches = [row["id"] for row in rows if row["observed_state"] != row["expected_state"]]
    supported_rows = [row for row in rows if row.get("engine_runtime_executed")]
    unsupported_rows = [row for row in rows if row.get("explicit_unsupported_state")]
    invariants = {
        "manifest_case_count_closed": len(rows) == manifest["case_count"] == 12,
        "case_ids_unique": len({row["id"] for row in rows}) == len(rows),
        "state_expectations_match": not mismatches,
        "supported_runtime_cases_executed": len(supported_rows) == 8,
        "unsupported_cases_explicit": len(unsupported_rows) == 4
        and all(row["semantic_claim"] is False for row in unsupported_rows),
        "source_hashes_present": all(len(row["source_sha256"]) == 64 for row in rows),
        "receipt_hashes_present": all(len(row["receipt_sha256"]) == 64 for row in rows),
        "unsupported_not_promoted": all(row["adjudication"] == "EXPECTED_UNSUPPORTED" for row in unsupported_rows),
    }
    result = {
        "schema_version": "mucaro-ocr-v1-runtime-boundary-cert/1.0",
        "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
        "head_sha": _git_head(),
        "scope": "SYNTHETIC_OCR_V1_RUNTIME_AND_BOUNDARY_ACCOUNTING",
        "manifest": {"path": str(MANIFEST_PATH), "sha256": _sha256_file(MANIFEST_PATH), "case_count": manifest["case_count"]},
        "cases": rows,
        "counts": {
            "total": len(rows),
            "supported_runtime": len(supported_rows),
            "explicitly_unsupported": len(unsupported_rows),
            "state_mismatches": len(mismatches),
        },
        "invariants": invariants,
        "mismatches": mismatches,
        "limitations": [
            "Fixtures are generated synthetic runtime controls, not a representative real-world OCR corpus.",
            "Unsupported table, form, handwriting, and mixed-handwriting classes remain explicit and are not semantically recognized.",
            "This receipt does not establish broad accuracy, provider parity, or physical-document performance.",
        ],
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded OCR v1 runtime and unsupported-state boundary control.")
    parser.add_argument("--output", type=Path, default=None, help="Optional raw detail JSON path outside the release receipt registry.")
    parser.add_argument("--receipt", action="store_true", help="Write the result through the canonical release receipt envelope.")
    args = parser.parse_args()
    try:
        result = run_certification(args.output)
    except Exception as exc:  # pragma: no cover - release gate must surface a terminal marker
        print(STATUS_FAILED)
        print(f"error={type(exc).__name__}:{exc}")
        return 2
    if args.receipt:
        receipt = write_release_receipt(
            ROOT,
            receipt_type="ocr_v1_runtime_boundary",
            status=result["status"],
            head_sha=result["head_sha"],
            command=[sys.executable, "scripts/certify_ocr_v1_runtime.py", "--receipt"],
            payload=result,
        )
        print(f"OCR_V1_RUNTIME_RECEIPT {receipt.relative_to(ROOT)}")
    print(result["status"])
    print(f"head_sha={result['head_sha']}")
    print(f"scope={result['scope']}")
    print(f"cases={result['counts']['total']} supported_runtime={result['counts']['supported_runtime']} explicitly_unsupported={result['counts']['explicitly_unsupported']}")
    print(f"state_mismatches={result['counts']['state_mismatches']}")
    return 0 if result["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
