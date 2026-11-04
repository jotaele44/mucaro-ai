import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.app.research_benchmark import prepare_blind_pairs, summarize_reviews


class BlindReviewTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            {
                "case_id": "case-1",
                "question": "What does the preserved source say?",
                "responses": [
                    {"system_id": "baseline", "run_id": "run-1", "answer": "Answer one [source]."},
                    {"system_id": "candidate", "run_id": "run-2", "answer": "Answer two [source]."},
                ],
            }
        ]

    def test_pairing_preserves_answers_and_keeps_metadata_private(self):
        original = copy.deepcopy(self.cases)
        public, private = prepare_blind_pairs(self.cases)
        self.assertEqual(self.cases, original)
        mapping = private["cases"][0]["mapping"]
        for label, answer in public["cases"][0]["answers"].items():
            self.assertNotIn("system_id", answer)
            source = next(r for r in self.cases[0]["responses"] if r["system_id"] == mapping[label]["system_id"])
            self.assertEqual(answer["answer"], source["answer"])
            self.assertEqual(answer["answer_sha256"], mapping[label]["answer_sha256"])

    def test_reject_empty_duplicate_and_unpaired(self):
        for cases in ([], self.cases * 2, [dict(self.cases[0], responses=self.cases[0]["responses"][:1])]):
            with self.assertRaises(ValueError):
                prepare_blind_pairs(cases)

    def test_complete_reviews_do_not_claim_superiority(self):
        public, _ = prepare_blind_pairs(self.cases)
        result = summarize_reviews(
            public, [{"case_id": "case-1", "scores": {"A": 90, "B": 80}, "identity_leakage": False}]
        )
        self.assertTrue(result["paired_reviews_complete"])
        self.assertEqual(result["comparative_superiority"], "NOT_ASSESSED")

    def test_missing_duplicate_unknown_reviews_rejected(self):
        public, _ = prepare_blind_pairs(self.cases)
        row = {"case_id": "case-1", "scores": {"A": 90, "B": 80}, "identity_leakage": False}
        for rows in ([], [row, row], [dict(row, case_id="unknown")]):
            with self.assertRaises(ValueError):
                summarize_reviews(public, rows)

    def test_cli_key_is_separate_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = root / "cases.json"
            cases.write_text(json.dumps(self.cases))
            key = root / "private.json"
            script = Path(__file__).resolve().parents[2] / "scripts" / "research_audit.py"
            command = [sys.executable, str(script), "blind", str(cases), "--private-key", str(key)]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertNotIn("system_id", first.stdout)
            original = key.read_bytes()
            if os.name == "posix":
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(key.read_bytes(), original)

    def test_invalid_scores_and_unreported_leakage(self):
        public, _ = prepare_blind_pairs(self.cases)
        for score in (float("nan"), float("inf"), True, -1, 101, "100"):
            with self.assertRaises(ValueError):
                summarize_reviews(
                    public, [{"case_id": "case-1", "scores": {"A": score, "B": 0}, "identity_leakage": False}]
                )
        with self.assertRaises(ValueError):
            summarize_reviews(public, [{"case_id": "case-1", "scores": {"A": 90, "B": 80}}])
