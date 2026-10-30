from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.artifact_identity import ArtifactIdentityError, create_artifact_record, list_artifacts, trace_ancestors
from backend.app.cli import main
from backend.app.main import create_app
from backend.tests.test_api import make_settings
from backend.tests.test_artifact_downloads import output_artifact


@pytest.mark.parametrize(
    "raw",
    [b"{broken", b"\xff", b'[{"artifact_id":"one","artifact_id":"two"}]', b'[{"value":NaN}]', b'[{"value":1e999}]'],
)
def test_corrupt_store_is_preserved_and_refuses_api_and_cli_recovery(tmp_path, capsys, raw):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    path = cfg.processed_dir / "artifact_lineage.json"
    path.write_bytes(raw)
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    for route in ["/artifacts", "/artifacts/unknown", "/artifacts/unknown/download"]:
        response = client.get(route)
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["status"] == "ERROR_ARTIFACT_STORE_INVALID" and detail["category"] == "UNTRUSTED_CONTENT"
        assert str(path) not in response.text
    assert main(["artifacts", "list"], cfg=cfg) == 2
    assert "ERROR_ARTIFACT_STORE_INVALID" in capsys.readouterr().err
    with pytest.raises(ArtifactIdentityError):
        create_artifact_record(
            processed_dir=cfg.processed_dir,
            artifact_type="SOURCE",
            payload=b"new",
            logical_identity="new",
            producer="test",
            producer_version="1",
        )
    assert path.read_bytes() == raw


def legacy_rows(cfg):
    source = create_artifact_record(
        processed_dir=cfg.processed_dir,
        artifact_type="SOURCE",
        payload=b"source",
        logical_identity="legacy",
        producer="test",
        producer_version="1",
    )
    path = cfg.processed_dir / "artifact_lineage.json"
    rows = json.loads(path.read_text())
    rows[0].pop("provenance")
    return path, rows, source.artifact_id


@pytest.mark.parametrize(
    "fault",
    [
        "source-parent",
        "missing-parent",
        "no-parent",
        "wrong-parent-type",
        "self-cycle",
        "two-node-cycle",
        "duplicate-id",
    ],
)
def test_graph_invariants_apply_to_legacy_records_too(tmp_path, fault):
    cfg = make_settings(tmp_path)
    path, rows, source_id = legacy_rows(cfg)
    child = {**rows[0], "artifact_id": "child", "artifact_type": "DERIVED", "parent_artifact_ids": [source_id]}
    rows.append(child)
    if fault == "source-parent":
        rows[0]["parent_artifact_ids"] = ["child"]
    elif fault == "missing-parent":
        child["parent_artifact_ids"] = ["absent"]
    elif fault == "no-parent":
        child["parent_artifact_ids"] = []
    elif fault == "wrong-parent-type":
        child["artifact_type"] = "EXPORT"
    elif fault == "self-cycle":
        child["parent_artifact_ids"] = ["child"]
    elif fault == "two-node-cycle":
        child["parent_artifact_ids"] = ["other"]
        rows.append({**child, "artifact_id": "other", "parent_artifact_ids": ["child"]})
    else:
        rows.append(dict(child))
    path.write_text(json.dumps(rows))
    original = path.read_bytes()
    with pytest.raises(ArtifactIdentityError):
        list_artifacts(cfg.processed_dir)
    with pytest.raises(ArtifactIdentityError):
        trace_ancestors(cfg.processed_dir, "child")
    assert path.read_bytes() == original


def test_deep_and_shared_legacy_dag_has_ordered_unique_ancestors_without_recursion(tmp_path):
    cfg = make_settings(tmp_path)
    path, rows, source_id = legacy_rows(cfg)
    previous = source_id
    for number in range(1100):
        current = f"node-{number}"
        rows.append({**rows[0], "artifact_id": current, "artifact_type": "DERIVED", "parent_artifact_ids": [previous]})
        previous = current
    rows.append({**rows[-1], "artifact_id": "shared", "parent_artifact_ids": [previous, source_id]})
    path.write_text(json.dumps(rows))
    assert len(list_artifacts(cfg.processed_dir)) == 1102
    ancestors = trace_ancestors(cfg.processed_dir, "shared")
    assert [row["artifact_id"] for row in ancestors] == [source_id] + [f"node-{number}" for number in range(1100)]


