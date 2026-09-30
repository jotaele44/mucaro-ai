from pathlib import Path

import pytest

from backend.app.release_receipts import (
    find_release_receipt,
    list_release_receipts,
    load_release_receipt,
    write_release_receipt,
)


def test_release_receipts_follow_external_data_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / "application"
    data_dir = tmp_path / "user-data"
    monkeypatch.setenv("MUCARO_AI_DATA_DIR", str(data_dir))

    path = write_release_receipt(
        project_root,
        receipt_type="operator_workflow",
        status="OPERATOR_WORKFLOW_CERT_OK",
        head_sha="a" * 40,
        command=["certify"],
        payload={"checks": 1},
    )

    expected = data_dir / "exports" / "release_receipts" / f"operator_workflow-{'a' * 12}.json"
    assert path == expected
    assert path.is_file()
    assert not (project_root / "data").exists()
    assert len(list_release_receipts(project_root)) == 1
    assert load_release_receipt(project_root, receipt_type="operator_workflow", head_sha="a" * 40) is not None
    assert find_release_receipt(project_root, path.stem) is not None


def test_release_receipts_reject_relative_external_data_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MUCARO_AI_DATA_DIR", "relative/data")

    with pytest.raises(ValueError, match="must be an absolute path"):
        write_release_receipt(
            tmp_path,
            receipt_type="operator_workflow",
            status="OPERATOR_WORKFLOW_CERT_OK",
            head_sha="b" * 40,
            command=["certify"],
            payload={},
        )
