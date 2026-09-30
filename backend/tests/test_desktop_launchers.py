from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.test_api import make_settings
from scripts import build_launchers, launch_app, one_click, progress_page

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "Start Mucaro AI.app"
APP_LAUNCHER = APP / "Contents" / "MacOS" / "mucaro-launcher"
EXE = REPO / "Start Mucaro AI.exe"
WINDOWS_SRC = REPO / "packaging" / "launchers" / "windows"
PAGE_SH = REPO / "scripts" / "progress_page.sh"
PS1 = REPO / "scripts" / "one_click_windows.ps1"
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")
# Same rules as test_one_click.py: a PATH tool at a command position would open the macOS developer-tools dialog.
PATH_TOOL_AT_COMMAND_POSITION = re.compile(
    r"(?:^|[;&|(!]|\$\(|\b(?:if|elif|while|until|then|do|else|exec|command)\b)\s*(python3?|git|node|npm|npx|pip3?)\b"
)
BASH4_ONLY = [r"declare\s+-A", r"\bmapfile\b", r"\breadarray\b", r"\$\{[^}]*,,", r"\$\{[^}]*\^\^", r"&>>", r"\bcoproc\b", r";;&", r"\|&"]
SAMPLE = {
    "step": 2,
    "title": 'Web <interface> & "packages"',
    "detail": "it's 50% done",
    "log": "/tmp/a b/setup.log",
    "url": "http://127.0.0.1:8001",
}


def _code_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]


def _project_version() -> str:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def _git_modes(*paths: str) -> dict[str, str] | None:
    if shutil.which("git") is None:
        return None
    proc = subprocess.run(["git", "ls-files", "-s", "--", *paths], cwd=REPO, text=True, capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return {line.split("\t", 1)[1]: line.split()[0] for line in proc.stdout.splitlines()}


# --- where the launchers live -------------------------------------------------------------------


def test_top_folder_has_the_four_double_click_launchers():
    # Start Mucaro AI.app/.exe run the source setup with no terminal window; Mucaro AI.command/.cmd run the
    # same setup in a Terminal/console window (the supported source setup). The native release app
    # (docs/release/MACOS_APP.md) is built by CI, never committed, so no top-level "Mucaro AI.app" exists.
    launchers = sorted(path.name for path in REPO.iterdir() if "Mucaro AI" in path.name)
    assert launchers == ["Mucaro AI.cmd", "Mucaro AI.command", "Start Mucaro AI.app", "Start Mucaro AI.exe"]


# --- macOS app bundle -----------------------------------------------------------------------------


def test_app_bundle_info_plist_matches_launcher_and_project():
    info = plistlib.loads((APP / "Contents" / "Info.plist").read_bytes())

    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleName"] == info["CFBundleDisplayName"] == "Start Mucaro AI"
    # Distinct from the native release app, so macOS never treats one as the other.
    assert info["CFBundleIdentifier"] == "ai.mucaro.source-launcher"
    assert (APP / "Contents" / "MacOS" / info["CFBundleExecutable"]) == APP_LAUNCHER
    assert info["CFBundleShortVersionString"] == info["CFBundleVersion"] == _project_version()
    assert info["LSUIElement"] is True
    assert info["LSMinimumSystemVersion"] == "13.0"
    icon = APP / "Contents" / "Resources" / f"{info['CFBundleIconFile']}.icns"
    assert icon.read_bytes()[:4] == b"icns"


def test_app_launcher_is_executable_in_git():
    modes = _git_modes("Start Mucaro AI.app/Contents/MacOS/mucaro-launcher")
    if modes is None:
        pytest.skip("not a git checkout")
    assert modes == {"Start Mucaro AI.app/Contents/MacOS/mucaro-launcher": "100755"}


@pytest.mark.skipif(BASH is None, reason="bash not installed")
def test_app_launcher_and_page_renderer_parse():
    for path in (APP_LAUNCHER, PAGE_SH):
        proc = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr


def test_app_launcher_follows_the_stage0_shell_rules():
    for path in (APP_LAUNCHER, PAGE_SH):
        path.read_bytes().decode("ascii")
        text = "\n".join(_code_lines(path))
        for line in _code_lines(path):
            assert not PATH_TOOL_AT_COMMAND_POSITION.search(line), f"{path.name}: {line}"
        for pattern in BASH4_ONLY:
            assert not re.search(pattern, text), f"{path.name}: {pattern}"


def test_app_launcher_detaches_opens_page_and_guards_translocation():
    text = APP_LAUNCHER.read_text(encoding="utf-8")
    assert "*/AppTranslocation/*" in text
    assert 'scripts/one_click.sh" --ui' in text
    assert "set -m" in text  # own process group, so the app outlives this short-lived launcher
    assert '/usr/bin/open "${PAGE}"' in text


# --- Windows launcher -----------------------------------------------------------------------------


def _pe_header(data: bytes) -> tuple[int, int]:
    assert data[:2] == b"MZ"
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe : pe + 4] == b"PE\0\0"
    machine = struct.unpack_from("<H", data, pe + 4)[0]
    subsystem = struct.unpack_from("<H", data, pe + 24 + 68)[0]
    return machine, subsystem


