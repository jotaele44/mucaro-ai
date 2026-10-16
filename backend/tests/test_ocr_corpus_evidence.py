import hashlib
import json
from pathlib import Path

import pytest

from backend.app.ocr_corpus_evidence import OcrCorpusEvidenceError, validate_ocr_v1_evidence


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_denominator(root: Path) -> Path:
    path=root/"denominator.json"
    path.write_text(json.dumps({
        "schema_version":"MUCARO_OCR_V1_CORPUS/1.0",
        "case_count": 2,
        "cases":[
            {"id":"OCRV1-01","class":"CLEAN_ENGLISH","expected_state":"SUPPORTED"},
            {"id":"OCRV1-09","class":"TABLE","expected_state":"UNSUPPORTED_SEMANTIC_TABLE"},
        ],
    }),encoding="utf-8")
    return path


def write_case(root: Path, name: str, content: bytes) -> tuple[str,str]:
    source=root/f"{name}.png"; source.write_bytes(content)
    receipt=root/f"{name}.json"; receipt.write_text(json.dumps({"status":"fixture"}),encoding="utf-8")
    return source.name, receipt.name


def test_evidence_ledger_closes_exact_case_denominator(tmp_path: Path) -> None:
    denominator=write_denominator(tmp_path)
    s1,r1=write_case(tmp_path,"one",b"image-one")
    s9,r9=write_case(tmp_path,"nine",b"image-nine")
    evidence=tmp_path/"evidence.json"
    evidence.write_text(json.dumps({
        "schema_version":"MUCARO_OCR_V1_EVIDENCE/1.0",
        "denominator_sha256":sha(denominator),
        "cases":[
            {"id":"OCRV1-01","source_path":s1,"source_sha256":sha(tmp_path/s1),"receipt_path":r1,"receipt_sha256":sha(tmp_path/r1),"observed_state":"SUPPORTED","adjudication":"PASS"},
            {"id":"OCRV1-09","source_path":s9,"source_sha256":sha(tmp_path/s9),"receipt_path":r9,"receipt_sha256":sha(tmp_path/r9),"observed_state":"UNSUPPORTED_SEMANTIC_TABLE","adjudication":"EXPECTED_UNSUPPORTED"},
        ],
    }),encoding="utf-8")
    report=validate_ocr_v1_evidence(denominator,evidence)
    assert report["status"] == "OCR_V1_EVIDENCE_READY"
    assert report["certification_ready"] is True
    assert report["counts"]["validated_cases"] == 2
    assert all(report["invariants"].values())


def test_missing_case_and_state_mismatch_remain_residue(tmp_path: Path) -> None:
    denominator=write_denominator(tmp_path)
    source,receipt=write_case(tmp_path,"one",b"image-one")
    evidence=tmp_path/"evidence.json"
    evidence.write_text(json.dumps({
        "schema_version":"MUCARO_OCR_V1_EVIDENCE/1.0",
        "denominator_sha256":sha(denominator),
        "cases":[
            {"id":"OCRV1-01","source_path":source,"source_sha256":sha(tmp_path/source),"receipt_path":receipt,"receipt_sha256":sha(tmp_path/receipt),"observed_state":"NO_TEXT","adjudication":"PASS"},
        ],
    }),encoding="utf-8")
    report=validate_ocr_v1_evidence(denominator,evidence)
    assert report["certification_ready"] is False
    assert "MISSING_CASE:OCRV1-09" in report["failures"]
    assert any(item.startswith("STATE_MISMATCH:OCRV1-01") for item in report["failures"])


def test_unsupported_case_cannot_be_silently_promoted(tmp_path: Path) -> None:
    denominator=write_denominator(tmp_path)
    s1,r1=write_case(tmp_path,"one",b"a")
    s9,r9=write_case(tmp_path,"nine",b"b")
    evidence=tmp_path/"evidence.json"
    evidence.write_text(json.dumps({
        "schema_version":"MUCARO_OCR_V1_EVIDENCE/1.0",
        "denominator_sha256":sha(denominator),
        "cases":[
            {"id":"OCRV1-01","source_path":s1,"source_sha256":sha(tmp_path/s1),"receipt_path":r1,"receipt_sha256":sha(tmp_path/r1),"observed_state":"SUPPORTED","adjudication":"PASS"},
            {"id":"OCRV1-09","source_path":s9,"source_sha256":sha(tmp_path/s9),"receipt_path":r9,"receipt_sha256":sha(tmp_path/r9),"observed_state":"UNSUPPORTED_SEMANTIC_TABLE","adjudication":"PASS"},
        ],
    }),encoding="utf-8")
    report=validate_ocr_v1_evidence(denominator,evidence)
    assert report["certification_ready"] is False
    assert "UNSUPPORTED_ADJUDICATION_REQUIRED:OCRV1-09" in report["failures"]


def test_denominator_count_drift_is_rejected_before_evidence_processing(tmp_path: Path) -> None:
    denominator = tmp_path / "denominator.json"
    denominator.write_text(
        json.dumps(
            {
                "schema_version": "MUCARO_OCR_V1_CORPUS/1.0",
                "case_count": 2,
                "cases": [{"id": "OCRV1-01", "expected_state": "SUPPORTED"}],
            }
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"schema_version": "MUCARO_OCR_V1_EVIDENCE/1.0", "cases": []}), encoding="utf-8")

    with pytest.raises(OcrCorpusEvidenceError) as raised:
        validate_ocr_v1_evidence(denominator, evidence)
    assert raised.value.status == "ERROR_OCR_DENOMINATOR_COUNT"


def test_symlinked_source_is_rejected_even_when_target_is_inside_root(tmp_path: Path) -> None:
    denominator = write_denominator(tmp_path)
    real_source, receipt = write_case(tmp_path, "one", b"image-one")
    source_link = tmp_path / "source-link.png"
    source_link.symlink_to(tmp_path / real_source)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "MUCARO_OCR_V1_EVIDENCE/1.0",
                "denominator_sha256": sha(denominator),
                "cases": [
                    {
                        "id": "OCRV1-01",
                        "source_path": source_link.name,
                        "source_sha256": sha(tmp_path / real_source),
                        "receipt_path": receipt,
                        "receipt_sha256": sha(tmp_path / receipt),
                        "observed_state": "SUPPORTED",
                        "adjudication": "PASS",
                    },
                    {
                        "id": "OCRV1-09",
                        "source_path": real_source,
                        "source_sha256": sha(tmp_path / real_source),
                        "receipt_path": receipt,
                        "receipt_sha256": sha(tmp_path / receipt),
                        "observed_state": "UNSUPPORTED_SEMANTIC_TABLE",
                        "adjudication": "EXPECTED_UNSUPPORTED",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OcrCorpusEvidenceError) as raised:
        validate_ocr_v1_evidence(denominator, evidence)
    assert raised.value.status == "ERROR_OCR_CORPUS_SYMLINK"
