"""Ratchet: user/model-reachable process/network primitives must go through permission_engine.

EXEMPT is intentionally narrow and audited. Release/certification tooling and loopback desktop
plumbing are outside model/skill execution. Real model-provider transport is permission-gated
through the same facade as process and public-network capabilities.
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.ocr_analysis import OCRAnalysisError, analyze_image_ocr
from backend.app.web_search import WebSearchError, search_web_brave

APP_DIR = Path(__file__).resolve().parents[1] / "app"
PRIMITIVE = re.compile(
    r"subprocess\.(run|Popen|check_output|check_call|getoutput|getstatusoutput)\b|"
    r"\bos\.(system|popen|exec[lv]p?e?|spawn[lv]p?e?)\(|"
    r"asyncio\.create_subprocess|"
    r"\bsocket\.(socket|create_connection)\b|"
    r"\bhttp\.client\b|\baiohttp\b|\burlopen\b|"
    r"urllib\.request\.(Request|build_opener)|"
    r"\brequests\.(get|post)\b|\bhttpx\."
)
FACADE_IMPORT = re.compile(r"from \.permission_engine import|import permission_engine")

EXEMPT = {
    "answer_trace_eval.py": "certification harness spawning the project's own CLI",
    "clean_machine_macos.py": "release tooling",
    "desktop_shell.py": "native desktop shell uses loopback self-probes; not a model/skill public-network boundary",
    "flagpole_inventory.py": "read-only git tree listing of the project itself",
    "gis_eval.py": "certification harness spawning the project's own CLI",
    "local_tools.py": "read-only xcode-select -p probe while resolving local helper paths; runs no model/skill-chosen command",
    "macos_app_store.py": "release tooling",
    "macos_distribution.py": "release tooling",
    "packaging.py": "release tooling",
    "portable_runtime.py": "release tooling",
    "portable_runtime_source.py": "release/runtime acquisition tooling",
    "release_manifest.py": "release tooling",
    "release_scope.py": "release-state tooling",
    "retrieval_quality_certification.py": "certification harness spawning the project's own CLI",
    "security_gates.py": "release/security certification tooling",
}


def _files_with_primitives() -> dict[str, bool]:
    found: dict[str, bool] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if PRIMITIVE.search(text):
            found[str(path.relative_to(APP_DIR))] = bool(FACADE_IMPORT.search(text))
    return found


def test_every_runtime_primitive_is_gated_or_explicitly_classified() -> None:
    files = _files_with_primitives()
    unclassified = [name for name, gated in files.items() if not gated and name not in EXEMPT]
    assert unclassified == [], f"ungated process/network primitive; route through permission_engine: {unclassified}"


def test_exemption_list_has_no_stale_entries() -> None:
    files = _files_with_primitives()
    for name in EXEMPT:
        assert name in files, f"{name} no longer has a primitive; remove it from EXEMPT"
        assert not files[name], f"{name} now imports permission_engine; remove it from EXEMPT"


def test_user_reachable_migrated_call_sites_are_gated() -> None:
    files = _files_with_primitives()
    expected = {
        "cli.py",
        "coding_container.py",
        "coding_lifecycle.py",
        "coding_mutation.py",
        "coding_patch.py",
        "coding_workspace.py",
        "container_runtime.py",
        "local_worker.py",
        "model_router.py",
        "ocr_analysis.py",
        "ocr_pdf.py",
        "web_browsing.py",
        "web_search.py",
    }
    assert {name for name in expected if files.get(name) is not True} == set()


def _cfg(root: Path, channel: str = "local") -> Settings:
    base = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    return replace(base, distribution_channel=channel)


def test_ocr_denied_on_app_store_channel_before_any_subprocess(tmp_path: Path) -> None:
    import base64

    image = tmp_path / "scan.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
    )
    calls: list[list[str]] = []

    def runner(command, timeout_seconds):
        calls.append(command)
        raise AssertionError("runner must not be reached")

    with pytest.raises(OCRAnalysisError) as exc:
        analyze_image_ocr(
            image,
            cfg=_cfg(tmp_path, "mac_app_store"),
            tesseract_path="/bin/echo",
            runner=runner,
        )
    assert exc.value.status == "ERROR_PERMISSION_DENIED"
    assert calls == []


def test_web_search_denied_on_unsupported_channel_before_any_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import urllib.request

    def boom(*args, **kwargs):
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(WebSearchError) as exc:
        search_web_brave(
            "mucaro",
            cfg=_cfg(tmp_path, "sideload"),
            allow_live_search=True,
            api_key="k",
        )
    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_local_worker_and_container_denied_on_app_store_before_spawn(tmp_path: Path) -> None:
    from backend.app.container_runtime import ContainerRuntimeError, run_container_worker
    from backend.app.local_worker import LocalWorkerError, run_local_worker
    from backend.app.permission_engine import PermissionRequest, evaluate

    decision = evaluate(
        PermissionRequest(capability="subprocess.execute", subject="s", origin="operator"),
        distribution_channel="mac_app_store",
    )
    assert decision.decision == "DENY"

    with pytest.raises(LocalWorkerError):
        run_local_worker(
            project_root=tmp_path,
            skill_id="third-party/x",
            distribution_channel="mac_app_store",
        )
    with pytest.raises(ContainerRuntimeError):
        run_container_worker(
            project_root=tmp_path,
            skill_id="third-party/x",
            image="x@sha256:" + "0" * 64,
            distribution_channel="mac_app_store",
        )


def test_web_fetch_denied_on_unsupported_channel_before_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import socket
    import urllib.request

    from backend.app.web_browsing import WebBrowsingError, fetch_web_snapshot

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("DNS reached before permission denial")),
    )
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("HTTP opener reached before permission denial")),
    )
    with pytest.raises(WebBrowsingError) as exc:
        fetch_web_snapshot("https://example.com/", cfg=_cfg(tmp_path, "sideload"))
    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_pdf_ocr_denied_on_app_store_before_any_subprocess(tmp_path: Path) -> None:
    from backend.app.ocr_pdf import PDFOCRAnalysisError, analyze_pdf_ocr

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    def runner(command, timeout_seconds):
        raise AssertionError("runner must not be reached")

    with pytest.raises(PDFOCRAnalysisError) as exc:
        analyze_pdf_ocr(
            pdf,
            cfg=_cfg(tmp_path, "mac_app_store"),
            renderer_path="/bin/echo",
            tesseract_path="/bin/echo",
            renderer_runner=runner,
            ocr_runner=runner,
        )
    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_coding_operations_denied_on_app_store_before_any_git_subprocess(tmp_path: Path) -> None:
    from backend.app.coding_workspace import CodingWorkspaceError, resolve_repository

    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(CodingWorkspaceError) as exc:
        resolve_repository(repo, _cfg(tmp_path, "mac_app_store"))
    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_cli_command_runner_denied_on_app_store_before_spawn(tmp_path: Path) -> None:
    from backend.app.cli import _command_output

    ok, detail = _command_output(_cfg(tmp_path, "mac_app_store"), ["git", "--version"])
    assert ok is False
    assert detail.startswith("ERROR_PERMISSION_DENIED")


def test_cli_gh_runner_requires_network_capability_and_is_denied_on_unsupported_channel(
    tmp_path: Path,
) -> None:
    from backend.app.cli import _command_output

    ok, detail = _command_output(_cfg(tmp_path, "sideload"), ["gh", "--version"])
    assert (ok, detail.startswith("ERROR_PERMISSION_DENIED")) == (False, True)


def test_model_provider_transport_is_gated_by_model_invoke_permission() -> None:
    text = (APP_DIR / "model_router.py").read_text(encoding="utf-8")
    assert FACADE_IMPORT.search(text)
    assert 'capability="model.invoke"' in text
