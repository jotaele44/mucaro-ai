from __future__ import annotations

import csv
import json
from pathlib import Path

from backend.app.agent_checkpoints import create_checkpoint, get_checkpoint
from backend.app.config import Settings
from backend.app.model_failover import FailoverPolicy
from backend.app.model_router import ModelProviderError
from backend.app.ingestion import ingest_web_snapshot
from backend.app.services import answer_question
from backend.app.utils import write_json
from backend.app.web_browsing import WEB_SNAPSHOT_SCHEMA_VERSION
from backend.app.vector_index import build_persistent_index


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


def seed_chunks(cfg: Settings) -> None:
    cfg.ensure_dirs()
    chunks = [
        {
            "chunk_id": "chunk_a",
            "source_id": "src_a",
            "chunk_index": 0,
            "text": "Source lineage requires cited chunk IDs.",
        }
    ]
    write_json(cfg.processed_dir / "document_chunks.json", chunks)
    build_persistent_index(chunks, cfg)


def test_answer_question_blocks_live_provider_without_explicit_permission(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)

    result = answer_question("What does source lineage require?", model_provider="anthropic", cfg=cfg)

    assert result["status"] == "ERROR_MODEL_ROUTE_BLOCKED"
    assert result["retrieved_chunks"]
    assert result["model_route"]["constraints"]["allow_live"] is False
    assert result["model_route"]["invariants"]["no_implicit_live_escalation"] is True