def test_windows_launcher_is_a_small_x64_gui_executable():
    data = EXE.read_bytes()
    machine, subsystem = _pe_header(data)

    assert machine == 0x8664  # x86-64
    assert subsystem == 2  # GUI: no console window flashes on double-click
    assert len(data) < 1_000_000  # certify_release large_file_scan limit


def test_windows_launcher_matches_its_recorded_build_hash():
    recorded = (WINDOWS_SRC / "BUILD_SHA256").read_text(encoding="utf-8").split()
    assert recorded[1:] == ["Start", "Mucaro", "AI.exe"]
    assert hashlib.sha256(EXE.read_bytes()).hexdigest() == recorded[0]


def test_windows_launcher_source_starts_hidden_setup():
    source = (WINDOWS_SRC / "mucaro_launcher.c").read_text(encoding="utf-8")
    assert "CREATE_NO_WINDOW" in source
    assert "scripts\\\\one_click_windows.ps1" in source
    assert "--ui" in source
    assert "-WindowStyle" not in source
    rc = (WINDOWS_SRC / "mucaro_launcher.rc").read_text(encoding="utf-8")
    assert f'VALUE "ProductVersion", "{_project_version()}"' in rc
    assert 'VALUE "OriginalFilename", "Start Mucaro AI.exe"' in rc
    ico = (REPO / "packaging" / "icons" / "mucaro.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", ico, 0)
    assert (reserved, kind) == (0, 1) and count >= 6


def test_build_requirements_are_hash_pinned():
    lines = (REPO / "packaging" / "launchers" / "build-requirements.txt").read_text(encoding="utf-8")
    assert "ziglang==0.16.0" in lines and "pillow==12.3.0" in lines
    assert len(re.findall(r"--hash=sha256:[0-9a-f]{64}", lines)) >= 3


def test_launcher_builder_fails_closed_outside_its_frozen_build_host():
    assert build_launchers.build_host_error(system="Linux", machine="x86_64", python_version=(3, 12)) is None

    for system, machine, python_version in (
        ("Darwin", "x86_64", (3, 12)),
        ("Linux", "aarch64", (3, 12)),
        ("Linux", "x86_64", (3, 13)),
    ):
        error = build_launchers.build_host_error(system=system, machine=machine, python_version=python_version)
        assert error == (
            "ERROR_LAUNCHER_BUILD_HOST_UNSUPPORTED "
            "expected=Linux/x86_64/Python-3.11-or-3.12 "
            f"actual={system}/{machine}/Python-{python_version[0]}.{python_version[1]}"
        )


# --- progress page --------------------------------------------------------------------------------


def test_progress_template_is_ascii_and_has_every_placeholder():
    template = progress_page.TEMPLATE.read_bytes().decode("ascii")
    for name in ("REFRESH", "STATE", "STEP", "TOTAL", "PERCENT", "TITLE", "DETAIL", "LOG", "URL"):
        assert "{{" + name + "}}" in template


