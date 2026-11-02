from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context

import pytest
from fastapi.testclient import TestClient

from backend.app import web_browsing, web_capture_store as store
from backend.app.main import create_app
from backend.app.cli import main
from backend.app.utils import stable_id
from backend.tests.test_web_browsing import _serve_fixture, make_settings


def observation(number=0):
    raw = f"Frozen observation {number}".encode()
    value = {
        "schema_version": "mucaro_web_snapshot_v1",
        "requested_url": f"https://example.org/{number}",
        "final_url": f"https://example.org/{number}",
        "retrieval_utc": "2026-11-02T00:00:00Z",
        "http_status": 200,
        "content_type": "text/plain",
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "content_size_bytes": len(raw),
        "content_base64": base64.b64encode(raw).decode(),
    }
    value["snapshot_id"] = stable_id(
        "websnap", value["requested_url"], value["final_url"], value["content_sha256"], value["retrieval_utc"]
    )
    return value


def store_in_process(pair):
    root, number = pair
    return store.persist_web_snapshot(observation(number), make_settings(root))


def test_concurrent_real_http_captures_retain_every_observation(tmp_path):
    cfg = make_settings(tmp_path)
    server, url = _serve_fixture()
    try:

        def fetch(number):
            return web_browsing.fetch_web_snapshot(
                f"{url}?capture={number}", cfg=cfg, policy=web_browsing.WebFetchPolicy(allow_private_hosts=True)
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(fetch, range(12)))
    finally:
        server.shutdown()
    ledger = store.load_capture_ledger(cfg)
    assert ledger["entry_count"] == 12
    assert {row["snapshot_id"] for row in ledger["entries"]} == {row["snapshot"]["snapshot_id"] for row in results}
    assert sorted(row["ledger_entry_count"] for row in results) == list(range(1, 13))
    for row in ledger["entries"]:
        raw = Path(row["snapshot_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["snapshot_sha256"]
        assert hashlib.sha256(base64.b64decode(json.loads(raw)["content_base64"])).hexdigest() == row["content_sha256"]


def test_independent_processes_use_the_same_capture_lock(tmp_path):
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        results = list(pool.map(store_in_process, [(tmp_path, number) for number in range(8)]))
    ledger = store.load_capture_ledger(make_settings(tmp_path))
    assert ledger["entry_count"] == 8 and len({row["snapshot_id"] for row in ledger["entries"]}) == 8
    assert sorted(row["ledger_entry_count"] for row in results) == list(range(1, 9))


def test_retry_is_idempotent_and_never_replaces_frozen_bytes(tmp_path):
    cfg = make_settings(tmp_path)
    value = observation()
    first = store.persist_web_snapshot(value, cfg)
    capture = Path(first["snapshot_path"])
    before = (capture.read_bytes(), capture.stat().st_mtime_ns, store.ledger_path(cfg).read_bytes())
    again = store.persist_web_snapshot(value, cfg)
    assert first == again and again["ledger_entry_count"] == 1
    assert before == (capture.read_bytes(), capture.stat().st_mtime_ns, store.ledger_path(cfg).read_bytes())
    with pytest.raises(store.WebCaptureStoreError) as conflict:
        store.persist_web_snapshot({**value, "headers": {"x-observation": "different"}}, cfg)
    assert conflict.value.status == "ERROR_WEB_CAPTURE_IDENTITY_CONFLICT"
    assert before == (capture.read_bytes(), capture.stat().st_mtime_ns, store.ledger_path(cfg).read_bytes())
    capture.write_bytes(b"changed outside the app")
    with pytest.raises(store.WebCaptureStoreError, match="different bytes"):
        store.persist_web_snapshot(value, cfg)
    assert capture.read_bytes() == b"changed outside the app" and store.ledger_path(cfg).read_bytes() == before[2]


@pytest.mark.parametrize("corrupt", [b"{bad", b"\xff", b'{"entries":[],"entries":[]}', b'{"entry_count":NaN}', b"[]"])
def test_malformed_index_is_preserved_without_new_capture_files(tmp_path, corrupt):
    cfg = make_settings(tmp_path)
    store.persist_web_snapshot(observation(), cfg)
    path = store.ledger_path(cfg)
    path.write_bytes(corrupt)
    existing = set(path.parent.glob("websnap_*.json"))
    with pytest.raises(store.WebCaptureStoreError) as refused:
        store.persist_web_snapshot(observation(1), cfg)
    assert refused.value.status == "ERROR_WEB_CAPTURE_STORE_INVALID"
    assert path.read_bytes() == corrupt and set(path.parent.glob("websnap_*.json")) == existing


@pytest.mark.parametrize(
    "fault", ["count", "bool-count", "duplicate-id", "wrong-id", "wrong-digest", "bool-size", "non-utc", "extra-field"]
)
def test_index_invariants_fail_closed_without_synthesizing_a_repair(tmp_path, fault):
    cfg = make_settings(tmp_path)
    store.persist_web_snapshot(observation(), cfg)
    path = store.ledger_path(cfg)
    ledger = json.loads(path.read_bytes())
    if fault == "count":
        ledger["entry_count"] = 7
    elif fault == "bool-count":
        ledger["entry_count"] = True
    elif fault == "duplicate-id":
        ledger["entries"] *= 2
        ledger["entry_count"] = 2
    elif fault == "wrong-id":
        ledger["entries"][0]["requested_url"] = "https://example.org/different"
    elif fault == "wrong-digest":
        ledger["entries"][0]["snapshot_sha256"] = "broken"
    elif fault == "bool-size":
        ledger["entries"][0]["content_size_bytes"] = True
    elif fault == "non-utc":
        ledger["entries"][0]["retrieval_utc"] = "2026-11-02T00:00:00"
    else:
        ledger["unknown"] = "do not silently discard"
    path.write_text(json.dumps(ledger))
    before = path.read_bytes()
    with pytest.raises(store.WebCaptureStoreError):
        store.persist_web_snapshot(observation(1), cfg)
    assert path.read_bytes() == before


def test_symlink_and_sparse_oversized_indexes_are_refused_before_reading(tmp_path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    path = store.ledger_path(cfg)
    path.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"private outside bytes")
    path.symlink_to(outside)
    with pytest.raises(store.WebCaptureStoreError):
        store.persist_web_snapshot(observation(), cfg)
    assert outside.read_bytes() == b"private outside bytes"
    path.unlink()
    with path.open("wb") as stream:
        stream.truncate(store.MAX_LEDGER_BYTES + 1)
    with pytest.raises(store.WebCaptureStoreError):
        store.persist_web_snapshot(observation(), cfg)
    assert path.stat().st_size == store.MAX_LEDGER_BYTES + 1


def test_encoded_budget_and_content_identity_are_checked_before_publication(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path)
    monkeypatch.setattr(store, "MAX_SNAPSHOT_BYTES", 100)
    with pytest.raises(store.WebCaptureStoreError) as limit:
        store.persist_web_snapshot({**observation(), "content_base64": "A" * 101}, cfg)
    assert limit.value.status == "ERROR_WEB_CAPTURE_STORE_LIMIT" and not store.ledger_path(cfg).exists()
    monkeypatch.setattr(store, "MAX_SNAPSHOT_BYTES", 16 * 1024 * 1024)
    for changed in [{"content_size_bytes": True}, {"content_sha256": "0" * 64}, {"content_base64": "not base64"}]:
        with pytest.raises(store.WebCaptureStoreError):
            store.persist_web_snapshot({**observation(), **changed}, cfg)
    assert not store.ledger_path(cfg).exists()


def test_capture_directory_symlink_never_writes_outside_the_data_root(tmp_path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    outside = tmp_path / "outside-collection"
    outside.mkdir()
    (outside / "retain.txt").write_bytes(b"unchanged")
    (cfg.export_dir / "web_snapshots").symlink_to(outside, target_is_directory=True)
    with pytest.raises(store.WebCaptureStoreError) as refused:
        store.persist_web_snapshot(observation(), cfg)
    assert refused.value.status == "ERROR_WEB_CAPTURE_BOUNDARY"
    assert [file.name for file in outside.iterdir()] == ["retain.txt"]


def test_index_write_failure_retains_an_orphan_that_can_be_committed_on_retry(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path)
    store.persist_web_snapshot(observation(), cfg)
    previous = store.ledger_path(cfg).read_bytes()
    writer = store.write_json

    def fail_write(*args):
        raise OSError("simulated full disk")

    monkeypatch.setattr(store, "write_json", fail_write)
    with pytest.raises(store.WebCaptureStoreError) as refused:
        store.persist_web_snapshot(observation(1), cfg)
    assert refused.value.status == "ERROR_WEB_CAPTURE_STORE_WRITE" and store.ledger_path(cfg).read_bytes() == previous
    orphan = store.ledger_path(cfg).parent / f"{observation(1)['snapshot_id']}.json"
    raw = orphan.read_bytes()
    assert observation(1)["snapshot_id"] in refused.value.detail
    assert hashlib.sha256(raw).hexdigest() in refused.value.detail
    monkeypatch.setattr(store, "write_json", writer)
    assert store.persist_web_snapshot(observation(1), cfg)["ledger_entry_count"] == 2
    assert orphan.read_bytes() == raw


def saved_orphan(cfg):
    value = observation(12)
    path = store.ledger_path(cfg).parent / f"{value['snapshot_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = store._encoded(value)
    path.write_bytes(raw)
    return value["snapshot_id"], hashlib.sha256(raw).hexdigest(), path, raw


def test_recovery_gui_api_and_cli_share_exact_byte_binding_without_network(tmp_path, capsys, monkeypatch):
    cfg = replace(make_settings(tmp_path), operator_token="capture-recovery-token")
    capture_id, digest, path, raw = saved_orphan(cfg)

    def forbidden_network(*args, **kwargs):
        raise AssertionError("recovery must not reacquire mutable source bytes")

    monkeypatch.setattr(web_browsing, "fetch_web_snapshot", forbidden_network)
    client = TestClient(create_app(cfg))
    route = f"/web-captures/{capture_id}/recover"
    assert client.post(route, json={"sha256": digest}).status_code == 401
    headers = {"X-Mucaro-Token": cfg.operator_token}
    for body in [{"sha256": "0" * 64}, {"sha256": "bad"}, {"sha256": digest, "url": "https://example.org"}]:
        assert client.post(route, json=body, headers=headers).status_code in {409, 422}
    result = client.post(route, json={"sha256": digest}, headers=headers)
    assert result.status_code == 200
    assert result.json()["status"] == "WEB_CAPTURE_RECOVERED" and result.json()["acquisition_repeated"] is False
    before = store.ledger_path(cfg).read_bytes()
    assert main(["captures", "recover", capture_id, "--sha256", digest], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["ledger_entry_count"] == 1
    assert path.read_bytes() == raw and store.ledger_path(cfg).read_bytes() == before


def test_recovery_refuses_missing_changed_symlinked_and_noncanonical_captures(tmp_path):
    cfg = make_settings(tmp_path)
    capture_id, digest, path, raw = saved_orphan(cfg)
    for selected in ["../outside", "not-an-id"]:
        with pytest.raises(store.WebCaptureStoreError) as denied:
            store.recover_web_snapshot(selected, digest, cfg)
        assert denied.value.status == "ERROR_WEB_CAPTURE_ARGUMENTS_INVALID"
    path.write_bytes(raw + b" ")
    with pytest.raises(store.WebCaptureStoreError):
        store.recover_web_snapshot(capture_id, hashlib.sha256(path.read_bytes()).hexdigest(), cfg)
    path.unlink()
    with pytest.raises(store.WebCaptureStoreError) as missing:
        store.recover_web_snapshot(capture_id, digest, cfg)
    assert missing.value.http_status == 404
    outside = tmp_path / "private.json"
    outside.write_bytes(raw)
    path.symlink_to(outside)
    with pytest.raises(store.WebCaptureStoreError) as boundary:
        store.recover_web_snapshot(capture_id, digest, cfg)
    assert boundary.value.http_status == 403 and outside.read_bytes() == raw
    assert not store.ledger_path(cfg).exists()


def test_lock_failure_reports_no_false_saved_capture(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path)

    def unavailable(*args):
        raise PermissionError("simulated inaccessible storage")

    monkeypatch.setattr(store, "file_lock", unavailable)
    with pytest.raises(store.WebCaptureStoreError) as refused:
        store.persist_web_snapshot(observation(), cfg)
    assert refused.value.status == "ERROR_WEB_CAPTURE_STORE_WRITE" and "Saved capture ID:" not in refused.value.detail


def test_governed_api_reports_damaged_index_before_acquiring_another_source(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    path = store.ledger_path(cfg)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{damaged")

    def forbidden_network(*args, **kwargs):
        raise AssertionError("bad store must refuse before HTTP acquisition")

    monkeypatch.setattr(web_browsing, "_governed_opener", forbidden_network)
    client = TestClient(create_app(cfg))
    request = client.post(
        "/tools/requests",
        json={"tool_name": "web.fetch", "arguments": {"url": "http://127.0.0.1:9/unused", "allow_private_hosts": True}},
    )
    call_id = request.json()["tool_call"]["tool_call_id"]
    assert client.post(f"/tools/requests/{call_id}/approve", json={}).status_code == 200
    result = client.post(f"/tools/requests/{call_id}/run")
    assert result.status_code == 400
    assert result.json()["detail"]["status"] == "ERROR_WEB_CAPTURE_STORE_INVALID"
    assert result.json()["detail"]["category"] == "UNTRUSTED_CONTENT"
    assert path.read_bytes() == b"{damaged"
