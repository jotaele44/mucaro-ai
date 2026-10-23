import tempfile
import unittest
from pathlib import Path
from shutil import copyfile
from backend.app.model_benchmark_adapter import execute_mock_calibration,source_file_sha256
from backend.app.observed_benchmark_runs import ObservedRunError
from backend.app.governed_learning_loop import digest

class ActualMockProviderCalibration(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[2]
        self.sha=source_file_sha256(self.root)
        self.inputs={"a":"What is A?","b":"What is B?","c":"What is C?","d":"What is D?"}
        self.kw=dict(project_root=self.root,source_head="a"*40,inputs=self.inputs,
            run_id="baseline",runner_id="isolated-runner",expected_file_digest=self.sha)
    def test_real_mock_router_four_calls_receipted(self):
        x=execute_mock_calibration(**self.kw)
        self.assertEqual(len(x["samples"]),4)
        self.assertEqual(x["provider"],"mock")
        self.assertFalse(x["independent_model_improvement"])
        self.assertEqual(x["cost_units_kind"],"UNMETERED")
        self.assertTrue(all("Mock cited answer" in a for a in x["observed_outputs"].values()))
        self.assertEqual(x["source_file_digest"],self.sha)
        self.assertEqual(x["receipt_hash"],digest({k:v for k,v in x.items() if k!="receipt_hash"}))
    def test_source_byte_mismatch_denied(self):
        with self.assertRaises(ObservedRunError):
            execute_mock_calibration(**{**self.kw,"expected_file_digest":digest("wrong")})
    def test_missing_principal_denied(self):
        with self.assertRaises(ObservedRunError):
            execute_mock_calibration(**{**self.kw,"runner_id":""})
    def test_incomplete_input_denied(self):
        with self.assertRaises(ObservedRunError):
            execute_mock_calibration(**{**self.kw,"inputs":{"a":"hello"}})
    def test_canonical_duplicate_run_same_outputs(self):
        a=execute_mock_calibration(**self.kw)
        b=execute_mock_calibration(**{**self.kw,"run_id":"candidate"})
        self.assertEqual(a["observed_outputs"],b["observed_outputs"])
        self.assertNotEqual(a["run_id"],b["run_id"])
    def test_missing_source_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ObservedRunError):
                source_file_sha256(Path(temp))
    def test_attested_source_is_bytes_not_caller_label(self):
        with tempfile.TemporaryDirectory() as temp:
            location=Path(temp)/"backend/app"
            location.mkdir(parents=True)
            copyfile(self.root/"backend/app/model_router.py",location/"model_router.py")
            self.assertEqual(source_file_sha256(Path(temp)),self.sha)
            (location/"model_router.py").write_bytes(b"fake")
            self.assertNotEqual(source_file_sha256(Path(temp)),self.sha)

if __name__=="__main__":
    unittest.main()
