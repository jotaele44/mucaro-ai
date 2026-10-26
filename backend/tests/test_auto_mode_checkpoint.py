"""Offline checkpoint regression tests; no model, network or shell execution."""
import json
import tempfile
import unittest
from pathlib import Path
from backend.app.auto_mode_checkpoint import encode_checkpoint, decode_checkpoint, save_checkpoint, load_checkpoint
from backend.app.auto_mode_scheduler import AutoModeError, Vector, schedule


class TestAutoModeCheckpoint(unittest.TestCase):
    def setUp(self):
        self.vectors = (Vector("audit"), Vector("test", ("audit",), isolation_key="repo"),
                        Vector("deploy", ("test",), requires_authorization=True))

    def test_deterministic_encoding(self):
        self.assertEqual(encode_checkpoint(self.vectors, {"audit": "PASS"}),
                         encode_checkpoint(self.vectors, {"audit": "PASS"}))

    def test_roundtrip(self):
        self.assertEqual(decode_checkpoint(encode_checkpoint(self.vectors, {"audit": "PASS"})),
                         (self.vectors, {"audit": "PASS"}))

    def test_dependency_replay(self):
        vectors, states = decode_checkpoint(encode_checkpoint(self.vectors, {"audit": "PASS"}))
        self.assertEqual(schedule(vectors, states).ready, ("test",))
        self.assertIn("deploy", schedule(vectors, states).blocked)

    def test_tamper_denied(self):
        raw = encode_checkpoint(self.vectors, {"audit": "PASS"})
        with self.assertRaisesRegex(AutoModeError, "INVALID_CHECKPOINT"):
            decode_checkpoint(raw.replace(b'PASS', b'FAIL'))

    def test_unknown_schema_denied(self):
        raw = json.loads(encode_checkpoint(self.vectors, {}))
        raw["payload"]["schema"] = 999
        with self.assertRaisesRegex(AutoModeError, "INVALID_CHECKPOINT"):
            decode_checkpoint(json.dumps(raw).encode())

    def test_noncanonical_denied(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_CHECKPOINT"):
            decode_checkpoint(encode_checkpoint(self.vectors, {}) + b" ")

    def test_atomic_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            digest = save_checkpoint(path, self.vectors, {"audit": "PASS"})
            self.assertEqual(len(digest), 64)
            self.assertEqual(load_checkpoint(path), (self.vectors, {"audit": "PASS"}))

    def test_symlink_target_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "actual.json"
            target.write_text("do not overwrite")
            link = Path(directory) / "link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(AutoModeError, "INVALID_CHECKPOINT_PATH"):
                save_checkpoint(link, self.vectors, {})
            self.assertEqual(target.read_text(), "do not overwrite")

    def test_corrupted_file_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_bytes(b"corrupted")
            with self.assertRaisesRegex(AutoModeError, "INVALID_CHECKPOINT"):
                load_checkpoint(path)

    def test_oversized_denied(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_CHECKPOINT"):
            decode_checkpoint(b"x" * 2_000_001)


if __name__ == "__main__":
    unittest.main()
