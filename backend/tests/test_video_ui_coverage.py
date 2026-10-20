import json
from pathlib import Path

import pytest

import scripts.certify_video_ui_coverage as cert


def relative_fixture(tmp_path: Path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "frontend/impl.jsx").write_text("export const x = 1;\n", encoding="utf-8")
    (tmp_path / "tests/test.mjs").write_text("test('x',()=>{});\n", encoding="utf-8")
    denominator = tmp_path / "den.json"
    coverage = tmp_path / "cov.json"
    denominator.write_text(json.dumps({"observations":[{"id":"OBS-1"},{"id":"OBS-2"}]}), encoding="utf-8")
    coverage.write_text(json.dumps({
        "counts":{"denominator":2,"unmapped":0},
        "coverage":[
            {"observation_id":"OBS-1","implementation":["frontend/impl.jsx"],"regression_surfaces":["tests/test.mjs"]},
            {"observation_id":"OBS-2","implementation":["frontend/impl.jsx"],"regression_surfaces":["tests/test.mjs"]},
        ],
        "global_gates":{"physical_mobile":"OPEN"},
    }), encoding="utf-8")
    return denominator, coverage


def patch_root(monkeypatch, root: Path):
    monkeypatch.setattr(cert, "ROOT", root)


def test_exact_coverage_passes(tmp_path, monkeypatch):
    denominator, coverage = relative_fixture(tmp_path)
    patch_root(monkeypatch, tmp_path)
    receipt = cert.certify(denominator, coverage)
    assert receipt["state"] == "PASS"
    assert receipt["coverage_count"] == 2


def test_missing_observation_fails_closed(tmp_path, monkeypatch):
    denominator, coverage = relative_fixture(tmp_path)
    patch_root(monkeypatch, tmp_path)
    data = json.loads(coverage.read_text())
    data["coverage"].pop()
    coverage.write_text(json.dumps(data))
    with pytest.raises(cert.CoverageFailure, match="DENOMINATOR_MISMATCH"):
        cert.certify(denominator, coverage)


def test_duplicate_coverage_id_fails_closed(tmp_path, monkeypatch):
    denominator, coverage = relative_fixture(tmp_path)
    patch_root(monkeypatch, tmp_path)
    data = json.loads(coverage.read_text())
    data["coverage"][1]["observation_id"] = "OBS-1"
    coverage.write_text(json.dumps(data))
    with pytest.raises(cert.CoverageFailure, match="DUPLICATE_COVERAGE_ID"):
        cert.certify(denominator, coverage)


def test_missing_implementation_path_fails_closed(tmp_path, monkeypatch):
    denominator, coverage = relative_fixture(tmp_path)
    patch_root(monkeypatch, tmp_path)
    data = json.loads(coverage.read_text())
    data["coverage"][0]["implementation"] = ["frontend/missing.jsx"]
    coverage.write_text(json.dumps(data))
    with pytest.raises(cert.CoverageFailure, match="MISSING_IMPLEMENTATION_PATH"):
        cert.certify(denominator, coverage)


def test_provider_product_name_in_ui_fails_closed(tmp_path, monkeypatch):
    denominator, coverage = relative_fixture(tmp_path)
    patch_root(monkeypatch, tmp_path)
    (tmp_path / "frontend/impl.jsx").write_text('export const label = "ChatGPT";\n')
    with pytest.raises(cert.CoverageFailure, match="PROVIDER_PRODUCT_UI_LEAK"):
        cert.certify(denominator, coverage)


def test_nonzero_unmapped_fails_closed(tmp_path, monkeypatch):
    denominator, coverage = relative_fixture(tmp_path)
    patch_root(monkeypatch, tmp_path)
    data = json.loads(coverage.read_text())
    data["counts"]["unmapped"] = 1
    coverage.write_text(json.dumps(data))
    with pytest.raises(cert.CoverageFailure, match="UNMAPPED_NONZERO"):
        cert.certify(denominator, coverage)
