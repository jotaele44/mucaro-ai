import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.research_evidence import audit_packet, read_json_strict


def digest(data):
    return hashlib.sha256(data).hexdigest()


class EvidenceReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = "Río Grande: source-bounded geography.".encode()
        self.snapshot = {
            "schema_version": "mucaro_web_snapshot_v1",
            "snapshot_id": "s1",
            "requested_url": "https://example.org/a",
            "final_url": "https://example.org/a",
            "retrieval_utc": "2026-10-30T12:00:00Z",
            "http_status": 200,
            "content_base64": base64.b64encode(self.content).decode(),
            "content_sha256": digest(self.content),
            "content_size_bytes": len(self.content),
        }
        self.packet = {
            "schema_version": "mucaro-research-packet/1",
            "sources": [{"source_id": "s1", "path": "snapshot.json", "snapshot_sha256": ""}],
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "A preserved passage exists.",
                    "source_id": "s1",
                    "byte_start": 0,
                    "byte_end": len(self.content),
                    "span_sha256": digest(self.content),
                }
            ],
        }
        self.freeze()

    def freeze(self):
        raw = json.dumps(self.snapshot).encode()
        (self.root / "snapshot.json").write_bytes(raw)
        self.packet["sources"][0]["snapshot_sha256"] = digest(raw)

    def audit(self):
        return audit_packet(self.root, self.packet)

    def test_roundtrip_does_not_certify_truth(self):
        before = (self.root / "snapshot.json").read_bytes()
        result = self.audit()
        self.assertEqual(result["integrity_status"], "PASS")
        self.assertEqual(result["release_certification"], "WITHHELD")
        self.assertEqual(result["claims"][0]["entailment"], "OPEN")
        self.assertEqual(before, (self.root / "snapshot.json").read_bytes())

    def test_tampered_snapshot(self):
        (self.root / "snapshot.json").write_bytes(b"{}")
        self.assertEqual(self.audit()["failures"], 2)

    def test_inner_hash_and_size_are_independently_checked(self):
        for field, value in [
            ("content_sha256", "0" * 64),
            ("content_size_bytes", True),
            ("content_base64", "%%%%"),
            ("retrieval_utc", "2026-10-30"),
            ("http_status", True),
        ]:
            with self.subTest(field=field):
                original = self.snapshot[field]
                self.snapshot[field] = value
                self.freeze()
                self.assertEqual(self.audit()["integrity_status"], "FAIL")
                self.snapshot[field] = original

    def test_error_page_preserved_but_not_affirmative_evidence(self):
        self.snapshot["http_status"] = 404
        self.freeze()
        result = self.audit()
        self.assertEqual(result["sources"][0]["status"], "PASS")
        self.assertEqual(result["claims"][0]["status"], "FAIL")

    def test_duplicate_sources_invalidate_binding(self):
        self.packet["sources"].append(dict(self.packet["sources"][0]))
        result = self.audit()
        self.assertEqual(result["sources_processed"], 2)
        self.assertEqual(result["claims"][0]["status"], "FAIL")

    def test_invalid_claim_spans_and_hashes(self):
        for field, value in [
            ("byte_start", -1),
            ("byte_start", True),
            ("byte_end", 999),
            ("byte_end", 0),
            ("span_sha256", "f" * 64),
            ("source_id", "missing"),
        ]:
            with self.subTest(field=field):
                original = self.packet["claims"][0][field]
                self.packet["claims"][0][field] = value
                self.assertEqual(self.audit()["integrity_status"], "FAIL")
                self.packet["claims"][0][field] = original

    def test_duplicate_claims_and_count_closure(self):
        self.packet["claims"].append(dict(self.packet["claims"][0]))
        result = self.audit()
        self.assertEqual(result["claims_declared"], result["claims_processed"])
        self.assertEqual(result["integrity_status"], "FAIL")

    def test_path_traversal_and_absolute_paths(self):
        for path in ("../snapshot.json", str(self.root / "snapshot.json")):
            self.packet["sources"][0]["path"] = path
            self.assertEqual(self.audit()["integrity_status"], "FAIL")

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "snapshot.json"
            outside.write_bytes((self.root / "snapshot.json").read_bytes())
            (self.root / "escape.json").symlink_to(outside)
            self.packet["sources"][0]["path"] = "escape.json"
            self.assertEqual(self.audit()["integrity_status"], "FAIL")

    def test_payload_budgets_fail_closed(self):
        with patch("backend.app.research_evidence.MAX_SNAPSHOT_BYTES", 1):
            self.assertEqual(self.audit()["integrity_status"], "FAIL")
        with patch("backend.app.research_evidence.MAX_PACKET_CONTENT_BYTES", 1):
            self.assertEqual(self.audit()["integrity_status"], "FAIL")

    def test_empty_denominator_is_not_pass(self):
        self.packet["claims"] = []
        with self.assertRaises(ValueError):
            self.audit()

    def test_duplicate_json_and_nonfinite_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.assertRaises(ValueError):
                read_json_strict(raw)

    def test_cli_replays_packet(self):
        packet = self.root / "packet.json"
        packet.write_text(json.dumps(self.packet))
        script = Path(__file__).resolve().parents[2] / "scripts" / "research_audit.py"
        result = subprocess.run(
            [sys.executable, str(script), "audit", str(packet), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["integrity_status"], "PASS")