@pytest.mark.parametrize("field", ["lineage_kind", "transformation", "created_utc"])
def test_provenance_must_match_its_record_not_only_its_digest(tmp_path, field):
    cfg = make_settings(tmp_path)
    artifact = output_artifact(cfg, cfg.export_dir / "result.json", b"output")
    path = cfg.processed_dir / "artifact_lineage.json"
    rows = json.loads(path.read_text())
    record = next(row for row in rows if row["artifact_id"] == artifact.artifact_id)
    if field == "lineage_kind":
        record["provenance"][field] = "SOURCE_MANIFESTATION"
    elif field == "created_utc":
        record["provenance"][field] = "different timestamp"
    else:
        record["provenance"][field]["producer"] = "different producer"
    path.write_text(json.dumps(rows))
    with pytest.raises(ArtifactIdentityError, match="provenance"):
        list_artifacts(cfg.processed_dir)


def test_symlinked_store_is_not_read_or_overwritten(tmp_path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    outside = tmp_path / "outside.json"
    outside.write_text("[]")
    (cfg.processed_dir / "artifact_lineage.json").symlink_to(outside)
    with pytest.raises(ArtifactIdentityError, match="symbolic link"):
        list_artifacts(cfg.processed_dir)
    assert outside.read_text() == "[]"


def test_library_pages_one_snapshot_and_never_exposes_host_paths(tmp_path):
    cfg = make_settings(tmp_path, operator_token="library-token")
    first = output_artifact(cfg, cfg.export_dir / "Múcaro terrain.tif", b"first")
    output_artifact(cfg, cfg.export_dir / "second.json", b"second")
    client = TestClient(create_app(cfg))
    assert client.get("/artifacts").status_code == 401
    headers = {"X-Mucaro-Token": cfg.operator_token}
    page = client.get("/artifacts?limit=1", headers=headers).json()
    assert page["total"] == 2 and page["next_offset"] == 1
    more = client.get(
        "/artifacts", params={"limit": 1, "offset": 1, "revision": page["revision"]}, headers=headers
    ).json()
    assert more["next_offset"] is None
    assert len({page["artifacts"][0]["artifact_id"], more["artifacts"][0]["artifact_id"]}) == 2
    assert str(tmp_path) not in json.dumps(page)
    detail = client.get(f"/artifacts/{first.artifact_id}", headers=headers).json()
    assert detail["artifact"]["filename"] == "Múcaro terrain.tif"
    assert detail["artifact"]["byte_verification"] == "CHECKED_ON_DOWNLOAD"
    assert detail["parents"][0]["artifact_type"] == "SOURCE"
    assert str(tmp_path) not in json.dumps(detail)
    output_artifact(cfg, cfg.export_dir / "third.json", b"third")
    stale = client.get("/artifacts", params={"offset": 1, "revision": page["revision"]}, headers=headers)
    assert stale.status_code == 409 and stale.json()["detail"]["status"] == "ERROR_ARTIFACT_STORE_CHANGED"
    filtered = client.get("/artifacts", params={"query": "MÚCARO"}, headers=headers).json()
    assert [row["artifact_id"] for row in filtered["artifacts"]] == [first.artifact_id]
    assert client.get("/artifacts/missing", headers=headers).status_code == 404
    for params in [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"query": "x" * 201}, {"revision": "bad"}]:
        assert client.get("/artifacts", params=params, headers=headers).status_code == 422


def test_cli_lists_inspects_and_saves_same_verified_output_without_overwrite(tmp_path, capsys):
    cfg = make_settings(tmp_path)
    raw = b"original bytes"
    artifact = output_artifact(cfg, cfg.export_dir / "saved.tif", raw)
    assert main(["artifacts", "list", "--query", "saved.tif"], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["artifacts"][0]["artifact_id"] == artifact.artifact_id
    assert main(["artifacts", "inspect", artifact.artifact_id], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["parents"][0]["artifact_type"] == "SOURCE"
    destination = tmp_path / "recovered.tif"
    command = ["artifacts", "save", artifact.artifact_id, "--output", str(destination)]
    assert main(command, cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == artifact.sha256
    assert destination.read_bytes() == raw and destination.stat().st_mode & 0o777 == 0o600
    destination.write_bytes(b"keep existing edits")
    assert main(command, cfg=cfg) == 2
    assert "ERROR_FILESYSTEM" in capsys.readouterr().err and destination.read_bytes() == b"keep existing edits"
    (cfg.export_dir / "saved.tif").write_bytes(b"tamper")
    assert (
        main(["artifacts", "save", artifact.artifact_id, "--output", str(tmp_path / "not-created.tif")], cfg=cfg) == 2
    )
    assert "ERROR_ARTIFACT_DOWNLOAD_CHANGED" in capsys.readouterr().err and not (tmp_path / "not-created.tif").exists()
