from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.app.cli import main
from backend.app.config import Settings
from backend.app.ocr_calibration import OCRCalibrationError, evaluate_ocr_calibration


def make_settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def report_fixture() -> dict:
    return {
        "schema_version": "mucaro_ocr_report_v1",
        "operation": "image_ocr",
        "spans": [
            {"span_index": 0, "raw_text": "Puerto", "confidence": 96.0, "confidence_state": "HIGH"},
            {"span_index": 1, "raw_text": "Rico", "confidence": 82.0, "confidence_state": "MEDIUM"},
            {"span_index": 2, "raw_text": "reciept", "confidence": 22.0, "confidence_state": "LOW"},
        ],
    }


def gold_fixture(report_path: Path) -> dict:
    return {
        "schema_version": "MUCARO_OCR_CALIBRATION_GOLD/1.0",
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "labels": [
            {"span_index": 0, "expected_text": "Puerto"},
            {"span_index": 1, "expected_text": "Rico"},
            {"span_index": 2, "expected_text": "receipt"},
        ],
    }


def test_calibration_reports_exact_accuracy_and_confidence_metrics(tmp_path: Path) -> None:
    report = report_fixture()
    report_path = tmp_path / "ocr-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    gold = gold_fixture(report_path)
    result = evaluate_ocr_calibration(
        report,
        gold,
        report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
    )

    assert result["status"] == "OCR_CALIBRATION_EVAL_OK"
    assert result["certification_ready"] is False
    assert result["counts"]["correct_spans"] == 2
    assert result["metrics"]["exact_normalized_accuracy"] == pytest.approx(2 / 3)
    assert result["metrics"]["confidence_coverage"] == 1.0
    assert result["metrics"]["high_confidence_precision"] == 1.0
    assert result["metrics"]["low_confidence_error_rate"] == 1.0
    assert all(result["invariants"].values())


def test_calibration_rejects_incomplete_gold_labels(tmp_path: Path) -> None:
    report = report_fixture()
    report_path = tmp_path / "ocr-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    gold = gold_fixture(report_path)
    gold["labels"] = gold["labels"][:2]

    with pytest.raises(OCRCalibrationError) as exc:
        evaluate_ocr_calibration(
            report,
            gold,
            report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        )
    assert exc.value.status == "ERROR_OCR_CALIBRATION_INCOMPLETE"


def test_calibration_rejects_report_hash_mismatch(tmp_path: Path) -> None:
    report = report_fixture()
    report_path = tmp_path / "ocr-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    gold = gold_fixture(report_path)
    gold["report_sha256"] = "0" * 64

    with pytest.raises(OCRCalibrationError) as exc:
        evaluate_ocr_calibration(
            report,
            gold,
            report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        )
    assert exc.value.status == "ERROR_OCR_CALIBRATION_REPORT_SHA256"


def test_calibration_rejects_empty_or_negative_span_denominators(tmp_path: Path) -> None:
    report = report_fixture()
    report_path = tmp_path / "ocr-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    gold = gold_fixture(report_path)
    empty_report = {**report, "spans": []}
    with pytest.raises(OCRCalibrationError) as empty:
        evaluate_ocr_calibration(empty_report, {**gold, "report_sha256": "unused", "labels": []}, report_sha256="unused")
    assert empty.value.status == "ERROR_OCR_CALIBRATION_LABELS"

    negative_report = {**report, "spans": [{**report["spans"][0], "span_index": -1}, *report["spans"][1:]]}
    with pytest.raises(OCRCalibrationError) as negative:
        evaluate_ocr_calibration(negative_report, {**gold, "report_sha256": "unused"}, report_sha256="unused")
    assert negative.value.status == "ERROR_OCR_CALIBRATION_SPAN_ID"


def test_cli_ocr_calibrate_emits_evaluation(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    report_path = tmp_path / "ocr-report.json"
    report_path.write_text(json.dumps(report_fixture()), encoding="utf-8")
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold_fixture(report_path)), encoding="utf-8")

    assert main(["ocr", "calibrate", str(report_path), str(gold_path), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OCR_CALIBRATION_EVAL_OK"
    assert payload["scope"] == "EXPLICIT_SPAN_GOLD_EXACT_MATCH"
