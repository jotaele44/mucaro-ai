from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app import local_tools


def _make_tool(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    path = root / f"{name}{suffix}"
    path.write_text("fixture", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)
    return path


def test_bundled_tool_wins_over_host_path(monkeypatch, tmp_path: Path) -> None:
    bundled = _make_tool(tmp_path, "git")
    monkeypatch.setenv(local_tools.BUNDLED_TOOLS_ENV, str(tmp_path))
    monkeypatch.setattr(local_tools.shutil, "which", lambda name: "/host/git")

    assert local_tools.resolve_local_tool("git") == str(bundled.resolve())
    status = local_tools.local_tool_status("git")
    assert status["available"] is True
    assert status["bundled"] is True


def test_bundled_only_mode_never_falls_back_to_host(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(local_tools.BUNDLED_TOOLS_ENV, str(tmp_path))
    monkeypatch.setenv(local_tools.BUNDLED_ONLY_ENV, "1")
    monkeypatch.setattr(local_tools.shutil, "which", lambda name: "/host/" + name)

    assert local_tools.resolve_local_tool("git") is None
    assert local_tools.local_tool_status("git")["host_path_fallback_allowed"] is False


def test_source_mode_can_use_host_tool_for_development(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(local_tools.BUNDLED_TOOLS_ENV, str(tmp_path))
    monkeypatch.delenv(local_tools.BUNDLED_ONLY_ENV, raising=False)
    monkeypatch.setattr(local_tools, "frozen_runtime", lambda: False)
    monkeypatch.setattr(local_tools.shutil, "which", lambda name: "/host/" + name)

    assert local_tools.resolve_local_tool("git") == "/host/git"


def test_frozen_runtime_is_implicitly_bundled_only(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(local_tools.BUNDLED_TOOLS_ENV, str(tmp_path))
    monkeypatch.delenv(local_tools.BUNDLED_ONLY_ENV, raising=False)
    monkeypatch.setattr(local_tools, "frozen_runtime", lambda: True)
    monkeypatch.setattr(local_tools.shutil, "which", lambda name: "/host/" + name)

    assert local_tools.resolve_local_tool("tesseract") is None


def test_frozen_runtime_rejects_explicit_host_executable(monkeypatch, tmp_path: Path) -> None:
    bundled_root = tmp_path / "bundle"
    host_root = tmp_path / "host"
    host_tool = _make_tool(host_root, "tesseract")
    monkeypatch.setenv(local_tools.BUNDLED_TOOLS_ENV, str(bundled_root))
    monkeypatch.setattr(local_tools, "frozen_runtime", lambda: True)

    assert local_tools.resolve_tool_request("tesseract", str(host_tool)) is None


def test_frozen_runtime_accepts_explicit_bundled_executable(monkeypatch, tmp_path: Path) -> None:
    bundled_root = tmp_path / "bundle"
    bundled_tool = _make_tool(bundled_root, "tesseract")
    monkeypatch.setenv(local_tools.BUNDLED_TOOLS_ENV, str(bundled_root))
    monkeypatch.setattr(local_tools, "frozen_runtime", lambda: True)

    assert local_tools.resolve_tool_request("tesseract", str(bundled_tool)) == str(bundled_tool.resolve())


def test_unknown_tool_fails_closed() -> None:
    with pytest.raises(ValueError):
        local_tools.resolve_local_tool("curl")


def _source_mode_with_host_git(monkeypatch, tmp_path: Path, host_git: str) -> None:
    monkeypatch.setenv(local_tools.BUNDLED_TOOLS_ENV, str(tmp_path / "empty-bundle"))
    monkeypatch.delenv(local_tools.BUNDLED_ONLY_ENV, raising=False)
    monkeypatch.delenv("DEVELOPER_DIR", raising=False)
    monkeypatch.setattr(local_tools, "frozen_runtime", lambda: False)
    monkeypatch.setattr(local_tools.sys, "platform", "darwin")
    monkeypatch.setattr(local_tools.shutil, "which", lambda name: host_git)
    monkeypatch.setattr(local_tools.os.path, "realpath", lambda path, *args, **kwargs: path)


def test_macos_git_stub_without_developer_tools_is_never_offered(monkeypatch, tmp_path: Path) -> None:
    # Running /usr/bin/git without the Command Line Tools opens Apple's install dialog.
    _source_mode_with_host_git(monkeypatch, tmp_path, "/usr/bin/git")
    monkeypatch.setattr(local_tools, "MACOS_COMMAND_LINE_TOOLS", tmp_path / "no-clt")
    ran: list[list[str]] = []

    def xcode_select(cmd, **kwargs):
        ran.append(cmd)
        return local_tools.subprocess.CompletedProcess(cmd, 2, stdout="", stderr="unable to get active developer directory")

    monkeypatch.setattr(local_tools.subprocess, "run", xcode_select)

    assert local_tools.resolve_local_tool("git") is None
    assert ran == [["/usr/bin/xcode-select", "-p"]]  # the stub itself is never run
    assert local_tools.local_tool_status("git")["available"] is False


def test_macos_git_stub_counts_when_command_line_tools_are_installed(monkeypatch, tmp_path: Path) -> None:
    _source_mode_with_host_git(monkeypatch, tmp_path, "/usr/bin/git")
    clt = tmp_path / "CommandLineTools"
    _make_tool(clt / "usr" / "bin", "git")
    monkeypatch.setattr(local_tools, "MACOS_COMMAND_LINE_TOOLS", clt)
    monkeypatch.setattr(local_tools.subprocess, "run", lambda *a, **k: pytest.fail("no probe needed"))

    assert local_tools.resolve_local_tool("git") == "/usr/bin/git"


def test_macos_git_stub_counts_with_the_selected_xcode(monkeypatch, tmp_path: Path) -> None:
    _source_mode_with_host_git(monkeypatch, tmp_path, "/usr/bin/git")
    monkeypatch.setattr(local_tools, "MACOS_COMMAND_LINE_TOOLS", tmp_path / "no-clt")
    xcode = tmp_path / "Xcode.app" / "Contents" / "Developer"
    _make_tool(xcode / "usr" / "bin", "git")
    monkeypatch.setattr(
        local_tools.subprocess,
        "run",
        lambda cmd, **kwargs: local_tools.subprocess.CompletedProcess(cmd, 0, stdout=f"{xcode}\n", stderr=""),
    )

    assert local_tools.resolve_local_tool("git") == "/usr/bin/git"


def test_macos_real_git_elsewhere_on_path_is_used_as_is(monkeypatch, tmp_path: Path) -> None:
    _source_mode_with_host_git(monkeypatch, tmp_path, "/opt/homebrew/bin/git")
    monkeypatch.setattr(local_tools.subprocess, "run", lambda *a, **k: pytest.fail("not a stub"))

    assert local_tools.resolve_local_tool("git") == "/opt/homebrew/bin/git"
