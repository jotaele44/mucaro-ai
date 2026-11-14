from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.main import create_app
from backend.app.permissions import DirectoryPermissionError, grant_directory, too_broad_grant_reason
from backend.tests.test_api import make_settings

UPLOAD = {"upload": ("note.md", b"# note\n\nlocal text\n", "text/markdown")}
LOOPBACK_PEER = ("127.0.0.1", 50000)


def loopback_client(tmp_path: Path, **overrides: object) -> TestClient:
    cfg = make_settings(tmp_path)
    if overrides:
        cfg = type(cfg)(**{**cfg.__dict__, **overrides})
    return TestClient(create_app(cfg), base_url="http://127.0.0.1:8000", client=LOOPBACK_PEER)


def test_cross_site_form_upload_is_refused_without_a_token(tmp_path: Path):
    client = loopback_client(tmp_path)

    response = client.post("/ingest", files=UPLOAD, headers={"Origin": "https://attacker.example"})

    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "ERROR_CROSS_SITE_REQUEST_DENIED"
    assert response.json()["detail"]["category"] == "BLOCKED_ACTION"
    assert not (tmp_path / "data" / "input" / "note.md").exists()


@pytest.mark.parametrize("origin", ["null", "http://127.0.0.1:9999", "http://localhost.attacker.example:8000"])
def test_foreign_origins_are_refused_for_state_changes(tmp_path: Path, origin: str):
    response = loopback_client(tmp_path).post("/ingest", files=UPLOAD, headers={"Origin": origin})

    assert response.status_code == 403


def test_cross_site_marker_without_origin_is_refused(tmp_path: Path):
    response = loopback_client(tmp_path).post("/ingest", files=UPLOAD, headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 403


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:8000", "http://localhost:5173"])
def test_own_app_dev_server_and_non_browser_clients_can_upload(tmp_path: Path, origin: str | None):
    headers = {"Origin": origin} if origin else {}

    response = loopback_client(tmp_path).post("/ingest", files=UPLOAD, headers=headers)

    assert response.status_code == 200, response.text


def test_cross_site_reads_are_left_to_the_cors_policy(tmp_path: Path):
    response = loopback_client(tmp_path).get("/health", headers={"Origin": "https://attacker.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_rebound_host_is_refused_on_tokenless_loopback(tmp_path: Path):
    client = loopback_client(tmp_path)

    response = client.get("/sources", headers={"Host": "attacker.example:8000"})

    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "ERROR_HOST_NOT_ALLOWED"


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000", "mucaro.localhost"])
def test_loopback_hosts_are_accepted(tmp_path: Path, host: str):
    response = loopback_client(tmp_path).get("/sources", headers={"Host": host})

    assert response.status_code == 200


def test_named_host_is_accepted_once_a_token_protects_the_service(tmp_path: Path):
    token = "t" * 40
    client = loopback_client(tmp_path, operator_token=token)

    response = client.get("/sources", headers={"Host": "mac.lan:8000", "X-Mucaro-Token": token})

    assert response.status_code == 200


def test_failed_ingest_does_not_leave_the_upload_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(main_module, "ingest_file", boom)
    client = TestClient(create_app(make_settings(tmp_path)))

    response = client.post("/ingest", files=UPLOAD)

    assert response.json()["detail"]["status"] == "ERROR_INGEST_FAILED"
    assert not (tmp_path / "data" / "input" / "note.md").exists()


def test_export_lookup_only_accepts_well_formed_run_ids(tmp_path: Path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    (cfg.export_dir / "notes.answer_trace.json").write_text("{}", encoding="utf-8")
    client = TestClient(create_app(cfg))

    assert client.get("/exports/notes").status_code == 404


def test_grant_refuses_home_its_parents_and_hidden_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home" / "user"
    (home / ".ssh").mkdir(parents=True)
    (home / ".config" / "tool").mkdir(parents=True)
    (home / "Documents" / "research").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    cfg = make_settings(tmp_path / "project")

    for refused in (home, home.parent, home / ".ssh", home / ".config" / "tool"):
        with pytest.raises(DirectoryPermissionError) as raised:
            grant_directory(refused, cfg, granted_by="test")
        assert raised.value.status == "ERROR_DIRECTORY_PERMISSION_TOO_BROAD"

    grant = grant_directory(home / "Documents" / "research", cfg, granted_by="test")
    assert grant["status"] == "DIRECTORY_PERMISSION_GRANTED"


def test_system_configuration_tree_is_too_broad():
    assert too_broad_grant_reason(Path("/etc")) is not None
    assert too_broad_grant_reason(Path("/etc/ssh")) is not None
    assert too_broad_grant_reason(Path("/srv/research")) is None
