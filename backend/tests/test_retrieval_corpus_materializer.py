import hashlib
from pathlib import Path

import pytest

from backend.app.retrieval_corpus_materializer import build_real_world_corpus_manifest
from backend.app.retrieval_corpus_certification import RetrievalCorpusError


def test_materializer_computes_identity_and_preserves_real_world_origin(tmp_path: Path) -> None:
    source = tmp_path / "record.txt"
    source.write_bytes(b"public record bytes")
    manifest = build_real_world_corpus_manifest(
        tmp_path,
        corpus_id="real-1",
        frozen_at="2026-09-19T00:00:00Z",
        sources=[{"source_id":"record","path":"record.txt","origin":"public_record","synthetic":False,"provenance_ref":"acq-1"}],
        queries=[{"query_id":"q1","query":"Which record?","expected_source_ids":["record"],"category":"record"}],
        thresholds={"min_sources":1,"min_queries":1,"min_formats":1,"min_total_bytes":1},
    )
    row=manifest["sources"][0]
    assert row["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row["byte_size"] == len(source.read_bytes())
    assert row["origin"] == "public_record"
    assert row["synthetic"] is False
    assert row["provenance_ref"] == "acq-1"


def test_materializer_refuses_synthetic_or_unsupported_origin(tmp_path: Path) -> None:
    (tmp_path/"a.txt").write_text("x",encoding="utf-8")
    base=dict(corpus_id="c",frozen_at="now",queries=[{"query_id":"q","query":"q","expected_source_ids":["a"]}],thresholds={"min_sources":1,"min_queries":1,"min_formats":1,"min_total_bytes":1})
    with pytest.raises(RetrievalCorpusError) as synthetic:
        build_real_world_corpus_manifest(tmp_path,sources=[{"source_id":"a","path":"a.txt","origin":"public_record","synthetic":True}],**base)
    assert synthetic.value.status == "ERROR_RETRIEVAL_CORPUS_NONSYNTHETIC_REQUIRED"
    with pytest.raises(RetrievalCorpusError) as origin:
        build_real_world_corpus_manifest(tmp_path,sources=[{"source_id":"a","path":"a.txt","origin":"fixture","synthetic":False}],**base)
    assert origin.value.status == "ERROR_RETRIEVAL_CORPUS_ORIGIN"


def test_materializer_rejects_escape_and_unknown_query_binding(tmp_path: Path) -> None:
    outside=tmp_path.parent/"outside.txt"
    outside.write_text("x",encoding="utf-8")
    with pytest.raises(RetrievalCorpusError) as escape:
        build_real_world_corpus_manifest(
            tmp_path,corpus_id="c",frozen_at="now",
            sources=[{"source_id":"a","path":"../outside.txt","origin":"public_record","synthetic":False}],
            queries=[{"query_id":"q","query":"q","expected_source_ids":["a"]}],
            thresholds={"min_sources":1,"min_queries":1,"min_formats":1,"min_total_bytes":1},
        )
    assert escape.value.status == "ERROR_RETRIEVAL_CORPUS_SOURCE_ESCAPE"

    (tmp_path/"a.txt").write_text("x",encoding="utf-8")
    with pytest.raises(RetrievalCorpusError) as unknown:
        build_real_world_corpus_manifest(
            tmp_path,corpus_id="c",frozen_at="now",
            sources=[{"source_id":"a","path":"a.txt","origin":"public_record","synthetic":False}],
            queries=[{"query_id":"q","query":"q","expected_source_ids":["missing"]}],
            thresholds={"min_sources":1,"min_queries":1,"min_formats":1,"min_total_bytes":1},
        )
    assert unknown.value.status == "ERROR_RETRIEVAL_CORPUS_QUERY_BINDINGS"
