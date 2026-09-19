from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.gis_corpus import validate_real_gis_corpus
from backend.app.gis_processing import GISProcessingError


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "_architecture" / "gis_real_corpus_manifest.json"


def make_settings(project_root: Path, export_root: Path) -> Settings:
    cfg = Settings(
        project_root=project_root,
        data_dir=export_root / "data",
        input_dir=export_root / "data" / "input",
        processed_dir=export_root / "data" / "processed",
        export_dir=export_root / "data" / "exports",
        index_dir=export_root / "data" / "indexes",
        sqlite_path=export_root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def copy_corpus_project(project_root: Path) -> tuple[Path, dict]:
    manifest_path = project_root / "_architecture" / MANIFEST.name
    manifest_path.parent.mkdir(parents=True)
    fixture_root = project_root / "backend" / "tests" / "fixtures" / "gis_corpus"
    fixture_root.mkdir(parents=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for asset in manifest["assets"]:
        shutil.copy2(ROOT / asset["path"], project_root / asset["path"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest


def test_real_gis_corpus_manifest_closes_authority_and_geometry_accounting(tmp_path: Path) -> None:
    report = validate_real_gis_corpus(MANIFEST, cfg=make_settings(ROOT, tmp_path))

    assert report["status"] == "GIS_CORPUS_VALIDATION_OK"
    assert len(report["assets"]) == 2
    assert report["assets"][0]["feature_count"] == 3
    assert report["assets"][0]["geometry_counts"] == {"Polygon": 2, "MultiPolygon": 1}
    assert report["assets"][0]["coordinate_dimension_counts"] == {"XY": 3}
    assert report["assets"][1]["feature_count"] == 30
    assert report["assets"][1]["geometry_counts"] == {"Point": 30}
    assert report["assets"][1]["coordinate_dimension_counts"] == {"XYZ": 30}
    assert report["assets"][1]["features_with_z"] == 30
    assert report["assets"][1]["features_with_m"] == 0
    assert all(report["invariants"].values())


def test_real_gis_corpus_rejects_changed_bytes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    manifest_path, manifest = copy_corpus_project(project_root)
    changed_path = project_root / manifest["assets"][0]["path"]
    changed_path.write_bytes(changed_path.read_bytes() + b"\n")

    with pytest.raises(GISProcessingError) as exc:
        validate_real_gis_corpus(manifest_path, cfg=make_settings(project_root, tmp_path / "runtime"))
    assert exc.value.status == "ERROR_GIS_CORPUS_HASH"


def test_real_gis_corpus_rejects_dimension_manifest_drift(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    manifest_path, manifest = copy_corpus_project(project_root)
    manifest["assets"][1]["expected_coordinate_dimension_counts"] = {"XY": 30}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(GISProcessingError) as exc:
        validate_real_gis_corpus(manifest_path, cfg=make_settings(project_root, tmp_path / "runtime"))
    assert exc.value.status == "ERROR_GIS_CORPUS_DIMENSIONS"
