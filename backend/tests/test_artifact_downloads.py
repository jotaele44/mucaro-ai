from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app.artifact_downloads import ArtifactDownloadError, read_artifact_download
from backend.app.artifact_identity import create_artifact_record
from backend.app.main import create_app
from backend.tests.test_api import make_settings


def output_artifact(cfg, path, payload):
    cfg.ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    source = create_artifact_record(
        processed_dir=cfg.processed_dir,
        artifact_type="SOURCE",
        payload=payload,
        logical_identity=f"fixture-source:{path}",
        producer="test",
        producer_version="1",
    )
    return create_artifact_record(
        processed_dir=cfg.processed_dir,
        artifact_type="DERIVED",
        payload=payload,
        logical_identity=f"test:{path}",
        producer="test",
        producer_version="1",
        parent_artifact_ids=[source.artifact_id],
        source_path_or_uri=str(path),
    )


def test_download_requires_auth_and_returns_the_recorded_bytes_with_hash(tmp_path):
    cfg = replace(make_settings(tmp_path), operator_token="artifact-download-token")
    raw = b"frozen-output-bytes"
    artifact = output_artifact(cfg, cfg.export_dir / "Múcaro output.tif", raw)
    client = TestClient(create_app(cfg))
    route = f"/artifacts/{artifact.artifact_id}/download"
    assert client.get(route).status_code == 401
    response = client.get(route, headers={"X-Mucaro-Token": cfg.operator_token})
    assert response.status_code == 200 and response.content == raw
    assert response.headers["x-mucaro-artifact-sha256"] == hashlib.sha256(raw).hexdigest()
    assert response.headers["content-type"] == "image/tiff"
    assert "M%C3%BAcaro%20output.tif" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"


def test_download_rejects_changed_and_missing_outputs(tmp_path):
    cfg = make_settings(tmp_path)
    path = cfg.export_dir / "result.geojson"
    artifact = output_artifact(cfg, path, b'{"type":"FeatureCollection","features":[]}')
    path.write_bytes(b"tampered")
    client = TestClient(create_app(cfg))
    response = client.get(f"/artifacts/{artifact.artifact_id}/download")
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "ERROR_ARTIFACT_DOWNLOAD_CHANGED"
    path.unlink()
    assert client.get(f"/artifacts/{artifact.artifact_id}/download").status_code == 404
    assert client.get("/artifacts/unknown/download").status_code == 404


def test_download_refuses_host_files_and_symlink_escape(tmp_path):
    cfg = make_settings(tmp_path)
    outside = cfg.project_root / "private.json"
    artifact = output_artifact(cfg, outside, b"private")
    with pytest.raises(ArtifactDownloadError) as denied:
        read_artifact_download(artifact.artifact_id, cfg)
    assert denied.value.http_status == 403
    link = cfg.export_dir / "linked.json"
    link.symlink_to(outside)
    linked = create_artifact_record(
        processed_dir=cfg.processed_dir,
        artifact_type="DERIVED",
        payload=b"private",
        logical_identity="linked",
        producer="test",
        producer_version="1",
        parent_artifact_ids=[artifact.artifact_id],
        source_path_or_uri=str(link),
    )
    with pytest.raises(ArtifactDownloadError) as denied:
        read_artifact_download(linked.artifact_id, cfg)
    assert denied.value.http_status == 403


def test_download_returns_the_checked_buffer_if_path_changes_after_read(tmp_path, monkeypatch):
    from backend.app import artifact_downloads

    cfg = make_settings(tmp_path)
    path = cfg.export_dir / "result.json"
    original = b'{"result":"original"}'
    artifact = output_artifact(cfg, path, original)
    digest = artifact_downloads.hashlib.sha256

    def replace_after_acquisition(payload):
        path.write_bytes(b"replacement that must never be served")
        return digest(payload)

    monkeypatch.setattr(artifact_downloads.hashlib, "sha256", replace_after_acquisition)
    payload, _, _, returned_hash = read_artifact_download(artifact.artifact_id, cfg)
    assert payload == original and returned_hash == artifact.sha256
    assert path.read_bytes() != payload
