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
