"""Ratchet: every process/network primitive under backend/app must go through permission_engine,
or be explicitly listed here with a reason. The migration list may only shrink."""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.ocr_analysis import OCRAnalysisError, analyze_image_ocr
from backend.app.web_search import WebSearchError, search_web_brave

APP_DIR = Path(__file__).resolve().parents[1] / "app"
PRIMITIVE = re.compile(r"subprocess\.(run|Popen|check_output|check_call)\b|\burlopen\b|urllib\.request\.(Request|build_opener)|\brequests\.(get|post)\b|\bhttpx\.")
FACADE_IMPORT = re.compile(r"from \.permission_engine import|import permission_engine")

# Runtime paths an agent or user request can reach. Must be migrated through permission_engine.
MIGRATE = {
    "cli.py": "operator command runner and git readouts (origin=operator)",
    "coding_container.py": "container execution with hard-coded grants",
    "coding_lifecycle.py": "git subprocess (git.read/git.mutate)",
    "coding_mutation.py": "subprocess (filesystem.write)",
    "coding_patch.py": "git apply subprocess (filesystem.write)",
    "coding_workspace.py": "git worktree subprocess",
    "container_runtime.py": "container subprocess; distribution check only",
    "local_worker.py": "worker subprocess; distribution check only",
    "ocr_pdf.py": "pdf rasterization subprocess (subprocess.execute)",
    "web_browsing.py": "governed HTTP fetch (network.public_http)",
}
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
    for path in sorted(APP_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if PRIMITIVE.search(text):
            found[path.name] = bool(FACADE_IMPORT.search(text))
    return found


def test_every_primitive_is_gated_or_explicitly_classified() -> None:
    unclassified = [
        name for name, gated in _files_with_primitives().items() if not gated and name not in MIGRATE and name not in EXEMPT
    ]
    assert unclassified == [], f"ungated process/network primitive; route through permission_engine: {unclassified}"


def test_classification_lists_have_no_stale_entries() -> None:
    files = _files_with_primitives()
    for name in {**MIGRATE, **EXEMPT}:
        assert name in files, f"{name} no longer has a primitive; remove it from the list"
        assert not files[name], f"{name} now imports permission_engine; remove it from the list"


def test_migrated_call_sites_are_gated() -> None:
    files = _files_with_primitives()
    for name in ("web_search.py", "ocr_analysis.py"):
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
