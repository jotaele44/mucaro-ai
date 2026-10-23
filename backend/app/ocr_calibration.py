from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

OCR_CALIBRATION_GOLD_SCHEMA = "MUCARO_OCR_CALIBRATION_GOLD/1.0"
OCR_CALIBRATION_REPORT_SCHEMA = "MUCARO_OCR_CALIBRATION_REPORT/1.0"
OCR_REPORT_SCHEMA = "mucaro_ocr_report_v1"


class OCRCalibrationError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _load_object(path: Path, status: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OCRCalibrationError(status, str(path)) from exc
    if not isinstance(value, dict):
        raise OCRCalibrationError(status, f"{path} must contain a JSON object")
    return value


def evaluate_ocr_calibration(
    report: dict[str, Any],
    gold: dict[str, Any],
    *,
    report_sha256: str,
    low_threshold: float = 50.0,
    high_threshold: float = 85.0,
) -> dict[str, Any]:
    """Evaluate explicit span labels without treating them as capability certification."""
    if not 0 <= low_threshold < high_threshold <= 100:
        raise OCRCalibrationError("ERROR_OCR_CALIBRATION_THRESHOLDS", "thresholds must satisfy 0 <= low < high <= 100")
    if report.get("schema_version") != OCR_REPORT_SCHEMA or report.get("operation") != "image_ocr":
        raise OCRCalibrationError("ERROR_OCR_CALIBRATION_REPORT_SCHEMA", "calibration requires an image OCR report")
    if gold.get("schema_version") != OCR_CALIBRATION_GOLD_SCHEMA:
        raise OCRCalibrationError("ERROR_OCR_CALIBRATION_GOLD_SCHEMA", "unsupported calibration gold schema")
    if gold.get("report_sha256") != report_sha256:
        raise OCRCalibrationError("ERROR_OCR_CALIBRATION_REPORT_SHA256", "gold labels are not bound to the OCR report bytes")

    spans = report.get("spans")
    labels = gold.get("labels")
    if not isinstance(spans, list) or not isinstance(labels, list):
        raise OCRCalibrationError("ERROR_OCR_CALIBRATION_LABELS", "report spans and gold labels must be arrays")
    if not spans:
        raise OCRCalibrationError("ERROR_OCR_CALIBRATION_LABELS", "calibration requires at least one saved OCR span")
    span_by_id: dict[int, dict[str, Any]] = {}
    for index, span in enumerate(spans):
        if not isinstance(span, dict) or not isinstance(span.get("span_index"), int) or isinstance(span.get("span_index"), bool) or span["span_index"] < 0:
            raise OCRCalibrationError("ERROR_OCR_CALIBRATION_SPAN_ID", f"invalid report span at index {index}")
        span_id = span["span_index"]
        if span_id in span_by_id:
            raise OCRCalibrationError("ERROR_OCR_CALIBRATION_SPAN_ID", f"duplicate report span_index {span_id}")
        span_by_id[span_id] = span

    label_by_id: dict[int, dict[str, Any]] = {}
    for index, label in enumerate(labels):
        if not isinstance(label, dict) or not isinstance(label.get("span_index"), int) or isinstance(label.get("span_index"), bool):
            raise OCRCalibrationError("ERROR_OCR_CALIBRATION_LABEL_ID", f"invalid gold label at index {index}")
        span_id = label["span_index"]
        if span_id in label_by_id:
            raise OCRCalibrationError("ERROR_OCR_CALIBRATION_LABEL_ID", f"duplicate gold span_index {span_id}")
        expected = label.get("expected_text")
        if not isinstance(expected, str):
            raise OCRCalibrationError("ERROR_OCR_CALIBRATION_EXPECTED_TEXT", f"gold label {span_id} expected_text must be a string")
        if span_id not in span_by_id:
            raise OCRCalibrationError("ERROR_OCR_CALIBRATION_LABEL_ID", f"gold label {span_id} has no report span")
        label_by_id[span_id] = label

    missing = sorted(set(span_by_id) - set(label_by_id))
    if missing:
        raise OCRCalibrationError("ERROR_OCR_CALIBRATION_INCOMPLETE", f"missing gold labels for span_index values: {missing}")

    rows: list[dict[str, Any]] = []
    known_confidences: list[float] = []
    correct_confidences: list[float] = []
    incorrect_confidences: list[float] = []
    brier_terms: list[float] = []
    for span_id in sorted(span_by_id):
        span = span_by_id[span_id]
        label = label_by_id[span_id]
        observed = span.get("raw_text")
        if not isinstance(observed, str):
            raise OCRCalibrationError("ERROR_OCR_CALIBRATION_OBSERVED_TEXT", f"report span {span_id} raw_text must be a string")
        expected = label["expected_text"]
        correct = _normalize_text(observed) == _normalize_text(expected)
        confidence = span.get("confidence")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 100:
                raise OCRCalibrationError("ERROR_OCR_CALIBRATION_CONFIDENCE", f"report span {span_id} confidence must be finite in [0, 100]")
            confidence = float(confidence)
            known_confidences.append(confidence)
            (correct_confidences if correct else incorrect_confidences).append(confidence)
            brier_terms.append((confidence / 100.0 - float(correct)) ** 2)
        rows.append(
            {
                "span_index": span_id,
                "observed_raw_text": observed,
                "expected_text": expected,
                "exact_normalized_match": correct,
                "confidence": confidence,
                "confidence_state": span.get("confidence_state"),
            }
        )

    labeled_count = len(rows)
    correct_count = sum(1 for row in rows if row["exact_normalized_match"])
    low_rows = [row for row in rows if row["confidence"] is not None and row["confidence"] < low_threshold]
    high_rows = [row for row in rows if row["confidence"] is not None and row["confidence"] >= high_threshold]
    bins = []
    for lower, upper in ((0.0, 20.0), (20.0, 40.0), (40.0, 60.0), (60.0, 80.0), (80.0, 100.000001)):
        selected = [row for row in rows if row["confidence"] is not None and lower <= row["confidence"] < upper]
        bins.append(
            {
                "lower": lower,
                "upper_exclusive": 100.0 if upper > 100 else upper,
                "count": len(selected),
                "mean_confidence": _mean([float(row["confidence"]) for row in selected]),
                "accuracy": sum(1 for row in selected if row["exact_normalized_match"]) / len(selected) if selected else None,
            }
        )

    return {
        "schema_version": OCR_CALIBRATION_REPORT_SCHEMA,
        "status": "OCR_CALIBRATION_EVAL_OK",
        "certification_ready": False,
        "scope": "EXPLICIT_SPAN_GOLD_EXACT_MATCH",
        "report_sha256": report_sha256,
        "gold_schema_version": gold["schema_version"],
        "thresholds": {"low": low_threshold, "high": high_threshold},
        "counts": {
            "report_spans": len(spans),
            "gold_labels": len(labels),
            "labeled_spans": labeled_count,
            "correct_spans": correct_count,
            "incorrect_spans": labeled_count - correct_count,
            "known_confidence_spans": len(known_confidences),
            "low_confidence_spans": len(low_rows),
            "high_confidence_spans": len(high_rows),
        },
        "metrics": {
            "exact_normalized_accuracy": correct_count / labeled_count if labeled_count else None,
            "confidence_coverage": len(known_confidences) / labeled_count if labeled_count else None,
            "brier_score": sum(brier_terms) / len(brier_terms) if brier_terms else None,
            "mean_confidence_correct": _mean(correct_confidences),
            "mean_confidence_incorrect": _mean(incorrect_confidences),
            "low_confidence_error_rate": sum(1 for row in low_rows if not row["exact_normalized_match"]) / len(low_rows) if low_rows else None,
            "high_confidence_precision": sum(1 for row in high_rows if row["exact_normalized_match"]) / len(high_rows) if high_rows else None,
            "reliability_bins": bins,
        },
        "labels": rows,
        "invariants": {
            "report_gold_binding_closed": True,
            "span_label_accounting_closed": len(labels) == len(spans) == labeled_count,
            "span_ids_unique": len(span_by_id) == len(spans),
            "confidence_values_bounded": all(row["confidence"] is None or 0 <= row["confidence"] <= 100 for row in rows),
            "accuracy_is_exact_normalized_match_only": True,
        },
        "limitations": [
            "Exact normalized span matching is a deterministic label metric, not semantic equivalence or character-level edit distance.",
            "Gold labels must be independently authored; this evaluator does not create labels from OCR output.",
            "Synthetic or narrow fixtures do not establish representative document accuracy or provider parity.",
            "This report remains evaluation evidence and does not change the OCR capability state to certified.",
        ],
        "falsification_criteria": [
            "reject when the gold report hash does not match the evaluated OCR report bytes",
            "reject when any saved span lacks exactly one gold label",
            "reject confidence values outside the finite 0-100 scale",
            "do not treat missing confidence as a zero-confidence score",
        ],
    }


def evaluate_ocr_calibration_files(report_path: Path, gold_path: Path, *, low_threshold: float = 50.0, high_threshold: float = 85.0) -> dict[str, Any]:
    report = _load_object(report_path, "ERROR_OCR_CALIBRATION_REPORT_READ")
    gold = _load_object(gold_path, "ERROR_OCR_CALIBRATION_GOLD_READ")
    return evaluate_ocr_calibration(
        report,
        gold,
        report_sha256=_sha256(report_path),
        low_threshold=low_threshold,
        high_threshold=high_threshold,
    )
