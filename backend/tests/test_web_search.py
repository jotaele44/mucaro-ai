from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import backend.app.web_search as web_search
from backend.app.config import Settings
from backend.app.web_search import WebSearchError, search_web_brave


class FakeResponse:
    status = 200
    def __init__(self, payload: dict):
        self.raw = json.dumps(payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size: int = -1): return self.raw if size < 0 else self.raw[:size]


def cfg(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root/"data",
        input_dir=root/"data/input",
        processed_dir=root/"data/processed",
        export_dir=root/"data/exports",
        index_dir=root/"data/indexes",
        sqlite_path=root/"data/processed/test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def test_global_offline_mode_blocks_direct_search_before_provider_call(tmp_path: Path, monkeypatch) -> None:
    offline_cfg = replace(cfg(tmp_path), offline_mode=True)
    called = False

    def forbidden_urlopen(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider request must not run in offline mode")

    monkeypatch.setattr(web_search.urllib.request, "urlopen", forbidden_urlopen)
    with pytest.raises(WebSearchError) as exc:
        search_web_brave(
            "Puerto Rico hydrology",
            cfg=offline_cfg,
            api_key="fixture",
            allow_live_search=True,
        )
    assert exc.value.status == "ERROR_OFFLINE_NETWORK_DISABLED"
    assert called is False


def test_live_search_requires_explicit_permission(tmp_path: Path) -> None:
    with pytest.raises(WebSearchError) as exc:
        search_web_brave("Puerto Rico hydrology", cfg=cfg(tmp_path), api_key="fixture")
    assert exc.value.status == "ERROR_WEB_SEARCH_LIVE_NOT_ALLOWED"


def test_search_receipt_excludes_key_and_deduplicates_urls(tmp_path: Path, monkeypatch) -> None:
    payload = {
        "query": {"original": "Puerto Rico hydrology", "more_results_available": True},
        "web": {"results": [
            {"title": "One", "url": "https://example.com/a#section", "description": "A"},
            {"title": "Duplicate", "url": "https://example.com/a", "description": "A2"},
            {"title": "Two", "url": "https://example.org/b", "description": "B"},
        ]},
    }
    monkeypatch.setattr(web_search.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse(payload))
    result = search_web_brave(
        "Puerto Rico hydrology",
        cfg=cfg(tmp_path),
        api_key="SUPER_SECRET_FIXTURE_KEY",
        allow_live_search=True,
        count=10,
    )
    receipt = result["receipt"]
    assert receipt["status"] == "WEB_SEARCH_OK"
    assert receipt["result_count"] == 2
    assert receipt["more_results_available"] is True
    assert receipt["credential"]["value_persisted"] is False
    raw = Path(result["receipt_path"]).read_text(encoding="utf-8")
    assert "SUPER_SECRET_FIXTURE_KEY" not in raw
    assert all(receipt["invariants"].values())


def test_query_and_pagination_limits_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(WebSearchError) as exc:
        search_web_brave("x " * 76, cfg=cfg(tmp_path), api_key="fixture", allow_live_search=True)
    assert exc.value.status == "ERROR_WEB_SEARCH_QUERY_LIMIT"
    with pytest.raises(WebSearchError) as exc:
        search_web_brave("valid", cfg=cfg(tmp_path), api_key="fixture", allow_live_search=True, count=21)
    assert exc.value.status == "ERROR_WEB_SEARCH_COUNT"
    with pytest.raises(WebSearchError) as exc:
        search_web_brave("valid", cfg=cfg(tmp_path), api_key="fixture", allow_live_search=True, offset=10)
    assert exc.value.status == "ERROR_WEB_SEARCH_OFFSET"
