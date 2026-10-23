from __future__ import annotations

import json
from pathlib import Path

from scripts.certify_ingestion_benchmark import certify


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "_architecture" / "ingestion_benchmark_v1.json"


def test_matrix010_manifest_closes_declared_case_denominator() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = payload["cases"]
    assert payload["benchmark_id"] == "MATRIX-010"
    assert payload["expected_format_count"] == 22
    assert len(cases) == 16
    assert len({row["case_id"] for row in cases}) == 16
    assert {row["handling_class"] for row in cases if row["kind"] == "representative"} == set(
        payload["representative_handling_classes"]
    )
    assert {row["adversarial_class"] for row in cases if row["kind"] == "adversarial"} == set(
        payload["required_adversarial_classes"]
    )


def test_matrix010_certifier_uses_canonical_registry_and_contract_classes(monkeypatch, tmp_path: Path) -> None:
    import scripts.certify_ingestion_benchmark as benchmark

    class Result:
        returncode = 0
        stdout = "pass"
        stderr = ""

    real_run = benchmark.subprocess.run

    def fake_run(args, **kwargs):
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return type("GitResult", (), {"returncode": 0, "stdout": "a" * 40 + "\n", "stderr": ""})()
        return Result()

    monkeypatch.setattr(benchmark.subprocess, "run", fake_run)
    receipt = certify(ROOT, MANIFEST)

    assert receipt["status"] == "MATRIX010_INGESTION_BENCHMARK_PASS"
    assert receipt["head_sha"] == "a" * 40
    assert receipt["registry_format_count"] == 22
    assert receipt["required_case_count"] == 16
    assert receipt["passed_case_count"] == 16
    assert receipt["failed_case_count"] == 0
    assert receipt["arithmetic_closes"] is True
