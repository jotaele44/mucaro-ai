from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.coding_certification import CodingCertificationError, build_coding_certification_report
from backend.app.config import Settings


def make_settings(root: Path, requirements: list[dict[str, str]]) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    arch = root / "_architecture"
    arch.mkdir(exist_ok=True)
    (arch / "coding_regression_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "MUCARO_CODING_REGRESSION/test",
                "scope": "test",
                "requirements": requirements,
                "certification_rule": "all pass",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return cfg


def requirements() -> list[dict[str, str]]:
    return [
        {"id": "R1", "class": "positive", "gate": "first gate"},
        {"id": "R2", "class": "negative", "gate": "second gate"},
        {"id": "R3", "class": "certification", "gate": "final gate"},
    ]


def pass_rows() -> list[dict[str, object]]:
    return [
        {"requirement_id": "R1", "state": "PASS", "evidence": ["receipt:r1"]},
        {"requirement_id": "R2", "state": "PASS", "evidence": ["receipt:r2"]},
        {"requirement_id": "R3", "state": "PASS", "evidence": ["receipt:r3"]},
    ]


def test_missing_rows_become_explicit_unresolved_residue(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path, requirements())
    report = build_coding_certification_report([], cfg=cfg)

    assert report["status"] == "CODING_CERTIFICATION_PROVISIONAL"
    assert report["certification_ready"] is False
    assert report["state_counts"] == {"PASS": 0, "FAIL": 0, "BLOCKED": 0, "UNRESOLVED": 3}
    assert report["row_count"] == 3
    assert report["material_residue_count"] == 6
    assert report["identity_residue"] == ["SOURCE_HEAD_IDENTITY", "RUNTIME_IDENTITY", "CODING_IMAGE_IDENTITY"]
    assert report["certification_statement_allowed"] is False
    assert report["allowed_certification_statement"] is None
    assert all(report["invariants"].values())


def test_any_fail_or_blocked_row_prevents_certification(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path, requirements())
    rows = pass_rows()
    rows[1] = {"requirement_id": "R2", "state": "FAIL", "evidence": ["receipt:failure"]}
    report = build_coding_certification_report(
        rows,
        source_head_sha="a" * 40,
        runtime_identity_sha256="b" * 64,
        coding_image_digest="sha256:" + "c" * 64,
        cfg=cfg,
    )
    assert report["status"] == "CODING_CERTIFICATION_FAILED"
    assert report["state_counts"] == {"PASS": 2, "FAIL": 1, "BLOCKED": 0, "UNRESOLVED": 0}
    assert report["certification_ready"] is False
    assert report["material_residue_count"] == 1

    rows[1] = {"requirement_id": "R2", "state": "BLOCKED", "evidence": ["receipt:blocker"]}
    report = build_coding_certification_report(
        rows,
        source_head_sha="a" * 40,
        runtime_identity_sha256="b" * 64,
        coding_image_digest="sha256:" + "c" * 64,
        cfg=cfg,
    )
    assert report["status"] == "CODING_CERTIFICATION_BLOCKED"
    assert report["certification_ready"] is False


def test_all_pass_still_requires_frozen_source_runtime_and_image_identity(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path, requirements())
    report = build_coding_certification_report(pass_rows(), cfg=cfg)

    assert report["state_counts"] == {"PASS": 3, "FAIL": 0, "BLOCKED": 0, "UNRESOLVED": 0}
    assert report["status"] == "CODING_CERTIFICATION_PROVISIONAL"
    assert report["certification_ready"] is False
    assert report["material_residue_count"] == 3
    assert report["identity"]["identity_closed"] is False


def test_all_pass_with_frozen_identities_allows_only_scoped_coding_statement(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path, requirements())
    report = build_coding_certification_report(
        pass_rows(),
        source_head_sha="a" * 40,
        runtime_identity_sha256="b" * 64,
        coding_image_digest="sha256:" + "c" * 64,
        cfg=cfg,
    )

    assert report["status"] == "CODING_CERTIFICATION_READY"
    assert report["certification_ready"] is True
    assert report["material_residue_count"] == 0
    assert report["certification_statement_allowed"] is True
    assert report["allowed_certification_statement"] == "MUCARO AI CODING PLANE CERTIFIED"
    assert all(report["invariants"].values())
    assert len(report["manifest"]["sha256"]) == 64
    assert len(report["report_sha256"]) == 64


def test_duplicate_unknown_invalid_state_and_pass_without_evidence_fail_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path, requirements())

    with pytest.raises(CodingCertificationError) as duplicate:
        build_coding_certification_report(
            [
                {"requirement_id": "R1", "state": "PASS", "evidence": ["one"]},
                {"requirement_id": "R1", "state": "PASS", "evidence": ["two"]},
            ],
            cfg=cfg,
        )
    assert duplicate.value.status == "ERROR_CODING_CERTIFICATION_EVIDENCE_DUPLICATE"

    with pytest.raises(CodingCertificationError) as unknown:
        build_coding_certification_report(
            [{"requirement_id": "RX", "state": "PASS", "evidence": ["one"]}],
            cfg=cfg,
        )
    assert unknown.value.status == "ERROR_CODING_CERTIFICATION_UNKNOWN_REQUIREMENT"

    with pytest.raises(CodingCertificationError) as state:
        build_coding_certification_report(
            [{"requirement_id": "R1", "state": "MAYBE", "evidence": ["one"]}],
            cfg=cfg,
        )
    assert state.value.status == "ERROR_CODING_CERTIFICATION_EVIDENCE_STATE"

    with pytest.raises(CodingCertificationError) as empty:
        build_coding_certification_report(
            [{"requirement_id": "R1", "state": "PASS", "evidence": []}],
            cfg=cfg,
        )
    assert empty.value.status == "ERROR_CODING_CERTIFICATION_PASS_WITHOUT_EVIDENCE"


def test_invalid_identity_shapes_do_not_silently_close_identity_gate(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path, requirements())
    report = build_coding_certification_report(
        pass_rows(),
        source_head_sha="A" * 40,
        runtime_identity_sha256="short",
        coding_image_digest="sha256:XYZ",
        cfg=cfg,
    )

    assert report["certification_ready"] is False
    assert report["identity"]["source_identity_valid"] is False
    assert report["identity"]["runtime_identity_valid"] is False
    assert report["identity"]["image_identity_valid"] is False
    assert report["identity_residue"] == ["SOURCE_HEAD_IDENTITY", "RUNTIME_IDENTITY", "CODING_IMAGE_IDENTITY"]
