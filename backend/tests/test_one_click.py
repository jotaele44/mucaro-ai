from __future__ import annotations

import json
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.launch_app as launch_app
import scripts.one_click as one_click
import scripts.preflight as preflight

REPO = Path(__file__).resolve().parents[2]
COMMAND_FILE = REPO / "Mucaro AI.command"
CMD_FILE = REPO / "Mucaro AI.cmd"
STAGE0_SH = REPO / "scripts" / "one_click.sh"
STAGE0_PS1 = REPO / "scripts" / "one_click_windows.ps1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
# A command word at a command position: line start, after ; & | ( $( ! or a keyword that starts a command.
PATH_TOOL_AT_COMMAND_POSITION = re.compile(
    r"(?:^|[;&|(!]|\$\(|\b(?:if|elif|while|until|then|do|else|exec|command)\b)\s*(python3?|git|node|npm|npx|pip3?)\b"
)


def _code_lines(path: Path, comment: str) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith(comment)]


# --- pins -------------------------------------------------------------------------------------


def test_pins_cover_every_supported_platform_with_sha256():
    pins = one_click.read_pins()

    assert re.fullmatch(r"\d+\.\d+\.\d+", pins["UV_VERSION"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", pins["NODE_VERSION"])
    assert pins["PYTHON_VERSION"] == "3.12"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", pins["PY_EXCLUDE_NEWER"])
    assert pins["SETUP_SCHEMA"].isdigit()
    for target in ("aarch64_apple_darwin", "x86_64_apple_darwin", "x86_64_unknown_linux_gnu", "x86_64_pc_windows_msvc"):
        assert HEX64.match(pins[f"UV_SHA256_{target}"]), target
    for plat in ("darwin_arm64", "darwin_x64", "linux_x64", "win_x64"):
        assert HEX64.match(pins[f"NODE_SHA256_{plat}"]), plat


def test_pins_file_is_plain_key_value_for_bash_and_powershell():
    for line in _code_lines(REPO / "scripts" / "one_click_pins.env", "#"):
        if line.strip():
            assert re.fullmatch(r"[A-Z][A-Za-z0-9_]*=[^\s\"'$`]+", line), line


def test_stage0_scripts_reference_only_pinned_targets():
    pins = one_click.read_pins()
    shell = STAGE0_SH.read_text(encoding="utf-8")
    for target in re.findall(r'UV_TARGET="([^"]+)"', shell):
        assert f"UV_SHA256_{target.replace('-', '_')}" in pins
    for plat in re.findall(r'NODE_PLAT="([^"]+)"', shell):
        assert f"NODE_SHA256_{plat.replace('-', '_')}" in pins
    ps1 = STAGE0_PS1.read_text(encoding="utf-8")
    for key in re.findall(r'"((?:UV|NODE)_SHA256_[a-z0-9_]+)"', ps1):
        assert key in pins


# --- double-click entry files and stage 0 scripts -----------------------------------------------


def test_command_file_delegates_to_stage0():
    text = COMMAND_FILE.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash\n")
    assert "scripts/one_click.sh" in text


def test_command_file_is_executable_in_git():
    # Finder only runs a .command file with the executable bit; git records it as mode 100755.
    # Extracted source archives (certify_source_artifact.py) do not restore modes, so check git only.
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    proc = subprocess.run(
        ["git", "ls-files", "-s", "--", COMMAND_FILE.name, "scripts/one_click.sh"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.skip("not a git checkout")
    lines = proc.stdout.splitlines()
    assert len(lines) == 2
    for line in lines:
        assert line.startswith("100755 "), line
    assert COMMAND_FILE.stat().st_mode & 0o111
    assert STAGE0_SH.stat().st_mode & 0o111


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_stage0_shell_scripts_parse():
    for path in (STAGE0_SH, COMMAND_FILE):
        proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr


def test_path_tool_pattern_flags_command_positions_only():
    assert PATH_TOOL_AT_COMMAND_POSITION.search("python3 -c 'print(1)'")
    assert PATH_TOOL_AT_COMMAND_POSITION.search("if git rev-parse HEAD; then")
    assert PATH_TOOL_AT_COMMAND_POSITION.search('found="$(node --version)"')
    assert PATH_TOOL_AT_COMMAND_POSITION.search("exec python3 app.py")
    assert not PATH_TOOL_AT_COMMAND_POSITION.search('"${UV_BIN}" python install 3.12')
    assert not PATH_TOOL_AT_COMMAND_POSITION.search('[ -x "${NODE_HOME}/bin/node" ]')


def test_stage0_shell_never_runs_python_git_node_or_npm_from_path():
    # On macOS without the Command Line Tools, /usr/bin/python3 and /usr/bin/git are stubs that
    # open an install dialog, which would break the no-input promise.
    for script in (STAGE0_SH, COMMAND_FILE):
        for line in _code_lines(script, "#"):
            assert not PATH_TOOL_AT_COMMAND_POSITION.search(line), f"{script.name}: {line}"


def test_stage0_shell_avoids_bash4_only_syntax():
    # macOS ships bash 3.2.
    forbidden = [r"declare\s+-A", r"\bmapfile\b", r"\breadarray\b", r"\$\{[^}]*,,", r"\$\{[^}]*\^\^", r"&>>", r"\bcoproc\b", r";;&", r"\|&"]
    for script in (STAGE0_SH, COMMAND_FILE):
        text = "\n".join(_code_lines(script, "#"))
        for pattern in forbidden:
            assert not re.search(pattern, text), f"{script.name}: {pattern}"


def test_windows_entry_files_are_ascii_and_cmd_uses_crlf():
    for path in (STAGE0_PS1, CMD_FILE):
        path.read_bytes().decode("ascii")
    data = CMD_FILE.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n") > 0
    assert "*.cmd text eol=crlf" in (REPO / ".gitattributes").read_text(encoding="utf-8")
    cmd = data.decode("ascii")
    assert "-ExecutionPolicy Bypass" in cmd
    assert r"scripts\one_click_windows.ps1" in cmd
    assert "if not defined CI pause" in cmd


def test_windows_stage0_never_merges_native_stderr():
    # In Windows PowerShell 5.1, 2>&1 on a native command turns its first stderr line into a terminating error.
    for line in _code_lines(STAGE0_PS1, "#"):
        assert "2>&1" not in line, line


def test_runtime_folder_is_git_ignored():
    assert ".mucaro-runtime/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()


# --- stage 1 helpers ------------------------------------------------------------------------------


def test_fingerprint_tracks_parts_file_content_and_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(one_click, "ROOT", tmp_path)
    source = tmp_path / "a.txt"
    source.write_text("one", encoding="utf-8")

    base = one_click.fingerprint(["x=1"], [source])
    assert base == one_click.fingerprint(["x=1"], [source])
    assert base != one_click.fingerprint(["x=2"], [source])
    source.write_text("two", encoding="utf-8")
    assert base != one_click.fingerprint(["x=1"], [source])
    assert one_click.fingerprint([], [tmp_path / "missing.txt"]) != one_click.fingerprint([], [])


def test_frontend_source_files_cover_build_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(one_click, "ROOT", tmp_path)
    frontend = tmp_path / "frontend"
    (frontend / "src" / "components").mkdir(parents=True)
    (frontend / "src" / "components" / "A.jsx").write_text("a", encoding="utf-8")
    (frontend / "vite.config.js").write_text("v", encoding="utf-8")
    (frontend / "node_modules" / "x").mkdir(parents=True)
    (frontend / "node_modules" / "x" / "index.js").write_text("n", encoding="utf-8")

    names = {path.relative_to(frontend).as_posix() for path in one_click.frontend_source_files()}

    assert {"index.html", "package.json", "package-lock.json", ".npmrc", "vite.config.js", "src/components/A.jsx"} <= names
    assert not any(name.startswith("node_modules") for name in names)


def test_state_round_trip_is_atomic_and_rejects_foreign_schema(tmp_path):
    path = tmp_path / "runtime" / "state.json"
    one_click.save_state(path, {"python_deps": "abc"})

    assert one_click.load_state(path)["python_deps"] == "abc"
    assert not (tmp_path / "runtime" / "state.json.tmp").exists()
    path.write_text(json.dumps({"schema": "other", "python_deps": "abc"}), encoding="utf-8")
    assert one_click.load_state(path) == {}
    path.write_text("{not json", encoding="utf-8")
    assert one_click.load_state(path) == {}


def test_node_env_isolates_user_npm_settings(tmp_path):
    base = {
        "PATH": "/usr/bin",
        "NODE_OPTIONS": "--max-old-space-size=1",
        "npm_config_registry": "https://dead.example/",
        "NPM_CONFIG_PREFIX": "/root-owned",
        "HOME": "/home/user",
    }
    env = one_click.node_env(base, tmp_path / "node", tmp_path / "runtime")

    node_exe, _ = one_click.node_layout(tmp_path / "node")
    assert env["PATH"].split(one_click.os.pathsep) == [str(node_exe.parent), "/usr/bin"]
    assert "NODE_OPTIONS" not in env
    assert "npm_config_registry" not in env and "NPM_CONFIG_PREFIX" not in env
    assert env["npm_config_cache"] == str(tmp_path / "runtime" / "cache" / "npm")
    assert Path(env["npm_config_userconfig"]).read_text(encoding="utf-8") == ""
    assert env["HOME"] == "/home/user"


def test_node_layout_matches_official_archives(tmp_path, monkeypatch):
    assert one_click.node_layout(tmp_path) == (
        tmp_path / "bin" / "node",
        tmp_path / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
    )
    monkeypatch.setattr(one_click.os, "name", "nt")
    assert one_click.node_layout(tmp_path) == (
        tmp_path / "node.exe",
        tmp_path / "node_modules" / "npm" / "bin" / "npm-cli.js",
    )


def test_run_with_retries_backs_off_then_raises(monkeypatch):
    codes = iter([1, 1, 1])
    waits = []
    monkeypatch.setattr(one_click, "run_logged", lambda cmd, **kwargs: next(codes))
    log = one_click.SetupLog(None)

    with pytest.raises(one_click.SetupError) as exc:
        one_click.run_with_retries("npm_ci", ["npm", "ci"], log=log, attempts=3, sleep=waits.append)

    assert exc.value.code == "ERROR_NPM_CI_FAILED"
    assert waits == [2.0, 4.0]


def test_run_with_retries_stops_after_first_success(monkeypatch):
    codes = iter([1, 0])
    waits = []
    monkeypatch.setattr(one_click, "run_logged", lambda cmd, **kwargs: next(codes))

    one_click.run_with_retries("py_deps", ["uv"], log=one_click.SetupLog(None), attempts=3, sleep=waits.append)

    assert waits == [2.0]


def test_run_logged_streams_output_to_console_and_log(tmp_path, capsys):
    log = one_click.SetupLog(tmp_path / "setup.log")
    code = one_click.run_logged([sys.executable, "-c", "print('hello from child')"], log=log, cwd=tmp_path)
    log.close()

    assert code == 0
    assert "hello from child" in capsys.readouterr().out
    assert "hello from child" in (tmp_path / "setup.log").read_text(encoding="utf-8")


def test_release_lock_removes_only_our_lock(tmp_path, monkeypatch):
    lock = tmp_path / "setup.lock"
    lock.mkdir()
    (lock / "pid").write_text("4242\n", encoding="utf-8")
    monkeypatch.setenv("MUCARO_SETUP_LOCK", str(lock))

    monkeypatch.setenv("MUCARO_SETUP_LOCK_TOKEN", "999")
    one_click.release_lock()
    assert lock.exists()

    monkeypatch.setenv("MUCARO_SETUP_LOCK_TOKEN", "4242")
    one_click.release_lock()
    assert not lock.exists()


def test_prune_logs_keeps_newest(tmp_path):
    for index in range(13):
        (tmp_path / f"setup-20260925-1200{index:02d}.log").write_text("x", encoding="utf-8")

    one_click.prune_logs(tmp_path, keep=10)

    remaining = sorted(path.name for path in tmp_path.glob("setup-*.log"))
    assert len(remaining) == 10
    assert remaining[0] == "setup-20260925-120003.log"


# --- stage 1 step orchestration -----------------------------------------------------------------


class FakeTools:
    """Stands in for uv, npm, vite, preflight, and ingestion; records which steps actually ran."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[str] = []

    def run_logged(self, cmd, *, log, cwd=None, env=None):
        del log, cwd, env
        joined = " ".join(str(part) for part in cmd)
        frontend = self.root / "frontend"
        if " pip install " in joined:
            self.calls.append("uv_pip")
        elif " ci " in joined:
            self.calls.append("npm_ci")
            (frontend / "node_modules" / "vite" / "bin").mkdir(parents=True, exist_ok=True)
            (frontend / "node_modules" / "vite" / "bin" / "vite.js").write_text("", encoding="utf-8")
            (frontend / "node_modules" / ".package-lock.json").write_text("{}", encoding="utf-8")
        elif joined.endswith("vite.js build"):
            self.calls.append("vite_build")
            (frontend / "dist").mkdir(exist_ok=True)
            (frontend / "dist" / "index.html").write_text("<div></div>", encoding="utf-8")
        elif "preflight.py" in joined:
            self.calls.append("preflight")
            (self.root / ".env").write_text("", encoding="utf-8")
        elif "ingest_folder.py" in joined:
            self.calls.append("ingest")
        else:
            raise AssertionError(f"unexpected command {joined}")
        return 0


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    frontend = root / "frontend"
    (frontend / "src").mkdir(parents=True)
    for name, text in {
        "index.html": "<html></html>",
        "package.json": "{}",
        "package-lock.json": "{}",
        ".npmrc": "registry=https://registry.npmjs.org/\n",
        "src/App.jsx": "export default 1;",
    }.items():
        (frontend / name).write_text(text, encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    runtime = root / ".mucaro-runtime"
    tools = FakeTools(root)
    monkeypatch.setattr(one_click, "ROOT", root)
    monkeypatch.setattr(one_click, "run_logged", tools.run_logged)
    monkeypatch.setattr(one_click, "missing_modules", lambda names=one_click.KEY_MODULES: [])
    monkeypatch.setattr(one_click.OneClickSetup, "check_stage0", lambda self: None)
    return root, runtime, tools


def _run_setup(runtime: Path) -> list[str]:
    return one_click.OneClickSetup(one_click.SetupLog(None), runtime, one_click.read_pins(REPO / "scripts" / "one_click_pins.env")).run()


def test_setup_runs_every_step_once_then_takes_the_fast_path(project):
    root, runtime, tools = project

    assert _run_setup(runtime) == ["python_deps", "frontend_deps", "frontend_build", "preflight", "samples"]
    assert tools.calls == ["uv_pip", "npm_ci", "vite_build", "preflight", "ingest"]

    tools.calls.clear()
    assert _run_setup(runtime) == []
    assert tools.calls == []


def test_setup_rebuilds_only_the_ui_after_a_source_change(project):
    root, runtime, tools = project
    _run_setup(runtime)
    tools.calls.clear()

    (root / "frontend" / "src" / "App.jsx").write_text("export default 2;", encoding="utf-8")

    assert _run_setup(runtime) == ["frontend_build", "preflight"]
    assert tools.calls == ["vite_build", "preflight"]


def test_setup_reinstalls_frontend_deps_when_node_modules_disappear(project):
    root, runtime, tools = project
    _run_setup(runtime)
    tools.calls.clear()

    shutil.rmtree(root / "frontend" / "node_modules")

    assert _run_setup(runtime) == ["frontend_deps", "preflight"]


def test_setup_reinstalls_python_deps_when_pyproject_or_imports_change(project, monkeypatch):
    root, runtime, tools = project
    _run_setup(runtime)
    tools.calls.clear()

    (root / "pyproject.toml").write_text("[project]\nname='y'\n", encoding="utf-8")
    assert _run_setup(runtime) == ["python_deps", "preflight"]

    tools.calls.clear()
    missing = iter([["rasterio"], []])
    monkeypatch.setattr(one_click, "missing_modules", lambda names=one_click.KEY_MODULES: next(missing))
    assert _run_setup(runtime) == ["python_deps", "preflight"]


def test_setup_never_seeds_samples_into_an_existing_library(project):
    root, runtime, tools = project
    manifest = root / "data" / "processed" / "source_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[]", encoding="utf-8")

    ran = _run_setup(runtime)

    assert "samples" not in ran
    assert "ingest" not in tools.calls
    assert one_click.load_state(runtime / "state.json")["samples_seeded"] is True


def test_setup_fails_closed_when_imports_still_missing_after_install(project, monkeypatch):
    root, runtime, tools = project
    monkeypatch.setattr(one_click, "missing_modules", lambda names=one_click.KEY_MODULES: ["rasterio"])

    with pytest.raises(one_click.SetupError) as exc:
        _run_setup(runtime)

    assert exc.value.code == "ERROR_PY_IMPORT_MISSING"
    assert "python_deps" not in one_click.load_state(runtime / "state.json")


def test_check_stage0_requires_venv_and_private_runtimes(tmp_path, monkeypatch):
    setup = one_click.OneClickSetup(one_click.SetupLog(None), tmp_path, one_click.read_pins())
    monkeypatch.setattr(one_click.sys, "base_prefix", "/somewhere/else")
    monkeypatch.setattr(one_click.sys, "prefix", "/venv")

    with pytest.raises(one_click.SetupError) as exc:
        setup.check_stage0()

    assert exc.value.code == "ERROR_STAGE0_MISSING"

    monkeypatch.setattr(one_click.sys, "prefix", "/same")
    monkeypatch.setattr(one_click.sys, "base_prefix", "/same")
    with pytest.raises(one_click.SetupError) as exc:
        setup.check_stage0()
    assert exc.value.code == "ERROR_NOT_IN_VENV"


def test_main_reports_failure_and_releases_lock(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "setup.lock"
    lock.mkdir()
    (lock / "pid").write_text("77", encoding="utf-8")
    monkeypatch.setenv("MUCARO_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("MUCARO_SETUP_LOCK", str(lock))
    monkeypatch.setenv("MUCARO_SETUP_LOCK_TOKEN", "77")
    monkeypatch.setenv("MUCARO_SETUP_LOG", str(tmp_path / "logs" / "setup-1.log"))

    def fail(self):
        raise one_click.SetupError("ERROR_NPM_CI_FAILED", "returncode=1")

    monkeypatch.setattr(one_click.OneClickSetup, "run", fail)
    launched = []
    monkeypatch.setattr(one_click.launch_app, "main", lambda argv=None: launched.append(argv) or 0)

    assert one_click.main([]) == 2
    out = capsys.readouterr().out
    assert "ERROR_NPM_CI_FAILED returncode=1" in out
    assert "double-click again" in out
    assert not lock.exists()
    assert launched == []
    assert "ERROR_NPM_CI_FAILED" in (tmp_path / "logs" / "setup-1.log").read_text(encoding="utf-8")


def test_main_launches_app_with_auto_port_and_passthrough_args(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MUCARO_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("MUCARO_SETUP_LOG", raising=False)
    monkeypatch.delenv("MUCARO_SETUP_LOCK", raising=False)
    monkeypatch.setattr(one_click.OneClickSetup, "run", lambda self: [])
    launched = []
    monkeypatch.setattr(one_click.launch_app, "main", lambda argv=None: launched.append(argv) or 0)

    assert one_click.main(["--smoke-test", "--no-browser"]) == 0

    assert launched == [one_click.LAUNCH_ARGS + ["--smoke-test", "--no-browser"]]
    assert "--auto-port" in launched[0] and "--exit-if-running" in launched[0]
    assert "ONE_CLICK_FAST_PATH" in capsys.readouterr().out
    assert list((tmp_path / "logs").glob("setup-*.log"))


def test_main_setup_only_does_not_launch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MUCARO_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("MUCARO_SETUP_LOG", raising=False)
    monkeypatch.setattr(one_click.OneClickSetup, "run", lambda self: ["python_deps"])
    monkeypatch.setattr(one_click.launch_app, "main", lambda argv=None: pytest.fail("must not launch"))

    assert one_click.main(["--setup-only"]) == 0
    out = capsys.readouterr().out
    assert "ONE_CLICK_SETUP_OK steps=python_deps" in out
    assert "ONE_CLICK_SETUP_ONLY_OK" in out


# --- preflight and launcher changes -------------------------------------------------------------


def test_preflight_skip_tool_probe_never_runs_path_tools(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["preflight.py", "--skip-tool-probe"])

    def no_path_tools(cmd, args=None):
        raise AssertionError(f"ran {cmd} from PATH")

    monkeypatch.setattr(preflight, "command_version", no_path_tools)
    preflight.main()

    assert "TOOL_PROBE_SKIPPED" in capsys.readouterr().out


class FakeProc:
    returncode = None

    def poll(self):
        return None


def _fake_backend(monkeypatch, *, busy_ports: set[int], healthy_ports: set[int] = frozenset()):
    started = []
    stopped = []
    monkeypatch.setattr(launch_app, "ensure_frontend_dist", lambda skip_build=False, verified=False: None)
    monkeypatch.setattr(launch_app, "port_open", lambda port: port in busy_ports or port in started)
    monkeypatch.setattr(launch_app, "health_ok", lambda port: port in healthy_ports or port in started)
    monkeypatch.setattr(launch_app, "start_backend", lambda port: started.append(port) or FakeProc())
    monkeypatch.setattr(launch_app, "wait_for_health", lambda port, timeout: True)
    monkeypatch.setattr(launch_app, "stop_backend", lambda proc, port: stopped.append(port) or True)
    return started, stopped


def test_launch_auto_port_moves_past_a_foreign_process(monkeypatch, capsys):
    started, stopped = _fake_backend(monkeypatch, busy_ports={54321, 54322})

    code = launch_app.main(["--port", "54321", "--auto-port", "--smoke-test", "--no-browser", "--hold-seconds", "0"])

    assert code == 0
    assert started == [54323]
    assert stopped == [54323]
    out = capsys.readouterr().out
    assert "PORT_FALLBACK from=54321 to=54323" in out
    assert "DESKTOP_SMOKE_OK" in out


def test_launch_auto_port_reuses_a_healthy_mucaro_on_a_later_port(monkeypatch, capsys):
    started, _ = _fake_backend(monkeypatch, busy_ports={54321, 54322}, healthy_ports={54322})
    opened = []
    monkeypatch.setattr(launch_app.webbrowser, "open", opened.append)

    code = launch_app.main(["--port", "54321", "--auto-port", "--exit-if-running"])

    assert code == 0
    assert started == []
    assert opened == ["http://127.0.0.1:54322"]
    assert "DESKTOP_LAUNCH_REUSED http://127.0.0.1:54322" in capsys.readouterr().out


def test_launch_without_auto_port_keeps_failing_on_conflict(monkeypatch, capsys):
    _fake_backend(monkeypatch, busy_ports={54321})

    assert launch_app.main(["--port", "54321", "--no-browser"]) == 5
    assert "ERROR_PORT_IN_USE_NON_MUCARO_AI port=54321" in capsys.readouterr().err


def test_launch_stops_backend_when_interrupted_during_startup(monkeypatch):
    started, stopped = _fake_backend(monkeypatch, busy_ports=set())

    def interrupted(port, timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(launch_app, "wait_for_health", interrupted)

    with pytest.raises(KeyboardInterrupt):
        launch_app.main(["--port", "54321", "--no-browser"])

    assert started == [54321]
    assert stopped == [54321]


def test_launch_installs_and_restores_stop_signal_handlers():
    before = signal.getsignal(signal.SIGTERM)
    previous = launch_app.install_stop_signals()
    try:
        handler = signal.getsignal(signal.SIGTERM)
        assert handler is launch_app._raise_keyboard_interrupt
        if hasattr(signal, "SIGHUP"):
            assert signal.getsignal(signal.SIGHUP) is launch_app._raise_keyboard_interrupt
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        launch_app.restore_signals(previous)
    assert signal.getsignal(signal.SIGTERM) == before
