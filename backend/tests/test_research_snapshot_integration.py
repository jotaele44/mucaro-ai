"""Exercise the existing governed acquisition producer, not a substitute schema."""

import hashlib
from pathlib import Path

from backend.app.research_evidence import audit_packet
from backend.app.web_browsing import WebFetchPolicy, fetch_web_snapshot
from backend.tests.test_web_browsing import StaticHandler, _serve_fixture, make_settings


def test_real_snapshot_producer_to_research_auditor(tmp_path):
    cfg = make_settings(tmp_path)
    server, url = _serve_fixture()
    try:
        fetched = fetch_web_snapshot(
            url, cfg=cfg, policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True)
        )
    finally:
        server.shutdown()
        server.server_close()
    path = Path(fetched["snapshot_path"])
    packet = {
        "schema_version": "mucaro-research-packet/1",
        "sources": [
            {
                "source_id": "captured",
                "path": str(path.relative_to(tmp_path)),
                "snapshot_sha256": fetched["snapshot_sha256"],
            }
        ],
        "claims": [
            {
                "claim_id": "capture",
                "text": "The fixture contains governed browsing text.",
                "source_id": "captured",
                "byte_start": 0,
                "byte_end": len(StaticHandler.body),
                "span_sha256": hashlib.sha256(StaticHandler.body).hexdigest(),
            }
        ],
    }
    result = audit_packet(tmp_path, packet)
    assert result["integrity_status"] == "PASS"
    assert result["release_certification"] == "WITHHELD"
    path.write_bytes(path.read_bytes() + b" ")
    assert audit_packet(tmp_path, packet)["integrity_status"] == "FAIL"
