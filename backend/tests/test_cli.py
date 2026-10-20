import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import backend.app.cli as cli
import backend.app.packaging as packaging
from backend.app.cli import main
from backend.app.config import Settings
from backend.app.release_receipts import write_release_receipt
from backend.app.retrieval_eval import build_retrieval_eval_report


def make_settings(root: Path, *, operator_token: str | None = None) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
        operator_token=operator_token,
    )


def test_cli_ingest_and_ask(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    ingest_output = capsys.readouterr().out
    assert "INGEST_FOLDER_OK" in ingest_output

    assert main(["ask", "What does source lineage require?", "--strategy", "persistent", "--format", "text"], cfg=cfg) == 0
    ask_output = capsys.readouterr().out
    assert "CITATION_OK" in ask_output
    assert "chunk_id=" in ask_output
    assert "citation=1" in ask_output
    assert "source_id=" in ask_output
    assert "rank=1" in ask_output


def test_cli_plan(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["plan", "Add", "CLI", "capability", "--input", "mucaro-ai"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "codebase_build" in output


def test_cli_plan_text_output(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["plan", "Add", "CLI", "capability", "--input", "mucaro-ai", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "OK" in output
    assert "active_vector=codebase_build" in output
    assert "recommended_mode=CLAUDE_CODE_MODE" in output
    assert "readiness_percent=" in output


def test_cli_help_lists_serve(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "certify" in output
    assert "capabilities" in output
    assert "models" in output
    assert "serve" in output


def test_cli_inspects_portable_runtime_archive_without_materializing(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    archive = tmp_path / "runtime.tar.gz"
    archive.write_bytes(b"fixture")
    expected = {
        "archive_bytes": 7,
        "archive_root": "python",
        "archive_sha256": "a" * 64,
        "logical_members_sha256": "b" * 64,
        "member_count": 2,
        "payload_multiset_sha256": "c" * 64,
        "regular_file_count": 1,
        "symlink_count": 1,
        "total_regular_bytes": 17,
    }

    monkeypatch.setattr(cli, "inspect_runtime_archive", lambda path: expected if path == archive else {})
    monkeypatch.setattr(
        cli,
        "materialize_portable_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not materialize")),
    )

    assert main(["package", "portable-runtime", "--inspect-archive", str(archive), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "PORTABLE_RUNTIME_ARCHIVE_INSPECTED", **expected}


def test_cli_archive_inspection_passes_explicit_safety_bounds(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    archive = tmp_path / "runtime.tar.gz"
    observed = {}

    def fake_inspect(path: Path, **kwargs):
        observed["path"] = path
        observed["kwargs"] = kwargs
        return {
            "archive_bytes": 1,
            "archive_root": "python",
            "archive_sha256": "a" * 64,
            "logical_members_sha256": "b" * 64,
            "member_count": 1,
            "payload_multiset_sha256": "c" * 64,
            "regular_file_count": 1,
            "symlink_count": 0,
            "total_regular_bytes": 1,
        }

    monkeypatch.setattr(cli, "inspect_runtime_archive", fake_inspect)

    assert (
        main(
            [
                "package",
                "portable-runtime",
                "--inspect-archive",
                str(archive),
                "--max-members",
                "2500",
                "--max-regular-bytes",
                "80000000",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    assert observed == {
        "path": archive,
        "kwargs": {"max_members": 2500, "max_regular_bytes": 80000000},
    }


def test_cli_archive_inspection_bounds_require_inspection_mode(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    monkeypatch.setattr(
        cli,
        "materialize_portable_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must fail before materialization")),
    )

    assert main(["package", "portable-runtime", "--max-members", "2500", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "ERROR_PORTABLE_RUNTIME_MODE_CONFLICT"
    assert "--inspect-archive" in payload["detail"]


def test_cli_archive_inspection_rejects_conflicting_modes(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    archive = tmp_path / "runtime.tar.gz"
    verify = tmp_path / "runtime"

    monkeypatch.setattr(
        cli,
        "inspect_runtime_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("conflict must fail before inspection")),
    )

    assert (
        main(
            [
                "package",
                "portable-runtime",
                "--inspect-archive",
                str(archive),
                "--verify",
                str(verify),
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "ERROR_PORTABLE_RUNTIME_MODE_CONFLICT"
    assert "--verify" in payload["detail"]


def test_cli_materializes_portable_runtime_with_governed_defaults(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    expected_output = tmp_path / "data" / "exports" / "portable_runtimes" / "macos-x86_64"

    def fake_materialize(root: Path, manifest: Path, output: Path, *, archive_path: Path | None = None):
        assert root == tmp_path
        assert manifest == tmp_path / "packaging" / "runtime" / "macos-x86_64.json"
        assert output == expected_output
        assert archive_path is None
        return {"status": "PORTABLE_RUNTIME_VERIFIED", "runtime_dir": str(output), "failed_checks": []}

    monkeypatch.setattr(cli, "materialize_portable_runtime", fake_materialize)

    assert main(["package", "portable-runtime", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PORTABLE_RUNTIME_VERIFIED"
    assert payload["runtime_dir"] == str(expected_output)


def test_cli_materializes_portable_runtime_with_explicit_manifest_output(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    manifest = tmp_path / "packaging" / "runtime" / "macos-arm64.json"
    expected_output = tmp_path / "data" / "exports" / "portable_runtimes" / "macos-arm64"

    def fake_materialize(root: Path, manifest_path: Path, output: Path, *, archive_path: Path | None = None):
        assert root == tmp_path
        assert manifest_path == manifest
        assert output == expected_output
        assert archive_path is None
        return {"status": "PORTABLE_RUNTIME_VERIFIED", "runtime_dir": str(output), "failed_checks": []}

    monkeypatch.setattr(cli, "materialize_portable_runtime", fake_materialize)

    assert (
        main(
            ["package", "portable-runtime", "--manifest", str(manifest), "--format", "json"],
            cfg=cfg,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PORTABLE_RUNTIME_VERIFIED"
    assert payload["runtime_dir"] == str(expected_output)


def test_cli_portable_runtime_explicit_output_overrides_manifest_name(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    manifest = tmp_path / "packaging" / "runtime" / "macos-arm64.json"
    explicit_output = tmp_path / "custom" / "runtime"

    def fake_materialize(root: Path, manifest_path: Path, output: Path, *, archive_path: Path | None = None):
        assert root == tmp_path
        assert manifest_path == manifest
        assert output == explicit_output
        assert archive_path is None
        return {"status": "PORTABLE_RUNTIME_VERIFIED", "runtime_dir": str(output), "failed_checks": []}

    monkeypatch.setattr(cli, "materialize_portable_runtime", fake_materialize)

    assert (
        main(
            [
                "package",
                "portable-runtime",
                "--manifest",
                str(manifest),
                "--output-dir",
                str(explicit_output),
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["runtime_dir"] == str(explicit_output)


def test_cli_refuses_portable_app_when_runtime_verification_fails(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    monkeypatch.setattr(
        cli,
        "verify_materialized_runtime",
        lambda *args, **kwargs: {
            "status": "PORTABLE_RUNTIME_VERIFY_FAILED",
            "failed_checks": ["runtime_tree"],
        },
    )

    def forbidden_builder(*args, **kwargs):
        raise AssertionError("portable app builder must not run")

    monkeypatch.setattr(cli, "build_portable_macos_app", forbidden_builder)

    assert main(["package", "portable-macos-app", "--runtime-dir", str(runtime)], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload == {"status": "ERROR_PORTABLE_RUNTIME_VERIFY", "detail": "runtime_tree"}


def test_cli_capabilities_contracts_reports_expanded_scope(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["capabilities", "contracts", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ADVANCED_CAPABILITY_CONTRACTS_READY"
    assert payload["contract_count"] == 5
    assert payload["complete_contract_count"] == 5
    assert payload["implementation_states"] == {"ABSENT": 1, "CANDIDATE": 4}

    assert main(["capabilities", "contracts", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "contract_count=5" in output
    assert "implementation_states=ABSENT:1,CANDIDATE:4" in output
    assert "CONTRACT_DEFINED CANDIDATE advanced_gis_processing" in output
    assert "CONTRACT_DEFINED CANDIDATE state_of_art_ocr_analysis" in output
    assert "CONTRACT_DEFINED CANDIDATE governed_web_browsing" in output
    assert "CONTRACT_DEFINED ABSENT coding_programming_expertise" in output
    assert "CONTRACT_DEFINED CANDIDATE transparency_objectivity_governance" in output


def test_cli_capabilities_validate_gis_report_checks_saved_receipts(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    geojson = tmp_path / "measure.geojson"
    geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
                "features": [
                    {
                        "type": "Feature",
                        "id": "square",
                        "properties": {"name": "square"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    assert main(["gis", "measure", str(geojson), "--coordinate-units", "meters", "--format", "json"], cfg=cfg) == 0
    measurement = json.loads(capsys.readouterr().out)

    assert main(["capabilities", "validate-gis-report", measurement["report_path"], "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "CAPABILITY_OUTPUT_CONTRACT_OK"
    assert payload["capability_id"] == "advanced_gis_processing"
    assert payload["operation"] == "measure_geojson"
    assert payload["counts"]["fail"] == 0

    report_payload = json.loads(Path(measurement["report_path"]).read_text(encoding="utf-8"))
    report_payload["invariants"]["row_accounting_closed"] = False
    weak_report = tmp_path / "weak-report.json"
    weak_report.write_text(json.dumps(report_payload), encoding="utf-8")

    assert main(["capabilities", "validate-gis-report", str(weak_report), "--format", "text"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "CAPABILITY_OUTPUT_CONTRACT_FAILED" in output
    assert "FAIL invariants.row_accounting_closed" in output


def test_cli_ocr_analyze_reports_receipt_summary(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (8).to_bytes(4, "big") + (6).to_bytes(4, "big"))

    def fake_analyze(path: Path, **kwargs):
        assert path == image
        assert kwargs["cfg"] == cfg
        assert kwargs["language"] == "eng"
        assert kwargs["layout_mode"] == "auto"
        assert kwargs["tesseract_path"] == "/usr/bin/tesseract"
        assert kwargs["timeout_seconds"] == 12
        return {
            "status": "OCR_ANALYSIS_OK",
            "operation": "image_ocr",
            "report_id": "ocrreport_fixture",
            "report_path": str(tmp_path / "data" / "exports" / "ocr_reports" / "ocrreport_fixture.json"),
            "report_sha256": "a" * 64,
            "source": {"sha256": "b" * 64, "mime_type": "image/png", "width": 8, "height": 6},
            "engine": {
                "name": "tesseract",
                "version": "tesseract 5.3.0",
                "language": "eng",
                "layout_mode": "auto",
            },
            "invariants": {"span_count": 1, "low_confidence_count": 0, "empty_text_state": False},
            "warnings": ["OCR_UNKNOWN_CONFIDENCE_PRESENT"],
        }

    monkeypatch.setattr(cli, "analyze_image_ocr", fake_analyze)

    assert (
        main(
            [
                "ocr",
                "analyze",
                str(image),
                "--language",
                "eng",
                "--layout",
                "auto",
                "--tesseract-path",
                "/usr/bin/tesseract",
                "--timeout-seconds",
                "12",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "OCR_ANALYSIS_OK" in output
    assert "operation=image_ocr" in output
    assert "source_sha256=" + "b" * 64 in output
    assert "ocr_engine=tesseract" in output
    assert "language=eng" in output
    assert "layout_mode=auto" in output
    assert "span_count=1" in output
    assert "warnings=OCR_UNKNOWN_CONFIDENCE_PRESENT" in output


def test_cli_capabilities_validate_ocr_report_checks_saved_receipts(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    report = {
        "schema_version": "mucaro_ocr_report_v1",
        "report_id": "ocrreport_cli",
        "status": "OCR_ANALYSIS_NO_TEXT",
        "operation": "image_ocr",
        "created_at": "2026-09-12T00:00:00Z",
        "source": {
            "path": "fixtures/blank.png",
            "sha256": "c" * 64,
            "mime_type": "image/png",
            "media_kind": "image",
            "bytes": 64,
            "width": 8,
            "height": 6,
            "parser_warnings": [],
        },
        "engine": {
            "name": "tesseract",
            "version": "tesseract 5.3.0",
            "language": "eng",
            "layout_mode": "single_block",
            "confidence_scale": "0-100",
        },
        "text": {"raw": "", "normalized": "", "canonical": ""},
        "spans": [],
        "invariants": {
            "raw_normalized_canonical_separated": True,
            "span_count": 0,
            "low_confidence_count": 0,
            "unknown_confidence_count": 0,
            "empty_text_state": True,
            "bounding_boxes_have_source_coordinates": True,
        },
        "warnings": ["OCR_NO_TEXT_DETECTED"],
        "limitations": ["image-only OCR fixture"],
        "falsification_criteria": ["reject if no-text state is not explicit"],
    }
    report_path = tmp_path / "ocr-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert main(["capabilities", "validate-ocr-report", str(report_path), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "CAPABILITY_OUTPUT_CONTRACT_OK"
    assert payload["capability_id"] == "state_of_art_ocr_analysis"
    assert payload["operation"] == "image_ocr"
    assert payload["counts"]["fail"] == 0

    report["warnings"] = []
    weak_report = tmp_path / "weak-ocr-report.json"
    weak_report.write_text(json.dumps(report), encoding="utf-8")

    assert main(["capabilities", "validate-ocr-report", str(weak_report), "--format", "text"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "CAPABILITY_OUTPUT_CONTRACT_FAILED" in output
    assert "FAIL no_text_status_has_warning" in output


def test_cli_ocr_analyze_reports_adapter_errors(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    def fake_analyze(path: Path, **kwargs):
        del path, kwargs
        raise cli.OCRAnalysisError("ERROR_OCR_ENGINE_UNAVAILABLE", "Local tesseract executable was not found.")

    monkeypatch.setattr(cli, "analyze_image_ocr", fake_analyze)

    assert main(["ocr", "analyze", str(image), "--format", "text"], cfg=cfg) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR_OCR_ENGINE_UNAVAILABLE Local tesseract executable was not found." in captured.err


def test_cli_sources_ingest_web_snapshot(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "mucaro_web_snapshot_v1",
                "snapshot_id": "websnap_cli",
                "requested_url": "https://example.com/cli",
                "fetch_url": "https://example.com/cli",
                "final_url": "https://example.com/cli",
                "redirect_chain": [],
                "fragment_removed": False,
                "retrieval_started_utc": "2026-09-10T00:00:00Z",
                "retrieval_utc": "2026-09-10T00:00:01Z",
                "http_status": 200,
                "content_type": "text/html; charset=utf-8",
                "content_sha256": "27063147d74d8ad4df1af70a0b7cd1585c6db476543a4b5f27f7347dade5ab4e",
                "content_size_bytes": 64,
                "content_base64": "",
                "parser_name": "web_snapshot_preview_v1",
                "parser_warnings": [],
                "charset": "utf-8",
                "text_preview": "CLI web snapshot citation fixture.",
                "policy": {"timeout_seconds": 5, "max_bytes": 1000, "allow_private_hosts": False},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    assert main(["sources", "ingest-web-snapshot", str(snapshot_path), "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "source_id=src_web_" in output
    assert "chunks_added=1" in output


def test_cli_gis_inspect_and_point_in_polygon(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    points_path = tmp_path / "points.geojson"
    polygons_path = tmp_path / "polygons.geojson"
    points_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                "features": [
                    {
                        "type": "Feature",
                        "id": "inside",
                        "properties": {"name": "inside"},
                        "geometry": {"type": "Point", "coordinates": [5, 5]},
                    },
                    {
                        "type": "Feature",
                        "id": "outside",
                        "properties": {"name": "outside"},
                        "geometry": {"type": "Point", "coordinates": [15, 5]},
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    polygons_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                "features": [
                    {
                        "type": "Feature",
                        "id": "square",
                        "properties": {"name": "square"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    assert main(["gis", "inspect", str(points_path), "--format", "text"], cfg=cfg) == 0
    inspect_output = capsys.readouterr().out
    assert "GIS_INSPECT_OK" in inspect_output
    assert "feature_count=2" in inspect_output
    assert "topology_valid_count=2" in inspect_output
    assert "topology_invalid_count=0" in inspect_output
    assert "topology_unresolved_count=0" in inspect_output
    assert "topology_planar_xy_only_count=0" in inspect_output
    assert "topology_not_applicable_count=0" in inspect_output
    assert "crs=EPSG:4326" in inspect_output
    assert "report_sha256=" in inspect_output

    assert main(["gis", "point-in-polygon", str(points_path), str(polygons_path), "--format", "text"], cfg=cfg) == 0
    join_output = capsys.readouterr().out
    assert "GIS_SPATIAL_JOIN_OK" in join_output
    assert "join_cardinality=1:1" in join_output
    assert "crs_match=True" in join_output
    assert "point_row_conservation=True" in join_output
    assert "FULLY_WITHIN=1" in join_output
    assert "OUTSIDE=1" in join_output

    mismatched_polygons_path = tmp_path / "polygons_3857.geojson"
    mismatched_payload = json.loads(polygons_path.read_text(encoding="utf-8"))
    mismatched_payload["crs"]["properties"]["name"] = "EPSG:3857"
    mismatched_polygons_path.write_text(json.dumps(mismatched_payload, sort_keys=True), encoding="utf-8")

    assert main(["gis", "point-in-polygon", str(points_path), str(mismatched_polygons_path), "--format", "text"], cfg=cfg) == 2
    denied_output = capsys.readouterr().err
    assert "ERROR_GIS_CRS_MISMATCH" in denied_output

    assert main(
        ["gis", "point-in-polygon", str(points_path), str(mismatched_polygons_path), "--allow-crs-mismatch", "--format", "text"],
        cfg=cfg,
    ) == 0
    allowed_output = capsys.readouterr().out
    assert "crs_match=False" in allowed_output
    assert "crs_mismatch_allowed=True" in allowed_output
    assert "warnings=GIS_CRS_MISMATCH_ALLOWED" in allowed_output

    assert main(["gis", "compare", str(polygons_path), str(polygons_path), "--format", "text"], cfg=cfg) == 0
    compare_output = capsys.readouterr().out
    assert "GIS_GEOMETRY_COMPARE_OK" in compare_output
    assert "match_on=id" in compare_output
    assert "topology_adapter=local_canonical_geometry_v1" in compare_output
    assert "planar_overlay_adapter=shapely_planar_overlay_v1" in compare_output
    assert "feature_key_conservation=True" in compare_output
    assert "compared_count=1" in compare_output
    assert "topological_equal_count=1" in compare_output
    assert "vertex_symmetric_difference_total=0" in compare_output
    assert "planar_overlay_measured_count=1" in compare_output
    assert "planar_overlay_symmetric_difference_area_total=0.0" in compare_output

    overlay_right_path = tmp_path / "overlay_right.geojson"
    overlay_right_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                "features": [
                    {
                        "type": "Feature",
                        "id": "square",
                        "properties": {"name": "square"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[5, 5], [15, 5], [15, 15], [5, 15], [5, 5]]],
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    assert main(["gis", "overlay", str(polygons_path), str(overlay_right_path), "--operation", "intersection", "--format", "text"], cfg=cfg) == 0
    overlay_output = capsys.readouterr().out
    assert "GIS_OVERLAY_OK" in overlay_output
    assert "operation=overlay_geojson_collections" in overlay_output
    assert "match_on=id" in overlay_output
    assert "overlay_operation=intersection" in overlay_output
    assert "adapter=shapely_planar_overlay_v1" in overlay_output
    assert "overlay_adapter=shapely_planar_overlay_v1" in overlay_output
    assert "output_path=" in overlay_output
    assert "output_sha256=" in overlay_output
    assert "output_crs=EPSG:4326" in overlay_output
    assert "left_feature_count=1" in overlay_output
    assert "right_feature_count=1" in overlay_output
    assert "shared_feature_key_count=1" in overlay_output
    assert "output_feature_count=1" in overlay_output
    assert "emitted_feature_count=1" in overlay_output
    assert "feature_key_accounting_closed=True" in overlay_output
    assert "overlay_pair_accounting_closed=True" in overlay_output
    assert "result_area_total=25.0" in overlay_output
    assert "EMITTED=1" in overlay_output

    assert main(["gis", "measure", str(polygons_path), "--coordinate-units", "meters", "--format", "text"], cfg=cfg) == 0
    measure_output = capsys.readouterr().out
    assert "GIS_MEASURE_OK" in measure_output
    assert "operation=measure_geojson" in measure_output
    assert "measurement_mode=planar" in measure_output
    assert "adapter=local_planar_shoelace_v1" in measure_output
    assert "area_units=meters^2" in measure_output
    assert "length_units=meters" in measure_output
    assert "feature_count=1" in measure_output
    assert "measured_feature_count=1" in measure_output
    assert "row_accounting_closed=True" in measure_output
    assert "total_area=100.0" in measure_output
    assert "total_length=40.0" in measure_output
    assert "MEASURED=1" in measure_output

    assert (
        main(
            [
                "gis",
                "measure",
                str(polygons_path),
                "--measurement-mode",
                "geodesic",
                "--coordinate-units",
                "degrees",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 0
    )
    geodesic_output = capsys.readouterr().out
    assert "GIS_MEASURE_OK" in geodesic_output
    assert "measurement_mode=geodesic" in geodesic_output
    assert "adapter=local_spherical_geodesic_v1" in geodesic_output
    assert "coordinate_units=degrees" in geodesic_output
    assert "area_units=meters^2" in geodesic_output
    assert "length_units=meters" in geodesic_output
    assert "row_accounting_closed=True" in geodesic_output

    assert main(["gis", "measure", str(polygons_path), "--coordinate-units", " ", "--format", "text"], cfg=cfg) == 2
    measure_error_output = capsys.readouterr().err
    assert "ERROR_GIS_MEASURE_UNITS_REQUIRED" in measure_error_output

    assert main(["gis", "reproject", str(points_path), "--target-crs", "EPSG:3857", "--format", "text"], cfg=cfg) == 0
    reproject_output = capsys.readouterr().out
    assert "GIS_REPROJECT_OK" in reproject_output
    assert "operation=reproject_geojson" in reproject_output
    assert "output_path=" in reproject_output
    assert "output_crs=EPSG:3857" in reproject_output
    assert "source_crs=EPSG:4326" in reproject_output
    assert "target_crs=EPSG:3857" in reproject_output
    assert "crs_changed=True" in reproject_output
    assert "feature_row_conservation=True" in reproject_output
    assert "coordinate_count_conservation=True" in reproject_output

    assert (
        main(
            [
                "gis",
                "filter-bbox",
                str(points_path),
                "--min-x",
                "0",
                "--min-y",
                "0",
                "--max-x",
                "10",
                "--max-y",
                "10",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 0
    )
    filter_output = capsys.readouterr().out
    assert "GIS_BBOX_FILTER_OK" in filter_output
    assert "operation=filter_geojson_by_bbox" in filter_output
    assert "output_path=" in filter_output
    assert "input_feature_count=2" in filter_output
    assert "retained_feature_count=1" in filter_output
    assert "excluded_feature_count=1" in filter_output
    assert "excluded_outside_count=1" in filter_output
    assert "row_accounting_closed=True" in filter_output
    assert "RETAINED=1" in filter_output
    assert "EXCLUDED_OUTSIDE=1" in filter_output

    assert (
        main(
            [
                "gis",
                "clip-bbox",
                str(polygons_path),
                "--min-x",
                "0",
                "--min-y",
                "0",
                "--max-x",
                "2",
                "--max-y",
                "2",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 0
    )
    clip_output = capsys.readouterr().out
    assert "GIS_CLIP_BBOX_OK" in clip_output
    assert "operation=clip_geojson_by_bbox" in clip_output
    assert "adapter=shapely_bbox_intersection_v1" in clip_output
    assert "bbox=[0.0, 0.0, 2.0, 2.0]" in clip_output
    assert "output_path=" in clip_output
    assert "input_feature_count=1" in clip_output
    assert "retained_feature_count=1" in clip_output
    assert "clipped_feature_count=1" in clip_output
    assert "unchanged_feature_count=0" in clip_output
    assert "excluded_feature_count=0" in clip_output
    assert "source_coordinate_count=5" in clip_output
    assert "output_coordinate_count=5" in clip_output
    assert "row_accounting_closed=True" in clip_output
    assert "RETAINED_CLIPPED=1" in clip_output

    assert (
        main(
            [
                "gis",
                "filter-bbox",
                str(points_path),
                "--min-x",
                "10",
                "--min-y",
                "0",
                "--max-x",
                "0",
                "--max-y",
                "10",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 2
    )
    filter_error_output = capsys.readouterr().err
    assert "ERROR_GIS_BBOX_INVALID" in filter_error_output

    assert (
        main(
            [
                "gis",
                "clip-bbox",
                str(points_path),
                "--min-x",
                "10",
                "--min-y",
                "0",
                "--max-x",
                "0",
                "--max-y",
                "10",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 2
    )
    clip_error_output = capsys.readouterr().err
    assert "ERROR_GIS_BBOX_INVALID" in clip_error_output

    csv_path = tmp_path / "points.csv"
    csv_path.write_text(
        "id,lat,lon,label\n"
        "alpha,18.4,-66.1,valid first point\n"
        "bad,91,-66.2,invalid latitude\n"
        "bravo,18.5,-66.3,valid second point\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "gis",
                "csv-points",
                str(csv_path),
                "--lat-column",
                "lat",
                "--lon-column",
                "lon",
                "--id-column",
                "id",
                "--skip-invalid-rows",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    csv_payload = json.loads(capsys.readouterr().out)
    assert csv_payload["status"] == "GIS_CSV_POINTS_WARN"
    assert csv_payload["output"]["feature_count"] == 2
    assert csv_payload["skipped_rows"][0]["row_number"] == 3
    assert csv_payload["invariants"]["row_accounting_closed"] is True

    assert (
        main(
            [
                "gis",
                "csv-points",
                str(csv_path),
                "--lat-column",
                "lat",
                "--lon-column",
                "lon",
                "--id-column",
                "id",
                "--skip-invalid-rows",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 0
    )
    csv_output = capsys.readouterr().out
    assert "GIS_CSV_POINTS_WARN" in csv_output
    assert "operation=csv_points_to_geojson" in csv_output
    assert "source_row_count=3" in csv_output
    assert "output_feature_count=2" in csv_output
    assert "skipped_row_count=1" in csv_output
    assert "row_accounting_closed=True" in csv_output
    assert "warnings=GIS_CSV_INVALID_ROWS_SKIPPED" in csv_output

    assert (
        main(
            [
                "gis",
                "csv-points",
                str(csv_path),
                "--lat-column",
                "lat",
                "--lon-column",
                "lon",
                "--id-column",
                "id",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 2
    )
    csv_error_output = capsys.readouterr().err
    assert "ERROR_GIS_CSV_INVALID_COORDINATES" in csv_error_output


def test_cli_models_list_reports_provider_registry(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    cfg = make_settings(tmp_path)

    assert main(["models", "list", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "MODEL_PROVIDERS_OK"
    providers = {provider["name"]: provider for provider in payload["providers"]}
    assert set(providers) == {"mock", "anthropic", "openai_compatible_local"}
    assert providers["mock"]["status"] == "MODEL_PROVIDER_READY"
    assert providers["anthropic"]["status"] == "MODEL_PROVIDER_DISABLED"

    assert main(["models", "list", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "providers=3" in text_output
    assert "MODEL_PROVIDER_READY mock" in text_output
    assert "MODEL_PROVIDER_DISABLED anthropic" in text_output


def test_cli_models_status_defaults_to_configured_provider(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["models", "status", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "MODEL_PROVIDER_STATUS"
    assert payload["provider"]["name"] == "mock"
    assert payload["provider"]["status"] == "MODEL_PROVIDER_READY"


def test_cli_models_status_reports_live_provider_gates(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = make_settings(tmp_path)

    assert main(["models", "status", "anthropic", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out

    assert "provider=anthropic" in text_output
    assert "status=MODEL_PROVIDER_DISABLED" in text_output
    assert "warnings=LIVE_MODE_DISABLED" in text_output

    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    assert main(["models", "status", "anthropic", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["provider"]["status"] == "MODEL_PROVIDER_NOT_READY"
    assert payload["provider"]["warnings"] == ["API_KEY_MISSING:ANTHROPIC_API_KEY"]


def test_cli_models_status_rejects_unknown_provider(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["models", "status", "other", "--format", "text"], cfg=cfg) == 2
    captured = capsys.readouterr()

    assert "ERROR_UNSUPPORTED_MODEL_PROVIDER other" in captured.err


def test_cli_version(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert output == "mucaro 0.1.23\n"


def test_cli_validate_text_output(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["validate", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "JSON_OK" in output
    assert "checked=0" in output


def test_cli_doctor_reports_warning_state_without_sources(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["doctor", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DOCTOR_WARN"
    assert payload["summary"]["source_count"] == 0
    assert payload["summary"]["index_status"] == "INDEX_MISSING"
    assert payload["summary"]["semantic_store_status"] == "SEMANTIC_STORE_MISSING"
    assert payload["summary"]["tool_count"] >= 1
    assert any(check["name"] == "source_library" and check["status"] == "WARN" for check in payload["checks"])
    assert any(check["name"] == "tool_runtime" and check["status"] == "PASS" for check in payload["checks"])

    assert main(["doctor", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "DOCTOR_WARN" in strict_output
    assert "recommendation=" in strict_output


def test_cli_doctor_reports_ready_state_after_local_setup(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path, operator_token="local-token")
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["doctor", "--strict", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DOCTOR_OK"
    assert payload["summary"]["source_count"] == 1
    assert payload["summary"]["index_status"] == "INDEX_OK"
    assert payload["summary"]["semantic_store_status"] == "SEMANTIC_STORE_OK"
    assert payload["summary"]["auth_required"] is True
    assert payload["summary"]["memory_count"] == 0


def test_cli_product_audit_reports_open_goal_gaps(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["audit", "product", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRODUCT_AUDIT_OPEN"
    assert payload["readiness_percent"] == 69.6
    assert payload["counts"] == {"pass": 16, "open": 7, "blocked": 0, "total": 23}
    by_id = {row["requirement_id"]: row for row in payload["requirements"]}
    assert by_id["permission_gated_user_directory"]["status"] == "PASS"
    assert by_id["source_lineage_citations"]["status"] == "PASS"
    assert by_id["semantic_vector_store"]["status"] == "PASS"
    assert by_id["tool_use_and_actions"]["status"] == "PASS"
    assert by_id["advanced_gis_processing"]["status"] == "OPEN"
    assert by_id["state_of_art_ocr_analysis"]["status"] == "OPEN"
    assert by_id["governed_web_browsing"]["status"] == "OPEN"
    assert by_id["coding_programming_expertise"]["status"] == "OPEN"
    assert by_id["transparency_objectivity_governance"]["status"] == "OPEN"
    assert by_id["multimodal_interface"]["status"] == "PASS"
    assert by_id["personalization_memory"]["status"] == "PASS"
    assert by_id["production_security"]["status"] == "PASS"
    assert by_id["source_artifact_packaging"]["status"] == "PASS"
    assert by_id["github_release_governance"]["status"] == "OPEN"
    assert by_id["final_phase_gui_operator_workspace"]["status"] == "OPEN"
    assert "canonical default workspace" in by_id["final_phase_gui_operator_workspace"]["evidence"]
    assert "Rendered desktop/mobile/accessibility certification" in by_id["final_phase_gui_operator_workspace"]["gap"]
    assert payload["next_recommended_vector"] == "advanced_gis_processing"

    assert main(["audit", "product", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "PRODUCT_AUDIT_OPEN" in strict_output
    assert "OPEN advanced_gis_processing" in strict_output
    assert "PASS source_artifact_packaging" in strict_output
    assert "OPEN github_release_governance" in strict_output
    assert "OPEN final_phase_gui_operator_workspace" in strict_output


def test_cli_product_roadmap_groups_audit_gaps(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["audit", "roadmap", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRODUCT_ROADMAP_OPEN"
    assert payload["audit_status"] == "PRODUCT_AUDIT_OPEN"
    assert payload["audit_counts"] == {"pass": 16, "open": 7, "blocked": 0, "total": 23}
    assert payload["next_recommended_stage"] == "advanced_capability_expansion"
    assert payload["next_recommended_vector"] == "advanced_gis_processing"

    stages = {stage["stage_id"]: stage for stage in payload["stages"]}
    assert stages["canonical_operator_surface"]["status"] == "PASS"
    assert stages["permissioned_source_grounding"]["status"] == "PASS"
    assert stages["controlled_assistant_behavior"]["status"] == "PASS"
    assert stages["advanced_capability_expansion"]["status"] == "OPEN"
    assert stages["advanced_capability_expansion"]["open_requirements"] == [
        "advanced_gis_processing",
        "state_of_art_ocr_analysis",
        "governed_web_browsing",
        "coding_programming_expertise",
        "transparency_objectivity_governance",
    ]
    assert stages["release_packaging_certification"]["status"] == "PASS"
    assert stages["hosted_release_governance"]["status"] == "OPEN"
    assert stages["hosted_release_governance"]["open_requirements"] == ["github_release_governance"]
    assert stages["final_operator_gui"]["status"] == "OPEN"
    assert stages["final_operator_gui"]["open_requirements"] == ["final_phase_gui_operator_workspace"]

    assert main(["audit", "roadmap", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "PRODUCT_ROADMAP_OPEN" in strict_output
    assert "audit_total=23" in strict_output
    assert "next_recommended_stage=advanced_capability_expansion" in strict_output
    assert "OPEN advanced_capability_expansion" in strict_output
    assert "OPEN hosted_release_governance" in strict_output
    assert "OPEN final_operator_gui" in strict_output
    assert "open_requirements=advanced_gis_processing,state_of_art_ocr_analysis,governed_web_browsing,coding_programming_expertise,transparency_objectivity_governance" in strict_output


def test_cli_eval_retrieval_reports_quality_metrics(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["eval", "retrieval", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_OK"
    assert payload["counts"] == {
        "chunks": 25,
        "queries": 31,
        "positive_queries": 25,
        "no_context_queries": 6,
        "expected_bindings": 28,
        "distractor_bindings": 0,
        "distractor_queries": 0,
        "source_ids": 25,
        "source_formats": 0,
        "multi_binding_queries": 3,
        "query_categories": 20,
        "strategies": 6,
    }
    assert payload["fixture_validation"]["status"] == "RETRIEVAL_FIXTURE_OK"
    assert payload["min_source_formats"] == 0
    assert payload["min_category_recall"] == 0.0
    assert payload["min_category_binding_recall"] == 0.0
    assert payload["category_threshold_failures"] == []
    assert payload["distractor_threshold_failures"] == []
    assert {strategy["strategy"] for strategy in payload["strategies"]} == {
        "lexical",
        "vector",
        "bm25",
        "hybrid",
        "persistent",
        "semantic",
    }
    hybrid = next(strategy for strategy in payload["strategies"] if strategy["strategy"] == "hybrid")
    assert hybrid["expected_binding_recall_at_k"] == 1.0
    assert hybrid["context_precision_at_k"] == 0.2857
    assert hybrid["full_binding_hit_rate"] == 1.0
    assert hybrid["multi_binding_full_hit_rate"] == 1.0
    assert hybrid["positive_retrieved_chunks"] == 98
    assert hybrid["relevant_retrieved_chunks"] == 28
    assert len(hybrid["category_metrics"]) == 20
    categories = {category["category"]: category for category in hybrid["category_metrics"]}
    assert categories["conversation_context"] == {
        "category": "conversation_context",
        "queries": 2,
        "positive_queries": 2,
        "no_context_queries": 0,
        "recall_at_k": 1.0,
        "expected_binding_recall_at_k": 1.0,
        "context_precision_at_k": 0.5,
        "top1_hit_rate": 1.0,
        "full_binding_hit_rate": 1.0,
        "multi_binding_full_hit_rate": 1.0,
        "distractor_free_rate": 1.0,
        "distractor_hit_rate": 0.0,
        "no_context_accuracy": 1.0,
        "expected_bindings": 3,
        "distractor_bindings": 0,
        "distractor_hits": 0,
        "distractor_top1_hits": 0,
        "positive_retrieved_chunks": 6,
        "relevant_retrieved_chunks": 3,
    }
    first_case = hybrid["cases"][0]
    assert first_case["category"] in categories

    assert main(["eval", "retrieval", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "fixture=backend/tests/fixtures/retrieval_eval.json" in text_output
    assert "positive_queries=25" in text_output
    assert "no_context_queries=6" in text_output
    assert "source_ids=25" in text_output
    assert "source_formats=0" in text_output
    assert "distractor_bindings=0" in text_output
    assert "multi_binding_queries=3" in text_output
    assert "query_categories=20" in text_output
    assert "fixture_validation=RETRIEVAL_FIXTURE_OK" in text_output
    assert "min_no_context_accuracy=1.0" in text_output
    assert "min_hybrid_precision=0.2" in text_output
    assert "min_expected_binding_recall=0.95" in text_output
    assert "min_distractor_free_rate=1.0" in text_output
    assert "min_category_recall=0.0" in text_output
    assert "min_category_binding_recall=0.0" in text_output
    assert "min_source_formats=0" in text_output
    assert "binding_recall=1.00" in text_output
    assert "precision=0.29" in text_output
    assert "multi_full=1.00" in text_output
    assert "hybrid category=cli_contract queries=1 positives=1 recall@5=1.00 binding_recall=1.00 precision=0.20" in text_output
    assert "hybrid recall@5=" in text_output
    assert "semantic recall@5=" in text_output
    assert "RETRIEVAL_EVAL_OK" in text_output


def test_cli_eval_retrieval_reports_mixed_format_quality_metrics(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    fixture = "backend/tests/fixtures/retrieval_eval_mixed_formats.json"

    assert main(["eval", "retrieval", "--fixture", fixture, "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_OK"
    assert payload["counts"] == {
        "chunks": 29,
        "queries": 36,
        "positive_queries": 30,
        "no_context_queries": 6,
        "expected_bindings": 34,
        "distractor_bindings": 3,
        "distractor_queries": 3,
        "source_ids": 29,
        "source_formats": 13,
        "multi_binding_queries": 4,
        "query_categories": 13,
        "strategies": 6,
    }
    assert payload["fixture_validation"]["status"] == "RETRIEVAL_FIXTURE_OK"
    assert payload["min_source_formats"] == 0
    hybrid = next(strategy for strategy in payload["strategies"] if strategy["strategy"] == "hybrid")
    assert hybrid["expected_binding_recall_at_k"] == 1.0
    assert hybrid["context_precision_at_k"] == 0.2378
    assert hybrid["full_binding_hit_rate"] == 1.0
    assert hybrid["multi_binding_full_hit_rate"] == 1.0
    assert hybrid["distractor_free_rate"] == 1.0
    assert hybrid["distractor_hit_rate"] == 1.0
    assert hybrid["distractor_hits"] == 3
    assert hybrid["distractor_top1_hits"] == 0
    assert hybrid["no_context_accuracy"] == 1.0
    assert hybrid["positive_retrieved_chunks"] == 143
    assert hybrid["relevant_retrieved_chunks"] == 34
    categories = {category["category"]: category for category in hybrid["category_metrics"]}
    assert set(categories) == {
        "csv",
        "docx",
        "eml",
        "html",
        "json",
        "markdown",
        "media",
        "msg",
        "multi_format",
        "no_context",
        "pdf",
        "traceability",
        "txt",
    }
    assert categories["multi_format"]["multi_binding_full_hit_rate"] == 1.0
    assert categories["no_context"]["no_context_accuracy"] == 1.0
    assert categories["txt"]["distractor_bindings"] == 1
    assert categories["txt"]["distractor_top1_hits"] == 0

    assert main(["eval", "retrieval", "--fixture", fixture, "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "fixture=backend/tests/fixtures/retrieval_eval_mixed_formats.json" in text_output
    assert "chunks=29" in text_output
    assert "positive_queries=30" in text_output
    assert "expected_bindings=34" in text_output
    assert "distractor_bindings=3" in text_output
    assert "distractor_queries=3" in text_output
    assert "source_formats=13" in text_output
    assert "min_source_formats=0" in text_output
    assert "query_categories=13" in text_output
    assert "hybrid category=csv queries=2 positives=2 recall@5=1.00" in text_output
    assert "hybrid category=html queries=3 positives=3 recall@5=1.00" in text_output
    assert "hybrid category=media queries=3 positives=3 recall@5=1.00" in text_output
    assert "RETRIEVAL_EVAL_OK" in text_output


def test_cli_eval_retrieval_fails_when_source_format_threshold_is_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_mixed_formats.json",
                "--min-source-formats",
                "14",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_source_formats"] == 14
    assert payload["source_format_threshold_failures"] == [
        {"source_formats": 13, "min_source_formats": 14}
    ]

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_mixed_formats.json",
                "--min-source-formats",
                "14",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 2
    )
    text_output = capsys.readouterr().out
    assert "min_source_formats=14" in text_output
    assert "source_format_threshold_failure source_formats=13 min_source_formats=14" in text_output
    assert "RETRIEVAL_EVAL_FAILED" in text_output


def test_retrieval_eval_rejects_negative_source_format_threshold():
    with pytest.raises(ValueError, match="MIN_SOURCE_FORMATS_MUST_BE_NON_NEGATIVE"):
        build_retrieval_eval_report(min_source_formats=-1)


def test_cli_eval_retrieval_fails_when_category_thresholds_are_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
                "--min-category-binding-recall",
                "1.0",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_category_binding_recall"] == 1.0
    assert {
        (failure["strategy"], failure["category"])
        for failure in payload["category_threshold_failures"]
    } >= {
        ("bm25", "multi_binding"),
        ("semantic", "multi_binding"),
    }

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
                "--min-category-binding-recall",
                "1.0",
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 2
    )
    text_output = capsys.readouterr().out
    assert "category_threshold_failure strategy=bm25 category=multi_binding" in text_output
    assert "RETRIEVAL_EVAL_FAILED" in text_output


def test_cli_eval_retrieval_fails_when_any_strategy_returns_unrelated_context(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
                "--min-no-context-accuracy",
                "1.1",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_no_context_accuracy"] == 1.1


def test_cli_eval_retrieval_fails_when_thresholds_are_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["eval", "retrieval", "--min-hybrid-recall", "1.1", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_hybrid_recall"] == 1.1


def test_cli_eval_retrieval_fails_when_precision_threshold_is_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
                "--min-hybrid-precision",
                "0.9",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    hybrid = next(strategy for strategy in payload["strategies"] if strategy["strategy"] == "hybrid")
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_hybrid_precision"] == 0.9
    assert hybrid["context_precision_at_k"] == 0.236


def test_cli_eval_retrieval_fails_when_binding_threshold_is_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
                "--min-expected-binding-recall",
                "1.0",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    bm25 = next(strategy for strategy in payload["strategies"] if strategy["strategy"] == "bm25")
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_expected_binding_recall"] == 1.0
    assert bm25["expected_binding_recall_at_k"] == 0.9667


def test_cli_eval_retrieval_fails_when_distractor_threshold_is_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    fixture = tmp_path / "distractor_retrieval_eval.json"
    chunks = [
        {"chunk_id": f"known_{idx}", "source_id": f"source_{idx:02d}", "chunk_index": 0, "text": f"known source text {idx}"}
        for idx in range(25)
    ]
    chunks[0] = {
        "chunk_id": "expected_execution",
        "source_id": "zzz_expected_execution",
        "chunk_index": 0,
        "text": "Execution approval requires operator lock evidence before local action.",
    }
    chunks[1] = {
        "chunk_id": "decoy_execution",
        "source_id": "aaa_decoy_execution",
        "chunk_index": 0,
        "text": "Execution approval appears in unrelated procurement notes before local action.",
    }
    queries = [
        {
            "query": "Execution approval before local action",
            "expected_chunk_ids": ["expected_execution"],
            "distractor_chunk_ids": ["decoy_execution"],
            "category": "decoy",
        },
        *[
            {"query": f"known source text {idx}", "expected_chunk_ids": [f"known_{idx}"], "category": f"cat_{idx}"}
            for idx in range(2, 23)
        ],
        *[
            {"query": "known source text 21 and 22", "expected_chunk_ids": ["known_21", "known_22"], "category": "multi_a"},
            {"query": "known source text 23 and 24", "expected_chunk_ids": ["known_23", "known_24"], "category": "multi_b"},
        ],
        *[
            {"query": f"outside topic {idx}", "expected_chunk_ids": [], "category": "no_context"}
            for idx in range(6)
        ],
    ]
    fixture.write_text(json.dumps({"chunks": chunks, "queries": queries}), encoding="utf-8")

    assert main(["eval", "retrieval", "--fixture", str(fixture), "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_distractor_free_rate"] == 1.0
    assert any(failure["strategy"] == "hybrid" for failure in payload["distractor_threshold_failures"])

    assert main(["eval", "retrieval", "--fixture", str(fixture), "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "distractor_threshold_failure strategy=hybrid" in text_output
    assert "RETRIEVAL_EVAL_FAILED" in text_output


def test_cli_eval_retrieval_fails_on_invalid_fixture(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    fixture = tmp_path / "invalid_retrieval_eval.json"
    fixture.write_text(
        json.dumps(
            {
                "chunks": [{"chunk_id": "known", "source_id": "source", "chunk_index": 0, "text": "known text"}],
                "queries": [{"query": "unknown binding", "expected_chunk_ids": ["missing"]}],
            }
        ),
        encoding="utf-8",
    )

    assert main(["eval", "retrieval", "--fixture", str(fixture), "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_INVALID"
    checks = {check["name"]: check for check in payload["fixture_validation"]["checks"]}
    assert checks["fixture_size"]["status"] == "FAIL"
    assert checks["expected_chunk_bindings"]["status"] == "FAIL"
    assert checks["negative_cases"]["status"] == "FAIL"


def test_cli_eval_retrieval_fails_on_non_object_fixture(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    fixture = tmp_path / "array_retrieval_eval.json"
    fixture.write_text("[]\n", encoding="utf-8")

    assert main(["eval", "retrieval", "--fixture", str(fixture), "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_INVALID"
    checks = {check["name"]: check for check in payload["fixture_validation"]["checks"]}
    assert checks["fixture_shape"]["status"] == "FAIL"
    assert checks["fixture_shape"]["detail"] == "fixture_type=list chunks_type=missing queries_type=missing"


def test_cli_certify_retrieval_quality_writes_exact_head_receipt(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["certify", "retrieval-quality", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_QUALITY_CERT_OK"
    assert payload["profile"] == "v0.1.23-local-release"
    assert payload["head_sha"] == "UNKNOWN_HEAD"
    assert payload["receipt"] == "data/exports/release_receipts/retrieval_quality-UNKNOWN_HEAD.json"
    assert [case["name"] for case in payload["cases"]] == [
        "default_fixture_strict_category_binding",
        "large_fixture_calibrated_multi_binding",
        "mixed_format_fixture_strict_binding",
    ]
    cases = {case["name"]: case for case in payload["cases"]}
    assert cases["default_fixture_strict_category_binding"]["thresholds"]["min_source_formats"] == 0
    assert cases["large_fixture_calibrated_multi_binding"]["thresholds"]["min_source_formats"] == 0
    assert cases["mixed_format_fixture_strict_binding"]["thresholds"]["min_source_formats"] == 13
    assert cases["mixed_format_fixture_strict_binding"]["counts"]["source_formats"] == 13
    assert payload["failed_cases"] == []

    receipt_path = tmp_path / payload["receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_type"] == "retrieval_quality"
    assert receipt["status"] == "RETRIEVAL_QUALITY_CERT_OK"
    assert receipt["head_sha"] == "UNKNOWN_HEAD"
    assert receipt["payload"]["profile"] == "v0.1.23-local-release"

    assert main(["certify", "retrieval-quality", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "RETRIEVAL_QUALITY_CERT_OK" in text_output
    assert "default_fixture_strict_category_binding" in text_output
    assert "large_fixture_calibrated_multi_binding" in text_output
    assert "mixed_format_fixture_strict_binding" in text_output
    assert "min_category_binding_recall=0.8" in text_output
    assert "min_category_binding_recall=1.0" in text_output
    assert "min_distractor_free_rate=1.0" in text_output
    assert "min_source_formats=13" in text_output


def test_cli_eval_answer_trace_reports_traceability_metrics(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["eval", "answer-trace", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ANSWER_TRACE_EVAL_OK"
    assert payload["counts"] == {
        "cases": 12,
        "static_cases": 9,
        "generated_cases": 3,
        "passed": 12,
        "failed": 0,
        "expected_errors": 6,
    }
    assert payload["case_pass_rate"] == 1.0
    assert payload["failures"] == []

    assert main(["eval", "answer-trace", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "ANSWER_TRACE_EVAL_OK" in text_output
    assert "valid_single_citation_trace" in text_output
    assert "generated_markdown_source_trace" in text_output
    assert "generated_multi_source_trace" in text_output
    assert "generated_web_snapshot_trace" in text_output
    assert "valid_web_snapshot_citation_trace" in text_output
    assert "web_snapshot_citation_missing_url_fails" in text_output
    assert "invented_citation_fails" in text_output
    assert "generated_cases=3" in text_output
    assert "expected_errors=6" in text_output


def test_cli_eval_answer_trace_requires_negative_cases(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    fixture = tmp_path / "answer_trace_eval_positive_only.json"
    fixture.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "only_positive",
                        "expected_status": "CITATION_OK",
                        "trace": {
                            "retrieved_chunk_ids": ["chunk_a"],
                            "cited_chunk_ids": ["chunk_a"],
                            "cited_chunks": [
                                {
                                    "citation_order": 1,
                                    "retrieval_rank": 1,
                                    "chunk_id": "chunk_a",
                                    "source_id": "src_a",
                                    "chunk_index": 0,
                                    "text_snippet": "Source lineage requires citations.",
                                }
                            ],
                            "validation_flags": [],
                        },
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["eval", "answer-trace", "--fixture", str(fixture), "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ANSWER_TRACE_EVAL_FAILED"
    assert payload["counts"]["expected_errors"] == 0
    assert payload["failures"][0]["case_id"] == "fixture"


def test_cli_eval_gis_reports_fixture_manifest_results(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["eval", "gis", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "GIS_EVAL_OK"
    assert payload["counts"]["cases"] == 70
    assert payload["counts"]["passed"] == 70
    assert payload["counts"]["failed"] == 0
    assert payload["counts"]["operations"] == 26
    assert payload["counts"]["negative_cases"] == 29

    assert main(["eval", "gis", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "GIS_EVAL_OK" in text_output
    assert "operation_count=26" in text_output
    assert "PASS raster_depression_fill raster_depression_fill_enclosed_bowl_closes_volume" in text_output
    assert "PASS raster_hillshade raster_hillshade_west_light_east_rising_plane" in text_output
    assert "PASS raster_d8_flow raster_d8_east_descent_closes_terminal_contributions" in text_output
    assert "PASS bbox_filter bbox_filter_intersects_with_null_residue" in text_output
    assert "PASS bbox_filter bbox_filter_invalid_bbox_fails_closed" in text_output
    assert "PASS clip_bbox clip_bbox_polygon_point_and_residue" in text_output
    assert "PASS clip_bbox clip_bbox_invalid_bbox_fails_closed" in text_output
    assert "PASS measure measure_planar_polygon_line_and_null" in text_output
    assert "PASS measure measure_geodesic_wgs84_square" in text_output
    assert "PASS measure measure_geodesic_non_wgs84_fails_closed" in text_output
    assert "PASS measure measure_units_required_fails_closed" in text_output
    assert "PASS inspect inspect_dimensionality" in text_output
    assert "PASS inspect inspect_invalid_topology_warns_without_repair" in text_output
    assert "PASS compare compare_planar_overlay_difference" in text_output
    assert "PASS overlay overlay_intersection_outputs_feature" in text_output
    assert "PASS overlay overlay_invalid_operation_fails_closed" in text_output
    assert "PASS csv_points csv_points_skip_invalid_rows" in text_output
    assert "PASS reproject reproject_invalid_latitude_fails_closed" in text_output
    assert "PASS reproject_crs reproject_crs_wgs84_to_utm20n" in text_output
    assert "PASS measure_ellipsoidal measure_ellipsoidal_antimeridian_line" in text_output
    assert "PASS measure_ellipsoidal measure_ellipsoidal_polar_cap_polygon" in text_output
    assert "PASS measure_ellipsoidal measure_ellipsoidal_invalid_latitude_is_unresolved" in text_output
    assert "PASS raster_terrain raster_terrain_east_rising_plane" in text_output
    assert "PASS raster_terrain raster_terrain_geographic_crs_fails_closed" in text_output
    assert "PASS repair_topology repair_topology_bowtie_explicit" in text_output
    assert "PASS raster_sample raster_sample_distinct_states" in text_output
    assert "PASS vector_raster vector_raster_preserves_zero_nodata_null" in text_output
    assert "PASS zonal_stats zonal_stats_close_numeric_nodata_and_zero_accounting" in text_output
    assert "PASS raster_resample raster_resample_preserves_grid_contract" in text_output
    assert "PASS corpus_validate real_corpus_bytes_schema_ids_and_geometry_validate" in text_output
    assert "PASS map_export map_export_binds_renderer_and_layers" in text_output


def test_cli_certify_answer_trace_quality_writes_exact_head_receipt(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["certify", "answer-trace-quality", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ANSWER_TRACE_QUALITY_CERT_OK"
    assert payload["profile"] == "v0.1.23-local-release"
    assert payload["head_sha"] == "UNKNOWN_HEAD"
    assert payload["receipt"] == "data/exports/release_receipts/answer_trace_quality-UNKNOWN_HEAD.json"
    assert payload["report"]["status"] == "ANSWER_TRACE_EVAL_OK"

    receipt_path = tmp_path / payload["receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_type"] == "answer_trace_quality"
    assert receipt["status"] == "ANSWER_TRACE_QUALITY_CERT_OK"
    assert receipt["head_sha"] == "UNKNOWN_HEAD"
    assert receipt["payload"]["profile"] == "v0.1.23-local-release"

    assert main(["certify", "answer-trace-quality", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "ANSWER_TRACE_QUALITY_CERT_OK" in text_output
    assert "valid_multi_citation_trace_preserves_order_and_rank" in text_output
    assert "generated_multi_source_trace" in text_output
    assert "case_pass_rate=1.0" in text_output


def test_cli_certify_gis_quality_writes_exact_head_receipt(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["certify", "gis-quality", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "GIS_QUALITY_CERT_OK"
    assert payload["profile"] == "v0.1.23-local-release"
    assert payload["head_sha"] == "UNKNOWN_HEAD"
    assert payload["receipt"] == "data/exports/release_receipts/gis_quality-UNKNOWN_HEAD.json"
    assert payload["report"]["status"] == "GIS_EVAL_OK"

    receipt_path = tmp_path / payload["receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_type"] == "gis_quality"
    assert receipt["status"] == "GIS_QUALITY_CERT_OK"
    assert receipt["head_sha"] == "UNKNOWN_HEAD"
    assert receipt["payload"]["profile"] == "v0.1.23-local-release"

    assert main(["certify", "gis-quality", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "GIS_QUALITY_CERT_OK" in text_output
    assert "independent_datum=PASS" in text_output
    assert "eval_case_count=70" in text_output
    assert "PASS raster_depression_fill raster_depression_fill_enclosed_bowl_closes_volume" in text_output
    assert "PASS raster_hillshade raster_hillshade_west_light_east_rising_plane" in text_output
    assert "PASS raster_d8_flow raster_d8_east_descent_closes_terminal_contributions" in text_output
    assert "PASS bbox_filter bbox_filter_intersects_with_null_residue" in text_output
    assert "PASS clip_bbox clip_bbox_polygon_point_and_residue" in text_output
    assert "PASS clip_bbox clip_bbox_invalid_bbox_fails_closed" in text_output
    assert "PASS measure measure_planar_polygon_line_and_null" in text_output
    assert "PASS measure measure_geodesic_wgs84_square" in text_output
    assert "PASS measure measure_geodesic_non_wgs84_fails_closed" in text_output
    assert "PASS csv_points csv_points_skip_invalid_rows" in text_output
    assert "PASS compare compare_rotated_polygon" in text_output
    assert "PASS compare compare_planar_overlay_difference" in text_output
    assert "PASS overlay overlay_intersection_outputs_feature" in text_output
    assert "PASS overlay overlay_invalid_operation_fails_closed" in text_output
    assert "PASS reproject reproject_invalid_latitude_fails_closed" in text_output
    assert "PASS reproject_crs reproject_crs_wgs84_to_utm20n" in text_output
    assert "PASS repair_topology repair_topology_bowtie_explicit" in text_output
    assert "PASS raster_sample raster_sample_distinct_states" in text_output
    assert "PASS vector_raster vector_raster_preserves_zero_nodata_null" in text_output
    assert "PASS zonal_stats zonal_stats_close_numeric_nodata_and_zero_accounting" in text_output
    assert "PASS raster_resample raster_resample_preserves_grid_contract" in text_output
    assert "PASS corpus_validate real_corpus_bytes_schema_ids_and_geometry_validate" in text_output
    assert "PASS map_export map_export_binds_renderer_and_layers" in text_output


def write_package_fixture(root: Path, *, frontend_version: str = "0.1.23", lock_version: str | None = None) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """
[project]
name = "mucaro-ai"
version = "0.1.23"

[project.scripts]
mucaro = "backend.app.cli:main"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Mucaro AI\n", encoding="utf-8")
    (root / "docs" / "PACKAGING_POLICY.md").write_text("# Packaging Policy\n", encoding="utf-8")
    source_root = Path(__file__).resolve().parents[2]
    for relative in [
        "docs/APPLE_APP_STORE_SECURITY.md",
        "_architecture/macos_app_store_security_profile.json",
        "_architecture/schemas/macos-app-store-candidate-receipt.schema.json",
        "_architecture/schemas/macos-app-store-vulnerability-scan.schema.json",
        "_architecture/schemas/macos-app-store-malware-scan.schema.json",
        "packaging/macos/MucaroAI-AppStore.entitlements",
        "packaging/macos/PrivacyInfo.xcprivacy",
        "packaging/runtime/macos-x86_64.json",
        "packaging/runtime/requirements-macos-x86_64.lock",
        "packaging/runtime/macos-arm64.json",
        "packaging/runtime/requirements-macos-arm64.lock",
    ]:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / relative).read_bytes())
    for path in [
        "scripts/mucaro.py",
        "scripts/launch_app.py",
        "scripts/bootstrap_macos.sh",
        "scripts/bootstrap_windows.ps1",
        "scripts/certify_release.py",
        "scripts/certify_fresh_clone.py",
        "scripts/package_desktop.py",
        "scripts/certify_source_artifact.py",
        "scripts/certify_python_distribution.py",
        "scripts/native_start.sh",
        "scripts/one_click.sh",
        "scripts/one_click.py",
    ]:
        (root / path).write_text("# fixture\n", encoding="utf-8")
    (root / "frontend" / "package.json").write_text(
        json.dumps({"name": "mucaro-ai-frontend", "version": frontend_version}),
        encoding="utf-8",
    )
    (root / "frontend" / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "mucaro-ai-frontend",
                "version": lock_version or frontend_version,
                "packages": {
                    "": {
                        "name": "mucaro-ai-frontend",
                        "version": lock_version or frontend_version,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_cli_package_status_reports_packaging_readiness(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)

    assert main(["package", "status", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "PACKAGE_WARN"
    assert payload["python_package"]["name"] == "mucaro-ai"
    assert payload["python_package"]["entrypoint"] == "backend.app.cli:main"
    assert payload["frontend_package"]["version"] == "0.1.23"
    assert checks["required_package_inputs"]["status"] == "PASS"
    assert checks["version_consistency"]["status"] == "PASS"
    assert checks["frontend_lockfile"]["status"] == "PASS"
    assert checks["generated_outputs"]["status"] == "WARN"
    assert checks["macos_app_bundle"]["status"] == "WARN"
    assert checks["release_artifact_policy"]["status"] == "PASS"
    assert checks["distribution_policy"]["status"] == "PASS"
    assert checks["mac_app_store_security_profile"]["status"] == "WARN"
    assert checks["hosted_release_gate"]["status"] == "WARN"

    assert main(["package", "status", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "PACKAGE_WARN" in text_output
    assert "PASS cli_entrypoint backend.app.cli:main" in text_output
    assert "WARN hosted_release_gate" in text_output


def test_cli_app_store_audit_reports_open_and_strict_exit(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)

    assert main(["package", "app-store-audit", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "MAC_APP_STORE_READINESS_OPEN"
    assert payload["counts"]["fail"] == 0
    assert {gate["state"] for gate in payload["external_gates"]} == {"BLOCKED"}

    assert main(["package", "app-store-audit", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "MAC_APP_STORE_READINESS_OPEN" in text_output
    assert "OPEN candidate_bundle" in text_output
    assert "external_gate=BLOCKED:MAS-REVIEW-001:Apple App Review acceptance" in text_output


def test_cli_package_macos_app_builds_and_verifies_bundle(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "index.html").write_text("<div>Mucaro</div>\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    for name in ("native_start.sh", "one_click.sh", "one_click.py"):
        (tmp_path / "scripts" / name).write_text("# launcher\n", encoding="utf-8")
    output_dir = tmp_path / "desktop-apps"

    assert (
        main(
            [
                "package",
                "macos-app",
                "--output-dir",
                str(output_dir),
                "--python-executable",
                sys.executable,
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "MACOS_APP_BUNDLE_READY"
    assert payload["app_path"] == str(output_dir / "Mucaro AI Setup.app")

    assert main(["package", "macos-app", "--verify", payload["app_path"], "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "MACOS_APP_BUNDLE_VERIFIED" in text_output
    assert "PASS launcher_points_to_native_start" in text_output


def test_cli_package_status_fails_on_version_mismatch(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path, frontend_version="0.1.99")

    assert main(["package", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "PACKAGE_FAIL"
    assert checks["version_consistency"]["status"] == "FAIL"
    assert checks["version_consistency"]["detail"] == "pyproject=0.1.23 frontend=0.1.99"


def test_cli_package_status_fails_on_stale_frontend_lockfile(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path, lock_version="0.1.22")

    assert main(["package", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "PACKAGE_FAIL"
    assert checks["frontend_lockfile"]["status"] == "FAIL"
    assert checks["frontend_lockfile"]["detail"] == (
        "package=mucaro-ai-frontend:0.1.22 "
        "root=mucaro-ai-frontend:0.1.22 "
        "expected=mucaro-ai-frontend:0.1.23"
    )


def commit_fixture_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=mucaro@example.test",
            "-c",
            "user.name=Mucaro Test",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def write_distribution_manifest_fixture_receipts(root: Path, head_sha: str) -> dict[str, Path]:
    artifacts_dir = root / "data" / "exports" / "distribution_fixture"
    artifacts_dir.mkdir(parents=True)
    artifacts = {
        "source_archive": artifacts_dir / "mucaro-ai-source.zip",
        "source_manifest": artifacts_dir / "mucaro-ai-artifact-manifest.json",
        "python_dist_manifest": artifacts_dir / "mucaro-ai-python-dist-manifest.json",
        "wheel": artifacts_dir / "mucaro_ai-0.1.23-py3-none-any.whl",
        "sdist": artifacts_dir / "mucaro_ai-0.1.23.tar.gz",
    }
    for name, path in artifacts.items():
        path.write_text(f"{name}\n", encoding="utf-8")

    write_release_receipt(
        root,
        receipt_type="local_release",
        status="CERTIFY_RELEASE_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_release.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        root,
        receipt_type="fresh_clone",
        status="FRESH_CLONE_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_fresh_clone.py", "--ref", head_sha],
        payload={"checks": []},
    )
    write_release_receipt(
        root,
        receipt_type="source_artifact",
        status="SOURCE_ARTIFACT_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_source_artifact.py", str(artifacts["source_manifest"])],
        payload={
            "archive_path": str(artifacts["source_archive"]),
            "manifest_path": str(artifacts["source_manifest"]),
        },
    )
    write_release_receipt(
        root,
        receipt_type="python_dist",
        status="PYTHON_DIST_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_python_distribution.py"],
        payload={
            "manifest": {"path": str(artifacts["python_dist_manifest"])},
            "wheel": {"path": str(artifacts["wheel"])},
            "sdist": {"path": str(artifacts["sdist"])},
        },
    )
    write_release_receipt(
        root,
        receipt_type="answer_trace_quality",
        status="ANSWER_TRACE_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_answer_trace_quality.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        root,
        receipt_type="retrieval_quality",
        status="RETRIEVAL_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_retrieval_quality.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        root,
        receipt_type="gis_quality",
        status="GIS_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_gis_quality.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        root,
        receipt_type="operator_workflow",
        status="OPERATOR_WORKFLOW_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_operator_workflow.py"],
        payload={"checks": []},
    )
    return artifacts


def test_cli_package_build_writes_source_artifact_manifest(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main(["package", "build", "--output-dir", str(output_dir), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "PACKAGE_ARTIFACT_READY"
    artifact = Path(payload["artifact"]["path"])
    manifest = Path(payload["manifest"]["path"])
    assert artifact.exists()
    assert manifest.exists()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["artifact_format"] == "source_zip_with_json_manifest_v1"
    assert manifest_payload["head_sha"]
    assert manifest_payload["member_count"] >= 1
    assert {member["path"] for member in manifest_payload["members"]} >= {"pyproject.toml", "README.md"}

    assert main(["package", "verify", str(manifest), "--require-current-head", "--format", "json"], cfg=cfg) == 0
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "PACKAGE_ARTIFACT_VERIFIED"
    assert verify_payload["manifest"]["head_sha"] == manifest_payload["head_sha"]
    assert verify_checks["current_head_binding"]["status"] == "PASS"
    assert verify_checks["archive_member_hashes"]["status"] == "PASS"


def test_cli_package_distribution_manifest_builds_and_verifies_current_head_handoff(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ).stdout.strip()
    artifacts = write_distribution_manifest_fixture_receipts(tmp_path, head_sha)

    assert main(["package", "distribution-manifest", "--save", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DISTRIBUTION_MANIFEST_READY"
    assert payload["scope"] == "local_distribution_handoff"
    assert payload["head_sha"] == head_sha
    assert payload["hosted_governance_status"] == "OPEN"
    assert payload["failed_checks"] == []
    assert payload["artifacts"]["wheel"]["sha256"] == hashlib.sha256(artifacts["wheel"].read_bytes()).hexdigest()
    assert set(payload["receipts"]) == {receipt_type for receipt_type, _ in packaging.REQUIRED_DISTRIBUTION_RECEIPTS}
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["receipt:operator_workflow"]["status"] == "PASS"
    assert checks["artifact:python_dist_manifest"]["status"] == "PASS"

    manifest_path = Path(payload["export"]["path"])
    expected_sha = payload["export"]["sha256"]
    assert manifest_path.exists()

    assert (
        main(
            [
                "package",
                "distribution-manifest",
                "--verify",
                str(manifest_path),
                "--expected-sha256",
                expected_sha,
                "--require-current-head",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "DISTRIBUTION_MANIFEST_VERIFIED"
    assert verify_payload["head_sha"] == head_sha
    assert verify_payload["hosted_governance_status"] == "OPEN"
    assert verify_checks["manifest_json"]["status"] == "PASS"
    assert verify_checks["current_head_binding"]["status"] == "PASS"
    assert verify_checks["receipt:local_release"]["status"] == "PASS"
    assert verify_checks["artifact:wheel"]["status"] == "PASS"

    assert main(["package", "distribution-manifest", "--verify", str(manifest_path), "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "DISTRIBUTION_MANIFEST_VERIFIED" in text_output
    assert "PASS artifact:wheel" in text_output
    assert "PASS receipt:operator_workflow" in text_output


def test_cli_package_distribution_manifest_fails_on_receipt_artifact_drift(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ).stdout.strip()
    artifacts = write_distribution_manifest_fixture_receipts(tmp_path, head_sha)

    assert main(["package", "distribution-manifest", "--save", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    manifest_path = Path(payload["export"]["path"])
    artifacts["wheel"].write_text("changed\n", encoding="utf-8")

    assert main(["package", "distribution-manifest", "--verify", str(manifest_path), "--format", "json"], cfg=cfg) == 2
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "DISTRIBUTION_MANIFEST_VERIFY_FAILED"
    assert verify_checks["artifact:wheel"]["status"] == "FAIL"
    assert verify_checks["receipt:local_release"]["status"] == "PASS"


def test_cli_package_verify_fails_on_archive_drift(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main(["package", "build", "--output-dir", str(output_dir), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    artifact = Path(payload["artifact"]["path"])
    manifest = Path(payload["manifest"]["path"])

    with zipfile.ZipFile(artifact, "a") as archive:
        archive.writestr("mucaro-ai-0.1.23/untracked.txt", "unexpected")

    assert main(["package", "verify", str(manifest), "--format", "json"], cfg=cfg) == 2
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "PACKAGE_ARTIFACT_VERIFY_FAILED"
    assert verify_checks["archive_sha256"]["status"] == "FAIL"
    assert verify_checks["archive_member_set"]["status"] == "FAIL"


def test_cli_package_verify_fails_on_malformed_manifest_members(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main(["package", "build", "--output-dir", str(output_dir), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    manifest = Path(payload["manifest"]["path"])
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["members"] = {}
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")

    assert main(["package", "verify", str(manifest), "--format", "json"], cfg=cfg) == 2
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "PACKAGE_ARTIFACT_VERIFY_FAILED"
    assert verify_checks["manifest_members"]["status"] == "FAIL"


def test_cli_package_verify_fails_on_duplicate_archive_members(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main(["package", "build", "--output-dir", str(output_dir), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    artifact = Path(payload["artifact"]["path"])
    manifest = Path(payload["manifest"]["path"])

    with zipfile.ZipFile(artifact, "a") as archive:
        archive.writestr("mucaro-ai-0.1.23/README.md", "# Duplicate member\n")

    assert main(["package", "verify", str(manifest), "--format", "json"], cfg=cfg) == 2
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "PACKAGE_ARTIFACT_VERIFY_FAILED"
    assert verify_checks["archive_member_duplicates"]["status"] == "FAIL"
    assert verify_checks["archive_member_set"]["status"] == "FAIL"


def test_cli_package_build_blocks_dirty_worktree(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    (tmp_path / "README.md").write_text("# Dirty Mucaro AI\n", encoding="utf-8")

    assert main(["package", "build", "--output-dir", str(tmp_path / "artifacts"), "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "PACKAGE_ARTIFACT_BLOCKED"
    assert checks["worktree_clean"]["status"] == "FAIL"


def test_cli_certify_status_reports_release_readiness(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["certify", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "CERTIFICATION_FAIL"
    assert payload["version"] == "0.1.23"
    assert payload["project_root"] == str(tmp_path)
    assert payload["product_audit_status"] == "PRODUCT_AUDIT_OPEN"
    assert payload["json_status"] == "JSON_OK"
    assert payload["github"] == {"enabled": False}
    assert payload["git"]["head_sha"] is None
    gates = {gate["name"]: gate for gate in payload["gates"]}
    assert gates["product_audit"]["status"] == "FAIL"
    assert gates["product_audit"]["detail"] == "PRODUCT_AUDIT_OPEN"
    assert gates["json_validation"]["status"] == "PASS"
    assert gates["git_repository"]["status"] == "WARN"
    assert gates["baseline_tag"]["status"] == "WARN"
    assert payload["next_commands"] == [
        "python3 scripts/certify_release.py",
        "python3 scripts/certify_fresh_clone.py --ref main",
    ]

    assert main(["certify", "status", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "CERTIFICATION_FAIL" in strict_output
    assert "FAIL product_audit PRODUCT_AUDIT_OPEN" in strict_output
    assert "WARN git_repository git metadata unavailable" in strict_output
    assert "command=python3 scripts/certify_release.py" in strict_output


def test_cli_certify_status_includes_github_governance(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40
    tag_name = "v0.1.23-governance-status"

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, tag_name
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args[:3] == ["git", "ls-remote", "origin"]:
            return True, f"{'b' * 40}\trefs/tags/{tag_name}\n{head_sha}\trefs/tags/{tag_name}^{{}}"
        if args[:3] == ["gh", "api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, json.dumps({"protected": False, "protection": {"required_status_checks": {"contexts": []}}})
        if args[:3] == ["gh", "api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, json.dumps({"message": "Upgrade to GitHub Pro or make this repository public", "status": "403"})
        if args[:7] == ["gh", "run", "list", "--repo", "jotaele44/mucaro-ai", "--workflow", "CI"]:
            return True, json.dumps(
                [
                    {
                        "databaseId": 1,
                        "status": "completed",
                        "conclusion": "success",
                        "headSha": head_sha,
                        "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/1",
                        "name": "CI",
                        "event": "push",
                        "createdAt": "2026-09-02T00:00:00Z",
                        "updatedAt": "2026-09-02T00:00:30Z",
                    }
                ]
            )
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["certify", "status", "--include-github", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    gates = {gate["name"]: gate for gate in payload["gates"]}
    assert payload["status"] == "CERTIFICATION_FAIL"
    assert payload["product_audit_status"] == "PRODUCT_AUDIT_OPEN"
    assert gates["product_audit"]["status"] == "FAIL"
    assert payload["github"]["enabled"] is True
    assert payload["github"]["repo"] == "jotaele44/mucaro-ai"
    assert payload["github"]["workflow"] == "CI"
    assert payload["github"]["branch_protected"] is False
    assert payload["github"]["latest_ci"]["head_sha"] == head_sha
    assert gates["github_repo"]["status"] == "PASS"
    assert gates["github_branch_protection"]["status"] == "WARN"
    assert gates["github_branch_protection_api"]["status"] == "WARN"
    assert gates["github_latest_ci"]["status"] == "PASS"
    assert gates["github_tag_binding"]["status"] == "PASS"

    assert main(["certify", "status", "--include-github", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "github_repo=jotaele44/mucaro-ai" in text_output
    assert "github_branch_protected=False" in text_output
    assert "github_workflow=CI" in text_output
    assert f"PASS github_latest_ci success {head_sha}" in text_output


def test_cli_release_state_reports_github_governance_open(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    head_sha = "c" * 40

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, ""
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args[:3] == ["gh", "api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, json.dumps({"protected": False, "protection": {"required_status_checks": {"contexts": []}}})
        if args[:3] == ["gh", "api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, json.dumps({"message": "Upgrade to GitHub Pro or make this repository public", "status": "403"})
        if args[:7] == ["gh", "run", "list", "--repo", "jotaele44/mucaro-ai", "--workflow", "CI"]:
            return True, json.dumps(
                [
                    {
                        "databaseId": 7,
                        "status": "completed",
                        "conclusion": "startup_failure",
                        "headSha": head_sha,
                        "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/7",
                        "name": "CI",
                        "event": "workflow_dispatch",
                        "createdAt": "2026-09-05T00:00:00Z",
                        "updatedAt": "2026-09-05T00:00:01Z",
                    }
                ]
            )
        if args == ["gh", "api", "repos/jotaele44/mucaro-ai/actions/runs/7/jobs"]:
            return True, json.dumps({"total_count": 0, "jobs": []})
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "state", "--include-github", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_OPEN"
    assert payload["current_state"] == "GITHUB_GOVERNANCE_OPEN"
    assert payload["highest_local_state"] == "CANONICAL_LOCAL"
    assert payload["product_audit_status"] == "PRODUCT_AUDIT_OPEN"
    assert payload["package_status"] == "PACKAGE_WARN"
    assert states["CANONICAL_LOCAL"]["status"] == "PASS"
    assert states["LOCAL_RELEASE_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["FRESH_CLONE_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["SOURCE_ARTIFACT_PACKAGING"]["status"] == "PASS"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["PYTHON_DIST_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["ANSWER_TRACE_QUALITY_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GITHUB_GOVERNANCE_OPEN"]["status"] == "OPEN"
    assert "github_hosted_runner_startup=FAIL" in states["GITHUB_GOVERNANCE_OPEN"]["evidence"]
    assert "github_branch_protection_api=WARN:HTTP 403: Upgrade to GitHub Pro" in states["GITHUB_GOVERNANCE_OPEN"]["evidence"]
    assert states["GOVERNED_RELEASE_READY"]["status"] == "OPEN"

    assert main(["release", "state", "--include-github", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "RELEASE_STATE_OPEN" in text_output
    assert "current_state=GITHUB_GOVERNANCE_OPEN" in text_output
    assert "OPEN GITHUB_GOVERNANCE_OPEN" in text_output


def test_cli_release_state_uses_exact_head_certification_receipts(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    head_sha = "d" * 40

    write_release_receipt(
        tmp_path,
        receipt_type="local_release",
        status="CERTIFY_RELEASE_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_release.py"],
        payload={"gates": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="fresh_clone",
        status="FRESH_CLONE_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_fresh_clone.py", "--ref", head_sha],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="source_artifact",
        status="SOURCE_ARTIFACT_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_source_artifact.py", "manifest.json"],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="python_dist",
        status="PYTHON_DIST_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_python_distribution.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="answer_trace_quality",
        status="ANSWER_TRACE_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_answer_trace_quality.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="retrieval_quality",
        status="RETRIEVAL_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_retrieval_quality.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="gis_quality",
        status="GIS_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_gis_quality.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="operator_workflow",
        status="OPERATOR_WORKFLOW_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_operator_workflow.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="fresh_clone",
        status="FRESH_CLONE_CERT_OK",
        head_sha="e" * 40,
        command=["python3", "scripts/certify_fresh_clone.py", "--ref", "e" * 40],
        payload={"checks": ["stale"]},
    )

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, ""
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "state", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert states["LOCAL_RELEASE_CERTIFIED"]["status"] == "PASS"
    assert states["FRESH_CLONE_CERTIFIED"]["status"] == "PASS"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "PASS"
    assert states["PYTHON_DIST_CERTIFIED"]["status"] == "PASS"
    assert states["ANSWER_TRACE_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["RETRIEVAL_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["GIS_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["OPERATOR_WORKFLOW_CERTIFIED"]["status"] == "PASS"
    assert payload["receipts"]["local_release"]["head_sha"] == head_sha
    assert payload["receipts"]["fresh_clone"]["head_sha"] == head_sha
    assert payload["receipts"]["source_artifact"]["head_sha"] == head_sha
    assert payload["receipts"]["python_dist"]["head_sha"] == head_sha
    assert payload["receipts"]["answer_trace_quality"]["head_sha"] == head_sha
    assert payload["receipts"]["retrieval_quality"]["head_sha"] == head_sha
    assert payload["receipts"]["gis_quality"]["head_sha"] == head_sha
    assert payload["receipts"]["operator_workflow"]["head_sha"] == head_sha


def test_cli_release_governance_report_summarizes_current_head_handoff(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    head_sha = "9" * 40
    tag_name = "v0.1.23-structured-startup-blocker"

    for receipt_type, status in [
        ("local_release", "CERTIFY_RELEASE_OK"),
        ("fresh_clone", "FRESH_CLONE_CERT_OK"),
        ("source_artifact", "SOURCE_ARTIFACT_CERT_OK"),
        ("python_dist", "PYTHON_DIST_CERT_OK"),
        ("answer_trace_quality", "ANSWER_TRACE_QUALITY_CERT_OK"),
        ("retrieval_quality", "RETRIEVAL_QUALITY_CERT_OK"),
        ("gis_quality", "GIS_QUALITY_CERT_OK"),
        ("operator_workflow", "OPERATOR_WORKFLOW_CERT_OK"),
    ]:
        write_release_receipt(
            tmp_path,
            receipt_type=receipt_type,
            status=status,
            head_sha=head_sha,
            command=["mucaro", "test"],
            payload={"checks": []},
        )

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, tag_name
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args[:3] == ["git", "ls-remote", "origin"]:
            return True, f"{'a' * 40}\trefs/tags/{tag_name}\n{head_sha}\trefs/tags/{tag_name}^{{}}"
        return False, f"unexpected command: {args}"

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args == ["api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, {"protected": False, "protection": {"required_status_checks": {"contexts": []}}}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, None, "HTTP 403: Upgrade to GitHub Pro or make this repository public"
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions"]:
            return True, {"enabled": True, "allowed_actions": "all"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions/workflow"]:
            return True, {"default_workflow_permissions": "read"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runners"]:
            return True, {"total_count": 0, "runners": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/workflows"]:
            return True, {
                "workflows": [
                    {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
                    {"id": 2, "name": "CI Startup Probe", "path": ".github/workflows/ci-startup-probe.yml", "state": "active"},
                ]
            }, ""
        if args[:2] == ["run", "list"] and "CI Startup Probe" in args:
            return True, [{"databaseId": 22, "status": "completed", "conclusion": "startup_failure", "headSha": head_sha}], ""
        if args[:2] == ["run", "list"] and "CI" in args:
            return True, [{"databaseId": 11, "status": "completed", "conclusion": "startup_failure", "headSha": head_sha}], ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/11/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/22/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", f"repos/jotaele44/mucaro-ai/commits/{head_sha}/check-suites"]:
            return True, {
                "total_count": 2,
                "check_suites": [
                    {
                        "id": 33,
                        "app": {"slug": "github-actions"},
                        "status": "completed",
                        "conclusion": "startup_failure",
                        "latest_check_runs_count": 0,
                        "url": "https://api.github.com/repos/jotaele44/mucaro-ai/check-suites/33",
                        "check_runs_url": "https://api.github.com/repos/jotaele44/mucaro-ai/check-suites/33/check-runs",
                        "created_at": "2026-09-05T00:00:00Z",
                        "updated_at": "2026-09-05T00:00:01Z",
                    },
                    {
                        "id": 34,
                        "app": {"slug": "github-actions"},
                        "status": "completed",
                        "conclusion": "startup_failure",
                        "latest_check_runs_count": 0,
                        "url": "https://api.github.com/repos/jotaele44/mucaro-ai/check-suites/34",
                        "check_runs_url": "https://api.github.com/repos/jotaele44/mucaro-ai/check-suites/34/check-runs",
                        "created_at": "2026-09-05T00:00:00Z",
                        "updated_at": "2026-09-05T00:00:01Z",
                    },
                ],
            }, ""
        if args == ["api", "users/jotaele44/settings/billing/actions"]:
            return False, None, 'HTTP 404: Not Found; This API operation needs the "user" scope.'
        return False, None, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)

    assert main(["release", "governance-report", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "GOVERNANCE_REPORT_OPEN"
    assert payload["current_state"] == "GITHUB_GOVERNANCE_OPEN"
    assert payload["head_sha"] == head_sha
    assert payload["tags_at_head"] == [tag_name]
    assert payload["current_head_receipts_summary"]["total"] == 8
    assert payload["current_head_receipts_summary"]["valid"] == 8
    assert payload["startup_blocker"]["status"] == "FAIL"
    assert payload["github_actions"]["check_suites"]["total_count"] == 2
    assert payload["github_actions"]["check_suites"]["suites"][0]["latest_check_runs_count"] == 0
    assert payload["github_support_summary"]["classification"] == "HOSTED_STARTUP_BEFORE_JOBS"
    assert payload["github_support_summary"]["actions_enabled"] is True
    assert payload["github_support_summary"]["allowed_actions"] == "all"
    assert payload["github_support_summary"]["default_workflow_permissions"] == "read"
    assert payload["github_support_summary"]["billing_required_scope"] == "user"
    assert payload["github_support_summary"]["check_suite_counts"] == {
        "total": 2,
        "github_actions": 2,
        "github_actions_zero_check_run_startup_failures": 2,
        "queued_external": 0,
    }
    assert payload["startup_blocker"]["isolated_before_jobs"] is True
    assert "private_branch_protection_plan_gate" in payload["startup_blocker"]["suspected_causes"]
    assert "github_branch_protection_api=WARN:HTTP 403" in payload["github_governance_evidence"]

    assert main(["release", "governance-report", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "GOVERNANCE_REPORT_OPEN" in text_output
    assert f"head_sha={head_sha}" in text_output
    assert "startup_blocker_status=FAIL" in text_output
    assert "startup_blocker_causes=" in text_output
    assert "github_support_classification=HOSTED_STARTUP_BEFORE_JOBS" in text_output
    assert "github_support_check_suite_counts=github_actions=2 zero_check_run_startup_failures=2 queued_external=0" in text_output
    assert "receipt_count=8" in text_output
    assert "receipt_valid=8" in text_output
    assert "next_action=Upgrade GitHub plan or make the repository public" in text_output

    output_path = tmp_path / "data" / "exports" / "governance_reports" / "handoff.json"
    assert main(["release", "governance-report", "--save", "--output", str(output_path), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["export"]["path"] == str(output_path)
    assert payload["export"]["sha256"]
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "mucaro_governance_report_v1"
    assert saved["report"]["head_sha"] == head_sha
    assert saved["report"]["startup_blocker"]["status"] == "FAIL"


def test_cli_release_governance_report_verifies_saved_artifact(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    head_sha = "9" * 40

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, "v0.1.23-governance-report-export"
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args[:3] == ["git", "ls-remote", "origin"]:
            return True, f"{head_sha}\trefs/tags/v0.1.23-governance-report-export^{{}}"
        return False, f"unexpected command: {args}"

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args == ["api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, {"protected": False, "protection": {"required_status_checks": {"contexts": []}}}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, None, "HTTP 403: Upgrade to GitHub Pro or make this repository public"
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions"]:
            return True, {"enabled": True, "allowed_actions": "all"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions/workflow"]:
            return True, {"default_workflow_permissions": "read"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runners"]:
            return True, {"total_count": 0, "runners": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/workflows"]:
            return True, {"workflows": []}, ""
        if args[:2] == ["run", "list"]:
            return True, [], ""
        if args == ["api", "users/jotaele44/settings/billing/actions"]:
            return False, None, 'HTTP 404: Not Found; This API operation needs the "user" scope.'
        if args == ["api", f"repos/jotaele44/mucaro-ai/commits/{head_sha}/check-suites"]:
            return True, {"total_count": 0, "check_suites": []}, ""
        return False, None, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)

    output_path = tmp_path / "data" / "exports" / "governance_reports" / "handoff.json"
    assert main(["release", "governance-report", "--save", "--output", str(output_path), "--format", "json"], cfg=cfg) == 0
    saved_payload = json.loads(capsys.readouterr().out)
    expected_sha256 = saved_payload["export"]["sha256"]

    assert (
        main(
            [
                "release",
                "governance-report",
                "--verify",
                str(output_path),
                "--require-current-head",
                "--expected-sha256",
                expected_sha256,
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "GOVERNANCE_REPORT_VERIFIED"
    assert payload["artifact"]["sha256"] == expected_sha256
    assert payload["head_sha"] == head_sha
    assert checks["schema_version"]["status"] == "PASS"
    assert checks["report_head_sha"]["status"] == "PASS"
    assert checks["current_head_binding"]["status"] == "PASS"
    assert checks["expected_sha256"]["status"] == "PASS"

    assert (
        main(
            [
                "release",
                "governance-report",
                "--verify",
                str(output_path),
                "--expected-sha256",
                "0" * 64,
                "--format",
                "text",
            ],
            cfg=cfg,
        )
        == 2
    )
    text_output = capsys.readouterr().out
    assert "GOVERNANCE_REPORT_VERIFY_FAILED" in text_output
    assert "FAIL expected_sha256" in text_output


def test_cli_release_governance_report_verify_fails_current_head_mismatch(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    artifact = {
        "schema_version": "mucaro_governance_report_v1",
        "created_utc": "2026-09-05T20:00:00Z",
        "report": {
            "status": "GOVERNANCE_REPORT_OPEN",
            "current_state": "GITHUB_GOVERNANCE_OPEN",
            "head_sha": "1" * 40,
            "current_head_receipts_summary": {},
            "startup_blocker": {},
        },
    }
    output_path = tmp_path / "governance-report.json"
    output_path.write_text(json.dumps(artifact), encoding="utf-8")

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, "2" * 40
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "governance-report", "--verify", str(output_path), "--require-current-head", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "GOVERNANCE_REPORT_VERIFY_FAILED"
    assert checks["current_head_binding"]["status"] == "FAIL"


def write_dashboard_governance_report_fixture(root: Path, head_sha: str) -> Path:
    path = root / "data" / "exports" / "governance_reports" / f"governance-report-{head_sha[:12]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "schema_version": "mucaro_governance_report_v1",
        "created_utc": "2026-09-07T00:00:00Z",
        "report": {
            "status": "GOVERNANCE_REPORT_OPEN",
            "current_state": "GITHUB_GOVERNANCE_OPEN",
            "head_sha": head_sha,
            "current_head_receipts_summary": {"total": 8, "valid": 8, "invalid": 0},
            "startup_blocker": {"status": "FAIL"},
        },
    }
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


def test_cli_release_dashboard_summarizes_current_handoff_artifacts(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "tag", "v0.1.23-dashboard"], cwd=tmp_path, check=True)
    write_distribution_manifest_fixture_receipts(tmp_path, head_sha)
    assert main(["package", "distribution-manifest", "--save", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()
    write_dashboard_governance_report_fixture(tmp_path, head_sha)

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "post-merge-local-branch"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, "v0.1.23-dashboard"
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args == ["git", "ls-remote", "origin", "refs/heads/main"]:
            return True, f"{head_sha}\trefs/heads/main"
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "dashboard", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "RELEASE_DASHBOARD_OPEN"
    assert payload["head_sha"] == head_sha
    assert payload["distribution_manifest_status"] == "DISTRIBUTION_MANIFEST_VERIFIED"
    assert payload["governance_report_status"] == "GOVERNANCE_REPORT_VERIFIED"
    assert payload["remote_branch_status"] == "PASS"
    assert payload["current_head_receipts_summary"]["total"] == 8
    assert checks["current_head_receipts"]["status"] == "PASS"
    assert checks["distribution_manifest"]["status"] == "PASS"
    assert checks["governance_report"]["status"] == "PASS"
    assert checks["release_state"]["status"] == "OPEN"

    assert main(["release", "dashboard", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "RELEASE_DASHBOARD_OPEN" in text_output
    assert "distribution_manifest_status=DISTRIBUTION_MANIFEST_VERIFIED" in text_output
    assert "governance_report_status=GOVERNANCE_REPORT_VERIFIED" in text_output
    assert "OPEN release_state" in text_output


def test_cli_release_dashboard_fails_without_distribution_manifest(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ).stdout.strip()
    write_distribution_manifest_fixture_receipts(tmp_path, head_sha)
    write_dashboard_governance_report_fixture(tmp_path, head_sha)

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return False, "no tags"
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args == ["git", "ls-remote", "origin", "refs/heads/main"]:
            return True, f"{head_sha}\trefs/heads/main"
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "dashboard", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "RELEASE_DASHBOARD_FAIL"
    assert payload["distribution_manifest_status"] == "DISTRIBUTION_MANIFEST_MISSING"
    assert checks["distribution_manifest"]["status"] == "FAIL"
    assert checks["current_head_receipts"]["status"] == "PASS"


def test_cli_release_support_bundle_saves_and_verifies_support_artifact(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "8" * 40

    def fake_dashboard(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert include_github is True
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        return {
            "status": "RELEASE_DASHBOARD_OPEN",
            "head_sha": head_sha,
            "hosted_governance_status": "OPEN",
            "github_actions_status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
            "github_actions": {
                "status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
                "startup_blocker": {"status": "FAIL", "suspected_causes": ["hosted_runner_availability"]},
                "support_summary": {
                    "classification": "HOSTED_STARTUP_BEFORE_JOBS",
                    "ci_run_id": 123,
                    "startup_probe_run_id": 456,
                },
                "next_actions": ["Inspect Actions runners."],
            },
            "blockers": ["github_actions=OPEN:GITHUB_ACTIONS_DIAGNOSTIC_FAIL"],
        }

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "build_release_dashboard_report", fake_dashboard)
    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    write_dashboard_governance_report_fixture(tmp_path, head_sha)

    output_path = tmp_path / "data" / "exports" / "support_bundles" / "support.json"
    assert main(["release", "support-bundle", "--save", "--output", str(output_path), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "SUPPORT_BUNDLE_READY"
    assert payload["case_status"] == "HOSTED_GOVERNANCE_OPEN"
    assert payload["support_classification"] == "HOSTED_STARTUP_BEFORE_JOBS"
    assert payload["export"]["path"] == str(output_path)
    expected_sha = payload["export"]["sha256"]
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "mucaro_release_support_bundle_v1"
    assert saved["bundle"]["head_sha"] == head_sha

    assert (
        main(
            [
                "release",
                "support-bundle",
                "--verify",
                str(output_path),
                "--expected-sha256",
                expected_sha,
                "--require-current-head",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    verify_payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "SUPPORT_BUNDLE_VERIFIED"
    assert verify_payload["head_sha"] == head_sha
    assert verify_payload["case_status"] == "HOSTED_GOVERNANCE_OPEN"
    assert checks["expected_sha256"]["status"] == "PASS"
    assert checks["current_head_binding"]["status"] == "PASS"

    assert main(["release", "support-bundle", "--verify", str(output_path), "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "SUPPORT_BUNDLE_VERIFIED" in text_output
    assert "case_status=HOSTED_GOVERNANCE_OPEN" in text_output
    assert "support_classification=HOSTED_STARTUP_BEFORE_JOBS" in text_output


def test_cli_release_support_bundle_save_creates_missing_governance_report(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "9" * 40
    governance_path = tmp_path / "data" / "exports" / "governance_reports" / f"governance-report-{head_sha[:12]}.json"

    def fake_governance_report(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        return {
            "status": "GOVERNANCE_REPORT_OPEN",
            "current_state": "GITHUB_GOVERNANCE_OPEN",
            "head_sha": head_sha,
            "current_head_receipts_summary": {"total": 8, "valid": 8, "invalid": 0},
            "startup_blocker": {"status": "FAIL"},
        }

    def fake_dashboard(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert include_github is True
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        assert governance_path.exists()
        return {
            "status": "RELEASE_DASHBOARD_OPEN",
            "head_sha": head_sha,
            "hosted_governance_status": "OPEN",
            "github_actions_status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
            "github_actions": {
                "status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
                "startup_blocker": {"status": "FAIL"},
                "support_summary": {"classification": "HOSTED_STARTUP_BEFORE_JOBS"},
                "next_actions": ["Inspect Actions runners."],
            },
            "blockers": ["github_actions=OPEN:GITHUB_ACTIONS_DIAGNOSTIC_FAIL"],
        }

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "build_release_governance_report", fake_governance_report)
    monkeypatch.setattr(cli, "build_release_dashboard_report", fake_dashboard)
    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    output_path = tmp_path / "data" / "exports" / "support_bundles" / "support.json"
    assert main(["release", "support-bundle", "--save", "--output", str(output_path), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    saved_governance = json.loads(governance_path.read_text(encoding="utf-8"))

    assert payload["status"] == "SUPPORT_BUNDLE_READY"
    assert payload["governance_report"]["status"] == "GOVERNANCE_REPORT_READY_FOR_SUPPORT"
    assert payload["governance_report"]["created"] is True
    assert checks["governance_report_ready"]["status"] == "PASS"
    assert saved_governance["schema_version"] == "mucaro_governance_report_v1"
    assert saved_governance["report"]["head_sha"] == head_sha


def test_cli_release_support_bundle_verify_fails_current_head_mismatch(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    artifact = {
        "schema_version": "mucaro_release_support_bundle_v1",
        "created_utc": "2026-09-07T00:00:00Z",
        "bundle": {
            "status": "SUPPORT_BUNDLE_READY",
            "head_sha": "1" * 40,
            "case_status": "HOSTED_GOVERNANCE_OPEN",
            "dashboard_status": "RELEASE_DASHBOARD_OPEN",
            "dashboard": {},
        },
    }
    output_path = tmp_path / "support-bundle.json"
    output_path.write_text(json.dumps(artifact), encoding="utf-8")

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, "2" * 40
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "support-bundle", "--verify", str(output_path), "--require-current-head", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "SUPPORT_BUNDLE_VERIFY_FAILED"
    assert checks["current_head_binding"]["status"] == "FAIL"


def test_cli_release_state_requires_exact_head_quality_receipts_for_governed_ready(
    tmp_path: Path,
    capsys,
    monkeypatch,
):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40

    def fake_product_audit(_cfg: Settings):
        return {
            "status": "PRODUCT_AUDIT_READY",
            "requirements": [
                {"requirement_id": "source_artifact_packaging", "status": "PASS"},
                {"requirement_id": "github_release_governance", "status": "PASS"},
            ],
        }

    def fake_package_status(_root: Path):
        return {"status": "PACKAGE_READY"}

    def fake_certification_status(_cfg: Settings, *, include_github: bool = False, github_workflow: str = "CI"):
        del include_github, github_workflow
        return {
            "status": "CERTIFICATION_READY",
            "git": {"head_sha": head_sha},
            "gates": [
                {"name": "git_repository", "status": "PASS", "detail": "ok"},
                {"name": "worktree_clean", "status": "PASS", "detail": "clean"},
                {"name": "upstream", "status": "PASS", "detail": "origin/main"},
                {"name": "github_latest_ci", "status": "PASS", "detail": "success"},
                {"name": "github_branch_protection", "status": "PASS", "detail": "protected"},
                {"name": "github_tag_binding", "status": "PASS", "detail": "bound"},
            ],
        }

    monkeypatch.setattr(cli, "build_product_audit", fake_product_audit)
    monkeypatch.setattr(cli, "build_package_status", fake_package_status)
    monkeypatch.setattr(cli, "build_certification_status", fake_certification_status)

    assert main(["release", "state", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_OPEN"
    assert payload["current_state"] == "SOURCE_ARTIFACT_CERTIFICATION_OPEN"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GOVERNED_RELEASE_READY"]["status"] == "OPEN"

    write_release_receipt(
        tmp_path,
        receipt_type="source_artifact",
        status="SOURCE_ARTIFACT_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_source_artifact.py", "manifest.json"],
        payload={"checks": []},
    )

    assert main(["release", "state", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_OPEN"
    assert payload["current_state"] == "ANSWER_TRACE_QUALITY_CERTIFICATION_OPEN"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "PASS"
    assert states["ANSWER_TRACE_QUALITY_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["RETRIEVAL_QUALITY_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GIS_QUALITY_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GOVERNED_RELEASE_READY"]["status"] == "OPEN"

    write_release_receipt(
        tmp_path,
        receipt_type="answer_trace_quality",
        status="ANSWER_TRACE_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_answer_trace_quality.py"],
        payload={"checks": []},
    )

    assert main(["release", "state", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_OPEN"
    assert payload["current_state"] == "RETRIEVAL_QUALITY_CERTIFICATION_OPEN"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "PASS"
    assert states["ANSWER_TRACE_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["RETRIEVAL_QUALITY_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GIS_QUALITY_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GOVERNED_RELEASE_READY"]["status"] == "OPEN"

    write_release_receipt(
        tmp_path,
        receipt_type="retrieval_quality",
        status="RETRIEVAL_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_retrieval_quality.py"],
        payload={"checks": []},
    )

    assert main(["release", "state", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_OPEN"
    assert payload["current_state"] == "GIS_QUALITY_CERTIFICATION_OPEN"
    assert states["RETRIEVAL_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["GIS_QUALITY_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["OPERATOR_WORKFLOW_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GOVERNED_RELEASE_READY"]["status"] == "OPEN"

    write_release_receipt(
        tmp_path,
        receipt_type="gis_quality",
        status="GIS_QUALITY_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_gis_quality.py"],
        payload={"checks": []},
    )

    assert main(["release", "state", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_OPEN"
    assert payload["current_state"] == "OPERATOR_WORKFLOW_CERTIFICATION_OPEN"
    assert states["GIS_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["OPERATOR_WORKFLOW_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GOVERNED_RELEASE_READY"]["status"] == "OPEN"

    write_release_receipt(
        tmp_path,
        receipt_type="operator_workflow",
        status="OPERATOR_WORKFLOW_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_operator_workflow.py"],
        payload={"checks": []},
    )

    assert main(["release", "state", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_READY"
    assert payload["current_state"] == "GOVERNED_RELEASE_READY"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "PASS"
    assert states["ANSWER_TRACE_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["RETRIEVAL_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["GIS_QUALITY_CERTIFIED"]["status"] == "PASS"
    assert states["OPERATOR_WORKFLOW_CERTIFIED"]["status"] == "PASS"
    assert states["GOVERNED_RELEASE_READY"]["status"] == "PASS"


def test_cli_audit_completion_reports_hosted_governance_open(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40

    product_requirements = [
        "installable_cli",
        "readiness_diagnostics",
        "permission_gated_user_directory",
        "document_ingestion",
        "source_lineage_citations",
        "local_retrieval_strategies",
        "semantic_vector_store",
        "personalization_memory",
        "tool_use_and_actions",
        "source_artifact_packaging",
    ]
    advanced_requirements = [
        "advanced_gis_processing",
        "state_of_art_ocr_analysis",
        "governed_web_browsing",
        "coding_programming_expertise",
        "transparency_objectivity_governance",
    ]

    def fake_product_audit(_cfg: Settings):
        return {
            "status": "PRODUCT_AUDIT_OPEN",
            "objective": "test objective",
            "requirements": [
                {
                    "requirement_id": requirement_id,
                    "status": "PASS",
                    "evidence": f"{requirement_id} evidence",
                    "gap": "",
                    "next_step": f"{requirement_id} next",
                }
                for requirement_id in product_requirements
            ] + [
                {
                    "requirement_id": requirement_id,
                    "requirement": f"{requirement_id} requirement",
                    "status": "OPEN",
                    "evidence": f"{requirement_id} evidence",
                    "gap": f"{requirement_id} gap",
                    "next_step": f"{requirement_id} next",
                }
                for requirement_id in advanced_requirements
            ],
        }

    def fake_release_state(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
    ):
        del github_workflow
        assert include_github is True
        return {
            "status": "RELEASE_STATE_OPEN",
            "current_state": "GITHUB_GOVERNANCE_OPEN",
            "highest_local_state": "CANONICAL_LOCAL",
            "git": {"head_sha": head_sha, "upstream": "origin/main", "worktree_clean": True},
            "states": [
                {"state": "CANONICAL_LOCAL", "status": "PASS", "evidence": "canonical local", "next_step": ""},
                {"state": "LOCAL_RELEASE_CERTIFIED", "status": "PASS", "evidence": "local release", "next_step": ""},
                {"state": "FRESH_CLONE_CERTIFIED", "status": "PASS", "evidence": "fresh clone", "next_step": ""},
                {"state": "SOURCE_ARTIFACT_PACKAGING", "status": "PASS", "evidence": "package", "next_step": ""},
                {"state": "SOURCE_ARTIFACT_CERTIFIED", "status": "PASS", "evidence": "source artifact", "next_step": ""},
                {"state": "PYTHON_DIST_CERTIFIED", "status": "PASS", "evidence": "python dist", "next_step": ""},
                {"state": "ANSWER_TRACE_QUALITY_CERTIFIED", "status": "PASS", "evidence": "answer trace", "next_step": ""},
                {"state": "RETRIEVAL_QUALITY_CERTIFIED", "status": "PASS", "evidence": "retrieval", "next_step": ""},
                {"state": "GIS_QUALITY_CERTIFIED", "status": "PASS", "evidence": "gis", "next_step": ""},
                {"state": "OPERATOR_WORKFLOW_CERTIFIED", "status": "PASS", "evidence": "operator", "next_step": ""},
                {"state": "GITHUB_GOVERNANCE_OPEN", "status": "OPEN", "evidence": "startup failure", "next_step": ""},
                {"state": "GOVERNED_RELEASE_READY", "status": "OPEN", "evidence": "not governed", "next_step": ""},
            ],
            "next_commands": ["mucaro certify github-actions --format text"],
        }

    def fake_dashboard(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        del github_workflow, probe_workflow
        assert include_github is True
        return {
            "status": "RELEASE_DASHBOARD_OPEN",
            "remote_branch_status": "PASS",
            "distribution_manifest_status": "DISTRIBUTION_MANIFEST_VERIFIED",
            "governance_report_status": "GOVERNANCE_REPORT_VERIFIED",
            "hosted_governance_status": "OPEN",
            "github_actions": {
                "support_summary": {
                    "classification": "HOSTED_STARTUP_BEFORE_JOBS",
                    "current_head_startup_run_ids": [31, 32],
                    "check_suite_counts": {
                        "github_actions": 1,
                        "github_actions_zero_check_run_startup_failures": 1,
                        "queued_external": 0,
                    },
                },
                "startup_blocker": {
                    "suspected_causes": [
                        "actions_billing_or_spending_limit",
                        "hosted_runner_availability",
                    ],
                },
            },
            "current_head_receipts_summary": {"total": 8, "valid": 8, "invalid": 0},
            "next_commands": ["mucaro release dashboard --include-github --format text"],
        }

    monkeypatch.setattr(cli, "build_product_audit", fake_product_audit)
    monkeypatch.setattr(cli, "build_release_state_report", fake_release_state)
    monkeypatch.setattr(cli, "build_release_dashboard_report", fake_dashboard)

    assert main(["audit", "completion", "--include-github", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    requirements = {row["requirement_id"]: row for row in payload["requirements"]}
    assert payload["status"] == "COMPLETION_AUDIT_OPEN"
    assert payload["counts"] == {"pass": 9, "open": 6, "blocked": 0, "fail": 0, "unverified": 0, "total": 15}
    assert requirements["advanced_gis_processing"]["status"] == "OPEN"
    assert requirements["transparency_objectivity_governance"]["status"] == "OPEN"
    assert requirements["hosted_governance"]["status"] == "OPEN"
    assert "support_classification=HOSTED_STARTUP_BEFORE_JOBS" in requirements["hosted_governance"]["evidence"]
    assert "current_head_startup_run_ids=31,32" in requirements["hosted_governance"]["evidence"]
    assert "zero_check_run_startup_failures:1" in requirements["hosted_governance"]["evidence"]
    assert "suspected_causes=actions_billing_or_spending_limit,hosted_runner_availability" in requirements["hosted_governance"]["evidence"]

    assert main(["audit", "completion", "--include-github", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "counts=pass:9 open:6 blocked:0 fail:0 unverified:0 total:15" in strict_output
    assert "OPEN advanced_gis_processing" in strict_output
    assert "OPEN hosted_governance" in strict_output


def test_cli_audit_completion_is_ready_when_release_grade_gates_pass(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "b" * 40

    def fake_product_audit(_cfg: Settings):
        requirement_ids = [
            "installable_cli",
            "readiness_diagnostics",
            "permission_gated_user_directory",
            "document_ingestion",
            "source_lineage_citations",
            "local_retrieval_strategies",
            "semantic_vector_store",
            "personalization_memory",
            "tool_use_and_actions",
            "source_artifact_packaging",
            "advanced_gis_processing",
            "state_of_art_ocr_analysis",
            "governed_web_browsing",
            "coding_programming_expertise",
            "transparency_objectivity_governance",
        ]
        return {
            "status": "PRODUCT_AUDIT_OK",
            "objective": "test objective",
            "requirements": [
                {"requirement_id": requirement_id, "status": "PASS", "evidence": "ok", "gap": "", "next_step": ""}
                for requirement_id in requirement_ids
            ],
        }

    def fake_release_state(_cfg: Settings, *, include_github: bool = False, github_workflow: str = "CI"):
        del include_github, github_workflow
        return {
            "status": "RELEASE_STATE_READY",
            "current_state": "GOVERNED_RELEASE_READY",
            "highest_local_state": "CANONICAL_LOCAL",
            "git": {"head_sha": head_sha, "upstream": "origin/main", "worktree_clean": True},
            "states": [
                {"state": "CANONICAL_LOCAL", "status": "PASS", "evidence": "canonical local", "next_step": ""},
                {"state": "LOCAL_RELEASE_CERTIFIED", "status": "PASS", "evidence": "local release", "next_step": ""},
                {"state": "FRESH_CLONE_CERTIFIED", "status": "PASS", "evidence": "fresh clone", "next_step": ""},
                {"state": "SOURCE_ARTIFACT_PACKAGING", "status": "PASS", "evidence": "package", "next_step": ""},
                {"state": "SOURCE_ARTIFACT_CERTIFIED", "status": "PASS", "evidence": "source artifact", "next_step": ""},
                {"state": "PYTHON_DIST_CERTIFIED", "status": "PASS", "evidence": "python dist", "next_step": ""},
                {"state": "ANSWER_TRACE_QUALITY_CERTIFIED", "status": "PASS", "evidence": "answer trace", "next_step": ""},
                {"state": "RETRIEVAL_QUALITY_CERTIFIED", "status": "PASS", "evidence": "retrieval", "next_step": ""},
                {"state": "GIS_QUALITY_CERTIFIED", "status": "PASS", "evidence": "gis", "next_step": ""},
                {"state": "OPERATOR_WORKFLOW_CERTIFIED", "status": "PASS", "evidence": "operator", "next_step": ""},
                {"state": "GITHUB_GOVERNANCE_OPEN", "status": "PASS", "evidence": "github governed", "next_step": ""},
                {"state": "GOVERNED_RELEASE_READY", "status": "PASS", "evidence": "governed", "next_step": ""},
            ],
            "next_commands": [],
        }

    def fake_dashboard(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        del include_github, github_workflow, probe_workflow
        return {
            "status": "RELEASE_DASHBOARD_READY",
            "remote_branch_status": "PASS",
            "distribution_manifest_status": "DISTRIBUTION_MANIFEST_VERIFIED",
            "governance_report_status": "GOVERNANCE_REPORT_VERIFIED",
            "hosted_governance_status": "PASS",
            "current_head_receipts_summary": {"total": 8, "valid": 8, "invalid": 0},
            "next_commands": [],
        }

    monkeypatch.setattr(cli, "build_product_audit", fake_product_audit)
    monkeypatch.setattr(cli, "build_release_state_report", fake_release_state)
    monkeypatch.setattr(cli, "build_release_dashboard_report", fake_dashboard)

    assert main(["audit", "completion", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "COMPLETION_AUDIT_READY"
    assert payload["readiness_percent"] == 100.0
    assert payload["counts"] == {"pass": 15, "open": 0, "blocked": 0, "fail": 0, "unverified": 0, "total": 15}


def test_cli_audit_completion_fails_bad_artifact_status(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)

    def fake_product_audit(_cfg: Settings):
        requirement_ids = [
            "installable_cli",
            "readiness_diagnostics",
            "permission_gated_user_directory",
            "document_ingestion",
            "source_lineage_citations",
            "local_retrieval_strategies",
            "semantic_vector_store",
            "personalization_memory",
            "tool_use_and_actions",
            "source_artifact_packaging",
            "advanced_gis_processing",
            "state_of_art_ocr_analysis",
            "governed_web_browsing",
            "coding_programming_expertise",
            "transparency_objectivity_governance",
        ]
        return {
            "status": "PRODUCT_AUDIT_OK",
            "objective": "test objective",
            "requirements": [
                {"requirement_id": requirement_id, "status": "PASS", "evidence": "ok", "gap": "", "next_step": ""}
                for requirement_id in requirement_ids
            ],
        }

    def fake_release_state(_cfg: Settings, *, include_github: bool = False, github_workflow: str = "CI"):
        del include_github, github_workflow
        return {
            "status": "RELEASE_STATE_READY",
            "current_state": "GOVERNED_RELEASE_READY",
            "highest_local_state": "CANONICAL_LOCAL",
            "git": {"head_sha": "c" * 40, "upstream": "origin/main", "worktree_clean": True},
            "states": [
                {"state": "CANONICAL_LOCAL", "status": "PASS", "evidence": "canonical local", "next_step": ""},
                {"state": "LOCAL_RELEASE_CERTIFIED", "status": "PASS", "evidence": "local release", "next_step": ""},
                {"state": "FRESH_CLONE_CERTIFIED", "status": "PASS", "evidence": "fresh clone", "next_step": ""},
                {"state": "SOURCE_ARTIFACT_PACKAGING", "status": "PASS", "evidence": "package", "next_step": ""},
                {"state": "SOURCE_ARTIFACT_CERTIFIED", "status": "PASS", "evidence": "source artifact", "next_step": ""},
                {"state": "PYTHON_DIST_CERTIFIED", "status": "PASS", "evidence": "python dist", "next_step": ""},
                {"state": "ANSWER_TRACE_QUALITY_CERTIFIED", "status": "PASS", "evidence": "answer trace", "next_step": ""},
                {"state": "RETRIEVAL_QUALITY_CERTIFIED", "status": "PASS", "evidence": "retrieval", "next_step": ""},
                {"state": "GIS_QUALITY_CERTIFIED", "status": "PASS", "evidence": "gis", "next_step": ""},
                {"state": "OPERATOR_WORKFLOW_CERTIFIED", "status": "PASS", "evidence": "operator", "next_step": ""},
                {"state": "GITHUB_GOVERNANCE_OPEN", "status": "PASS", "evidence": "github governed", "next_step": ""},
                {"state": "GOVERNED_RELEASE_READY", "status": "PASS", "evidence": "governed", "next_step": ""},
            ],
            "next_commands": [],
        }

    def fake_dashboard(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        del include_github, github_workflow, probe_workflow
        return {
            "status": "RELEASE_DASHBOARD_FAIL",
            "remote_branch_status": "PASS",
            "distribution_manifest_status": "DISTRIBUTION_MANIFEST_VERIFY_FAILED",
            "governance_report_status": "GOVERNANCE_REPORT_VERIFIED",
            "hosted_governance_status": "PASS",
            "current_head_receipts_summary": {"total": 8, "valid": 8, "invalid": 0},
            "next_commands": [],
        }

    monkeypatch.setattr(cli, "build_product_audit", fake_product_audit)
    monkeypatch.setattr(cli, "build_release_state_report", fake_release_state)
    monkeypatch.setattr(cli, "build_release_dashboard_report", fake_dashboard)

    assert main(["audit", "completion", "--include-github", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    requirements = {row["requirement_id"]: row for row in payload["requirements"]}
    assert payload["status"] == "COMPLETION_AUDIT_FAIL"
    assert requirements["production_quality_packaging_path"]["status"] == "FAIL"


def test_cli_release_receipts_lists_and_shows_exact_head_receipts(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "f" * 40
    stale_sha = "1" * 40
    receipt_dir = tmp_path / "data" / "exports" / "release_receipts"

    current_receipt = write_release_receipt(
        tmp_path,
        receipt_type="local_release",
        status="CERTIFY_RELEASE_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_release.py"],
        payload={"gates": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="fresh_clone",
        status="FRESH_CLONE_CERT_OK",
        head_sha=stale_sha,
        command=["python3", "scripts/certify_fresh_clone.py", "--ref", stale_sha],
        payload={"checks": ["stale"]},
    )
    (receipt_dir / "broken.json").write_text("{not-json\n", encoding="utf-8")

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "receipts", "--current-head", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RELEASE_RECEIPTS_OK"
    assert payload["current_head"] == head_sha
    assert payload["count"] == 1
    assert payload["receipts"][0]["receipt_id"] == current_receipt.stem
    assert payload["receipts"][0]["valid"] is True
    assert payload["receipts"][0]["file_sha256"]
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["valid"] == 1
    assert payload["summary"]["invalid"] == 0

    assert main(["release", "receipt", current_receipt.stem, "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RELEASE_RECEIPT_OK"
    assert payload["summary"]["receipt_id"] == current_receipt.stem
    assert payload["summary"]["valid"] is True
    assert payload["receipt"]["head_sha"] == head_sha

    assert main(["release", "receipt", "missing-receipt", "--format", "text"], cfg=cfg) == 2
    assert "ERROR_RELEASE_RECEIPT_NOT_FOUND" in capsys.readouterr().err

    assert main(["release", "receipt", "../outside", "--format", "text"], cfg=cfg) == 2
    assert "ERROR_RELEASE_RECEIPT_NOT_FOUND" in capsys.readouterr().err

    assert main(["release", "receipts", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["valid"] == 2
    assert payload["summary"]["invalid"] == 1
    broken = next(row for row in payload["receipts"] if row["receipt_id"] == "broken")
    assert broken["valid"] is False
    assert broken["validation_error"].startswith("json_invalid:")


def test_github_governance_fails_when_latest_ci_is_for_different_head(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40
    stale_sha = "c" * 40

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args[:2] == ["api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, {"protected": True, "protection": {"required_status_checks": {"contexts": ["validate"]}}}, ""
        if args[:2] == ["run", "list"]:
            return True, [
                {
                    "databaseId": 7,
                    "status": "completed",
                    "conclusion": "success",
                    "headSha": stale_sha,
                    "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/7",
                }
            ], ""
        return False, None, f"unexpected command: {args}"

    def fake_find_remote_tag_commit(_cfg: Settings, tag_name: str, expected_head: str | None):
        assert tag_name == "v0.1.23-governance-status"
        assert expected_head == head_sha
        return True, f"{tag_name} -> {head_sha}"

    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)
    monkeypatch.setattr(cli, "_find_remote_tag_commit", fake_find_remote_tag_commit)

    _github, gates = cli.build_github_governance_status(
        cfg,
        repo="jotaele44/mucaro-ai",
        branch="main",
        head_sha=head_sha,
        tags_at_head=["v0.1.23-governance-status"],
        workflow_name="CI",
    )
    by_name = {gate["name"]: gate for gate in gates}
    assert by_name["github_branch_protection"]["status"] == "PASS"
    assert by_name["github_latest_ci"]["status"] == "FAIL"
    assert f"does not match HEAD {head_sha}" in by_name["github_latest_ci"]["detail"]
    assert "run_id=7" in by_name["github_latest_ci"]["detail"]


def test_github_governance_fails_when_current_head_ci_fails(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args[:2] == ["api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, {"protected": True, "protection": {"required_status_checks": {"contexts": ["validate"]}}}, ""
        if args[:2] == ["run", "list"]:
            return True, [
                {
                    "databaseId": 9,
                    "status": "completed",
                    "conclusion": "startup_failure",
                    "headSha": head_sha,
                    "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/9",
                }
            ], ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/9/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        return False, None, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)

    github, gates = cli.build_github_governance_status(
        cfg,
        repo="jotaele44/mucaro-ai",
        branch="main",
        head_sha=head_sha,
        tags_at_head=[],
        workflow_name="CI",
    )
    by_name = {gate["name"]: gate for gate in gates}
    assert by_name["github_latest_ci"]["status"] == "FAIL"
    assert f"completed startup_failure {head_sha}" in by_name["github_latest_ci"]["detail"]
    assert "run_id=9" in by_name["github_latest_ci"]["detail"]
    assert by_name["github_hosted_runner_startup"]["status"] == "FAIL"
    assert github["latest_ci"]["jobs_count"] == 0
    assert github["latest_ci"]["startup_layer"] == "before_jobs"


def test_detached_head_resolves_exact_remote_default_branch(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40

    def fake_git_output(_cfg: Settings, args: list[str]):
        outputs = {
            ("branch", "--show-current"): (True, ""),
            ("symbolic-ref", "--short", "refs/remotes/origin/HEAD"): (True, "origin/main"),
            ("rev-parse", "origin/main"): (True, head_sha),
        }
        return outputs.get(tuple(args), (False, "unexpected command"))

    monkeypatch.setattr(cli, "_git_output", fake_git_output)

    assert cli._git_branch_for_head(cfg, head_sha) == (True, "main")
    assert cli._git_upstream_for_head(cfg, head_sha, "main") == (True, "origin/main")
    assert cli._git_branch_for_head(cfg, "b" * 40) == (False, "")
    assert cli._git_upstream_for_head(cfg, "b" * 40, "main") == (False, "")
    assert cli._git_upstream_for_head(cfg, head_sha, "release") == (False, "")


def test_exact_head_ci_with_jobs_supersedes_stale_startup_probe():
    head_sha = "a" * 40
    gate = cli._classify_startup_probe(
        ci_latest={"databaseId": 11, "status": "completed", "conclusion": "success", "headSha": head_sha},
        ci_jobs_count=1,
        probe_latest={"databaseId": 22, "status": "completed", "conclusion": "success", "headSha": "b" * 40},
        probe_error=None,
        head_sha=head_sha,
        probe_jobs_count=1,
    )

    assert gate["status"] == "PASS"
    assert "NOT_REQUIRED exact-head CI passed with jobs=1" in gate["detail"]
    assert "run_id=22" in gate["detail"]


def test_cli_certify_github_actions_isolates_startup_layer(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        return False, f"unexpected command: {args}"

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions"]:
            return True, {"enabled": True, "allowed_actions": "all"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions/workflow"]:
            return True, {"default_workflow_permissions": "read"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, None, "HTTP 403: Upgrade to GitHub Pro or make this repository public"
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runners"]:
            return True, {"total_count": 0, "runners": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/workflows"]:
            return True, {
                "workflows": [
                    {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
                    {
                        "id": 2,
                        "name": "CI Startup Probe",
                        "path": ".github/workflows/ci-startup-probe.yml",
                        "state": "active",
                    },
                ]
            }, ""
        if args[:2] == ["run", "list"] and "CI Startup Probe" in args:
            return True, [
                {
                    "databaseId": 22,
                    "status": "completed",
                    "conclusion": "startup_failure",
                    "headSha": head_sha,
                    "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/22",
                }
            ], ""
        if args[:2] == ["run", "list"] and "CI" in args:
            return True, [
                {
                    "databaseId": 11,
                    "status": "completed",
                    "conclusion": "startup_failure",
                    "headSha": head_sha,
                    "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/11",
                }
            ], ""
        if args[:2] == ["run", "list"]:
            return True, [
                {
                    "databaseId": 31,
                    "status": "completed",
                    "conclusion": "startup_failure",
                    "headSha": head_sha,
                    "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/31",
                    "name": "",
                    "event": "push",
                    "createdAt": "2026-09-07T00:00:00Z",
                    "updatedAt": "2026-09-07T00:00:01Z",
                },
                {
                    "databaseId": 32,
                    "status": "completed",
                    "conclusion": "startup_failure",
                    "headSha": head_sha,
                    "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/32",
                    "name": "",
                    "event": "push",
                    "createdAt": "2026-09-07T00:00:00Z",
                    "updatedAt": "2026-09-07T00:00:01Z",
                },
            ], ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/11/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/22/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/31/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/32/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", f"repos/jotaele44/mucaro-ai/commits/{head_sha}/check-suites"]:
            return True, {
                "total_count": 1,
                "check_suites": [
                    {
                        "id": 44,
                        "app": {"slug": "github-actions"},
                        "status": "completed",
                        "conclusion": "startup_failure",
                        "latest_check_runs_count": 0,
                        "url": "https://api.github.com/repos/jotaele44/mucaro-ai/check-suites/44",
                        "check_runs_url": "https://api.github.com/repos/jotaele44/mucaro-ai/check-suites/44/check-runs",
                        "created_at": "2026-09-05T00:00:00Z",
                        "updated_at": "2026-09-05T00:00:01Z",
                    }
                ],
            }, ""
        if args == ["api", "users/jotaele44/settings/billing/actions"]:
            return False, None, 'HTTP 404: Not Found; This API operation needs the "user" scope.'
        return False, None, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)

    assert main(["certify", "github-actions", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "GITHUB_ACTIONS_DIAGNOSTIC_FAIL"
    assert payload["runs"]["ci"]["database_id"] == 11
    assert payload["runs"]["ci"]["jobs_count"] == 0
    assert payload["runs"]["startup_probe"]["database_id"] == 22
    assert payload["runs"]["startup_probe"]["jobs_count"] == 0
    assert [run["database_id"] for run in payload["current_head_startup_runs"]] == [31, 32]
    assert payload["support_summary"]["current_head_startup_run_ids"] == [31, 32]
    assert payload["check_suites"]["total_count"] == 1
    assert payload["check_suites"]["suites"][0]["id"] == 44
    assert payload["check_suites"]["suites"][0]["latest_check_runs_count"] == 0
    assert payload["support_summary"]["classification"] == "HOSTED_STARTUP_BEFORE_JOBS"
    assert payload["support_summary"]["ci_run_id"] == 11
    assert payload["support_summary"]["startup_probe_run_id"] == 22
    assert payload["actions_permissions"] == {
        "available": True,
        "enabled": True,
        "allowed_actions": "all",
        "sha_pinning_required": None,
    }
    assert payload["workflow_permissions"] == {
        "available": True,
        "default_workflow_permissions": "read",
        "can_approve_pull_request_reviews": None,
    }
    assert payload["billing"]["required_scope"] == "user"
    assert payload["support_summary"]["actions_enabled"] is True
    assert payload["support_summary"]["allowed_actions"] == "all"
    assert payload["support_summary"]["default_workflow_permissions"] == "read"
    assert payload["support_summary"]["billing_required_scope"] == "user"
    assert payload["support_summary"]["check_suite_counts"] == {
        "total": 1,
        "github_actions": 1,
        "github_actions_zero_check_run_startup_failures": 1,
        "queued_external": 0,
    }
    assert payload["startup_blocker"]["status"] == "FAIL"
    assert payload["startup_blocker"]["isolated_before_jobs"] is True
    assert "private_branch_protection_plan_gate" in payload["startup_blocker"]["suspected_causes"]
    assert checks["latest_ci"]["status"] == "FAIL"
    assert checks["startup_probe"]["status"] == "FAIL"
    assert checks["current_head_push_startup"]["status"] == "FAIL"
    assert checks["startup_layer_isolated"]["status"] == "FAIL"
    assert checks["check_suites_lookup"]["status"] == "PASS"
    assert checks["github_actions_check_suites_before_runs"]["status"] == "FAIL"
    assert checks["branch_protection_api"]["status"] == "WARN"
    assert checks["self_hosted_runner_inventory"]["status"] == "PASS"
    assert checks["actions_billing"]["status"] == "WARN"
    assert payload["branch_protection"]["available"] is False
    assert payload["runner_inventory"]["self_hosted_total_count"] == 0

    assert main(["certify", "github-actions", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "GITHUB_ACTIONS_DIAGNOSTIC_FAIL" in text_output
    assert "startup_blocker_status=FAIL" in text_output
    assert "private_branch_protection_plan_gate" in text_output
    assert "WARN branch_protection_api HTTP 403: Upgrade to GitHub Pro" in text_output
    assert "PASS self_hosted_runner_inventory self_hosted_total_count=0" in text_output
    assert "FAIL latest_ci completed startup_failure" in text_output
    assert "run_id=11" in text_output
    assert "run=ci id=11 status=completed conclusion=startup_failure" in text_output
    assert "run=startup_probe id=22 status=completed conclusion=startup_failure" in text_output
    assert "current_head_startup_run id=31 name= event=push status=completed conclusion=startup_failure" in text_output
    assert "check_suites_total=1" in text_output
    assert "check_suite id=44 app=github-actions status=completed conclusion=startup_failure check_runs=0" in text_output
    assert "support_classification=HOSTED_STARTUP_BEFORE_JOBS" in text_output
    assert "support_ci_run_id=11" in text_output
    assert "support_startup_probe_run_id=22" in text_output
    assert "support_current_head_startup_run_ids=31,32" in text_output
    assert "actions_permissions_available=True" in text_output
    assert "actions_enabled=True" in text_output
    assert "allowed_actions=all" in text_output
    assert "default_workflow_permissions=read" in text_output
    assert "billing_required_scope=user" in text_output
    assert "support_billing_required_scope=user" in text_output
    assert "support_check_suite_counts=github_actions=1 zero_check_run_startup_failures=1 queued_external=0" in text_output
    assert "FAIL startup_layer_isolated" in text_output
    assert "FAIL github_actions_check_suites_before_runs" in text_output
    assert "FAIL current_head_push_startup" in text_output
    assert "next_action=Upgrade GitHub plan or make the repository public" in text_output
    assert "next_action=Run `gh auth refresh -h github.com -s user`" in text_output


def test_cli_release_hosted_governance_summarizes_operator_remediation(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "d" * 40
    support_path = tmp_path / "data" / "exports" / "support_bundles" / f"support-bundle-{head_sha[:12]}.json"

    def fake_dashboard(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert include_github is True
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        return {
            "status": "RELEASE_DASHBOARD_OPEN",
            "head_sha": head_sha,
            "tags_at_head": ["v0.1.23-hosted-governance"],
            "release_state_status": "RELEASE_STATE_OPEN",
            "hosted_governance_status": "OPEN",
            "github_actions": {
                "status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
                "repo": "jotaele44/mucaro-ai",
                "checks": [
                    {"name": "actions_enabled", "status": "PASS", "detail": "enabled=True allowed_actions=all"},
                    {"name": "workflow_token_permissions", "status": "PASS", "detail": "default_workflow_permissions=read"},
                    {"name": "workflow_registered:CI", "status": "PASS", "detail": ".github/workflows/ci.yml active"},
                    {
                        "name": "workflow_registered:CI Startup Probe",
                        "status": "PASS",
                        "detail": ".github/workflows/ci-startup-probe.yml active",
                    },
                    {"name": "latest_ci", "status": "FAIL", "detail": f"completed startup_failure {head_sha} jobs=0 run_id=11"},
                    {"name": "startup_probe", "status": "FAIL", "detail": f"completed startup_failure {head_sha} jobs=0 run_id=22"},
                    {
                        "name": "github_actions_check_suites_before_runs",
                        "status": "FAIL",
                        "detail": "GitHub Actions check suites completed startup_failure with latest_check_runs_count=0",
                    },
                    {
                        "name": "actions_billing",
                        "status": "WARN",
                        "detail": 'HTTP 404: Not Found; This API operation needs the "user" scope.',
                    },
                    {
                        "name": "branch_protection_api",
                        "status": "WARN",
                        "detail": "HTTP 403: Upgrade to GitHub Pro or make this repository public",
                    },
                ],
                "billing": {
                    "available": False,
                    "required_scope": "user",
                    "error": 'HTTP 404: Not Found; This API operation needs the "user" scope.',
                },
                "branch_protection": {
                    "available": False,
                    "error": "HTTP 403: Upgrade to GitHub Pro or make this repository public",
                },
                "startup_blocker": {
                    "status": "FAIL",
                    "isolated_before_jobs": True,
                    "suspected_causes": ["actions_billing_visibility_unverified"],
                },
                "support_summary": {
                    "classification": "HOSTED_STARTUP_BEFORE_JOBS",
                    "ci_run_id": 11,
                    "startup_probe_run_id": 22,
                    "ci_jobs_count": 0,
                    "startup_probe_jobs_count": 0,
                    "actions_enabled": True,
                    "allowed_actions": "all",
                    "default_workflow_permissions": "read",
                },
                "next_actions": ["Run `gh auth refresh -h github.com -s user` and inspect GitHub Actions billing/minutes."],
            },
        }

    def fake_support_verify(_cfg: Settings, artifact_path: Path, *, require_current_head: bool = False, expected_sha256: str | None = None):
        assert artifact_path == support_path
        assert require_current_head is True
        assert expected_sha256 is None
        return {"status": "SUPPORT_BUNDLE_VERIFIED"}

    monkeypatch.setattr(cli, "build_release_dashboard_report", fake_dashboard)
    monkeypatch.setattr(cli, "verify_release_support_bundle", fake_support_verify)

    assert main(["release", "hosted-governance", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    steps = {step["step_id"]: step for step in payload["steps"]}
    assert payload["status"] == "HOSTED_GOVERNANCE_OPEN"
    assert payload["support_classification"] == "HOSTED_STARTUP_BEFORE_JOBS"
    assert payload["ci_run_id"] == 11
    assert payload["startup_probe_run_id"] == 22
    assert payload["billing_required_scope"] == "user"
    assert payload["support_bundle"]["status"] == "PASS"
    assert steps["actions_repository_policy"]["status"] == "PASS"
    assert steps["ci_run_admission"]["status"] == "OPEN"
    assert steps["startup_probe_admission"]["status"] == "OPEN"
    assert steps["billing_visibility"]["command"] == "gh auth refresh -h github.com -s user"

    assert main(["release", "hosted-governance", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "HOSTED_GOVERNANCE_OPEN" in text_output
    assert "ci_run_id=11" in text_output
    assert "startup_probe_jobs_count=0" in text_output
    assert "billing_required_scope=user" in text_output
    assert f"support_bundle_path={support_path}" in text_output
    assert "OPEN ci_run_admission" in text_output
    assert "command=gh auth refresh -h github.com -s user" in text_output


def test_cli_release_hosted_governance_ready_when_dashboard_and_steps_pass(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "e" * 40

    def fake_dashboard(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        del include_github, github_workflow, probe_workflow
        return {
            "status": "RELEASE_DASHBOARD_READY",
            "head_sha": head_sha,
            "tags_at_head": ["v0.1.23-hosted-ready"],
            "release_state_status": "RELEASE_STATE_READY",
            "hosted_governance_status": "PASS",
            "github_actions": {
                "status": "GITHUB_ACTIONS_DIAGNOSTIC_OK",
                "repo": "jotaele44/mucaro-ai",
                "checks": [
                    {"name": "actions_enabled", "status": "PASS", "detail": "enabled=True allowed_actions=all"},
                    {"name": "workflow_token_permissions", "status": "PASS", "detail": "default_workflow_permissions=read"},
                    {"name": "workflow_registered:CI", "status": "PASS", "detail": ".github/workflows/ci.yml active"},
                    {
                        "name": "workflow_registered:CI Startup Probe",
                        "status": "PASS",
                        "detail": ".github/workflows/ci-startup-probe.yml active",
                    },
                    {"name": "latest_ci", "status": "PASS", "detail": f"success {head_sha} jobs=1 run_id=11"},
                    {"name": "startup_probe", "status": "PASS", "detail": f"success {head_sha} jobs=1 run_id=22"},
                    {"name": "github_actions_check_suites_before_runs", "status": "PASS", "detail": "none"},
                    {"name": "actions_billing", "status": "PASS", "detail": "billing endpoint readable"},
                    {"name": "branch_protection_api", "status": "PASS", "detail": "branch protection endpoint readable"},
                ],
                "billing": {"available": True},
                "branch_protection": {"available": True},
                "startup_blocker": {"status": "PASS", "isolated_before_jobs": False, "suspected_causes": []},
                "support_summary": {
                    "classification": "HOSTED_GOVERNANCE_REVIEW",
                    "ci_run_id": 11,
                    "startup_probe_run_id": 22,
                    "ci_jobs_count": 1,
                    "startup_probe_jobs_count": 1,
                    "actions_enabled": True,
                    "allowed_actions": "all",
                    "default_workflow_permissions": "read",
                },
                "next_actions": [],
            },
        }

    def fake_support_verify(_cfg: Settings, artifact_path: Path, *, require_current_head: bool = False, expected_sha256: str | None = None):
        del artifact_path, require_current_head, expected_sha256
        return {"status": "SUPPORT_BUNDLE_VERIFIED"}

    monkeypatch.setattr(cli, "build_release_dashboard_report", fake_dashboard)
    monkeypatch.setattr(cli, "verify_release_support_bundle", fake_support_verify)

    assert main(["release", "hosted-governance", "--strict", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "HOSTED_GOVERNANCE_READY"
    assert payload["counts"] == {"pass": 11, "open": 0, "blocked": 0, "fail": 0, "unverified": 0, "total": 11}


def test_cli_release_incident_packet_saves_and_verifies_current_head(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "7" * 40
    output_path = tmp_path / "data" / "exports" / "hosted_incident_packets" / "incident.json"

    def fake_hosted(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        return {
            "status": "HOSTED_GOVERNANCE_OPEN",
            "project_root": str(tmp_path),
            "head_sha": head_sha,
            "tag_at_head": ["v0.1.23-hosted-blocker-evidence"],
            "hosted_governance_status": "OPEN",
            "github_actions_status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
            "support_classification": "HOSTED_STARTUP_BEFORE_JOBS",
            "current_head_startup_run_ids": [31],
            "startup_blocker": {
                "status": "FAIL",
                "suspected_causes": [
                    "actions_billing_or_spending_limit",
                    "hosted_runner_availability",
                ],
            },
            "support_bundle": {
                "path": str(tmp_path / "data" / "exports" / "support_bundles" / f"support-bundle-{head_sha[:12]}.json"),
                "status": "PASS",
            },
            "steps": [
                {
                    "step_id": "actions_repository_policy",
                    "status": "PASS",
                    "owner": "operator",
                    "evidence": "enabled=True allowed_actions=all",
                    "command": "Open GitHub repository Settings -> Actions -> General.",
                },
                {
                    "step_id": "current_head_push_startup",
                    "status": "OPEN",
                    "owner": "operator",
                    "evidence": "run_ids=31",
                    "command": "gh run view 31 --json databaseId,name,status,conclusion,headSha,jobs,url",
                },
                {
                    "step_id": "billing_visibility",
                    "status": "OPEN",
                    "owner": "operator",
                    "evidence": "needs user scope",
                    "command": "gh auth refresh -h github.com -s user",
                },
            ],
            "next_actions": ["Inspect current-head push startup run 31."],
        }

    def fake_completion(
        _cfg: Settings,
        *,
        include_github: bool = False,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert include_github is True
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        return {
            "status": "COMPLETION_AUDIT_OPEN",
            "current_state": "GITHUB_GOVERNANCE_OPEN",
            "readiness_percent": 90.0,
            "counts": {"pass": 9, "open": 1, "blocked": 0, "fail": 0, "unverified": 0, "total": 10},
            "requirements": [
                {
                    "requirement_id": "hosted_governance",
                    "status": "OPEN",
                    "evidence": "support_classification=HOSTED_STARTUP_BEFORE_JOBS current_head_startup_run_ids=31",
                    "gap": "Hosted governance is not proven at current HEAD.",
                    "next_step": "Repair hosted Actions startup.",
                }
            ],
        }

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "build_release_hosted_governance_report", fake_hosted)
    monkeypatch.setattr(cli, "build_completion_audit_report", fake_completion)
    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "incident-packet", "--save", "--output", str(output_path), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "HOSTED_INCIDENT_PACKET_READY"
    assert payload["head_sha"] == head_sha
    assert payload["case_status"] == "HOSTED_GOVERNANCE_OPEN"
    assert payload["current_head_startup_run_ids"] == [31]
    assert [step["step_id"] for step in payload["open_steps"]] == ["current_head_push_startup", "billing_visibility"]
    assert "mucaro certify github-actions --format text" in payload["diagnostic_commands"]
    assert any(line == "current_head_startup_run_ids=31" for line in payload["copy_lines"])
    assert any(line.startswith("open_step=current_head_push_startup") for line in payload["copy_lines"])
    assert payload["export"]["path"] == str(output_path)

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == cli.HOSTED_INCIDENT_PACKET_SCHEMA_VERSION
    assert saved["packet"]["completion_audit"]["hosted_governance"]["status"] == "OPEN"
    expected_sha = payload["export"]["sha256"]

    assert (
        main(
            [
                "release",
                "incident-packet",
                "--verify",
                str(output_path),
                "--expected-sha256",
                expected_sha,
                "--require-current-head",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    verified = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in verified["checks"]}
    assert verified["status"] == "HOSTED_INCIDENT_PACKET_VERIFIED"
    assert verified["case_status"] == "HOSTED_GOVERNANCE_OPEN"
    assert checks["expected_sha256"]["status"] == "PASS"
    assert checks["current_head_binding"]["status"] == "PASS"
    assert checks["copy_lines"]["status"] == "PASS"

    assert main(["release", "incident-packet", "--verify", str(output_path), "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "HOSTED_INCIDENT_PACKET_VERIFIED" in text_output
    assert "incident_copy=current_head_startup_run_ids=31" in text_output
    assert "diagnostic_command=mucaro certify github-actions --format text" in text_output


def test_cli_release_next_actions_prioritizes_open_operator_runbook(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "f" * 40
    support_path = tmp_path / "data" / "exports" / "support_bundles" / f"support-bundle-{head_sha[:12]}.json"

    def fake_hosted(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        return {
            "status": "HOSTED_GOVERNANCE_OPEN",
            "project_root": str(tmp_path),
            "head_sha": head_sha,
            "tag_at_head": ["v0.1.23-runbook"],
            "hosted_governance_status": "OPEN",
            "release_dashboard_status": "RELEASE_DASHBOARD_OPEN",
            "release_state_status": "RELEASE_STATE_OPEN",
            "github_actions_status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
            "support_classification": "HOSTED_STARTUP_BEFORE_JOBS",
            "ci_run_id": 11,
            "ci_run_url": "https://github.com/jotaele44/mucaro-ai/actions/runs/11",
            "startup_probe_run_id": 22,
            "startup_probe_run_url": "https://github.com/jotaele44/mucaro-ai/actions/runs/22",
            "ci_jobs_count": 0,
            "startup_probe_jobs_count": 0,
            "support_bundle": {"path": str(support_path), "status": "PASS"},
            "steps": [
                {
                    "step_id": "ci_run_admission",
                    "status": "OPEN",
                    "owner": "operator",
                    "evidence": "completed startup_failure jobs=0",
                    "command": "gh run view 11 --json databaseId,name,status,conclusion,headSha,jobs,url",
                },
                {
                    "step_id": "billing_visibility",
                    "status": "OPEN",
                    "owner": "operator",
                    "evidence": "needs user scope",
                    "command": "gh auth refresh -h github.com -s user",
                },
                {
                    "step_id": "support_bundle",
                    "status": "PASS",
                    "owner": "operator",
                    "evidence": "SUPPORT_BUNDLE_VERIFIED",
                    "command": "mucaro release support-bundle --save --format text",
                },
            ],
        }

    monkeypatch.setattr(cli, "build_release_hosted_governance_report", fake_hosted)

    output_path = tmp_path / "data" / "exports" / "release_next_actions" / "runbook.json"
    assert main(["release", "next-actions", "--save", "--output", str(output_path), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RELEASE_NEXT_ACTIONS_OPEN"
    assert payload["counts"] == {"open": 2, "total": 2}
    assert payload["actions"][0]["action_id"] == "billing_visibility"
    assert payload["actions"][0]["priority"] == 10
    assert payload["actions"][1]["action_id"] == "ci_run_admission"
    assert payload["artifact_paths"]["support_bundle"] == str(support_path)
    assert payload["support_copy"]["run_handles"]["ci_run_url"] == "https://github.com/jotaele44/mucaro-ai/actions/runs/11"
    assert payload["support_copy"]["run_handles"]["startup_probe_run_url"] == "https://github.com/jotaele44/mucaro-ai/actions/runs/22"
    assert any(line.startswith("open_action=priority:10 action:billing_visibility") for line in payload["support_copy"]["copy_lines"])
    assert payload["export"]["path"] == str(output_path)
    expected_sha = payload["export"]["sha256"]
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "mucaro_release_next_actions_v2"
    assert saved["runbook"]["head_sha"] == head_sha
    assert saved["runbook"]["support_copy"]["title"] == "Mucaro AI release governance handoff"
    assert any(command.startswith("mucaro release support-bundle --verify") for command in payload["verification_commands"])

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert (
        main(
            [
                "release",
                "next-actions",
                "--verify",
                str(output_path),
                "--expected-sha256",
                expected_sha,
                "--require-current-head",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    verified = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in verified["checks"]}
    assert verified["status"] == "RELEASE_NEXT_ACTIONS_VERIFIED"
    assert verified["runbook_status"] == "RELEASE_NEXT_ACTIONS_OPEN"
    assert checks["expected_sha256"]["status"] == "PASS"
    assert checks["current_head_binding"]["status"] == "PASS"
    assert checks["support_copy_payload"]["status"] == "PASS"
    assert checks["support_copy_lines"]["status"] == "PASS"

    assert main(["release", "next-actions", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "RELEASE_NEXT_ACTIONS_OPEN" in text_output
    assert "release_dashboard_status=RELEASE_DASHBOARD_OPEN" in text_output
    assert "ci_run_id=11" in text_output
    assert "ci_run_url=https://github.com/jotaele44/mucaro-ai/actions/runs/11" in text_output
    assert "verify_command=mucaro release dashboard --include-github --format text" in text_output
    assert "support_copy_title=Mucaro AI release governance handoff" in text_output
    assert "support_copy=open_action=priority:10 action:billing_visibility" in text_output
    assert "OPEN priority=10 action=billing_visibility" in text_output


def test_cli_release_next_actions_verify_fails_current_head_mismatch(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    artifact = {
        "schema_version": "mucaro_release_next_actions_v2",
        "created_utc": "2026-09-07T00:00:00Z",
        "runbook": {
            "status": "RELEASE_NEXT_ACTIONS_OPEN",
            "head_sha": "1" * 40,
            "actions": [],
            "verification_commands": [],
            "counts": {"open": 0, "total": 0},
            "support_copy": {
                "title": "Mucaro AI release governance handoff",
                "copy_lines": ["Mucaro AI release governance handoff"],
            },
        },
    }
    output_path = tmp_path / "next-actions.json"
    output_path.write_text(json.dumps(artifact), encoding="utf-8")

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, "2" * 40
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "next-actions", "--verify", str(output_path), "--require-current-head", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "RELEASE_NEXT_ACTIONS_VERIFY_FAILED" in text_output
    assert "FAIL current_head_binding" in text_output


def test_cli_release_next_actions_promotes_dashboard_failure_first(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "2" * 40

    def fake_hosted(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        del github_workflow, probe_workflow
        return {
            "status": "HOSTED_GOVERNANCE_OPEN",
            "project_root": str(tmp_path),
            "head_sha": head_sha,
            "tag_at_head": [],
            "hosted_governance_status": "OPEN",
            "release_dashboard_status": "RELEASE_DASHBOARD_FAIL",
            "release_state_status": "RELEASE_STATE_OPEN",
            "github_actions_status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
            "support_classification": "HOSTED_STARTUP_BEFORE_JOBS",
            "support_bundle": {"path": None, "status": "OPEN"},
            "steps": [
                {
                    "step_id": "support_bundle",
                    "status": "OPEN",
                    "owner": "operator",
                    "evidence": "SUPPORT_BUNDLE_MISSING",
                    "command": "mucaro release support-bundle --save --format text",
                },
            ],
        }

    monkeypatch.setattr(cli, "build_release_hosted_governance_report", fake_hosted)

    assert main(["release", "next-actions", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RELEASE_NEXT_ACTIONS_OPEN"
    assert payload["actions"][0]["action_id"] == "local_release_evidence"
    assert payload["actions"][0]["priority"] == 5
    assert payload["actions"][1]["action_id"] == "support_bundle"


def test_cli_release_next_actions_ready_when_hosted_governance_ready(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "1" * 40

    def fake_hosted(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        del github_workflow, probe_workflow
        return {
            "status": "HOSTED_GOVERNANCE_READY",
            "project_root": str(tmp_path),
            "head_sha": head_sha,
            "tag_at_head": ["v0.1.23-ready"],
            "hosted_governance_status": "PASS",
            "support_classification": "HOSTED_GOVERNANCE_REVIEW",
            "ci_run_id": 11,
            "startup_probe_run_id": 22,
            "support_bundle": {"path": None, "status": "PASS"},
            "steps": [
                {
                    "step_id": "ci_run_admission",
                    "status": "PASS",
                    "owner": "operator",
                    "evidence": "success jobs=1",
                    "command": "gh run view 11",
                },
            ],
        }

    monkeypatch.setattr(cli, "build_release_hosted_governance_report", fake_hosted)

    assert main(["release", "next-actions", "--strict", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RELEASE_NEXT_ACTIONS_READY"
    assert payload["actions"] == []
    assert payload["counts"] == {"open": 0, "total": 0}


def test_cli_release_handoff_saves_and_verifies_ordered_artifact_set(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "b" * 40
    calls: list[str] = []

    distribution_path = tmp_path / "data" / "exports" / "distribution_manifests" / f"distribution-manifest-{head_sha[:12]}.json"
    governance_path = tmp_path / "data" / "exports" / "governance_reports" / f"governance-report-{head_sha[:12]}.json"
    support_path = tmp_path / "data" / "exports" / "support_bundles" / f"support-bundle-{head_sha[:12]}.json"
    next_actions_path = tmp_path / "data" / "exports" / "release_next_actions" / f"next-actions-{head_sha[:12]}.json"
    receipts = [
        {"head_sha": head_sha, "receipt_type": f"receipt_{index}", "status": "OK", "valid": True}
        for index in range(8)
    ]

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        return False, f"unexpected command: {args}"

    def fake_distribution(root: Path, *, output_path: Path | None = None, save: bool = False):
        assert root == tmp_path
        assert output_path is None
        assert save is True
        calls.append("distribution")
        return {
            "status": "DISTRIBUTION_MANIFEST_READY",
            "head_sha": head_sha,
            "export": {"path": str(distribution_path), "sha256": "d" * 64, "size_bytes": 10},
        }

    def fake_distribution_verify(
        artifact_path: Path,
        *,
        root: Path,
        expected_sha256: str | None = None,
        require_current_head: bool = False,
    ):
        assert artifact_path == distribution_path
        assert root == tmp_path
        assert expected_sha256 == "d" * 64
        assert require_current_head is True
        calls.append("distribution_verify")
        return {
            "status": "DISTRIBUTION_MANIFEST_VERIFIED",
            "artifact": {"path": str(distribution_path), "sha256": "d" * 64, "size_bytes": 10},
        }

    def fake_support(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
        output_path: Path | None = None,
        save: bool = False,
    ):
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        assert output_path is None
        assert save is True
        calls.append("support")
        return {
            "status": "SUPPORT_BUNDLE_READY",
            "head_sha": head_sha,
            "case_status": "HOSTED_GOVERNANCE_OPEN",
            "governance_report": {"path": str(governance_path)},
            "export": {"path": str(support_path), "sha256": "s" * 64, "size_bytes": 11},
        }

    def fake_support_verify(
        _cfg: Settings,
        artifact_path: Path,
        *,
        require_current_head: bool = False,
        expected_sha256: str | None = None,
    ):
        assert artifact_path == support_path
        assert require_current_head is True
        assert expected_sha256 == "s" * 64
        calls.append("support_verify")
        return {
            "status": "SUPPORT_BUNDLE_VERIFIED",
            "artifact": {"path": str(support_path), "sha256": "s" * 64, "size_bytes": 11},
        }

    def fake_governance_verify(
        _cfg: Settings,
        artifact_path: Path,
        *,
        require_current_head: bool = False,
        expected_sha256: str | None = None,
    ):
        assert artifact_path == governance_path
        assert require_current_head is True
        assert expected_sha256 is None
        calls.append("governance_verify")
        return {
            "status": "GOVERNANCE_REPORT_VERIFIED",
            "artifact": {"path": str(governance_path), "sha256": "g" * 64, "size_bytes": 12},
        }

    def fake_next_actions(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        calls.append("next_actions")
        return {
            "status": "RELEASE_NEXT_ACTIONS_OPEN",
            "head_sha": head_sha,
            "tag_at_head": ["v0.1.23-handoff"],
            "hosted_governance_status": "OPEN",
            "release_dashboard_status": "RELEASE_DASHBOARD_OPEN",
            "release_state_status": "RELEASE_STATE_OPEN",
            "github_actions_status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
        }

    def fake_write_next_actions(_cfg: Settings, report: dict[str, object], *, output_path: Path | None = None):
        assert report["head_sha"] == head_sha
        assert output_path is None
        calls.append("next_actions_write")
        return {"path": str(next_actions_path), "sha256": "n" * 64, "size_bytes": 13}

    def fake_next_actions_verify(
        _cfg: Settings,
        artifact_path: Path,
        *,
        require_current_head: bool = False,
        expected_sha256: str | None = None,
    ):
        assert artifact_path == next_actions_path
        assert require_current_head is True
        assert expected_sha256 == "n" * 64
        calls.append("next_actions_verify")
        return {
            "status": "RELEASE_NEXT_ACTIONS_VERIFIED",
            "artifact": {"path": str(next_actions_path), "sha256": "n" * 64, "size_bytes": 13},
        }

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli, "list_release_receipts", lambda _root: receipts)
    monkeypatch.setattr(cli, "build_distribution_manifest", fake_distribution)
    monkeypatch.setattr(cli, "verify_distribution_manifest", fake_distribution_verify)
    monkeypatch.setattr(cli, "build_release_support_bundle", fake_support)
    monkeypatch.setattr(cli, "verify_release_support_bundle", fake_support_verify)
    monkeypatch.setattr(cli, "verify_release_governance_report", fake_governance_verify)
    monkeypatch.setattr(cli, "build_release_next_actions_report", fake_next_actions)
    monkeypatch.setattr(cli, "write_release_next_actions_report", fake_write_next_actions)
    monkeypatch.setattr(cli, "verify_release_next_actions_report", fake_next_actions_verify)

    assert main(["release", "handoff", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["status"] == "RELEASE_HANDOFF_OPEN"
    assert payload["head_sha"] == head_sha
    assert payload["counts"] == {"pass": 7, "open": 1, "fail": 0, "total": 8}
    assert payload["artifacts"]["distribution_manifest"]["path"] == str(distribution_path)
    assert payload["artifacts"]["governance_report"]["path"] == str(governance_path)
    assert payload["artifacts"]["support_bundle"]["path"] == str(support_path)
    assert payload["artifacts"]["next_actions"]["path"] == str(next_actions_path)
    assert checks["worktree_clean"]["status"] == "PASS"
    assert checks["hosted_governance"]["status"] == "OPEN"
    assert calls == [
        "distribution",
        "distribution_verify",
        "support",
        "support_verify",
        "governance_verify",
        "next_actions",
        "next_actions_write",
        "next_actions_verify",
    ]

    assert main(["release", "handoff", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "RELEASE_HANDOFF_OPEN" in text_output
    assert f"artifact_distribution_manifest={distribution_path}" in text_output
    assert "OPEN hosted_governance OPEN" in text_output


def test_cli_release_handoff_refreshes_certification_before_handoff_artifacts(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "d" * 40
    calls: list[str] = []
    receipt_calls = {"count": 0}

    distribution_path = tmp_path / "data" / "exports" / "distribution_manifests" / f"distribution-manifest-{head_sha[:12]}.json"
    governance_path = tmp_path / "data" / "exports" / "governance_reports" / f"governance-report-{head_sha[:12]}.json"
    support_path = tmp_path / "data" / "exports" / "support_bundles" / f"support-bundle-{head_sha[:12]}.json"
    next_actions_path = tmp_path / "data" / "exports" / "release_next_actions" / f"next-actions-{head_sha[:12]}.json"

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        return False, f"unexpected command: {args}"

    def fake_run(cmd: list[str], **_kwargs):
        command_text = " ".join(cmd)
        if "certify_release.py" in command_text:
            calls.append("refresh:local_release")
            return subprocess.CompletedProcess(cmd, 0, stdout="CERTIFY_RELEASE_OK\n")
        if "certify_python_distribution.py" in command_text:
            calls.append("refresh:python_dist")
            return subprocess.CompletedProcess(cmd, 0, stdout="PYTHON_DIST_CERT_OK\n")
        if "certify_source_artifact.py" in command_text:
            assert cmd[-1].endswith(f"mucaro-ai-0.1.23-{head_sha[:12]}-artifact-manifest.json")
            calls.append("refresh:source_artifact")
            return subprocess.CompletedProcess(cmd, 0, stdout="SOURCE_ARTIFACT_CERT_OK\n")
        if "certify_fresh_clone.py" in command_text:
            assert cmd[-1] == head_sha
            calls.append("refresh:fresh_clone")
            return subprocess.CompletedProcess(cmd, 0, stdout="FRESH_CLONE_CERT_OK\n")
        raise AssertionError(f"unexpected subprocess command: {cmd}")

    def fake_receipts(_root: Path):
        receipt_calls["count"] += 1
        if receipt_calls["count"] == 1:
            return []
        return [
            {"head_sha": head_sha, "receipt_type": f"receipt_{index}", "status": "OK", "valid": True}
            for index in range(8)
        ]

    def fake_distribution(root: Path, *, output_path: Path | None = None, save: bool = False):
        assert root == tmp_path
        assert output_path is None
        assert save is True
        calls.append("distribution")
        return {"export": {"path": str(distribution_path), "sha256": "d" * 64, "size_bytes": 10}}

    def fake_distribution_verify(
        artifact_path: Path,
        *,
        root: Path,
        expected_sha256: str | None = None,
        require_current_head: bool = False,
    ):
        assert artifact_path == distribution_path
        assert root == tmp_path
        assert expected_sha256 == "d" * 64
        assert require_current_head is True
        calls.append("distribution_verify")
        return {"status": "DISTRIBUTION_MANIFEST_VERIFIED", "artifact": {"path": str(distribution_path), "sha256": "d" * 64}}

    def fake_support(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
        output_path: Path | None = None,
        save: bool = False,
    ):
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        assert output_path is None
        assert save is True
        calls.append("support")
        return {
            "case_status": "HOSTED_GOVERNANCE_OPEN",
            "governance_report": {"path": str(governance_path)},
            "export": {"path": str(support_path), "sha256": "s" * 64, "size_bytes": 11},
        }

    def fake_support_verify(
        _cfg: Settings,
        artifact_path: Path,
        *,
        require_current_head: bool = False,
        expected_sha256: str | None = None,
    ):
        assert artifact_path == support_path
        assert require_current_head is True
        assert expected_sha256 == "s" * 64
        calls.append("support_verify")
        return {"status": "SUPPORT_BUNDLE_VERIFIED", "artifact": {"path": str(support_path), "sha256": "s" * 64}}

    def fake_governance_verify(
        _cfg: Settings,
        artifact_path: Path,
        *,
        require_current_head: bool = False,
        expected_sha256: str | None = None,
    ):
        assert artifact_path == governance_path
        assert require_current_head is True
        assert expected_sha256 is None
        calls.append("governance_verify")
        return {"status": "GOVERNANCE_REPORT_VERIFIED", "artifact": {"path": str(governance_path), "sha256": "g" * 64}}

    def fake_next_actions(
        _cfg: Settings,
        *,
        github_workflow: str = "CI",
        probe_workflow: str = "CI Startup Probe",
    ):
        assert github_workflow == "CI"
        assert probe_workflow == "CI Startup Probe"
        calls.append("next_actions")
        return {
            "head_sha": head_sha,
            "tag_at_head": ["v0.1.23-handoff-refresh"],
            "hosted_governance_status": "OPEN",
            "release_dashboard_status": "RELEASE_DASHBOARD_OPEN",
            "release_state_status": "RELEASE_STATE_OPEN",
            "github_actions_status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
        }

    def fake_write_next_actions(_cfg: Settings, report: dict[str, object], *, output_path: Path | None = None):
        assert report["head_sha"] == head_sha
        assert output_path is None
        calls.append("next_actions_write")
        return {"path": str(next_actions_path), "sha256": "n" * 64, "size_bytes": 13}

    def fake_next_actions_verify(
        _cfg: Settings,
        artifact_path: Path,
        *,
        require_current_head: bool = False,
        expected_sha256: str | None = None,
    ):
        assert artifact_path == next_actions_path
        assert require_current_head is True
        assert expected_sha256 == "n" * 64
        calls.append("next_actions_verify")
        return {"status": "RELEASE_NEXT_ACTIONS_VERIFIED", "artifact": {"path": str(next_actions_path), "sha256": "n" * 64}}

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "list_release_receipts", fake_receipts)
    monkeypatch.setattr(cli, "build_distribution_manifest", fake_distribution)
    monkeypatch.setattr(cli, "verify_distribution_manifest", fake_distribution_verify)
    monkeypatch.setattr(cli, "build_release_support_bundle", fake_support)
    monkeypatch.setattr(cli, "verify_release_support_bundle", fake_support_verify)
    monkeypatch.setattr(cli, "verify_release_governance_report", fake_governance_verify)
    monkeypatch.setattr(cli, "build_release_next_actions_report", fake_next_actions)
    monkeypatch.setattr(cli, "write_release_next_actions_report", fake_write_next_actions)
    monkeypatch.setattr(cli, "verify_release_next_actions_report", fake_next_actions_verify)

    assert main(["release", "handoff", "--refresh-certification", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "RELEASE_HANDOFF_OPEN"
    assert payload["certification_refresh"]["status"] == "CERTIFICATION_REFRESH_OK"
    assert payload["certification_refresh"]["requested_steps"] == [
        "local_release",
        "python_dist",
        "source_artifact",
        "fresh_clone",
    ]
    assert payload["certification_refresh"]["timeout_seconds"] == 0
    assert [step["step_id"] for step in payload["certification_refresh"]["steps"]] == [
        "local_release",
        "python_dist",
        "source_artifact",
        "fresh_clone",
    ]
    refresh_log = payload["certification_refresh"]["log"]
    refresh_log_payload = json.loads(Path(refresh_log["path"]).read_text(encoding="utf-8"))
    assert refresh_log["schema_version"] == cli.RELEASE_REFRESH_LOG_SCHEMA_VERSION
    assert refresh_log_payload["schema_version"] == cli.RELEASE_REFRESH_LOG_SCHEMA_VERSION
    assert refresh_log_payload["head_sha"] == head_sha
    assert refresh_log_payload["refresh"]["status"] == "CERTIFICATION_REFRESH_OK"
    assert refresh_log_payload["refresh"]["total_elapsed_seconds"] >= 0
    assert refresh_log_payload["refresh"]["slowest_steps"]
    assert refresh_log_payload["refresh"]["requested_steps"] == [
        "local_release",
        "python_dist",
        "source_artifact",
        "fresh_clone",
    ]
    assert payload["current_head_receipts_summary"]["total"] == 8
    assert receipt_calls["count"] == 2
    assert calls == [
        "refresh:local_release",
        "refresh:python_dist",
        "refresh:source_artifact",
        "refresh:fresh_clone",
        "distribution",
        "distribution_verify",
        "support",
        "support_verify",
        "governance_verify",
        "next_actions",
        "next_actions_write",
        "next_actions_verify",
    ]


def test_cli_release_handoff_refresh_step_selects_canonical_subset(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "f" * 40
    calls: list[str] = []
    receipt_calls = {"count": 0}
    monotonic_values = iter([0.0, 10.0, 12.5, 20.0, 24.25, 30.0])

    distribution_path = tmp_path / "data" / "exports" / "distribution_manifests" / f"distribution-manifest-{head_sha[:12]}.json"
    governance_path = tmp_path / "data" / "exports" / "governance_reports" / f"governance-report-{head_sha[:12]}.json"
    support_path = tmp_path / "data" / "exports" / "support_bundles" / f"support-bundle-{head_sha[:12]}.json"
    next_actions_path = tmp_path / "data" / "exports" / "release_next_actions" / f"next-actions-{head_sha[:12]}.json"

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        return False, f"unexpected command: {args}"

    def fake_run(cmd: list[str], **kwargs):
        assert kwargs["timeout"] == 9
        command_text = " ".join(cmd)
        if "certify_python_distribution.py" in command_text:
            calls.append("refresh:python_dist")
            return subprocess.CompletedProcess(cmd, 0, stdout="PYTHON_DIST_CERT_OK\n")
        if "certify_fresh_clone.py" in command_text:
            assert cmd[-1] == head_sha
            calls.append("refresh:fresh_clone")
            return subprocess.CompletedProcess(cmd, 0, stdout="FRESH_CLONE_CERT_OK\n")
        raise AssertionError(f"unexpected subprocess command: {cmd}")

    def fake_receipts(_root: Path):
        receipt_calls["count"] += 1
        if receipt_calls["count"] == 1:
            return []
        return [
            {"head_sha": head_sha, "receipt_type": f"receipt_{index}", "status": "OK", "valid": True}
            for index in range(8)
        ]

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "list_release_receipts", fake_receipts)
    monkeypatch.setattr(
        cli,
        "build_distribution_manifest",
        lambda _root, *, output_path=None, save=False: {
            "export": {"path": str(distribution_path), "sha256": "d" * 64, "size_bytes": 10}
        },
    )
    monkeypatch.setattr(
        cli,
        "verify_distribution_manifest",
        lambda *_args, **_kwargs: {
            "status": "DISTRIBUTION_MANIFEST_VERIFIED",
            "artifact": {"path": str(distribution_path), "sha256": "d" * 64},
        },
    )
    monkeypatch.setattr(
        cli,
        "build_release_support_bundle",
        lambda *_args, **_kwargs: {
            "case_status": "HOSTED_GOVERNANCE_OPEN",
            "governance_report": {"path": str(governance_path)},
            "export": {"path": str(support_path), "sha256": "s" * 64, "size_bytes": 11},
        },
    )
    monkeypatch.setattr(
        cli,
        "verify_release_support_bundle",
        lambda *_args, **_kwargs: {
            "status": "SUPPORT_BUNDLE_VERIFIED",
            "artifact": {"path": str(support_path), "sha256": "s" * 64},
        },
    )
    monkeypatch.setattr(
        cli,
        "verify_release_governance_report",
        lambda *_args, **_kwargs: {
            "status": "GOVERNANCE_REPORT_VERIFIED",
            "artifact": {"path": str(governance_path), "sha256": "g" * 64},
        },
    )
    monkeypatch.setattr(
        cli,
        "build_release_next_actions_report",
        lambda *_args, **_kwargs: {
            "head_sha": head_sha,
            "tag_at_head": ["v0.1.23-handoff-refresh"],
            "hosted_governance_status": "OPEN",
            "release_dashboard_status": "RELEASE_DASHBOARD_OPEN",
            "release_state_status": "RELEASE_STATE_OPEN",
            "github_actions_status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
        },
    )
    monkeypatch.setattr(
        cli,
        "write_release_next_actions_report",
        lambda *_args, **_kwargs: {"path": str(next_actions_path), "sha256": "n" * 64, "size_bytes": 13},
    )
    monkeypatch.setattr(
        cli,
        "verify_release_next_actions_report",
        lambda *_args, **_kwargs: {
            "status": "RELEASE_NEXT_ACTIONS_VERIFIED",
            "artifact": {"path": str(next_actions_path), "sha256": "n" * 64},
        },
    )

    assert (
        main(
            [
                "release",
                "handoff",
                "--refresh-step",
                "fresh_clone",
                "--refresh-step",
                "python_dist",
                "--refresh-timeout-seconds",
                "9",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["certification_refresh"]["requested"] is True
    assert payload["certification_refresh"]["status"] == "CERTIFICATION_REFRESH_OK"
    assert payload["certification_refresh"]["requested_steps"] == ["python_dist", "fresh_clone"]
    assert payload["certification_refresh"]["timeout_seconds"] == 9
    assert payload["certification_refresh"]["total_elapsed_seconds"] == 30.0
    assert [step["step_id"] for step in payload["certification_refresh"]["steps"]] == ["python_dist", "fresh_clone"]
    assert [step["elapsed_seconds"] for step in payload["certification_refresh"]["steps"]] == [2.5, 4.25]
    assert [step["step_id"] for step in payload["certification_refresh"]["slowest_steps"]] == ["fresh_clone", "python_dist"]
    assert calls == [
        "refresh:python_dist",
        "refresh:fresh_clone",
    ]


def test_cli_release_handoff_refresh_timeout_stops_before_handoff_artifacts(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40
    calls: list[str] = []

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        return False, f"unexpected command: {args}"

    def fake_run(cmd: list[str], **kwargs):
        assert kwargs["timeout"] == 1
        calls.append("refresh:local_release")
        raise subprocess.TimeoutExpired(cmd, 1, output="still running\n")

    def unexpected_artifact_write(*_args, **_kwargs):
        raise AssertionError("release handoff should not write artifacts after certification refresh timeout")

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "list_release_receipts", lambda _root: [])
    monkeypatch.setattr(cli, "build_distribution_manifest", unexpected_artifact_write)
    monkeypatch.setattr(cli, "build_release_support_bundle", unexpected_artifact_write)
    monkeypatch.setattr(cli, "write_release_next_actions_report", unexpected_artifact_write)

    assert (
        main(
            [
                "release",
                "handoff",
                "--refresh-certification",
                "--refresh-timeout-seconds",
                "1",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    step = payload["certification_refresh"]["steps"][0]

    assert payload["status"] == "RELEASE_HANDOFF_FAILED"
    assert payload["certification_refresh"]["status"] == "CERTIFICATION_REFRESH_FAILED"
    assert payload["certification_refresh"]["timeout_seconds"] == 1
    assert step["step_id"] == "local_release"
    assert step["timed_out"] is True
    assert step["returncode"] is None
    assert step["detail"] == "TIMEOUT_AFTER_SECONDS:1"
    assert step["output_tail"] == ["still running"]
    assert step["elapsed_seconds"] >= 0
    assert payload["certification_refresh"]["slowest_steps"][0]["step_id"] == "local_release"
    assert payload["artifacts"] == {}
    assert calls == ["refresh:local_release"]


def test_release_handoff_refresh_log_preserves_repeated_attempts(tmp_path: Path):
    cfg = make_settings(tmp_path)
    head_sha = "b" * 40
    refresh = {
        "requested": True,
        "status": "CERTIFICATION_REFRESH_FAILED",
        "requested_steps": ["fresh_clone"],
        "timeout_seconds": 1,
        "steps": [],
    }

    first = cli.write_release_handoff_refresh_log(cfg, refresh, head_sha)
    second = cli.write_release_handoff_refresh_log(cfg, refresh, head_sha)

    assert first["path"] != second["path"]
    assert Path(first["path"]).exists()
    assert Path(second["path"]).exists()
    assert Path(first["path"]).name.startswith(f"refresh-{head_sha[:12]}-")
    assert Path(second["path"]).name.startswith(f"refresh-{head_sha[:12]}-")


def test_cli_release_handoff_refresh_failure_stops_before_handoff_artifacts(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "e" * 40
    calls: list[str] = []

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        return False, f"unexpected command: {args}"

    def fake_run(cmd: list[str], **_kwargs):
        calls.append("refresh")
        return subprocess.CompletedProcess(cmd, 2, stdout="CERTIFY_RELEASE_FAILED\n")

    def unexpected_artifact_write(*_args, **_kwargs):
        raise AssertionError("release handoff should not write artifacts after certification refresh failure")

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "list_release_receipts", lambda _root: [])
    monkeypatch.setattr(cli, "build_distribution_manifest", unexpected_artifact_write)
    monkeypatch.setattr(cli, "build_release_support_bundle", unexpected_artifact_write)
    monkeypatch.setattr(cli, "write_release_next_actions_report", unexpected_artifact_write)

    assert main(["release", "handoff", "--refresh-certification", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["status"] == "RELEASE_HANDOFF_FAILED"
    assert payload["certification_refresh"]["status"] == "CERTIFICATION_REFRESH_FAILED"
    assert [step["step_id"] for step in payload["certification_refresh"]["steps"]] == ["local_release"]
    assert payload["artifacts"] == {}
    assert checks["certification_refresh:local_release"]["status"] == "FAIL"
    assert calls == ["refresh"]


def test_cli_release_handoff_fails_before_writing_when_worktree_is_dirty(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "c" * 40

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, " M backend/app/cli.py"
        return False, f"unexpected command: {args}"

    def unexpected_artifact_write(*_args, **_kwargs):
        raise AssertionError("release handoff should not write artifacts from a dirty worktree")

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli, "list_release_receipts", lambda _root: [])
    monkeypatch.setattr(cli, "build_distribution_manifest", unexpected_artifact_write)
    monkeypatch.setattr(cli, "build_release_support_bundle", unexpected_artifact_write)
    monkeypatch.setattr(cli, "write_release_next_actions_report", unexpected_artifact_write)

    assert main(["release", "handoff", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["status"] == "RELEASE_HANDOFF_FAILED"
    assert payload["counts"] == {"pass": 1, "open": 0, "fail": 1, "total": 2}
    assert checks["current_head"]["status"] == "PASS"
    assert checks["worktree_clean"]["status"] == "FAIL"
    assert payload["artifacts"] == {}


def test_cli_index_status_reports_missing_index(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["index", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INDEX_MISSING"
    assert payload["chunk_count"] == 0
    assert payload["stale"] is False
    assert payload["semantic_store"]["status"] == "SEMANTIC_STORE_MISSING"


def test_cli_sources_list_reindex_and_delete(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Local assistants need approved files and rebuildable indexes.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["sources", "list", "--format", "json"], cfg=cfg) == 0
    sources_payload = json.loads(capsys.readouterr().out)
    assert sources_payload["status"] == "SOURCES_OK"
    assert sources_payload["index"]["status"] == "INDEX_OK"
    assert sources_payload["index"]["semantic_store"]["status"] == "SEMANTIC_STORE_OK"
    source_id = sources_payload["sources"][0]["source_id"]

    assert main(["sources", "reindex", "--format", "text"], cfg=cfg) == 0
    reindex_output = capsys.readouterr().out
    assert "REINDEX_OK" in reindex_output
    assert "index_status=INDEX_OK" in reindex_output
    assert "semantic_store_status=SEMANTIC_STORE_OK" in reindex_output

    assert main(["sources", "delete", source_id, "--format", "json"], cfg=cfg) == 0
    delete_payload = json.loads(capsys.readouterr().out)
    assert delete_payload["status"] == "SOURCE_DELETE_OK"
    assert delete_payload["remaining_sources"] == 0

    assert main(["sources", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "SOURCES_OK" in list_output
    assert "sources=0" in list_output


def test_cli_conversations_ask_and_runs(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["conversations", "create", "--title", "Lineage", "--format", "json"], cfg=cfg) == 0
    created = json.loads(capsys.readouterr().out)
    conversation_id = created["conversation_id"]
    assert created["title"] == "Lineage"

    assert main(["conversations", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "CONVERSATIONS_OK" in list_output
    assert conversation_id in list_output

    assert (
        main(
            [
                "conversations",
                "ask",
                conversation_id,
                "What",
                "does",
                "source",
                "lineage",
                "require?",
                "--strategy",
                "persistent",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    asked = json.loads(capsys.readouterr().out)
    assert asked["answer"]["citation_status"] == "CITATION_OK"
    assert asked["conversation"]["conversation_id"] == conversation_id
    assert len(asked["conversation"]["turns"]) == 2
    run_id = asked["answer"]["run_id"]

    assert main(["runs", "summary", "--format", "json"], cfg=cfg) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "RUN_HISTORY_OK"
    assert summary["run_count"] == 1
    assert summary["latest_run_id"] == run_id

    assert main(["runs", "show", run_id, "--format", "text"], cfg=cfg) == 0
    run_output = capsys.readouterr().out
    assert "CITATION_OK" in run_output
    assert "persistent_tfidf_v1" in run_output

    assert main(["conversations", "rename", conversation_id, "Renamed", "Lineage", "--format", "json"], cfg=cfg) == 0
    renamed = json.loads(capsys.readouterr().out)
    assert renamed["title"] == "Renamed Lineage"

    assert main(["conversations", "delete", conversation_id, "--format", "json"], cfg=cfg) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["status"] == "CONVERSATION_DELETE_OK"

    assert main(["runs", "show", run_id, "--format", "json"], cfg=cfg) == 0
    preserved = json.loads(capsys.readouterr().out)
    assert preserved["run_id"] == run_id


def test_cli_conversation_and_run_missing_ids(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["conversations", "show", "conv_missing", "--format", "json"], cfg=cfg) == 2
    missing_conversation = json.loads(capsys.readouterr().out)
    assert missing_conversation["status"] == "ERROR_CONVERSATION_NOT_FOUND"

    assert main(["runs", "show", "run_missing", "--format", "json"], cfg=cfg) == 2
    missing_run = json.loads(capsys.readouterr().out)
    assert missing_run["status"] == "ERROR_RUN_NOT_FOUND"


def test_cli_tools_require_approval_and_audit_execution(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["tools", "list", "--format", "text"], cfg=cfg) == 0
    tools_output = capsys.readouterr().out
    assert "TOOLS_OK" in tools_output
    assert "source_library.status" in tools_output
    assert "memory.list" in tools_output

    assert (
        main(
            [
                "tools",
                "request",
                "source_library.status",
                "--reason",
                "readiness check",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    requested = json.loads(capsys.readouterr().out)
    tool_call_id = requested["tool_call"]["tool_call_id"]
    assert requested["tool_call"]["status"] == "PENDING_APPROVAL"

    assert main(["tools", "run", tool_call_id, "--format", "json"], cfg=cfg) == 2
    denied_run = json.loads(capsys.readouterr().err)
    assert denied_run["status"] == "ERROR_TOOL_APPROVAL_REQUIRED"

    assert main(["tools", "approve", tool_call_id, "--format", "json"], cfg=cfg) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["tool_call"]["status"] == "APPROVED"

    assert main(["tools", "run", tool_call_id, "--format", "json"], cfg=cfg) == 0
    executed = json.loads(capsys.readouterr().out)
    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "SOURCE_LIBRARY_STATUS_OK"

    assert main(["tools", "audit", "--format", "json"], cfg=cfg) == 0
    audit = json.loads(capsys.readouterr().out)
    assert [event["event"] for event in audit["events"]] == [
        "TOOL_CALL_REQUESTED",
        "TOOL_CALL_APPROVED",
        "TOOL_CALL_STARTED",
        "TOOL_CALL_EXECUTED",
    ]


def test_cli_memory_controls_and_export(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["memory", "remember", "Prefer", "concise", "answers.", "--kind", "preference", "--format", "json"], cfg=cfg) == 0
    saved = json.loads(capsys.readouterr().out)
    memory_id = saved["memory"]["memory_id"]
    assert saved["status"] == "MEMORY_SAVED"
    assert saved["memory"]["enabled"] is True

    assert main(["memory", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "MEMORY_OK" in list_output
    assert "enabled=1" in list_output
    assert memory_id in list_output

    assert main(["memory", "disable", memory_id, "--format", "json"], cfg=cfg) == 0
    disabled = json.loads(capsys.readouterr().out)
    assert disabled["memory"]["enabled"] is False

    assert main(["memory", "list", "--active-only", "--format", "json"], cfg=cfg) == 0
    active_only = json.loads(capsys.readouterr().out)
    assert active_only["memory_count"] == 0

    assert main(["memory", "enable", memory_id, "--format", "json"], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["memory"]["enabled"] is True

    assert main(["memory", "export", "--format", "json"], cfg=cfg) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["status"] == "MEMORY_EXPORT_OK"
    assert exported["memory_count"] == 1

    assert main(["memory", "delete", memory_id, "--format", "json"], cfg=cfg) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["status"] == "MEMORY_DELETE_OK"
    assert deleted["remaining_memories"] == 0


def test_cli_certify_operator_workflow_exercises_local_boundary(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["certify", "operator-workflow", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "OPERATOR_WORKFLOW_CERT_OK"
    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["source_count"] == 2
    assert payload["summary"]["run_count"] == 1
    assert payload["summary"]["tool_audit_event_count"] == 4
    assert payload["summary"]["trace_export_count"] == 3
    assert payload["receipt"] == "data/exports/release_receipts/operator_workflow-UNKNOWN_HEAD.json"
    by_name = {check["name"]: check for check in payload["checks"]}
    assert by_name["permission_required_before_grant"]["status"] == "PASS"
    assert by_name["source_ingestion_and_indexes"]["status"] == "PASS"
    assert by_name["ask_trace_memory_and_exports"]["status"] == "PASS"
    assert by_name["tool_denied_before_approval"]["status"] == "PASS"
    assert by_name["tool_approval_execution_audit"]["status"] == "PASS"

    receipt = tmp_path / payload["receipt"]
    assert receipt.exists()
    saved_receipt = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved_receipt["receipt_type"] == "operator_workflow"
    assert saved_receipt["status"] == "OPERATOR_WORKFLOW_CERT_OK"

    assert main(["certify", "operator-workflow", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "OPERATOR_WORKFLOW_CERT_OK" in text_output
    assert "PASS permission_required_before_grant" in text_output
    assert "PASS tool_approval_execution_audit" in text_output


def test_cli_security_status_and_serve_public_bind_guard(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path, operator_token="x" * 32)

    assert main(["security", "status", "--host", "127.0.0.1", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "SECURITY_OK"

    assert main(["security", "status", "--host", "0.0.0.0", "--format", "text"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "SECURITY_FAIL" in output
    assert "bind_host" in output

    called = False

    def fake_run(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["serve", "--host", "0.0.0.0"], cfg=cfg) == 2
    assert called is False


def test_cli_serve_passes_host_port_and_reload(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    captured = {}

    def fake_run(app: str, *, host: str, port: int, reload: bool) -> None:
        captured.update({"app": app, "host": host, "port": port, "reload": reload})

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["serve", "--host", "127.0.0.1", "--port", "8123", "--reload"], cfg=cfg) == 0
    assert captured == {
        "app": "backend.app.main:app",
        "host": "127.0.0.1",
        "port": 8123,
        "reload": True,
    }


def test_cli_serve_rejects_invalid_port(capsys):
    try:
        main(["serve", "--port", "70000"])
    except SystemExit as exc:
        assert exc.code == 2
    error_output = capsys.readouterr().err
    assert "PORT_RANGE:1-65535" in error_output


def test_cli_ingest_missing_path_returns_structured_error(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["ingest", "missing.md", "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_INPUT_PATH_NOT_FOUND" in error_output
    assert "missing.md" in error_output


def test_cli_ingest_external_path_requires_permission(tmp_path: Path, capsys):
    project = tmp_path / "project"
    cfg = make_settings(project)
    external = tmp_path / "external"
    external.mkdir()
    sample = external / "sample.md"
    sample.write_text("External directory access is permission-gated.", encoding="utf-8")

    assert main(["ingest", str(external), "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_DIRECTORY_PERMISSION_REQUIRED" in error_output

    assert main(["permissions", "grant", str(external), "--yes", "--format", "json"], cfg=cfg) == 0
    grant_output = capsys.readouterr().out
    assert "DIRECTORY_PERMISSION_GRANTED" in grant_output

    assert main(["ingest", str(external), "--format", "json"], cfg=cfg) == 0
    ingest_output = capsys.readouterr().out
    assert "INGEST_FOLDER_OK" in ingest_output
    assert "PERMISSION_OK" in ingest_output


def test_cli_permission_grant_requires_confirmation_when_not_yes(tmp_path: Path, capsys):
    project = tmp_path / "project"
    cfg = make_settings(project)
    external = tmp_path / "external"
    external.mkdir()

    assert main(["permissions", "grant", str(external), "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_PERMISSION_CONFIRMATION_REQUIRED" in error_output


def test_cli_ask_no_sources_returns_error_status(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["ask", "What is source lineage?", "--format", "json", "--no-persist"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "ERROR_NO_SOURCES" in output


def test_cli_ask_invalid_top_k_returns_error_status(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    try:
        main(["ask", "What is source lineage?", "--top-k", "0", "--format", "json"], cfg=cfg)
    except SystemExit as exc:
        assert exc.code == 2
    error_output = capsys.readouterr().err
    assert "TOP_K_RANGE:1-20" in error_output


def test_cli_ocr_structure_derives_saved_form_receipt(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    report_path = tmp_path / "ocr-form.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "mucaro_ocr_report_v1",
                "report_id": "ocrreport_cli_form",
                "source": {"sha256": "c" * 64},
                "engine": {"name": "tesseract", "version": "fixture"},
                "spans": [
                    {
                        "span_index": 0,
                        "raw_text": "Name:",
                        "page_index": 0,
                        "block_num": 1,
                        "paragraph_num": 1,
                        "line_num": 1,
                        "word_num": 1,
                        "bbox": {"x": 100, "y": 100, "width": 120, "height": 24},
                    },
                    {
                        "span_index": 1,
                        "raw_text": "Alice",
                        "page_index": 0,
                        "block_num": 1,
                        "paragraph_num": 1,
                        "line_num": 1,
                        "word_num": 2,
                        "bbox": {"x": 300, "y": 100, "width": 130, "height": 24},
                    },
                    {
                        "span_index": 2,
                        "raw_text": "City:",
                        "page_index": 0,
                        "block_num": 1,
                        "paragraph_num": 1,
                        "line_num": 2,
                        "word_num": 1,
                        "bbox": {"x": 100, "y": 190, "width": 120, "height": 24},
                    },
                    {
                        "span_index": 3,
                        "raw_text": "Ponce",
                        "page_index": 0,
                        "block_num": 1,
                        "paragraph_num": 1,
                        "line_num": 2,
                        "word_num": 2,
                        "bbox": {"x": 300, "y": 190, "width": 130, "height": 24},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["ocr", "structure", str(report_path), "--kind", "form", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OCR_FORM_RECONSTRUCTION_OK"
    assert payload["structure"]["field_count"] == 2
    assert payload["structure"]["certification_state"] == "CANDIDATE_NOT_IDENTITY"


def test_cli_ocr_structure_returns_nonzero_for_unresolved(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    report_path = tmp_path / "ocr-single-column.json"
    spans = []
    for index, y in enumerate((50, 120, 190, 260)):
        spans.append(
            {
                "span_index": index,
                "raw_text": f"Line{index}",
                "page_index": 0,
                "block_num": 1,
                "paragraph_num": 1,
                "line_num": index + 1,
                "word_num": 1,
                "bbox": {"x": 50, "y": y, "width": 180, "height": 24},
            }
        )
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "mucaro_ocr_report_v1",
                "report_id": "ocrreport_cli_single",
                "source": {"sha256": "d" * 64},
                "engine": {"name": "tesseract"},
                "spans": spans,
            }
        ),
        encoding="utf-8",
    )
    assert main(
        ["ocr", "structure", str(report_path), "--kind", "multi_column", "--format", "json"],
        cfg=cfg,
    ) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OCR_STRUCTURE_UNRESOLVED"
    assert payload["structure"]["certification_state"] == "UNRESOLVED"
