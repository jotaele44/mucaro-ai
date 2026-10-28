from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.artifact_identity import create_artifact_record, list_artifacts
from backend.app.exports import EXPORT_FILES, export_answer_trace, export_file_path
from backend.app.main import create_app
from backend.tests.test_api import make_settings
from backend.app.utils import write_json

TRACE = {
    "run_id": "run_deadbeef",
    "query": "q",
    "model_provider": "mock",
    "model_name": "mock",
    "prompt_template_id": "p",
    "retrieved_chunk_ids": ["chunk_a"],
    "cited_chunk_ids": ["chunk_a"],
    "source_ids": ["src_a"],
    "answer": "answer",
    "confidence": 0.6,
    "validation_flags": [],
    "created_at": "now",
}


def test_export_answer_trace(tmp_path: Path):
    root = tmp_path / "proj"
    cfg = Settings(
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
    trace = {
        "run_id": "run_deadbeef",
        "query": "q",
        "model_provider": "mock",
        "model_name": "mock",
        "prompt_template_id": "p",
        "retrieved_chunk_ids": ["chunk_a"],
        "cited_chunk_ids": ["chunk_a"],
        "source_ids": ["src_a"],
        "answer": "answer",
        "confidence": 0.6,
        "validation_flags": [],
        "created_at": "now",
    }
    paths = export_answer_trace(trace, cfg)
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()
    assert Path(paths["csv"]).exists()


def test_export_answer_trace_registers_derived_and_export_artifacts(tmp_path: Path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    source = create_artifact_record(
        processed_dir=cfg.processed_dir,
        artifact_type="SOURCE",
        payload=b"source bytes",
        logical_identity="source:src_a",
        producer="test",
        producer_version="1",
    )
    write_json(
        cfg.processed_dir / "source_manifest.json",
        [{"source_id": "src_a", "artifact_id": source.artifact_id}],
    )

    trace = dict(TRACE)
    paths = export_answer_trace(trace, cfg)

    assert trace["artifact_lineage"]["status"] == "VALID"
    assert trace["artifact_lineage"]["source_artifact_ids"] == [source.artifact_id]
    records = list_artifacts(cfg.processed_dir)
    assert sum(record["artifact_type"] == "DERIVED" for record in records) == 1
    assert sum(record["artifact_type"] == "EXPORT" for record in records) == 3
    assert all(Path(path).exists() for path in paths.values())


def test_export_files_download_with_their_type_and_name(tmp_path: Path):
    cfg = make_settings(tmp_path)
    paths = export_answer_trace(dict(TRACE), cfg)
    client = TestClient(create_app(cfg))
    for kind, (suffix, media_type) in EXPORT_FILES.items():
        response = client.get(f"/exports/run_deadbeef/files/{kind}")
        assert response.status_code == 200, kind
        assert response.headers["content-type"] == media_type
        assert response.headers["content-disposition"] == f'attachment; filename="run_deadbeef.{suffix}"'
        assert response.content == Path(paths[kind]).read_bytes()


@pytest.mark.parametrize(
    ("run_id", "kind"),
    [
        ("run_deadbeef", "pdf"),  # unknown kind
        ("run_feedface", "json"),  # well-formed but never exported
        ("run_DEADBEEF", "json"),  # not a run id this app writes
        ("run_deadbeef.answer_trace.json#", "json"),
        ("..", "json"),
    ],
)
def test_export_file_refuses_unknown_kinds_and_malformed_or_missing_runs(tmp_path: Path, run_id: str, kind: str):
    cfg = make_settings(tmp_path)
    export_answer_trace(dict(TRACE), cfg)
    response = TestClient(create_app(cfg)).get(f"/exports/{run_id}/files/{kind}")
    assert response.status_code == 404
    assert export_file_path(run_id, kind, cfg) is None


def test_export_file_path_never_leaves_the_export_dir(tmp_path: Path):
    cfg = make_settings(tmp_path)
    export_answer_trace(dict(TRACE), cfg)
    (cfg.data_dir / "secret.answer_trace.json").write_text("{}", encoding="utf-8")
    assert export_file_path("../secret", "json", cfg) is None
    assert export_file_path("run_deadbeef", "json", cfg) == cfg.export_dir / "run_deadbeef.answer_trace.json"

