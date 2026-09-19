from __future__ import annotations

from pathlib import Path

import pytest

import backend.app.web_traversal as traversal
from backend.app.config import Settings
from backend.app.web_browsing import WebBrowsingError, WebFetchPolicy
from backend.app.web_traversal import WebTraversalError, traverse_web_bounded


def make_settings(root: Path) -> Settings:
    cfg = Settings(
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
    cfg.ensure_dirs()
    return cfg


def install_graph(monkeypatch: pytest.MonkeyPatch, graph: dict[str, list[str]], *, fail: set[str] | None = None, truncated: set[str] | None = None) -> None:
    fail = fail or set()
    truncated = truncated or set()

    def fake_validate(url: str, *, allow_private_hosts: bool = False):
        assert allow_private_hosts is False
        host = url.split("/", 3)[2]
        return {"fetch_url": url, "hostname": host, "requested_url": url, "fragment_removed": False}

    def fake_fetch(url: str, *, cfg, policy):
        if url in fail:
            raise WebBrowsingError("ERROR_WEB_FETCH_FAILED", "fixture failure")
        token = url.rsplit("/", 1)[-1] or "root"
        return {
            "status": "WEB_FETCH_SNAPSHOT_OK",
            "snapshot_path": str(cfg.export_dir / f"{token}.json"),
            "snapshot_sha256": token.ljust(64, "0")[:64],
            "snapshot": {
                "snapshot_id": f"snap-{token}",
                "final_url": url,
                "http_status": 200,
                "content_type": "text/html; charset=utf-8",
                "content_sha256": token.ljust(64, "1")[:64],
            },
        }

    def fake_discover(path: Path, *, require_same_host: bool, max_selected: int):
        token = path.stem
        url = "https://example.test/" + ("" if token == "root" else token)
        selected = graph.get(url, [])[:max_selected]
        return {
            "report_id": f"disc-{token}",
            "status": "WEB_DISCOVERY_WARN" if url in truncated else "WEB_DISCOVERY_OK",
            "scope_state": "BOUNDED_TRUNCATED" if url in truncated else "FROZEN_HTML_ANCHOR_EXHAUSTED",
            "selected_urls": selected,
            "state_counts": {"selected_count": len(selected)},
        }

    monkeypatch.setattr(traversal, "validate_web_fetch_url", fake_validate)
    monkeypatch.setattr(traversal, "fetch_web_snapshot", fake_fetch)
    monkeypatch.setattr(traversal, "discover_snapshot_links", fake_discover)


def test_bounded_traversal_drains_queue_and_accounts_duplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {
        "https://example.test/": ["https://example.test/b", "https://example.test/c"],
        "https://example.test/b": ["https://example.test/c"],
        "https://example.test/c": [],
    }
    install_graph(monkeypatch, graph)
    result = traverse_web_bounded(
        "https://example.test/",
        cfg=make_settings(tmp_path),
        max_pages=10,
        max_depth=3,
        max_links_per_page=10,
    )
    receipt = result["receipt"]

    assert result["status"] == "WEB_TRAVERSAL_OK"
    assert receipt["scope_state"] == "BOUNDED_EXHAUSTED"
    assert receipt["counts"]["attempted_pages"] == 3
    assert receipt["counts"]["fetched_pages"] == 3
    assert receipt["counts"]["duplicate_candidates"] == 1
    assert receipt["counts"]["page_limit_deferred"] == 0
    assert receipt["counts"]["unresolved"] == 0
    assert all(receipt["invariants"].values())
    assert len(result["receipt_sha256"]) == 64


def test_page_ceiling_yields_truncated_not_exhausted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {
        "https://example.test/": ["https://example.test/b", "https://example.test/c"],
        "https://example.test/b": [],
        "https://example.test/c": [],
    }
    install_graph(monkeypatch, graph)
    receipt = traverse_web_bounded(
        "https://example.test/",
        cfg=make_settings(tmp_path),
        max_pages=2,
        max_depth=3,
        max_links_per_page=10,
    )["receipt"]

    assert receipt["status"] == "WEB_TRAVERSAL_WARN"
    assert receipt["scope_state"] == "BOUNDED_TRUNCATED"
    assert receipt["counts"]["attempted_pages"] == 2
    assert receipt["counts"]["page_limit_deferred"] == 1
    assert any(row["reason"] == "MAX_PAGES" for row in receipt["exclusions"])
    assert receipt["invariants"]["exhaustion_not_claimed_when_truncated"] is True


def test_discovery_truncation_propagates_to_traversal_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {"https://example.test/": []}
    install_graph(monkeypatch, graph, truncated={"https://example.test/"})
    receipt = traverse_web_bounded(
        "https://example.test/",
        cfg=make_settings(tmp_path),
        max_pages=5,
        max_depth=1,
    )["receipt"]

    assert receipt["scope_state"] == "BOUNDED_TRUNCATED"
    assert receipt["status"] == "WEB_TRAVERSAL_WARN"
    assert receipt["counts"]["discovery_truncated_pages"] == 1


def test_fetch_failure_is_explicit_unresolved_residue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {"https://example.test/": ["https://example.test/b"]}
    install_graph(monkeypatch, graph, fail={"https://example.test/b"})
    receipt = traverse_web_bounded(
        "https://example.test/",
        cfg=make_settings(tmp_path),
        max_pages=5,
        max_depth=2,
    )["receipt"]

    assert receipt["scope_state"] == "BOUNDED_EXHAUSTED"
    assert receipt["status"] == "WEB_TRAVERSAL_WARN"
    assert receipt["counts"]["failed_pages"] == 1
    assert receipt["counts"]["unresolved"] == 1
    failed = next(row for row in receipt["pages"] if row["fetch_state"] == "FETCH_FAILED")
    assert failed["error_status"] == "ERROR_WEB_FETCH_FAILED"


def test_private_host_autonomy_and_invalid_limits_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_graph(monkeypatch, {"https://example.test/": []})
    with pytest.raises(WebTraversalError) as private:
        traverse_web_bounded(
            "https://example.test/",
            cfg=make_settings(tmp_path),
            fetch_policy=WebFetchPolicy(allow_private_hosts=True),
        )
    assert private.value.status == "ERROR_WEB_TRAVERSAL_PRIVATE_HOST_POLICY"

    with pytest.raises(WebTraversalError) as limit:
        traverse_web_bounded("https://example.test/", cfg=make_settings(tmp_path), max_pages=0)
    assert limit.value.status == "ERROR_WEB_TRAVERSAL_LIMIT"
