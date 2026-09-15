from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from backend.app.utils import write_json
from backend.app.web_discovery import WebDiscoveryError, discover_snapshot_links


def write_snapshot(path: Path, html: str, *, final_url: str = "https://example.test/root/index.html") -> Path:
    content = html.encode("utf-8")
    write_json(
        path,
        {
            "snapshot_id": "websnap_test",
            "requested_url": final_url,
            "final_url": final_url,
            "retrieval_utc": "2026-09-15T00:00:00Z",
            "content_type": "text/html; charset=utf-8",
            "content_size_bytes": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
    )
    return path


def test_discovers_complete_static_anchor_set_with_explicit_residue(tmp_path: Path) -> None:
    snapshot = write_snapshot(
        tmp_path / "snapshot.json",
        """
        <html><body>
          <a href="/a#section">A</a>
          <a href="/a">A duplicate</a>
          <a href="b">B</a>
          <a href="https://other.test/c">C</a>
          <a href="mailto:test@example.test">mail</a>
          <a href="javascript:void(0)">js</a>
          <a href="">empty</a>
          <a>No href</a>
        </body></html>
        """,
    )

    report = discover_snapshot_links(snapshot, require_same_host=True, max_candidates=50, max_selected=10)

    assert report["status"] == "WEB_DISCOVERY_OK"
    assert report["scope_state"] == "FROZEN_HTML_ANCHOR_EXHAUSTED"
    assert report["selected_urls"] == ["https://example.test/a", "https://example.test/root/b"]
    counts = report["state_counts"]
    assert counts["anchors_observed"] == 8
    assert counts["hrefs_parsed"] == 7
    assert counts["unique_candidate_count"] == 2
    assert counts["duplicate_count"] == 1
    assert counts["excluded_count"] == 4
    assert counts["deferred_count"] == 0
    assert counts["selected_count"] == 2
    assert all(report["invariants"].values())
    reasons = {row["reason"] for row in report["candidates"] if row["reason"]}
    assert "CROSS_HOST" in reasons
    assert "MAILTO_SCHEME" in reasons
    assert "JAVASCRIPT_SCHEME" in reasons
    assert "EMPTY_HREF" in reasons


def test_selection_overflow_is_deferred_not_dropped(tmp_path: Path) -> None:
    snapshot = write_snapshot(
        tmp_path / "snapshot.json",
        '<a href="/a">a</a><a href="/b">b</a><a href="/c">c</a>',
    )
    report = discover_snapshot_links(snapshot, max_candidates=10, max_selected=2)

    assert report["status"] == "WEB_DISCOVERY_OK"
    assert report["selected_urls"] == ["https://example.test/a", "https://example.test/b"]
    assert report["state_counts"]["unique_candidate_count"] == 3
    assert report["state_counts"]["deferred_count"] == 1
    assert report["invariants"]["selection_accounting_closed"] is True
    deferred = [row for row in report["candidates"] if row["state"] == "DEFERRED"]
    assert [row["resolved_url"] for row in deferred] == ["https://example.test/c"]


def test_candidate_cap_prevents_exhaustion_claim(tmp_path: Path) -> None:
    snapshot = write_snapshot(
        tmp_path / "snapshot.json",
        '<a href="/a">a</a><a href="/b">b</a><a href="/c">c</a>',
    )
    report = discover_snapshot_links(snapshot, max_candidates=2, max_selected=2)

    assert report["status"] == "WEB_DISCOVERY_WARN"
    assert report["scope_state"] == "BOUNDED_TRUNCATED"
    assert report["state_counts"]["overflow_href_count"] == 1
    assert "WEB_DISCOVERY_MAX_CANDIDATES_TRUNCATED" in report["warnings"]


def test_tampered_content_hash_and_size_fail_closed(tmp_path: Path) -> None:
    snapshot = write_snapshot(tmp_path / "snapshot.json", '<a href="/a">a</a>')
    payload = __import__("json").loads(snapshot.read_text(encoding="utf-8"))
    payload["content_sha256"] = "0" * 64
    write_json(snapshot, payload)
    with pytest.raises(WebDiscoveryError) as bad_hash:
        discover_snapshot_links(snapshot)
    assert bad_hash.value.status == "ERROR_WEB_DISCOVERY_CONTENT_HASH"

    snapshot = write_snapshot(tmp_path / "snapshot2.json", '<a href="/a">a</a>')
    payload = __import__("json").loads(snapshot.read_text(encoding="utf-8"))
    payload["content_size_bytes"] += 1
    write_json(snapshot, payload)
    with pytest.raises(WebDiscoveryError) as bad_size:
        discover_snapshot_links(snapshot)
    assert bad_size.value.status == "ERROR_WEB_DISCOVERY_CONTENT_SIZE"


def test_non_html_snapshot_fails_closed(tmp_path: Path) -> None:
    content = b'{"x": 1}'
    snapshot = tmp_path / "json-snapshot.json"
    write_json(
        snapshot,
        {
            "snapshot_id": "websnap_json",
            "requested_url": "https://example.test/api",
            "final_url": "https://example.test/api",
            "retrieval_utc": "2026-09-15T00:00:00Z",
            "content_type": "application/json",
            "content_size_bytes": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
    )
    with pytest.raises(WebDiscoveryError) as exc:
        discover_snapshot_links(snapshot)
    assert exc.value.status == "ERROR_WEB_DISCOVERY_NOT_HTML"
