from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.app.retrieval_corpus_certification import RetrievalCorpusError, certify_retrieval_corpus


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def write_manifest(root: Path, *, synthetic: bool = False, expected_sha: str | None = None, min_sources: int = 2) -> Path:
    (root / "a.txt").write_text("Alpha public record.\n", encoding="utf-8")
    (root / "b.html").write_text("<html><body>Beta snapshot.</body></html>\n", encoding="utf-8")
    manifest = {
        "schema_version": "mucaro-retrieval-corpus/1.0",
        "corpus_id": "fixture-real-world",
        "frozen_at": "2026-09-16T00:00:00Z",
        "thresholds": {"min_sources": min_sources, "min_queries": 2, "min_formats": 2, "min_total_bytes": 20},
        "sources": [
            {
                "source_id": "a",
                "path": "a.txt",
                "sha256": expected_sha or sha(root / "a.txt"),
                "byte_size": (root / "a.txt").stat().st_size,
                "source_format": "txt",
                "origin": "public_record",
                "synthetic": synthetic,
            },
            {
                "source_id": "b",
                "path": "b.html",
                "sha256": sha(root / "b.html"),
                "byte_size": (root / "b.html").stat().st_size,
                "source_format": "html",
                "origin": "web_snapshot",
                "synthetic": False,
            },
        ],
        "queries": [
            {"query_id": "q1", "query": "Which record says alpha?", "expected_source_ids": ["a"], "category": "record"},
            {"query_id": "q2", "query": "Which snapshot says beta?", "expected_source_ids": ["b"], "category": "web"},
        ],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def evaluation_for(manifest_path: Path) -> dict[str, str]:
    manifest_sha = sha(manifest_path)
    return {
        "status": "RETRIEVAL_EVAL_OK",
        "corpus_manifest_sha256": manifest_sha,
        "source_ids_sha256": stable_hash(["a", "b"]),
        "query_ids_sha256": stable_hash(["q1", "q2"]),
    }


def test_ready_only_with_real_world_identity_threshold_and_eval_closure(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path)
    report = certify_retrieval_corpus(manifest, evaluation_receipt=evaluation_for(manifest))

    assert report["status"] == "RETRIEVAL_REAL_WORLD_CORPUS_CERT_READY"
    assert report["certification_ready"] is True
    assert report["residue_count"] == 0
    expected_bytes = (tmp_path / "a.txt").stat().st_size + (tmp_path / "b.html").stat().st_size
    assert report["counts"] == {"sources": 2, "queries": 2, "formats": 2, "total_bytes": expected_bytes}
    assert all(report["invariants"].values())
    assert report["allowed_denominator_event"] == "RT-09 OPEN->PROVISIONAL"
    assert len(report["report_sha256"]) == 64


def test_synthetic_source_blocks_real_world_claim(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path, synthetic=True)
    report = certify_retrieval_corpus(manifest, evaluation_receipt=evaluation_for(manifest))

    assert report["certification_ready"] is False
    assert "SYNTHETIC_OR_UNDECLARED:a" in report["material_failures"]
    assert report["invariants"]["all_sources_explicitly_nonsynthetic"] is False
    assert report["allowed_denominator_event"] is None


def test_source_hash_drift_blocks_readiness(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path, expected_sha="0" * 64)
    report = certify_retrieval_corpus(manifest, evaluation_receipt=evaluation_for(manifest))

    assert report["certification_ready"] is False
    assert "SHA256_MISMATCH:a" in report["material_failures"]
    assert report["invariants"]["all_source_identities_match"] is False


def test_threshold_shortfall_remains_explicit(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path, min_sources=3)
    report = certify_retrieval_corpus(manifest, evaluation_receipt=evaluation_for(manifest))

    assert report["certification_ready"] is False
    assert "MIN_SOURCES:2<3" in report["threshold_failures"]
    assert report["invariants"]["thresholds_closed"] is False


def test_evaluation_must_bind_exact_manifest_source_and_query_denominators(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path)
    receipt = evaluation_for(manifest)
    receipt["query_ids_sha256"] = "0" * 64
    report = certify_retrieval_corpus(manifest, evaluation_receipt=receipt)

    assert report["certification_ready"] is False
    assert report["evaluation_failures"] == ["EVALUATION_QUERY_DENOMINATOR_MISMATCH"]
    assert report["invariants"]["evaluation_bound_to_exact_denominator"] is False


def test_unknown_query_binding_is_residue_not_silent_drop(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["queries"][0]["expected_source_ids"] = ["missing"]
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    receipt = {
        "status": "RETRIEVAL_EVAL_OK",
        "corpus_manifest_sha256": sha(manifest),
        "source_ids_sha256": stable_hash(["a", "b"]),
        "query_ids_sha256": stable_hash(["q1", "q2"]),
    }
    report = certify_retrieval_corpus(manifest, evaluation_receipt=receipt)

    assert report["certification_ready"] is False
    assert "QUERY_UNKNOWN_SOURCE:q1:missing" in report["material_failures"]
    assert report["invariants"]["all_query_bindings_resolve"] is False


def test_duplicate_ids_and_path_escape_fail_closed(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sources"][1]["source_id"] = "a"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RetrievalCorpusError) as duplicate:
        certify_retrieval_corpus(manifest)
    assert duplicate.value.status == "ERROR_RETRIEVAL_CORPUS_SOURCE_DUPLICATE"

    manifest = write_manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sources"][0]["path"] = "../outside.txt"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RetrievalCorpusError) as escape:
        certify_retrieval_corpus(manifest)
    assert escape.value.status == "ERROR_RETRIEVAL_CORPUS_SOURCE_ESCAPE"