def test_progress_page_states():
    running = progress_page.render("running", **SAMPLE)
    ready = progress_page.render("ready", **SAMPLE)
    error = progress_page.render("error", **SAMPLE)
    stopped = progress_page.render("stopped", **SAMPLE)

    assert '<meta http-equiv="refresh" content="1">' in running and 'class="state-running"' in running
    assert '<meta http-equiv="refresh" content="0; url=http://127.0.0.1:8001">' in ready
    assert 'href="http://127.0.0.1:8001"' in ready
    assert "http-equiv" not in error.split("-->", 1)[1] and "http-equiv" not in stopped.split("-->", 1)[1]
    assert "/tmp/a b/setup.log" in error
    assert "{{" not in running.split("-->", 1)[1]
    assert "Web &lt;interface&gt; &amp; &quot;packages&quot;" in running
    assert "it&#x27;s 50% done" in running
    assert 'style' in running and "width: 33%" in running
    with pytest.raises(ValueError):
        progress_page.render("unknown")


def test_progress_page_write_is_atomic_and_try_write_never_raises(tmp_path):
    page = tmp_path / "ui" / "progress.html"
    progress_page.write(page, "running", step=1, title="t")
    assert 'class="state-running"' in page.read_text(encoding="utf-8")
    assert not (tmp_path / "ui" / "progress.html.tmp").exists()

    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    progress_page.try_write(blocker / "sub" / "progress.html", "running")  # parent is a file: ignored
    progress_page.try_write(None, "running")


@pytest.mark.skipif(BASH is None, reason="bash not installed")
@pytest.mark.parametrize("state", progress_page.STATES)
def test_bash_renderer_matches_python(state):
    proc = subprocess.run(
        [BASH, "-c", 'source "$0"; progress_page_render "$@"', str(PAGE_SH), state, str(SAMPLE["step"]), SAMPLE["title"], SAMPLE["detail"], SAMPLE["log"], SAMPLE["url"]],
        capture_output=True,
        check=True,
    )
    assert proc.stdout.decode("utf-8") == progress_page.render(state, **SAMPLE)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 not installed")
@pytest.mark.parametrize("state", ["running", "error"])
def test_powershell_renderer_matches_python(state, tmp_path):
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        "param([string]$Out, [string]$State)\n"
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{PS1}', [ref]$null, [ref]$null)\n"
        "$fns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "@('ConvertTo-PageText','Write-ProgressPage') -contains $n.Name }, $true)\n"
        "foreach ($f in $fns) { . ([ScriptBlock]::Create($f.Extent.Text)) }\n"
        f"$UiMode = $true; $PageTemplate = '{progress_page.TEMPLATE}'; $script:Log = '{SAMPLE['log']}'; $Page = $Out\n"
        f"Write-ProgressPage $State {SAMPLE['step']} 'Web <interface> & \"packages\"' \"it's 50% done\"\n",
        encoding="utf-8",
    )
    out = tmp_path / "page.html"
    subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-File", str(harness), "-Out", str(out), "-State", state], check=True)
    expected = progress_page.render(state, step=SAMPLE["step"], title=SAMPLE["title"], detail=SAMPLE["detail"], log=SAMPLE["log"])
    assert out.read_text(encoding="utf-8") == expected


# --- quit path: POST /shutdown ---------------------------------------------------------------------

LOCAL = ("127.0.0.1", 50000)
QUIT = {"X-Mucaro-Action": "quit"}


