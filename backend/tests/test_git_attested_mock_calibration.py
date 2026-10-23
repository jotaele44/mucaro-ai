"""Git-conserved Mucaro mock adapter verification; requires Git checkout."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from backend.app.model_benchmark_adapter import (
    execute_git_attested_mock_calibration,source_file_sha256)
from backend.app.observed_benchmark_runs import ObservedRunError

class GitAttestedMockTests(unittest.TestCase):
    def setUp(self):
        self.repo=Path(__file__).resolve().parents[2]
        self.head=subprocess.run(["git","-C",str(self.repo),"rev-parse","HEAD"],
                   check=True,capture_output=True,text=True).stdout.strip()
        self.params=dict(project_root=self.repo,source_head=self.head,
            inputs={c:c for c in ("a","b","c","d")},
            run_id="mock-runtime-calibration",runner_id="test-runner",
            expected_file_digest=source_file_sha256(self.repo))

    def test_exact_checkout_runs_actual_imported_mock(self):
        value=execute_git_attested_mock_calibration(**self.params)
        self.assertEqual(len(value["samples"]),4)
        self.assertEqual(value["source_identity_classification"],"LOCAL_GIT_CHECKOUT_ONLY")
        self.assertTrue(value["git_source_receipt_hash"].startswith("sha256:"))
        self.assertFalse(value["independent_model_improvement"])

    def test_wrong_git_head_denied(self):
        with self.assertRaises(Exception):
            execute_git_attested_mock_calibration(**{**self.params,"source_head":"0"*40})

    def test_wrong_source_digest_denied(self):
        with self.assertRaises(Exception):
            execute_git_attested_mock_calibration(
                **{**self.params,"expected_file_digest":"sha256:"+"0"*64})

    def test_copied_fixture_cannot_impersonate_imported_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            other=Path(temp)
            (other/"backend/app").mkdir(parents=True)
            (other/"backend/app/model_router.py").write_bytes(
                (self.repo/"backend/app/model_router.py").read_bytes())
            with self.assertRaisesRegex(ObservedRunError,"IMPORTED_MODEL_ROUTER_OUTSIDE_CHECKOUT"):
                execute_git_attested_mock_calibration(
                    **{**self.params,"project_root":other})
if __name__=="__main__":
    unittest.main()