def test_answer_question_flags_uncited_live_answer(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    def fake_generate_answer(*args, **kwargs):
        return {
            "model_provider": "anthropic",
            "model_name": "fake-live",
            "answer": "This answer intentionally lacks chunk citations.",
            "cited_chunk_ids": [],
        }

    monkeypatch.setattr("backend.app.services.generate_answer", fake_generate_answer)

    result = answer_question("What does source lineage require?", model_provider="mock", cfg=cfg, persist=False)

    assert result["citation_status"] == "CITATION_ERROR"
    assert "ERROR_UNCITED_ANSWER" in result["validation_flags"]
    assert result["evidence_contract"]["claim_type"] == "UNKNOWN"
    assert result["evidence_contract"]["uncertainty_state"] == "UNRESOLVED"


def test_answer_question_accepts_persistent_retrieval_strategy(tmp_path: Path):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    result = answer_question(
        "What does source lineage require?",
        retrieval_strategy="persistent",
        model_provider="mock",
        cfg=cfg,
        persist=False,
    )

    assert result["citation_status"] == "CITATION_OK"
    assert result["retrieval_strategy"] == "persistent_tfidf_v1"


def test_answer_question_exports_citation_trace_records(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    def fake_generate_answer(*args, **kwargs):
        return {
            "model_provider": "mock",
            "model_name": "fake-cited",
            "answer": "The answer cites chunk_a.",
            "cited_chunk_ids": ["chunk_a"],
        }

    monkeypatch.setattr("backend.app.services.generate_answer", fake_generate_answer)

    result = answer_question("What does source lineage require?", model_provider="mock", cfg=cfg)

    assert result["citation_status"] == "CITATION_OK"
    assert result["source_ids"] == ["src_a"]
    assert result["evidence_contract"]["claim_type"] == "COMPUTED"
    assert result["evidence_contract"]["source_identity_type"] == "SOURCE_ID_PLUS_CHUNK_ID"
    citation = result["cited_chunks"][0]
    assert citation["citation_order"] == 1
    assert citation["retrieval_rank"] == 1
    assert citation["chunk_id"] == "chunk_a"
    assert citation["source_id"] == "src_a"
    assert citation["chunk_index"] == 0
    assert citation["text_snippet"] == "Source lineage requires cited chunk IDs."
    assert citation["score"] > 0
    assert citation["retrieval_strategy"] == "hybrid_tfidf_v1"
    assert citation["matched_terms"] == ["lineage", "source"]

    json_path = Path(result["export_paths"]["json"])
    md_path = Path(result["export_paths"]["markdown"])
    csv_path = Path(result["export_paths"]["csv"])
    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported["cited_chunks"][0]["retrieval_rank"] == 1
    assert "source_id=src_a retrieval_rank=1" in md_path.read_text(encoding="utf-8")
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["run_id"] == result["run_id"]
    assert rows[0]["chunk_id"] == "chunk_a"
    assert rows[0]["source_id"] == "src_a"
    assert rows[0]["retrieval_rank"] == "1"


def test_answer_question_carries_web_snapshot_citation_metadata(tmp_path: Path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    snapshot_path = cfg.export_dir / "web_snapshots" / "websnap_fixture.json"
    write_json(
        snapshot_path,
        {
            "schema_version": WEB_SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": "websnap_fixture",
            "requested_url": "https://example.com/brief",
            "fetch_url": "https://example.com/brief",
            "final_url": "https://example.com/brief",
            "redirect_chain": [],
            "fragment_removed": False,
            "retrieval_started_utc": "2026-09-10T00:00:00Z",
            "retrieval_utc": "2026-09-10T00:00:01Z",
            "http_status": 200,
            "content_type": "text/html; charset=utf-8",
            "content_sha256": "4d70936fef2a7cae9c35d7adeccf0fb553981e9f1df96a20d27cc3f5b7173fbb",
            "content_size_bytes": 80,
            "content_base64": "",
            "parser_name": "web_snapshot_preview_v1",
            "parser_warnings": [],
            "charset": "utf-8",
            "text_preview": "Governed browsing citations preserve URL hash and retrieval time.",
            "policy": {"timeout_seconds": 5, "max_bytes": 1000, "allow_private_hosts": False},
        },
    )
    ingested = ingest_web_snapshot(snapshot_path, cfg)

    result = answer_question("What do governed browsing citations preserve?", model_provider="mock", cfg=cfg)

    assert result["citation_status"] == "CITATION_OK"
    citation = result["cited_chunks"][0]
    assert citation["source_id"] == ingested["source"]["source_id"]
    assert citation["source_format"] == "web_snapshot"
    assert citation["source_url"] == "https://example.com/brief"
    assert citation["snapshot_id"] == "websnap_fixture"
    assert citation["snapshot_sha256"] == ingested["snapshot_sha256"]
    assert citation["content_sha256"] == "4d70936fef2a7cae9c35d7adeccf0fb553981e9f1df96a20d27cc3f5b7173fbb"
    assert citation["retrieval_utc"] == "2026-09-10T00:00:01Z"
    md_text = Path(result["export_paths"]["markdown"]).read_text(encoding="utf-8")
    assert "source_format=web_snapshot" in md_text
    assert "source_url=https://example.com/brief" in md_text
    with Path(result["export_paths"]["csv"]).open(encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f))
    assert row["source_format"] == "web_snapshot"
    assert row["source_url"] == "https://example.com/brief"
    assert row["snapshot_id"] == "websnap_fixture"



def test_answer_question_binds_route_and_bounded_memory_receipts(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    observed = {}

    def fake_generate_answer(*args, **kwargs):
        observed["provider"] = kwargs["model_provider"]
        observed["memory_context"] = kwargs["memory_context"]
        return {
            "model_provider": kwargs["model_provider"],
            "model_name": kwargs["model_name"] or "mock",
            "answer": "The answer cites chunk_a.",
            "cited_chunk_ids": ["chunk_a"],
        }

    monkeypatch.setattr("backend.app.services.generate_answer", fake_generate_answer)
    memory = [
        {"memory_id": "m1", "kind": "preference", "content": " Prefer concise answers. ", "enabled": True, "source": "test"},
        {"memory_id": "m2", "kind": "preference", "content": "Prefer concise answers.", "enabled": True, "source": "test"},
        {"memory_id": "m3", "kind": "fact", "content": "Puerto Rico", "enabled": False, "source": "test"},
    ]

    result = answer_question(
        "What does source lineage require?",
        model_provider="mock",
        memory_context=memory,
        cfg=cfg,
        persist=False,
    )

    assert result["citation_status"] == "CITATION_OK"
    assert observed["provider"] == "mock"
    assert [item["memory_id"] for item in observed["memory_context"]] == ["m1"]
    assert result["memory_context_count"] == 1
    assert result["memory_ids"] == ["m1"]
    assert result["memory_excluded_count"] == 2
    assert len(result["memory_selection_sha256"]) == 64
    assert len(result["model_route_sha256"]) == 64
    assert result["model_route_selection_reason"] == "EXPLICIT_PREFERRED_PROVIDER"


def test_answer_question_explicit_mock_fallback_is_policy_driven(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    result = answer_question(
        "What does source lineage require?",
        model_provider="anthropic",
        allow_mock_fallback=True,
        cfg=cfg,
        persist=False,
    )

    assert result["citation_status"] == "CITATION_OK"
    assert result["model_provider"] == "mock"
    assert result["model_route_fallback_reason"] in {"PRIMARY_ROUTE_BLOCKED", "ERROR_MODEL_ROUTING_LIVE_NOT_ALLOWED"}
    assert len(result["model_route_sha256"]) == 64



def _failover_route(*providers: str) -> dict:
    return {
        "status": "MODEL_ROUTE_READY",
        "selected_provider": providers[0],
        "selected_model": f"{providers[0]}-model",
        "selection_reason": "TEST_ROUTE",
        "route_sha256": "a" * 64,
        "failover_order": [
            {"provider": provider, "model": f"{provider}-model"}
            for provider in providers
        ],
    }


def test_answer_question_records_single_provider_attempt(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.setattr("backend.app.services.plan_model_route", lambda **kwargs: _failover_route("mock"))

    result = answer_question("What does source lineage require?", cfg=cfg, persist=False)

    assert result["model_failed_over"] is False
    assert [attempt["status"] for attempt in result["model_attempts"]] == ["OK"]


def test_answer_question_provider_outage_pauses_checkpoint_without_identity_drift(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.setattr("backend.app.services.plan_model_route", lambda **kwargs: _failover_route("anthropic"))

    def unavailable(*args, **kwargs):
        raise ModelProviderError("ERROR_MODEL_PROVIDER_UNAVAILABLE", "outage")

    monkeypatch.setattr("backend.app.services.generate_answer", unavailable)
    checkpoint = create_checkpoint(
        session_id="session-v04",
        run_id="run-v04",
        objective="Answer with live provider",
        source_sha="c" * 40,
        state="EXECUTING",
        cfg=cfg,
    )

    result = answer_question(
        "What does source lineage require?",
        cfg=cfg,
        persist=False,
        checkpoint_id=checkpoint["checkpoint_id"],
        failover_policy=FailoverPolicy(max_attempts_per_provider=1, backoff_seconds=()),
    )

    stored = get_checkpoint(checkpoint["checkpoint_id"], cfg)
    assert result["status"] == "PAUSED_PROVIDER"
    assert result["provider_error"] == "ERROR_MODEL_PROVIDER_UNAVAILABLE"
    assert stored["state"] == "PAUSED_PROVIDER"
    assert stored["provider"] == "anthropic"
    assert stored["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert (stored["session_id"], stored["run_id"]) == ("session-v04", "run-v04")


def test_answer_question_opted_in_failover_records_attempt_chain(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.setattr(
        "backend.app.services.plan_model_route",
        lambda **kwargs: _failover_route("anthropic", "mock"),
    )
    calls: list[str] = []

    def scripted(*args, model_provider=None, **kwargs):
        calls.append(model_provider)
        if model_provider == "anthropic":
            raise ModelProviderError("ERROR_MODEL_PROVIDER_UNAVAILABLE", "outage")
        return {
            "model_provider": "mock",
            "model_name": "mock-model",
            "answer": "Source lineage requires chunk_a.",
            "cited_chunk_ids": ["chunk_a"],
        }

    monkeypatch.setattr("backend.app.services.generate_answer", scripted)

    result = answer_question(
        "What does source lineage require?",
        allow_mock_fallback=True,
        cfg=cfg,
        persist=False,
        failover_policy=FailoverPolicy(max_attempts_per_provider=1, backoff_seconds=()),
    )

    assert calls == ["anthropic", "mock"]
    assert result["model_provider"] == "mock"
    assert result["model_failed_over"] is True
    assert [attempt["status"] for attempt in result["model_attempts"]] == [
        "ERROR_MODEL_PROVIDER_UNAVAILABLE",
        "OK",
    ]


def test_answer_question_hard_failure_never_masks_or_pauses(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.setattr(
        "backend.app.services.plan_model_route",
        lambda **kwargs: _failover_route("anthropic", "mock"),
    )

    def bad_request(*args, **kwargs):
        raise ModelProviderError("ERROR_MODEL_PROVIDER_CALL_FAILED", "bad request")

    monkeypatch.setattr("backend.app.services.generate_answer", bad_request)
    checkpoint = create_checkpoint(
        session_id="session-hard",
        run_id="run-hard",
        objective="Answer",
        source_sha="d" * 40,
        state="EXECUTING",
        cfg=cfg,
    )

    result = answer_question(
        "What does source lineage require?",
        cfg=cfg,
        persist=False,
        checkpoint_id=checkpoint["checkpoint_id"],
        failover_policy=FailoverPolicy(max_attempts_per_provider=1, backoff_seconds=()),
    )

    assert result["status"] == "ERROR_MODEL_PROVIDER_CALL_FAILED"
    assert len(result["model_attempts"]) == 1
    assert get_checkpoint(checkpoint["checkpoint_id"], cfg)["state"] == "EXECUTING"


def test_multi_provider_failover_suppresses_partial_streaming(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.setattr(
        "backend.app.services.plan_model_route",
        lambda **kwargs: _failover_route("openai_compatible_local", "mock"),
    )
    seen_callbacks: list[object] = []

    def scripted(*args, model_provider=None, on_text_delta=None, **kwargs):
        seen_callbacks.append(on_text_delta)
        if model_provider == "openai_compatible_local":
            raise ModelProviderError("ERROR_LOCAL_MODEL_UNAVAILABLE", "local server down")
        return {
            "model_provider": "mock",
            "model_name": "mock-model",
            "answer": "Source lineage requires chunk_a.",
            "cited_chunk_ids": ["chunk_a"],
        }

    monkeypatch.setattr("backend.app.services.generate_answer", scripted)
    deltas: list[str] = []

    result = answer_question(
        "What does source lineage require?",
        cfg=cfg,
        persist=False,
        on_text_delta=deltas.append,
        failover_policy=FailoverPolicy(max_attempts_per_provider=1, backoff_seconds=()),
    )

    assert seen_callbacks == [None, None]
    assert deltas == []
    assert result["model_failover_streaming_suppressed"] is True
    assert result["model_failed_over"] is True


def test_single_provider_path_preserves_streaming_callback(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.setattr(
        "backend.app.services.plan_model_route",
        lambda **kwargs: _failover_route("openai_compatible_local"),
    )

    def local_success(*args, on_text_delta=None, **kwargs):
        assert on_text_delta is not None
        on_text_delta("delta")
        return {
            "model_provider": "openai_compatible_local",
            "model_name": "local-model",
            "answer": "Source lineage requires chunk_a.",
            "cited_chunk_ids": ["chunk_a"],
            "streamed": True,
        }

    monkeypatch.setattr("backend.app.services.generate_answer", local_success)
    deltas: list[str] = []

    result = answer_question(
        "What does source lineage require?",
        cfg=cfg,
        persist=False,
        on_text_delta=deltas.append,
    )

    assert deltas == ["delta"]
    assert result["model_failover_streaming_suppressed"] is False
    assert result["answer_streamed"] is True


def test_answer_trace_records_stage_timings(tmp_path: Path):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    result = answer_question("What does source lineage require?", model_provider="mock", cfg=cfg, persist=False)

    timings = result["timings_ms"]
    assert set(timings) == {"retrieval_ms", "model_first_token_ms", "model_ms"}
    assert all(value >= 0 for value in timings.values())
    # mock does not stream, so its first token arrives with the whole answer
    assert timings["model_first_token_ms"] == timings["model_ms"]
    assert result["answer_streamed"] is False



def test_single_provider_retry_never_repeats_streamed_text(tmp_path: Path, monkeypatch) -> None:
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    monkeypatch.setattr("backend.app.services.plan_model_route", lambda **kwargs: _failover_route("anthropic"))
    calls: list[int] = []

    def drops_mid_stream(*args, on_text_delta=None, **kwargs):
        calls.append(1)
        on_text_delta("Partial answer ")
        raise ModelProviderError("ERROR_MODEL_PROVIDER_TIMEOUT", "stream timed out")

    monkeypatch.setattr("backend.app.services.generate_answer", drops_mid_stream)
    deltas: list[str] = []

    result = answer_question(
        "What does source lineage require?",
        cfg=cfg,
        persist=False,
        on_text_delta=deltas.append,
        failover_policy=FailoverPolicy(max_attempts_per_provider=2, backoff_seconds=()),
    )

    assert calls == [1]
    assert deltas == ["Partial answer "]
    assert result["status"] == "ERROR_MODEL_PROVIDER_TIMEOUT"
    assert [attempt["status"] for attempt in result["model_attempts"]] == ["ERROR_MODEL_PROVIDER_TIMEOUT"]
