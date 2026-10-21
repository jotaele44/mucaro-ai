"""Desktop shell gates (TGT-001, TGT-017, TGT-026): the packaged app's launcher, run headless."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from backend.app import desktop_shell

ROOT = Path(__file__).resolve().parents[2]


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MUCARO_AI_")}
    env["PYTHONPATH"] = str(ROOT)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    return env


def _shell(*args: str) -> list[str]:
    return [sys.executable, "-m", "backend.app.desktop_shell", *args]


def _get(url: str, token: str | None = None, *, gui: bool = False) -> tuple[int, bytes]:
    headers = {"X-Mucaro-Token": token} if token else {}
    if gui:
        headers["X-Mucaro-Client"] = "mucaro-gui"
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _cors_origin(url: str, origin: str) -> str | None:
    request = urllib.request.Request(url, headers={"Origin": origin})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        return response.headers.get("access-control-allow-origin")


def _wait_serving(process: subprocess.Popen[str], data_dir: Path, timeout: float = 60.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    instance = data_dir / "instance.json"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"shell exited early: {process.returncode} {process.stderr.read() if process.stderr else ''}")
        if instance.exists():
            try:
                return json.loads(instance.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        time.sleep(0.1)
    raise AssertionError("desktop shell never wrote instance.json")


def test_smoke_starts_serves_and_stops_with_data_outside_the_code_tree(tmp_path: Path) -> None:
    data_dir = tmp_path / "Mucaro AI"
    result = subprocess.run(_shell("--smoke", "--data-dir", str(data_dir)), cwd=tmp_path, env=_env(), capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    assert "DESKTOP_SMOKE_OK" in result.stdout
    assert (data_dir / "processed" / "mucaro_ai.sqlite3").exists()
    assert (data_dir / "logs").is_dir()
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700, "a new data directory is private to the user"
    assert not (data_dir / "instance.json").exists(), "instance file must be removed on exit"


def test_no_window_service_enforces_the_launch_token_and_single_instance(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    process = subprocess.Popen(_shell("--no-window", "--data-dir", str(data_dir)), cwd=tmp_path, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        instance = _wait_serving(process, data_dir)
        instance_path = data_dir / "instance.json"
        assert stat.S_IMODE(instance_path.stat().st_mode) == 0o600
        port, token = instance["port"], str(instance["token"])
        assert len(token) >= 32
        base = f"http://127.0.0.1:{port}"

        assert _get(f"{base}/health", gui=True)[0] == 200
        # Positive and negative gate: private routes need this launch's token.
        assert _get(f"{base}/conversations", gui=True)[0] == 401
        assert _get(f"{base}/conversations", token="wrong-token", gui=True)[0] == 401
        assert _get(f"{base}/conversations", token=token)[0] == 200
        # Neither public, rejected nor non-GUI calls count as the window's GUI being connected.
        assert "ui" not in json.loads(instance_path.read_text(encoding="utf-8"))
        assert _get(f"{base}/conversations", token=token, gui=True)[0] == 200
        ui = json.loads(instance_path.read_text(encoding="utf-8"))["ui"]
        assert ui["status"] == "GUI_CONNECTED" and ui["first_path"] == "/conversations"
        assert stat.S_IMODE(instance_path.stat().st_mode) == 0o600

        # The service listens on loopback only.
        assert instance["url"] == f"{base}/"

        # The packaged runtime is a production deployment, and only its own origin may read it cross-origin.
        security = json.loads(instance_path.read_text(encoding="utf-8"))["security"]
        assert security == {"status": "SECURITY_OK", "production_ready": True, "deployment_profile": "production", "cors_origins": [base], "failing": []}
        own = _cors_origin(f"{base}/health", base)
        assert own == base
        with pytest.raises(urllib.error.HTTPError) as foreign:
            _cors_origin(f"{base}/health", "http://localhost:5173")
        assert foreign.value.code == 403
        assert foreign.value.headers.get("access-control-allow-origin") is None

        second = subprocess.run(_shell("--smoke", "--data-dir", str(data_dir)), cwd=tmp_path, env=_env(), capture_output=True, text=True, timeout=120, check=False)
        assert second.returncode == desktop_shell.EXIT_ALREADY_RUNNING
        assert "ERROR_ALREADY_RUNNING" in second.stderr
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
    assert process.returncode == 0
    assert not (data_dir / "instance.json").exists()


def test_service_serves_the_built_gui_when_present(tmp_path: Path) -> None:
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        pytest.skip("frontend/dist not built in this checkout")
    data_dir = tmp_path / "data"
    process = subprocess.Popen(_shell("--no-window", "--data-dir", str(data_dir)), cwd=tmp_path, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        instance = _wait_serving(process, data_dir)
        status, body = _get(f"http://127.0.0.1:{instance['port']}/")
        assert status == 200
        assert b'<div id="root">' in body
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)


def test_startup_failure_exits_with_status_and_writes_a_log(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "processed").write_text("not a directory", encoding="utf-8")
    result = subprocess.run(_shell("--smoke", "--data-dir", str(data_dir)), cwd=tmp_path, env=_env(), capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == desktop_shell.EXIT_START_FAILED
    assert "ERROR_DESKTOP_START_FAILED" in result.stderr
    assert (data_dir / "logs" / "startup-error.log").read_text(encoding="utf-8").strip()


def test_instance_lock_is_exclusive(tmp_path: Path) -> None:
    first = desktop_shell.InstanceLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(desktop_shell.AlreadyRunning):
            desktop_shell.InstanceLock(tmp_path).acquire()
    finally:
        first.release()
    again = desktop_shell.InstanceLock(tmp_path)
    again.acquire()
    again.release()


def test_instance_file_is_private(tmp_path: Path) -> None:
    path = desktop_shell.write_instance_file(tmp_path, 43210, "t" * 43)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8"))["port"] == 43210


def test_macos_data_dir_is_application_support(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(desktop_shell.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_shell.Path, "home", classmethod(lambda cls: tmp_path))
    assert desktop_shell.default_data_dir() == tmp_path / "Library" / "Application Support" / "Mucaro AI"


def test_frozen_bundle_sets_resource_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in ("MUCARO_AI_DATA_DIR", "MUCARO_AI_OPERATOR_TOKEN", "MUCARO_AI_ROOT", "MUCARO_AI_DEPLOYMENT_PROFILE"):
        # setenv first so monkeypatch restores the original state after prepare_environment writes os.environ.
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    monkeypatch.setattr(desktop_shell.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_shell.sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    desktop_shell.prepare_environment(tmp_path / "data", "launch-token")
    assert os.environ["MUCARO_AI_ROOT"] == str(tmp_path / "bundle")
    assert os.environ["MUCARO_AI_DATA_DIR"] == str(tmp_path / "data")
    assert os.environ["MUCARO_AI_OPERATOR_TOKEN"] == "launch-token"


class _Event:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


def test_window_allows_downloads_so_save_and_share_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[dict] = []
    window = types.SimpleNamespace(events=types.SimpleNamespace(shown=_Event(), closing=_Event()), destroy=lambda: None)
    fake = types.ModuleType("webview")
    fake.settings = {"ALLOW_DOWNLOADS": False}
    fake.create_window = lambda *args, **kwargs: window
    fake.start = lambda **kwargs: started.append(kwargs)
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop_shell, "_graceful_sigterm", lambda on_signal: None)

    outcome = desktop_shell._open_window(
        "http://127.0.0.1:1/", tmp_path, instance=tmp_path / "instance.json", started=time.monotonic(), on_closing=lambda: None
    )

    assert outcome == "NATIVE_WINDOW_CLOSED"
    assert fake.settings["ALLOW_DOWNLOADS"] is True
    assert started == [{"private_mode": False, "storage_path": str(tmp_path / "webview")}]