def test_shutdown_requires_the_quit_header(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_STOP_FILE", str(tmp_path / "stop"))
    client = TestClient(create_app(make_settings(tmp_path)), client=LOCAL)

    response = client.post("/shutdown")

    assert response.status_code == 400
    assert response.json()["detail"]["status"] == "SHUTDOWN_HEADER_REQUIRED"
    assert not (tmp_path / "stop").exists()


def test_shutdown_refuses_when_not_started_by_the_launcher(tmp_path, monkeypatch):
    monkeypatch.delenv("MUCARO_AI_STOP_FILE", raising=False)
    client = TestClient(create_app(make_settings(tmp_path)), client=LOCAL)

    response = client.post("/shutdown", headers=QUIT)

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "SHUTDOWN_NOT_SUPERVISED"
    assert client.get("/config").json()["desktop"] == {"quit_supported": False}


@pytest.mark.parametrize("host", ["203.0.113.9", "192.168.1.20", "testclient"])
def test_shutdown_is_loopback_only(tmp_path, monkeypatch, host):
    monkeypatch.setenv("MUCARO_AI_STOP_FILE", str(tmp_path / "stop"))
    client = TestClient(create_app(make_settings(tmp_path)), client=(host, 1))

    response = client.post("/shutdown", headers=QUIT)

    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "SHUTDOWN_LOOPBACK_ONLY"
    assert not (tmp_path / "stop").exists()


def test_shutdown_enforces_the_operator_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCARO_AI_STOP_FILE", str(tmp_path / "stop"))
    client = TestClient(create_app(make_settings(tmp_path, operator_token="t" * 40)), client=LOCAL)

    assert client.post("/shutdown", headers=QUIT).status_code == 401
    assert client.post("/shutdown", headers={**QUIT, "X-Mucaro-Token": "t" * 40}).status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_shutdown_writes_the_stop_file_for_the_launcher(tmp_path, monkeypatch, host):
    stop = tmp_path / "stop-request"
    monkeypatch.setenv("MUCARO_AI_STOP_FILE", str(stop))
    client = TestClient(create_app(make_settings(tmp_path)), client=(host, 1))

    response = client.post("/shutdown", headers=QUIT)

    assert response.status_code == 200
    assert response.json() == {"status": "SHUTDOWN_REQUESTED"}
    assert stop.read_text(encoding="utf-8") == "quit\n"
    assert client.get("/config").json()["desktop"] == {"quit_supported": True}


# --- quit path: launcher side ---------------------------------------------------------------------


class RunningProc:
    returncode = None

    def poll(self):
        return None


def _fake_backend(monkeypatch, *, busy_ports=frozenset()):
    started: list[tuple[int, dict]] = []
    stopped: list[int] = []
    monkeypatch.setattr(launch_app, "ensure_frontend_dist", lambda skip_build=False, verified=False: None)
    monkeypatch.setattr(launch_app, "port_open", lambda port: port in busy_ports or any(p == port for p, _ in started))
    monkeypatch.setattr(launch_app, "health_ok", lambda port: any(p == port for p, _ in started))
    monkeypatch.setattr(launch_app, "start_backend", lambda port, extra_env=None: started.append((port, extra_env or {})) or RunningProc())
    monkeypatch.setattr(launch_app, "wait_for_health", lambda port, timeout: True)
    monkeypatch.setattr(launch_app, "stop_backend", lambda proc, port: stopped.append(port) or True)
    monkeypatch.setattr(launch_app.time, "sleep", lambda seconds: None)
    return started, stopped


def test_launch_stops_cleanly_when_the_quit_button_writes_the_stop_file(tmp_path, monkeypatch, capsys):
    started, stopped = _fake_backend(monkeypatch)
    stop = tmp_path / "stop-request"
    page = tmp_path / "progress.html"
    stop.write_text("left over from an earlier session", encoding="utf-8")
    polls = {"count": 0}
    real_exists = Path.exists

    def exists(path):
        if path == stop:
            polls["count"] += 1
            if polls["count"] == 3:  # the 1st check clears the leftover; the quit arrives while running
                stop.write_text("quit\n", encoding="utf-8")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists)

    code = launch_app.main(["--port", "54321", "--no-browser", "--status-file", str(page), "--stop-file", str(stop)])

    assert code == 0
    assert started == [(54321, {"MUCARO_AI_STOP_FILE": str(stop)})]
    assert stopped == [54321]
    assert not stop.exists()
    assert "DESKTOP_STOP_REQUESTED reason=quit_button" in capsys.readouterr().out
    assert 'class="state-stopped"' in page.read_text(encoding="utf-8")


def test_launch_reusing_a_running_app_keeps_its_pending_quit_request(tmp_path, monkeypatch):
    # Quit clicked, then a double-click within the same second: the new launcher must not eat the request.
    _fake_backend(monkeypatch)
    monkeypatch.setattr(launch_app, "port_open", lambda port: True)
    monkeypatch.setattr(launch_app, "health_ok", lambda port: True)
    monkeypatch.setattr(launch_app, "start_backend", lambda port, extra_env=None: pytest.fail("must reuse"))
    stop = tmp_path / "stop-request"
    stop.write_text("quit\n", encoding="utf-8")

    code = launch_app.main(["--port", "54321", "--no-browser", "--exit-if-running", "--stop-file", str(stop)])

    assert code == 0
    assert stop.exists()


