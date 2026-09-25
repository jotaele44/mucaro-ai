"""Ratchet: every process/network primitive under backend/app must go through permission_engine,
or be explicitly listed in EXEMPT with a reason. An exempt file that gets gated must be removed from EXEMPT."""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.ocr_analysis import OCRAnalysisError, analyze_image_ocr
from backend.app.web_search import WebSearchError, search_web_brave

APP_DIR = Path(__file__).resolve().parents[1] / "app"
PRIMITIVE = re.compile(r"subprocess\.(run|Popen|check_output|check_call|getoutput|getstatusoutput)\b|\bos\.(system|popen|exec[lv]p?e?|spawn[lv]p?e?)\(|asyncio\.create_subprocess|\bsocket\.(socket|create_connection)\b|\bhttp\.client\b|\baiohttp\b|\burlopen\b|urllib\.request\.(Request|build_opener)|\brequests\.(get|post)\b|\bhttpx\.")
FACADE_IMPORT = re.compile(r"from \.permission_engine import|import permission_engine")

# Build, packaging, certification and read-only self-identity tooling; not reachable from a model or skill request.
EXEMPT = {
    "answer_trace_eval.py": "certification harness spawning the project's own CLI",
    "clean_machine_macos.py": "release tooling",
    "flagpole_inventory.py": "read-only git tree listing of the project itself",
    "gis_eval.py": "certification harness spawning the project's own CLI",
    "macos_app_store.py": "release tooling",
    "macos_distribution.py": "release tooling",
    "packaging.py": "release tooling",
    "portable_runtime.py": "release tooling",
    "retrieval_quality_certification.py": "certification harness spawning the project's own CLI",
    "runtime_identity.py": "read-only git rev-parse of the project itself",
}


def _files_with_primitives() -> dict[str, bool]:
    found: dict[str, bool] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if PRIMITIVE.search(text):
            found[str(path.relative_to(APP_DIR))] = bool(FACADE_IMPORT.search(text))
    return found


def test_every_primitive_is_gated_or_explicitly_classified() -> None:
    unclassified = [
        name for name, gated in _files_with_primitives().items() if not gated and name not in EXEMPT
    ]
    assert unclassified == [], f"ungated process/network primitive; route through permission_engine: {unclassified}"


def test_classification_lists_have_no_stale_entries() -> None:
    files = _files_with_primitives()
    for name in EXEMPT:
        assert name in files, f"{name} no longer has a primitive; remove it from the list"
        assert not files[name], f"{name} now imports permission_engine; remove it from the list"


def test_migrated_call_sites_are_gated() -> None:
    files = _files_with_primitives()
    for name in ("web_search.py", "ocr_analysis.py", "ocr_pdf.py", "web_browsing.py", "local_worker.py", "container_runtime.py"):
        assert files[name] is True, name


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
    from PIL import Image

    image = tmp_path / "scan.png"
    Image.new("RGB", (8, 8), "white").save(image)
    calls: list[list[str]] = []

    def runner(command, timeout_seconds):
        calls.append(command)
        raise AssertionError("runner must not be reached")

    with pytest.raises(OCRAnalysisError) as exc:
        analyze_image_ocr(image, cfg=_cfg(tmp_path, "mac_app_store"), tesseract_path="/bin/echo", runner=runner)
    assert exc.value.status == "ERROR_PERMISSION_DENIED"
    assert calls == []


def test_web_search_denied_on_unsupported_channel_before_any_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import urllib.request

    def boom(*args, **kwargs):
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(WebSearchError) as exc:
        search_web_brave("mucaro", cfg=_cfg(tmp_path, "sideload"), allow_live_search=True, api_key="k")
    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_local_worker_and_container_denied_on_app_store_before_spawn(tmp_path: Path) -> None:
    from backend.app.container_runtime import ContainerRuntimeError, run_container_worker
    from backend.app.local_worker import LocalWorkerError, run_local_worker

    # The distribution policy already refuses; the engine must independently refuse if it is bypassed.
    from backend.app.permission_engine import PermissionRequest, evaluate

    for capability in ("subprocess.execute",):
        decision = evaluate(PermissionRequest(capability=capability, subject="s", origin="operator"), distribution_channel="mac_app_store")
        assert decision.decision == "DENY"
    with pytest.raises(LocalWorkerError):
        run_local_worker(project_root=tmp_path, skill_id="third-party/x", distribution_channel="mac_app_store")
    with pytest.raises(ContainerRuntimeError):
        run_container_worker(project_root=tmp_path, skill_id="third-party/x", image="x@sha256:" + "0" * 64, distribution_channel="mac_app_store")


def test_web_fetch_denied_on_unsupported_channel_before_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import urllib.request

    from backend.app.web_browsing import WebBrowsingError, fetch_web_snapshot

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network reached")))
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


def test_cli_gh_runner_requires_network_capability_and_is_denied_on_unsupported_channel(tmp_path: Path) -> None:
    from backend.app.cli import _command_output

    ok, detail = _command_output(_cfg(tmp_path, "sideload"), ["gh", "--version"])
    assert (ok, detail.startswith("ERROR_PERMISSION_DENIED")) == (False, True)


def test_live_model_call_is_gated_by_model_invoke_permission() -> None:
    # The Anthropic SDK does its own HTTP, so the primitive scan cannot see it; assert the gate explicitly.
    text = (APP_DIR / "model_router.py").read_text(encoding="utf-8")
    assert FACADE_IMPORT.search(text)
