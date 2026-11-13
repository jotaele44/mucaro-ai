from __future__ import annotations

import json
from pathlib import Path

from backend.app.format_registry import declared_format_registry
from backend.app.ingestion_benchmark import STATUS_OK, certify_ingestion_benchmark

ROOT = Path(__file__).resolve().parents[2]


def test_matrix010_frozen_registry_matches_runtime_declaration() -> None:
    frozen = json.loads((ROOT / "_architecture" / "ingest_format_registry_v1.json").read_text(encoding="utf-8"))
    assert frozen == declared_format_registry()
    assert frozen["format_count"] == 22
    assert len(frozen["formats"]) == 22


def test_matrix010_full_benchmark_closes_22_plus_8_denominator(tmp_path: Path) -> None:
    receipt = certify_ingestion_benchmark(ROOT, tmp_path / "workspace", head_sha="a" * 40)

    assert receipt["status"] == STATUS_OK
    assert receipt["head_sha"] == "a" * 40
    assert receipt["arithmetic"] == {
        "format_count_expected": 22,
        "format_count_observed": 22,
        "format_pass": 22,
        "format_fail": 0,
        "adversarial_count_expected": 8,
        "adversarial_count_observed": 8,
        "adversarial_pass": 8,
        "adversarial_fail": 0,
        "format_arithmetic_closes": True,
        "adversarial_arithmetic_closes": True,
    }
    assert all(receipt["denominator_checks"].values())
    assert all(receipt["terminal_checks"].values())
    assert len(receipt["receipt_sha256"]) == 64
    assert {row["suffix"] for row in receipt["format_rows"]} == {
        ".txt", ".md", ".csv", ".json", ".html", ".htm", ".docx", ".zip", ".eml", ".msg", ".pdf",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".wav", ".mp3", ".m4a", ".mp4", ".mov", ".webm",
    }
    assert [row["case_id"] for row in receipt["adversarial_rows"]] == [
        "MALFORMED",
        "MISLABELED",
        "EMPTY",
        "TRUNCATED",
        "PASSWORD_ACCESS_BLOCKED",
        "OVERSIZED",
        "ARCHIVE_ATTACK",
        "UNSUPPORTED",
    ]
