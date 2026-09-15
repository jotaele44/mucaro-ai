from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

OCR_QUALITY_SCHEMA_VERSION = "mucaro_ocr_quality_v1"


class OCRQualityError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class OCRQualityPolicy:
    low_threshold: float = 50.0
    high_threshold: float = 85.0
    max_low_ratio: float = 0.25
    max_unknown_ratio: float = 0.05
    min_mean_confidence: float = 70.0

    def validate(self) -> None:
        if not (0 <= self.low_threshold < self.high_threshold <= 100):
            raise OCRQualityError("ERROR_OCR_QUALITY_THRESHOLDS", "confidence thresholds must satisfy 0 <= low < high <= 100")
        for label, value in (("max_low_ratio", self.max_low_ratio), ("max_unknown_ratio", self.max_unknown_ratio)):
            if not 0 <= value <= 1:
                raise OCRQualityError("ERROR_OCR_QUALITY_RATIO", f"{label} must be between 0 and 1")
        if not 0 <= self.min_mean_confidence <= 100:
            raise OCRQualityError("ERROR_OCR_QUALITY_MEAN", "min_mean_confidence must be between 0 and 100")


def evaluate_ocr_quality(report: dict[str, Any], *, policy: OCRQualityPolicy = OCRQualityPolicy()) -> dict[str, Any]:
    policy.validate()
    if not isinstance(report, dict) or report.get("schema_version") != "mucaro_ocr_report_v1":
        raise OCRQualityError("ERROR_OCR_QUALITY_REPORT", "expected a mucaro_ocr_report_v1 report")
    spans = report.get("spans")
    if not isinstance(spans, list):
        raise OCRQualityError("ERROR_OCR_QUALITY_SPANS", "OCR report spans must be an array")
    invariant_span_count = (report.get("invariants") or {}).get("span_count") if isinstance(report.get("invariants"), dict) else None
    if invariant_span_count != len(spans):
        raise OCRQualityError("ERROR_OCR_QUALITY_ACCOUNTING", "reported span_count does not equal saved spans")

    low = 0
    medium = 0
    high = 0
    unknown = 0
    known_values: list[float] = []
    contradictions: list[dict[str, Any]] = []
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            raise OCRQualityError("ERROR_OCR_QUALITY_SPAN", f"span {index} is not an object")
        confidence = span.get("confidence")
        state = span.get("confidence_state")
        if confidence is None:
            expected_state = "UNKNOWN"
            unknown += 1
        elif isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= float(confidence) <= 100:
            value = float(confidence)
            known_values.append(value)
            if value < policy.low_threshold:
                expected_state = "LOW"
                low += 1
            elif value >= policy.high_threshold:
                expected_state = "HIGH"
                high += 1
            else:
                expected_state = "MEDIUM"
                medium += 1
        else:
            raise OCRQualityError("ERROR_OCR_QUALITY_CONFIDENCE", f"span {index} confidence is outside 0-100 or invalid")
        if state != expected_state:
            contradictions.append(
                {
                    "span_index": index,
                    "recorded_state": state,
                    "computed_state": expected_state,
                    "classification": "CLASS",
                }
            )

    total = len(spans)
    low_ratio = low / total if total else 0.0
    unknown_ratio = unknown / total if total else 0.0
    mean_confidence = sum(known_values) / len(known_values) if known_values else None
    gates = {
        "state_consistency": not contradictions,
        "low_ratio_within_policy": low_ratio <= policy.max_low_ratio,
        "unknown_ratio_within_policy": unknown_ratio <= policy.max_unknown_ratio,
        "mean_confidence_within_policy": mean_confidence is not None and mean_confidence >= policy.min_mean_confidence if total else False,
    }
    if total == 0:
        status = "OCR_QUALITY_NO_TEXT"
    elif all(gates.values()):
        status = "OCR_QUALITY_PASS"
    else:
        status = "OCR_QUALITY_REVIEW"

    payload = {
        "schema_version": OCR_QUALITY_SCHEMA_VERSION,
        "status": status,
        "source_report_id": report.get("report_id"),
        "source_report_sha256": report.get("report_sha256"),
        "policy": {
            "low_threshold": policy.low_threshold,
            "high_threshold": policy.high_threshold,
            "max_low_ratio": policy.max_low_ratio,
            "max_unknown_ratio": policy.max_unknown_ratio,
            "min_mean_confidence": policy.min_mean_confidence,
        },
        "counts": {"total": total, "low": low, "medium": medium, "high": high, "unknown": unknown},
        "metrics": {
            "low_ratio": low_ratio,
            "unknown_ratio": unknown_ratio,
            "mean_confidence": mean_confidence,
        },
        "gates": gates,
        "contradictions": contradictions,
        "limitations": [
            "policy thresholds are deterministic acceptance criteria, not empirical calibration proof",
            "OCR confidence is engine-reported and must be calibrated on a frozen representative corpus before certification",
            "a quality PASS does not prove textual correctness or semantic interpretation",
        ],
        "falsification_criteria": [
            "reject span-accounting mismatch",
            "reject confidence values outside the declared 0-100 scale",
            "preserve confidence-state contradictions instead of silently rewriting source spans",
            "do not classify no-text OCR as quality PASS",
        ],
    }
    payload["receipt_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return payload
