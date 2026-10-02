"""Phone access (SD-012): a phone on the same private network reaches the computer's service only with a token.

Covers the startup security profile, the server-side remote-client guard, host-path redaction for remote
clients, and the launcher's phone plan. A remote phone is simulated with TestClient(client=(ip, port)); the default
TestClient peer is "testclient", which is not an IP address and is treated as local.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import scripts.launch_app as launch_app
from backend.app.auth import REMOTE_ACCESS_STATUS, client_is_remote
from backend.app.main import create_app
from backend.app.security import build_security_report
from backend.tests.test_security import make_settings

REMOTE = ("192.168.1.5", 50000)
TOKEN = "t" * 40


def remote_client(tmp_path: Path, *, token: str | None = None) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, operator_token=token)), client=REMOTE)


# ---------------------------------------------------------------------------------------------------------------------
# Who counts as remote


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1", False),
        ("127.8.9.10", False),
        ("::1", False),
        ("::ffff:127.0.0.1", False),
        ("192.168.1.5", True),
        ("10.0.0.2", True),
        ("100.64.12.1", True),
        ("169.254.1.1", True),
        ("8.8.8.8", True),
        ("::ffff:192.168.1.5", True),
        ("fe80::1%en0", True),
        ("2001:db8::1", True),
        # Not IP addresses: the ASGI test client reports "testclient". Real servers always send an address.
        ("testclient", False),
        ("", False),
        ("not an address", False),
    ],
)
def test_client_is_remote_classifies_peer_addresses(host, expected):
    assert client_is_remote({"client": (host, 50000)}) is expected


def test_client_is_remote_treats_a_missing_peer_as_local():
    assert client_is_remote({}) is False
    assert client_is_remote({"client": None}) is False


# ---------------------------------------------------------------------------------------------------------------------
# Startup security profile


@pytest.mark.parametrize("token, ok", [(None, False), ("", False), ("x" * 31, False), ("x" * 32, True)])
def test_public_bind_requires_a_strong_token_in_every_profile(tmp_path, token, ok):
    cfg = make_settings(tmp_path, operator_token=token, allow_public_bind=True)

    report = build_security_report(cfg, bind_host="0.0.0.0")

    token_check = next(check for check in report["checks"] if check["name"] == "operator_token")
    assert (token_check["status"] == "PASS") is ok
    assert (report["status"] == "SECURITY_OK") is ok


def test_local_profile_without_the_public_bind_opt_in_still_only_warns_about_a_missing_token(tmp_path):
    report = build_security_report(make_settings(tmp_path))

    assert report["status"] == "SECURITY_WARN"


def test_app_refuses_to_start_with_the_public_bind_opt_in_and_a_weak_token(tmp_path):
    with pytest.raises(RuntimeError, match="ERROR_SECURITY_PROFILE_NOT_READY"):
        create_app(make_settings(tmp_path, operator_token="short", allow_public_bind=True))

    assert create_app(make_settings(tmp_path, operator_token="x" * 32, allow_public_bind=True)).title == "Mucaro AI"


# ---------------------------------------------------------------------------------------------------------------------
# Server-side guard


@pytest.mark.parametrize(
    "path", ["/conversations", "/memory/export", "/work-runs/modes", "/sources", "/tools/requests"]
)
def test_remote_client_without_a_configured_token_is_refused(tmp_path, path):
    response = remote_client(tmp_path).get(path)

    assert response.status_code == 403
    assert response.json()["detail"]["status"] == REMOTE_ACCESS_STATUS


def test_remote_client_cannot_write_without_a_configured_token(tmp_path):
    client = remote_client(tmp_path)

    response = client.post("/memory", json={"content": "remote write probe", "kind": "fact"})

    assert response.status_code == 403
    assert response.json()["detail"]["status"] == REMOTE_ACCESS_STATUS
    assert TestClient(client.app).get("/memory").json()["memories"] == []  # nothing was stored


def test_loopback_client_is_unaffected_without_a_token(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    assert client.get("/conversations").status_code == 200
    assert client.post("/memory", json={"content": "local note", "kind": "fact"}).status_code == 200


def test_remote_client_with_the_right_token_is_served_and_other_tokens_get_401(tmp_path):
    client = remote_client(tmp_path, token=TOKEN)

    assert client.get("/conversations", headers={"X-Mucaro-Token": TOKEN}).status_code == 200
    assert client.get("/conversations", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    for headers in ({}, {"X-Mucaro-Token": TOKEN[:-1]}, {"X-Mucaro-Token": ""}):
        response = client.get("/conversations", headers=headers)
        assert response.status_code == 401
        assert response.json()["detail"]["status"] == "ERROR_OPERATOR_AUTH_REQUIRED"


def test_public_status_routes_stay_reachable_for_remote_clients(tmp_path):
    client = remote_client(tmp_path, token=TOKEN)

    for path in ("/health", "/config", "/security", "/tools"):
        assert client.get(path).status_code == 200, path
    assert client.get("/config").json()["auth"]["auth_required"] is True


def test_remote_client_can_load_the_ui_so_it_can_explain_the_refusal(tmp_path):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Mucaro</title>", encoding="utf-8")

    assert remote_client(tmp_path).get("/").status_code == 200


# ---------------------------------------------------------------------------------------------------------------------
# Host paths are not for remote eyes


def test_remote_clients_do_not_see_host_paths(tmp_path):
    client = remote_client(tmp_path)

    health = client.get("/health").json()
    config = client.get("/config").json()

    assert health["status"] == "OK"
    assert "project_root" not in health
    for key in ("project_root", "data_dir", "cors_origins"):
        assert key not in config, key
    assert config["auth"]["auth_required"] is False  # the UI needs this to say what is missing
    assert config["max_upload_bytes"] > 0


def test_local_clients_still_see_the_status_the_desktop_app_uses(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    assert client.get("/health").json()["project_root"] == str(tmp_path)
    config = client.get("/config").json()
    assert config["project_root"] == str(tmp_path)
    assert "data_dir" in config and "cors_origins" in config


# ---------------------------------------------------------------------------------------------------------------------
# Launcher


class FakeProc:
    returncode = None

    def poll(self):
        return None


def fake_launch(
    monkeypatch,
    *,
    busy: bool = False,
    healthy: bool = True,
    addresses=("192.168.1.20", "100.64.0.9"),
    real_env: bool = False,
):
    started = []
    monkeypatch.setattr(launch_app, "ensure_frontend_dist", lambda skip_build=False, verified=False: None)
    monkeypatch.setattr(launch_app, "port_open", lambda port: busy)
    monkeypatch.setattr(launch_app, "health_ok", lambda port, **kwargs: healthy)
    monkeypatch.setattr(
        launch_app, "start_backend", lambda port, **kwargs: started.append((port, kwargs)) or FakeProc()
    )
    monkeypatch.setattr(launch_app, "wait_for_health", lambda port, timeout, **kwargs: True)
    monkeypatch.setattr(launch_app, "stop_backend", lambda proc, port: True)
    monkeypatch.setattr(launch_app, "discover_lan_addresses", lambda: list(addresses))
    monkeypatch.delenv("MUCARO_AI_PHONE_ACCESS", raising=False)
    monkeypatch.delenv("MUCARO_AI_OPERATOR_TOKEN", raising=False)
    if not real_env:
        # A developer's own .env must not change these tests.
        monkeypatch.setattr(launch_app, "build_env", lambda: {"PATH": "/bin"})
    return started


def test_phone_plan_generates_a_strong_per_launch_token_when_none_is_configured():
    plan = launch_app.plan_phone_access({"PATH": "/bin"})

    assert plan.generated and len(plan.token) >= 32
    assert plan.env["MUCARO_AI_OPERATOR_TOKEN"] == plan.token
    assert plan.env["MUCARO_AI_ALLOW_PUBLIC_BIND"] == "1"
    assert plan.env["PATH"] == "/bin"  # the rest of the environment is untouched
    assert launch_app.plan_phone_access({}).token != plan.token


def test_phone_plan_keeps_a_strong_configured_token_and_replaces_a_short_one():
    strong = "s" * 40

    kept = launch_app.plan_phone_access({"MUCARO_AI_OPERATOR_TOKEN": strong})
    replaced = launch_app.plan_phone_access({"MUCARO_AI_OPERATOR_TOKEN": "short"})

    assert kept.token == strong and not kept.generated
    assert replaced.generated and replaced.token != "short"


def test_phone_plan_scheme_follows_tls():
    assert launch_app.plan_phone_access({}).scheme == "http"
    assert launch_app.plan_phone_access({}, tls=True).scheme == "https"


def test_phone_link_puts_the_token_in_the_fragment_and_escapes_it():
    assert (
        launch_app.phone_link("http", "192.168.1.20", 8000, "abc-DEF_123")
        == "http://192.168.1.20:8000/#token=abc-DEF_123"
    )
    assert launch_app.phone_link("https", "10.0.0.5", 8443, "a b%c#d") == "https://10.0.0.5:8443/#token=a%20b%25c%23d"


def test_lan_addresses_keep_private_and_overlay_ipv4_only():
    found = launch_app.lan_addresses(
        [
            "127.0.0.1",
            "192.168.1.20",
            "10.1.2.3",
            "172.20.0.4",
            "100.101.102.103",
            "169.254.9.9",
            "8.8.8.8",
            "::1",
            "fe80::1",
            "garbage",
            "192.168.1.20",
        ]
    )

    assert found == ["192.168.1.20", "10.1.2.3", "172.20.0.4", "100.101.102.103"]


def test_backend_command_binds_the_given_host_and_serves_tls_when_given(monkeypatch):
    observed = {}

    def fake_popen(command, *, cwd, env):
        observed.update(command=command, env=env)
        return object()

    monkeypatch.setattr(launch_app.subprocess, "Popen", fake_popen)
    launch_app.start_backend(8123, host="0.0.0.0", env={"MARKER": "yes"}, tls=("cert.pem", "key.pem"))

    command = observed["command"]
    assert command[1:4] == ["-s", "-P", "-B"]
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert command[command.index("--ssl-certfile") + 1] == "cert.pem"
    assert command[command.index("--ssl-keyfile") + 1] == "key.pem"
    assert observed["env"] == {"MARKER": "yes"}  # the phone plan's environment is used as given


def test_health_probe_only_ever_targets_loopback_and_skips_certificate_checks_only_for_tls(monkeypatch):
    calls = []
    served_root = [str(launch_app.ROOT)]

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"status": "OK", "project_root": served_root[0]}).encode()

    def fake_urlopen(url, timeout, context):
        calls.append((url, context))
        return Reply()

    monkeypatch.setattr(launch_app.urllib.request, "urlopen", fake_urlopen)

    assert launch_app.health_ok(8123) is True
    assert launch_app.health_ok(8123, tls=True) is True

    (plain_url, plain_context), (tls_url, tls_context) = calls
    assert plain_url == "http://127.0.0.1:8123/health" and plain_context is None
    assert tls_url == "https://127.0.0.1:8123/health"
    assert tls_context.verify_mode == launch_app.ssl.CERT_NONE and tls_context.check_hostname is False

    # A healthy service that belongs to another checkout is still not this one, over TLS as well.
    served_root[0] = "/somewhere/else"
    assert launch_app.health_ok(8123, tls=True) is False


def test_default_launch_is_unchanged_loopback_http(monkeypatch, capsys):
    started = fake_launch(monkeypatch)
    opened = []
    monkeypatch.setattr(launch_app.webbrowser, "open", opened.append)

    assert launch_app.main(["--smoke-test", "--port", "54321"]) == 0

    assert started == [(54321, {})]  # no host, env or tls arguments: the default path is untouched
    assert opened == ["http://127.0.0.1:54321"]
    assert "PHONE_" not in capsys.readouterr().out


def test_phone_launch_binds_all_interfaces_with_a_token_and_prints_the_links(monkeypatch, capsys):
    started = fake_launch(monkeypatch)
    opened = []
    monkeypatch.setattr(launch_app.webbrowser, "open", opened.append)

    assert launch_app.main(["--phone", "--smoke-test", "--port", "54321"]) == 0

    ((port, kwargs),) = started
    token = kwargs["env"]["MUCARO_AI_OPERATOR_TOKEN"]
    assert port == 54321 and kwargs["host"] == "0.0.0.0" and kwargs["tls"] is None
    assert len(token) >= 32 and kwargs["env"]["MUCARO_AI_ALLOW_PUBLIC_BIND"] == "1"
    assert opened == [f"http://127.0.0.1:54321/#token={token}"]  # the local window is authorized too
    out = capsys.readouterr().out
    assert "PHONE_ACCESS_ENABLED" in out
    assert f"PHONE_URL http://192.168.1.20:54321/#token={token}" in out
    assert f"PHONE_URL http://100.64.0.9:54321/#token={token}" in out
    assert "PHONE_ACCESS_CLEARTEXT_HTTP" in out


def test_phone_access_can_be_switched_on_from_the_env_file(monkeypatch, tmp_path, capsys):
    started = fake_launch(monkeypatch, real_env=True)
    (tmp_path / ".env").write_text("MUCARO_AI_PHONE_ACCESS=1\n", encoding="utf-8")
    monkeypatch.setattr(launch_app, "ROOT", tmp_path)

    assert launch_app.main(["--smoke-test", "--no-browser", "--port", "54321"]) == 0

    assert started[0][1]["host"] == "0.0.0.0"
    assert "PHONE_ACCESS_ENABLED" in capsys.readouterr().out


def test_phone_launch_says_so_when_no_private_address_is_found(monkeypatch, capsys):
    fake_launch(monkeypatch, addresses=())

    assert launch_app.main(["--phone", "--smoke-test", "--no-browser", "--port", "54321"]) == 0

    out = capsys.readouterr().out
    assert "PHONE_ACCESS_NO_LAN_ADDRESS" in out
    assert "PHONE_URL" not in out


def test_phone_launch_does_not_claim_links_for_a_backend_it_did_not_start(monkeypatch, capsys):
    started = fake_launch(monkeypatch, busy=True, healthy=True)

    assert launch_app.main(["--phone", "--no-browser", "--exit-if-running", "--port", "54321"]) == 0

    out = capsys.readouterr().out
    assert started == []
    assert "PHONE_ACCESS_UNAVAILABLE" in out
    assert "PHONE_URL" not in out


def test_tls_launch_serves_https_links_without_the_cleartext_warning(monkeypatch, tmp_path, capsys):
    started = fake_launch(monkeypatch)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")

    assert (
        launch_app.main(
            [
                "--phone",
                "--smoke-test",
                "--no-browser",
                "--port",
                "54321",
                "--tls-cert",
                str(cert),
                "--tls-key",
                str(key),
            ]
        )
        == 0
    )

    assert started[0][1]["tls"] == (str(cert), str(key))
    out = capsys.readouterr().out
    assert "PHONE_URL https://192.168.1.20:54321/#token=" in out
    assert "PHONE_ACCESS_CLEARTEXT_HTTP" not in out


@pytest.mark.parametrize(
    "extra, code",
    [
        (["--phone", "--tls-cert", "only-a-cert.pem"], "ERROR_TLS_CERT_AND_KEY_REQUIRED"),
        (["--phone", "--tls-cert", "missing.pem", "--tls-key", "missing.key"], "ERROR_TLS_FILE_MISSING"),
        (["--tls-cert", "a.pem", "--tls-key", "a.key"], "ERROR_TLS_REQUIRES_PHONE_ACCESS"),
    ],
)
def test_tls_arguments_are_validated_before_anything_starts(monkeypatch, capsys, extra, code):
    started = fake_launch(monkeypatch)

    assert launch_app.main([*extra, "--smoke-test", "--no-browser", "--port", "54321"]) == 2

    assert started == []
    assert code in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------------------------
# The native app has no Terminal: its output goes to a log file on disk.


def fake_native(monkeypatch):
    offered, windows = [], []
    native = launch_app.native_window
    monkeypatch.setattr(native, "activate_existing_window", lambda pid_file: False)
    monkeypatch.setattr(native, "show_phone_link", lambda message, link: offered.append((message, link)))
    monkeypatch.setattr(native, "run_window", lambda url, **kwargs: windows.append(url))
    return offered, windows


def test_native_phone_launch_keeps_the_token_out_of_the_log_and_offers_the_link_in_a_dialog(monkeypatch, capsys):
    started = fake_launch(monkeypatch)
    offered, windows = fake_native(monkeypatch)

    assert launch_app.main(["--phone", "--native", "--port", "54321"]) == 0

    token = started[0][1]["env"]["MUCARO_AI_OPERATOR_TOKEN"]
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert "PHONE_URL http://192.168.1.20:54321/ (token withheld from this log)" in captured.out
    ((message, link),) = offered
    assert link == f"http://192.168.1.20:54321/#token={token}"
    assert "Copy the link" in message and token not in message
    assert windows == [f"http://127.0.0.1:54321/#token={token}"]  # this computer's own window needs the token too


def test_native_phone_launch_says_so_when_there_is_no_private_address(monkeypatch):
    fake_launch(monkeypatch, addresses=())
    offered, windows = fake_native(monkeypatch)

    assert launch_app.main(["--phone", "--native", "--port", "54321"]) == 0

    ((message, link),) = offered
    assert link == "" and "no private network address" in message
    assert len(windows) == 1


def test_native_launch_without_phone_access_offers_nothing_and_opens_the_plain_url(monkeypatch):
    fake_launch(monkeypatch)
    offered, windows = fake_native(monkeypatch)

    assert launch_app.main(["--native", "--port", "54321"]) == 0

    assert offered == [] and windows == ["http://127.0.0.1:54321"]


def test_the_phone_dialog_keeps_the_link_out_of_the_process_arguments(monkeypatch):
    native = launch_app.native_window
    spawned = []
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(native.subprocess, "Popen", lambda cmd, **kwargs: spawned.append((cmd, kwargs)))
    link = "http://192.168.1.20:8000/#token=SECRET"

    native.show_phone_link("Phone access is on.", link)
    native.show_phone_link("No private network address.", "")

    (cmd, kwargs), (plain_cmd, plain_kwargs) = spawned
    assert "SECRET" not in " ".join(cmd) and "Copy link" in " ".join(cmd)
    assert kwargs["env"]["MUCARO_PHONE_LINK"] == link  # argv is visible to every local user; the environment is not
    assert "Copy link" not in " ".join(plain_cmd) and plain_kwargs["env"] is None


def test_the_windows_phone_dialog_asks_before_copying_and_keeps_the_link_out_of_the_dialog(monkeypatch):
    native = launch_app.native_window
    boxes, copies = [], []
    answers = iter([6, 7])  # IDYES, then IDNO

    class User32:
        def MessageBoxW(self, hwnd, text, caption, flags):
            boxes.append((text, caption, flags))
            return next(answers)

    monkeypatch.setattr(native, "_windll", lambda name: User32())
    monkeypatch.setattr(native.subprocess, "run", lambda cmd, **kwargs: copies.append((cmd, kwargs)))
    link = "http://192.168.1.20:8000/#token=SECRET"

    native._windows_phone_dialog("Phone access is on.", link)  # the person says Yes
    native._windows_phone_dialog("Phone access is on.", link)  # the person says No
    native._windows_phone_dialog("No private network address.", "")  # nothing to copy: a plain message

    assert len(boxes) == 3 and all("SECRET" not in text for text, _, _ in boxes)
    assert boxes[0][0].endswith("Copy the link to the clipboard?") and boxes[0][2] & 0x4  # a Yes/No box
    assert not boxes[2][2] & 0x4 and "clipboard" not in boxes[2][0]  # no question without a link
    assert [cmd for cmd, _ in copies] == [["clip"]] and copies[0][1]["input"] == link  # only the Yes copied


def test_the_windows_phone_dialog_runs_on_its_own_thread_so_the_window_still_opens(monkeypatch):
    native = launch_app.native_window
    threads = []

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            threads.append((target, args, daemon))

        def start(self):
            threads.append("started")

    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native.threading, "Thread", FakeThread)

    native.show_phone_link("Phone access is on.", "http://192.168.1.20:8000/#token=SECRET")

    assert threads == [(native._windows_phone_dialog, ("Phone access is on.", "http://192.168.1.20:8000/#token=SECRET"), True), "started"]


def test_the_phone_dialog_does_nothing_off_macos_and_windows(monkeypatch):
    native = launch_app.native_window
    spawned = []
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native.subprocess, "Popen", lambda cmd, **kwargs: spawned.append(cmd))

    native.show_phone_link("Phone access is on.", "http://192.168.1.20:8000/#token=SECRET")

    assert spawned == []
