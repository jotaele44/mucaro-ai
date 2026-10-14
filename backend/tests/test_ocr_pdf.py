from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pypdf import PdfWriter

from backend.app import ocr_pdf
from backend.app.capability_contracts import CapabilityOutputContractError, validate_pdf_ocr_report_file
from backend.app.config import Settings
from backend.app.ocr_pdf import PDFOCRAnalysisError, analyze_pdf_ocr


def make_settings(root: Path) -> Settings:
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
    return cfg


def make_pdf(path: Path, pages: int = 2) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def minimal_png(width: int = 100, height: int = 120) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def renderer_runner_for(page_count: int):
    def runner(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        del timeout
        if command[-1] == "-v" or "-v" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="pdftoppm version 24.01")
        prefix = Path(command[-1])
        for page_number in range(1, page_count + 1):
            prefix.with_name(f"{prefix.name}-{page_number}.png").write_bytes(minimal_png())
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return runner


def ocr_runner(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    del timeout
    if "--version" in command:
        return subprocess.CompletedProcess(command, 0, stdout="tesseract 5.4.0\n", stderr="")
    page_name = Path(command[1]).name
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    if page_name == "page-2.png":
        return subprocess.CompletedProcess(command, 0, stdout=header, stderr="")
    row = "5\t1\t1\t1\t1\t1\t10\t12\t30\t11\t96.5\tAlpha\n"
    return subprocess.CompletedProcess(command, 0, stdout=header + row, stderr="")


def test_pdf_ocr_preserves_page_lineage_and_no_text_residue(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    pdf = make_pdf(tmp_path / "two-pages.pdf", pages=2)

    report = analyze_pdf_ocr(
        pdf,
        cfg=cfg,
        renderer_path="/usr/bin/pdftoppm",
        tesseract_path="/usr/bin/tesseract",
        renderer_runner=renderer_runner_for(2),
        ocr_runner=ocr_runner,
    )

    assert report["status"] == "OCR_PDF_WARN"
    assert report["source"]["page_count"] == 2
    assert report["invariants"]["page_accounting_closed"] is True
    assert report["invariants"]["page_indices_contiguous"] is True
    assert report["invariants"]["rendered_page_count"] == 2
    assert report["invariants"]["ocr_page_count"] == 2
    assert report["invariants"]["total_span_count"] == 1
    assert report["invariants"]["no_text_page_count"] == 1
    assert report["pages"][0]["ocr_status"] == "OCR_ANALYSIS_OK"
    assert report["pages"][1]["ocr_status"] == "OCR_ANALYSIS_NO_TEXT"
    assert all(page["rendered_sha256"] for page in report["pages"])
    assert all(page["ocr_report_sha256"] for page in report["pages"])
    assert "OCR_PDF_NO_TEXT_PAGES_PRESENT" in report["warnings"]
    validation = validate_pdf_ocr_report_file(Path(report["report_path"]))
    assert validation["status"] == "CAPABILITY_OUTPUT_CONTRACT_OK"
    assert validation["counts"]["fail"] == 0


def test_pdf_ocr_fails_closed_on_rendered_page_count_mismatch(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    pdf = make_pdf(tmp_path / "two-pages.pdf", pages=2)

    with pytest.raises(PDFOCRAnalysisError) as exc:
        analyze_pdf_ocr(
            pdf,
            cfg=cfg,
            renderer_path="/usr/bin/pdftoppm",
            tesseract_path="/usr/bin/tesseract",
            renderer_runner=renderer_runner_for(1),
            ocr_runner=ocr_runner,
        )
    assert exc.value.status == "ERROR_OCR_PDF_PAGE_COUNT_MISMATCH"


def test_pdf_ocr_rejects_non_pdf_and_invalid_dpi(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    text = tmp_path / "not-pdf.txt"
    text.write_text("x", encoding="utf-8")
    with pytest.raises(PDFOCRAnalysisError) as unsupported:
        analyze_pdf_ocr(text, cfg=cfg, renderer_path="pdftoppm", tesseract_path="tesseract")
    assert unsupported.value.status == "ERROR_OCR_PDF_UNSUPPORTED_INPUT"

    pdf = make_pdf(tmp_path / "one.pdf", pages=1)
    with pytest.raises(PDFOCRAnalysisError) as dpi:
        analyze_pdf_ocr(pdf, cfg=cfg, dpi=20, renderer_path="pdftoppm", tesseract_path="tesseract")
    assert dpi.value.status == "ERROR_OCR_PDF_DPI"


def test_pdf_report_contract_fails_before_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    report = {"report_id": "ocr_pdf_contract_failure"}
    report_path = cfg.export_dir / "ocr_reports" / f"{report['report_id']}.json"

    def reject_report(report: dict[str, object], *, report_path: str | None = None) -> None:
        raise CapabilityOutputContractError("ERROR_OCR_PDF_REPORT_CONTRACT", "test contract rejection", report=report)

    monkeypatch.setattr(ocr_pdf, "require_pdf_ocr_report_contract", reject_report)

    with pytest.raises(CapabilityOutputContractError) as exc:
        ocr_pdf._write_report(cfg, report)

    assert exc.value.status == "ERROR_OCR_PDF_REPORT_CONTRACT"
    assert not report_path.exists()
