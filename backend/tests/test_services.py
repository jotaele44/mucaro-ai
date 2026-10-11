from __future__ import annotations

import csv
import json
from pathlib import Path

from backend.app.config import Settings
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


# --- V04: failover, attempt chain, and provider-outage pause wired into the answer path ---------------

from backend.app.agent_checkpoints import create_checkpoint, get_checkpoint  # noqa: E402
from backend.app.model_failover import FailoverPolicy  # noqa: E402
from backend.app.model_router import ModelProviderError  # noqa: E402

RATE = "ERROR_MODEL_PROVIDER_RATE_LIMIT"
NO_SLEEP = FailoverPolicy(max_attempts_per_provider=2, backoff_seconds=(0.0,))


def _live_ready(monkeypatch) -> None:
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("backend.app.model_router.importlib.util.find_spec", lambda name: object())


def _scripted_generate(monkeypatch, per_provider: dict[str, list]):
    seen: list[str] = []

    def fake(query, chunks, *, model_provider=None, **kwargs):
        seen.append(model_provider)
        step = per_provider[model_provider].pop(0)
        if isinstance(step, str):
            raise ModelProviderError(step, f"{model_provider} down")
        return step

    monkeypatch.setattr("backend.app.services.generate_answer", fake)
    return seen


def _live_answer(text="Lineage needs chunk_a."):
    return {"model_provider": "anthropic", "model_name": "live", "answer": text, "cited_chunk_ids": ["chunk_a"]}


def test_outage_with_checkpoint_pauses_run_instead_of_failing(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    _live_ready(monkeypatch)
    seen = _scripted_generate(monkeypatch, {"anthropic": [RATE, RATE]})
    checkpoint = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha="a" * 40, state="EXECUTING", cfg=cfg)

    result = answer_question(
        "What does source lineage require?", model_provider="anthropic", allow_live=True, cfg=cfg,
        checkpoint_id=checkpoint["checkpoint_id"], failover_policy=NO_SLEEP, persist=False,
    )

    assert result["status"] == "PAUSED_PROVIDER" and result["provider_error"] == RATE
    assert seen == ["anthropic", "anthropic"] and len(result["model_attempts"]) == 2
    stored = get_checkpoint(checkpoint["checkpoint_id"], cfg)
    assert stored["state"] == "PAUSED_PROVIDER" and stored["provider"] == "anthropic"
    assert (stored["session_id"], stored["run_id"]) == ("s", "r")  # identity preserved across the outage


def test_outage_without_checkpoint_keeps_the_original_error_shape(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    _live_ready(monkeypatch)
    _scripted_generate(monkeypatch, {"anthropic": [RATE, RATE]})

    result = answer_question("What does source lineage require?", model_provider="anthropic", allow_live=True, cfg=cfg, failover_policy=NO_SLEEP, persist=False)

    assert result["status"] == RATE
    assert result["retrieved_chunks"] and result["model_route"]["selected_provider"] == "anthropic"
    assert len(result["model_attempts"]) == 2


def test_live_outage_never_silently_degrades_to_mock_without_opt_in(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    _live_ready(monkeypatch)
    seen = _scripted_generate(monkeypatch, {"anthropic": ["ERROR_MODEL_PROVIDER_UNAVAILABLE"], "mock": [_live_answer()]})

    result = answer_question("What does source lineage require?", model_provider="anthropic", allow_live=True, cfg=cfg, failover_policy=NO_SLEEP, persist=False)

    assert result["status"] == "ERROR_MODEL_PROVIDER_UNAVAILABLE"
    assert seen == ["anthropic"]


def test_opt_in_offline_failover_answers_and_records_the_chain(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    _live_ready(monkeypatch)
    mock_answer = {"model_provider": "mock", "model_name": "mock", "answer": "Lineage needs chunk_a.", "cited_chunk_ids": ["chunk_a"]}
    seen = _scripted_generate(monkeypatch, {"anthropic": ["ERROR_MODEL_PROVIDER_UNAVAILABLE"], "mock": [mock_answer]})

    result = answer_question(
        "What does source lineage require?", model_provider="anthropic", allow_live=True, allow_mock_fallback=True,
        cfg=cfg, failover_policy=NO_SLEEP, persist=False,
    )

    assert seen == ["anthropic", "mock"]
    assert result["model_provider"] == "mock" and result["model_failed_over"] is True
    assert [a["status"] for a in result["model_attempts"]] == ["ERROR_MODEL_PROVIDER_UNAVAILABLE", "OK"]


def test_hard_provider_failure_is_not_masked_by_failover_or_pause(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)
    _live_ready(monkeypatch)
    _scripted_generate(monkeypatch, {"anthropic": ["ERROR_MODEL_PROVIDER_CALL_FAILED"], "mock": [_live_answer()]})
    checkpoint = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha="a" * 40, state="EXECUTING", cfg=cfg)

    result = answer_question(
        "What does source lineage require?", model_provider="anthropic", allow_live=True, allow_mock_fallback=True,
        cfg=cfg, checkpoint_id=checkpoint["checkpoint_id"], failover_policy=NO_SLEEP, persist=False,
    )

    assert result["status"] == "ERROR_MODEL_PROVIDER_CALL_FAILED"
    assert get_checkpoint(checkpoint["checkpoint_id"], cfg)["state"] == "EXECUTING"  # a bad request is not an outage


def test_successful_answer_records_single_ok_attempt(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    seed_chunks(cfg)

    result = answer_question("What does source lineage require?", cfg=cfg, persist=False)

    assert result["model_failed_over"] is False
    assert [a["status"] for a in result["model_attempts"]] == ["OK"]


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
    _live_ready(monkeypatch)
    calls: list[int] = []

    def drops_mid_stream(*args, on_text_delta=None, **kwargs):
        calls.append(1)
        on_text_delta("Partial answer ")
        raise ModelProviderError("ERROR_MODEL_PROVIDER_TIMEOUT", "stream timed out")

    monkeypatch.setattr("backend.app.services.generate_answer", drops_mid_stream)
    deltas: list[str] = []

    result = answer_question(
        "What does source lineage require?", model_provider="anthropic", allow_live=True, cfg=cfg,
        on_text_delta=deltas.append, failover_policy=NO_SLEEP, persist=False,
    )

    assert calls == [1]
    assert deltas == ["Partial answer "]
    assert result["status"] == "ERROR_MODEL_PROVIDER_TIMEOUT"
    assert [attempt["status"] for attempt in result["model_attempts"]] == ["ERROR_MODEL_PROVIDER_TIMEOUT"]
