import json
from pathlib import Path

MANIFEST=Path(__file__).resolve().parents[2]/"_architecture"/"ocr_v1_corpus_manifest.json"

def load(): return json.loads(MANIFEST.read_text(encoding="utf-8"))

def test_ocr_v1_corpus_is_frozen_and_unique():
    data=load(); cases=data["cases"]; assert data["schema_version"]=="MUCARO_OCR_V1_CORPUS/1.0"; assert data["case_count"]==12==len(cases); ids=[c["id"] for c in cases]; assert len(ids)==len(set(ids)); assert ids==[f"OCRV1-{i:02d}" for i in range(1,13)]

def test_unsupported_semantic_classes_remain_explicit():
    states={c["class"]:c["expected_state"] for c in load()["cases"]}; assert states["TABLE"]=="UNSUPPORTED_SEMANTIC_TABLE"; assert states["FORM"]=="UNSUPPORTED_SEMANTIC_FORM"; assert states["HANDWRITING"]=="UNSUPPORTED_HANDWRITING"; assert states["MIXED_PRINT_HANDWRITING"]=="UNSUPPORTED_MIXED_HANDWRITING"

def test_fixture_bytes_are_required_before_certification():
    data=load(); assert all("source_sha256" in c["required_evidence"] for c in data["cases"]); assert "Manifest presence freezes the denominator only" in data["certification_rule"]
