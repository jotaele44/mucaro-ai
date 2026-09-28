import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_rt09_denominator_is_large_real_world_and_mixed_format() -> None:
    payload = json.loads((ROOT / "_architecture/retrieval_real_world_corpus_v1.json").read_text(encoding="utf-8"))
    sources = payload["sources"]
    queries = payload["queries"]
    assert payload["schema_version"] == "MUCARO_RT09_REAL_WORLD_CORPUS_DENOMINATOR/1.0"
    assert len(sources) == 24
    assert len(queries) == 24
    assert payload["thresholds"] == {
        "min_sources": 24,
        "min_queries": 24,
        "min_formats": 3,
        "min_total_bytes": 2000000,
    }
    assert len({row["source_id"] for row in sources}) == 24
    assert {row["source_format"] for row in sources} == {"txt", "html", "pdf"}
    assert all(row["synthetic"] is False and row["origin"] == "public_record" for row in sources)
    allowed = set(payload["acquisition"]["allowed_hosts"])
    assert allowed == {"www.rfc-editor.org", "www.w3.org", "nvlpubs.nist.gov"}
    source_ids = {row["source_id"] for row in sources}
    assert all(set(row["expected_source_ids"]) <= source_ids for row in queries)


def test_rt09_certifier_markers_are_fail_closed() -> None:
    from scripts import certify_retrieval_real_world_live as cert
    assert cert.STATUS_OK == "RT09_REAL_WORLD_CORPUS_CERT_OK"
    assert cert.STATUS_FAILED == "RT09_REAL_WORLD_CORPUS_CERT_FAILED"
