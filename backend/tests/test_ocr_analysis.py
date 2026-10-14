import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from backend.app import ocr_analysis
from backend.app.capability_contracts import CapabilityOutputContractError
from backend.app.config import Settings
from backend.app.ocr_analysis import OCRAnalysisError, analyze_image_ocr


def make_settings(root: Path) -> Settings:
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
    )


def minimal_png(*, width: int = 8, height: int = 6) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def write_image(path: Path) -> Path:
    path.write_bytes(minimal_png())
    return path


def completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["tesseract"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_analyze_image_ocr_preserves_text_confidence_layout_and_receipt(tmp_path: Path):
    cfg = make_settings(tmp_path)
    image = write_image(tmp_path / "scan.png")
    tsv = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t12\t96.4\tMucaro",
            "5\t1\t1\t1\t1\t2\tbad\t25\t18\t12\t42\tAI",
            "5\t1\t1\t1\t1\t3\t50\t25\t18\t12\t-1\t",
            "5\t2\t1\t1\t1\t1\t5\t6\t7\t8\tnan?\tTrace",
        ]
    )
    calls: list[list[str]] = []

    def fake_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-1] == "--version":
            return completed(stdout="tesseract 5.3.0\n")
        return completed(stdout=tsv)

    result = analyze_image_ocr(
        image,
        cfg=cfg,
        language="eng",
        layout_mode="auto",
        tesseract_path="/usr/local/bin/tesseract",
        runner=fake_runner,
    )

    assert result["status"] == "OCR_ANALYSIS_OK"
    assert result["source"]["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert result["source"]["mime_type"] == "image/png"
    assert result["source"]["width"] == 8
    assert result["source"]["height"] == 6
    assert result["engine"]["version"] == "tesseract 5.3.0"
    assert result["engine"]["language"] == "eng"
    assert result["engine"]["layout_mode"] == "auto"
    assert result["engine"]["page_segmentation_mode"] == "3"
    assert result["text"]["raw"] == "Mucaro\nAI\nTrace"
    assert result["text"]["normalized"] == "Mucaro\nAI\nTrace"
    assert result["text"]["canonical"] == "mucaro\nai\ntrace"
    assert result["invariants"]["span_count"] == 3
    assert result["invariants"]["low_confidence_count"] == 1
    assert result["invariants"]["unknown_confidence_count"] == 1
    assert result["spans"][0]["confidence_state"] == "HIGH"
    assert result["spans"][1]["confidence_state"] == "LOW"
    assert result["spans"][1]["bbox"]["x"] == 0
    assert result["spans"][2]["confidence_state"] == "UNKNOWN"
    assert result["spans"][2]["page_index"] == 1
    assert result["warnings"] == ["OCR_LOW_CONFIDENCE_PRESENT", "OCR_UNKNOWN_CONFIDENCE_PRESENT"]
    assert calls[0][-1] == "tsv"
    assert calls[1] == ["/usr/local/bin/tesseract", "--version"]
    receipt = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert receipt["report_id"] == result["report_id"]
    assert result["report_sha256"]


def test_analyze_image_ocr_records_no_text_without_synthesis(tmp_path: Path):
    cfg = make_settings(tmp_path)
    image = write_image(tmp_path / "blank.png")

    def fake_runner(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        if command[-1] == "--version":
            return completed(stdout="tesseract 5.3.0\n")
        return completed(stdout="level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n")

    result = analyze_image_ocr(image, cfg=cfg, tesseract_path="/usr/bin/tesseract", runner=fake_runner)

    assert result["status"] == "OCR_ANALYSIS_NO_TEXT"
    assert result["text"] == {"raw": "", "normalized": "", "canonical": ""}
    assert result["spans"] == []
    assert result["invariants"]["empty_text_state"] is True
    assert result["warnings"] == ["OCR_NO_TEXT_DETECTED"]


def test_analyze_image_ocr_fails_closed_when_engine_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = make_settings(tmp_path)
    image = write_image(tmp_path / "scan.png")
    monkeypatch.setattr("backend.app.ocr_analysis.resolve_tool_request", lambda *args, **kwargs: None)

    with pytest.raises(OCRAnalysisError) as exc:
        analyze_image_ocr(image, cfg=cfg)

    assert exc.value.status == "ERROR_OCR_ENGINE_UNAVAILABLE"


def test_analyze_image_ocr_rejects_unsupported_inputs(tmp_path: Path):
    cfg = make_settings(tmp_path)
    document = tmp_path / "note.txt"
    document.write_text("not an image", encoding="utf-8")

    with pytest.raises(OCRAnalysisError) as exc:
        analyze_image_ocr(document, cfg=cfg, tesseract_path="/usr/bin/tesseract")

    assert exc.value.status == "ERROR_OCR_UNSUPPORTED_INPUT"


def test_analyze_image_ocr_rejects_invalid_layout_mode(tmp_path: Path):
    cfg = make_settings(tmp_path)
    image = write_image(tmp_path / "scan.png")

    with pytest.raises(OCRAnalysisError) as exc:
        analyze_image_ocr(image, cfg=cfg, layout_mode="columns", tesseract_path="/usr/bin/tesseract")

    assert exc.value.status == "ERROR_OCR_LAYOUT_INVALID"


def test_ocr_report_contract_fails_before_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = make_settings(tmp_path)
    report = {"report_id": "ocr_report_contract_failure"}
    report_path = cfg.export_dir / "ocr_reports" / f"{report['report_id']}.json"

    def reject_report(report: dict[str, object], *, report_path: str | None = None) -> None:
        raise CapabilityOutputContractError("ERROR_OCR_REPORT_CONTRACT", "test contract rejection", report=report)

    monkeypatch.setattr(ocr_analysis, "require_ocr_report_contract", reject_report)

    with pytest.raises(CapabilityOutputContractError) as exc:
        ocr_analysis._write_report(cfg, report)

    assert exc.value.status == "ERROR_OCR_REPORT_CONTRACT"
    assert not report_path.exists()
