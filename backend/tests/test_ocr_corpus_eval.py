import hashlib
import json
from pathlib import Path

import pytest

from backend.app import cli
from backend.app.config import Settings
from backend.app.ocr_corpus_eval import (
    DEFAULT_OCR_CORPUS_MANIFEST,
    OcrCorpusEvaluationError,
    _evaluate_case,
    load_ocr_corpus_manifest,
)


def make_settings(root: Path) -> Settings:
    return Settings(
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


def test_representative_manifest_is_frozen_and_source_bound() -> None:
    manifest, digest = load_ocr_corpus_manifest()
    assert manifest["corpus_id"] == "mucaro-ocr-v1-representative-bounded"
    assert manifest["case_count"] == 6 == len(manifest["cases"])
    assert len({row["id"] for row in manifest["cases"]}) == 6
    assert len(digest) == 64
    assert manifest["cases"][1]["expected_state"] == "PROVISIONAL"
    assert manifest["cases"][2]["expected_state"] == "NO_TEXT"


def test_manifest_source_hash_drift_fails_closed(tmp_path: Path) -> None:
    manifest = json.loads(DEFAULT_OCR_CORPUS_MANIFEST.read_text(encoding="utf-8"))
    manifest["cases"][0]["source_sha256"] = "0" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(OcrCorpusEvaluationError) as raised:
        load_ocr_corpus_manifest(path)
    assert raised.value.status == "ERROR_OCR_CORPUS_MANIFEST_SHA256"


def test_case_evaluation_keeps_accent_quality_separate_from_token_recall(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"fixture")
    report_path = tmp_path / "data" / "exports" / "report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("{}", encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    row = {
        "id": "OCRREP-TEST",
        "class": "SPANISH_ACCENTS",
        "input_kind": "image",
        "source_path": "source.png",
        "source_sha256": source_sha,
        "expected_state": "PROVISIONAL",
        "required_tokens": ["Puerto", "Rico"],
        "accent_tokens": ["información"],
        "required_warnings": [],
        "adjudication": "PROVISIONAL",
    }
    report = {
        "report_path": str(report_path),
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "status": "OCR_ANALYSIS_OK",
        "source": {"sha256": source_sha},
        "text": {"normalized": "Puerto Rico informaci n"},
        "warnings": [],
        "spans": [{"span_index": 0, "bbox": {"x": 1, "y": 1, "width": 10, "height": 10}}],
        "invariants": {"span_count": 1, "bounding_boxes_have_source_coordinates": True},
    }

    result = _evaluate_case(row, report, source, tmp_path)
    assert result["token_recall"] == 1.0
    assert result["bounded_observation_pass"] is True
    assert result["missing_accent_tokens"] == ["información"]
    assert "ACCENT_PRESERVATION_NOT_PROVEN" in result["quality_flags"]


def test_cli_strict_quality_fails_on_provisional_report(tmp_path: Path, capsys, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setattr(
        cli,
        "build_ocr_corpus_evaluation",
        lambda *args, **kwargs: {
            "status": "OCR_REPRESENTATIVE_CORPUS_READY",
            "accuracy_certified": False,
            "quality_state": "PROVISIONAL",
        },
    )

    assert cli.main(["certify", "ocr-corpus", "--strict-quality", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OCR_REPRESENTATIVE_CORPUS_READY"