def test_launch_marks_the_page_ready_then_stopped_in_a_smoke_run(tmp_path, monkeypatch):
    _fake_backend(monkeypatch)
    page = tmp_path / "progress.html"
    seen = []
    real_try_write = progress_page.try_write
    monkeypatch.setattr(
        launch_app.progress_page,
        "try_write",
        lambda path, state, **fields: seen.append((state, fields.get("url"))) or real_try_write(path, state, **fields),
    )

    assert launch_app.main(["--port", "54321", "--smoke-test", "--no-browser", "--hold-seconds", "0", "--status-file", str(page)]) == 0

    assert seen == [("ready", "http://127.0.0.1:54321"), ("stopped", "")]


def test_launch_reports_a_port_conflict_on_the_page(tmp_path, monkeypatch):
    _fake_backend(monkeypatch, busy_ports={54321})
    page = tmp_path / "progress.html"

    assert launch_app.main(["--port", "54321", "--no-browser", "--status-file", str(page)]) == 5

    text = page.read_text(encoding="utf-8")
    assert 'class="state-error"' in text
    assert "Port 54321 is used by another program" in text


# --- stage 1 in --ui mode --------------------------------------------------------------------------


def test_one_click_ui_mode_reports_steps_and_hands_the_page_to_the_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCARO_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("MUCARO_RUN_LOG", str(tmp_path / "logs" / "run-1.log"))  # skips the fd redirect
    monkeypatch.delenv("MUCARO_SETUP_LOG", raising=False)
    monkeypatch.delenv("MUCARO_SETUP_LOCK", raising=False)
    pages = []

    def fake_run(self):
        self._step(1, "Python packages")
        pages.append((tmp_path / "ui" / "progress.html").read_text(encoding="utf-8"))
        return []

    monkeypatch.setattr(one_click.OneClickSetup, "run", fake_run)
    launched = []
    monkeypatch.setattr(one_click.launch_app, "main", lambda argv=None: launched.append(argv) or 0)

    assert one_click.main(["--ui", "--no-browser"]) == 0

    assert "Installing Python packages" in pages[0] and "Step 1 of 6" in pages[0]
    page = tmp_path / "ui" / "progress.html"
    assert "Starting Mucaro AI" in page.read_text(encoding="utf-8")
    argv = launched[0]
    assert argv[: len(one_click.LAUNCH_ARGS)] == one_click.LAUNCH_ARGS
    assert argv[argv.index("--status-file") + 1] == str(page)
    assert argv[argv.index("--stop-file") + 1] == str(tmp_path / "stop-request")
    assert "--ui" not in argv


@pytest.mark.parametrize(
    ("error", "code", "expected"),
    [
        (one_click.SetupError("ERROR_NPM_CI_FAILED", "returncode=1"), 2, "ERROR_NPM_CI_FAILED: returncode=1"),
        (RuntimeError("boom"), 1, "ERROR_UNEXPECTED: boom"),
    ],
)
def test_one_click_ui_mode_never_leaves_the_page_running_after_a_failure(tmp_path, monkeypatch, error, code, expected):
    monkeypatch.setenv("MUCARO_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("MUCARO_RUN_LOG", str(tmp_path / "logs" / "run-1.log"))
    monkeypatch.delenv("MUCARO_SETUP_LOG", raising=False)
    monkeypatch.delenv("MUCARO_SETUP_LOCK", raising=False)

    def fail(self):
        raise error

    monkeypatch.setattr(one_click.OneClickSetup, "run", fail)
    monkeypatch.setattr(one_click.launch_app, "main", lambda argv=None: pytest.fail("must not launch"))

    assert one_click.main(["--ui"]) == code

    text = (tmp_path / "ui" / "progress.html").read_text(encoding="utf-8")
    assert 'class="state-error"' in text
    assert expected in text


def test_one_click_ui_mode_sends_all_output_to_a_run_log(tmp_path):
    # Run in a child process: the redirect replaces file descriptors 1 and 2.
    code = (
        "import os, subprocess, sys; from pathlib import Path; import scripts.one_click as oc; "
        "os.environ.pop('MUCARO_RUN_LOG', None); "
        f"oc._redirect_output_to_run_log(Path({str(tmp_path)!r})); "
        "print('from python', flush=True); "
        "subprocess.run([sys.executable, '-c', 'print(\"from child\")'], check=True)"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True, env={**os.environ, "PYTHONPATH": str(REPO)})

    assert proc.stdout == ""
    logs = list((tmp_path / "logs").glob("run-*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "from python" in text and "from child" in text
