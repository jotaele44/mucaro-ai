from __future__ import annotations

import builtins
import wave
import zipfile
from pathlib import Path

import pytest

from backend.app.ingestion import ingest_file
from backend.app.config import Settings
from backend.app.parser import ParseError, _parse_pdf, parse_file


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


def minimal_png(width: int = 2, height: int = 3) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def write_minimal_docx(path: Path, *, title: str = "Mucaro DOCX Brief") -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>DOCX source lineage requires cited chunks.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Field</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Evidence</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
  xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/">
  <dc:title>{title}</dc:title>
  <dc:creator>Mucaro Test</dc:creator>
</cp:coreProperties>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("docProps/core.xml", core_xml)


def test_csv_parser_records_delimiter_row_count_and_duplicate_headers(tmp_path: Path):
    path = tmp_path / "sample.csv"
    path.write_text("name;name;score\nAda;Lovelace;10\nGrace;Hopper;9\n", encoding="utf-8")

    result = parse_file(path)

    assert result["parser"] == "csv_dictreader"
    assert result["delimiter"] == ";"
    assert result["row_count"] == 2
    assert result["headers"] == ["name", "name", "score"]
    assert "CSV_DUPLICATE_HEADERS:name" in result["warnings"]
    assert result["parse_status"] == "PARTIAL"


def test_ingest_source_manifest_includes_parser_evidence(tmp_path: Path):
    cfg = make_settings(tmp_path / "proj")
    src = tmp_path / "sample.csv"
    src.write_text("city,value\nSan Juan,1\n", encoding="utf-8")

    result = ingest_file(src, cfg)
    source = result["source"]

    assert source["bytes"] == src.stat().st_size
    assert source["parse_status"] == "OK"
    assert source["parser_warnings"] == []
    assert source["parser_metadata"]["delimiter"] == ","
    assert source["parser_metadata"]["row_count"] == 1


def test_html_parser_extracts_visible_text_and_metadata(tmp_path: Path):
    path = tmp_path / "brief.html"
    path.write_text(
        """
        <!doctype html>
        <html>
          <head>
            <title>Mucaro Source Brief</title>
            <style>.hidden { display: none; }</style>
            <script>window.secret = "ignore me";</script>
          </head>
          <body>
            <h1>Planning Gate</h1>
            <p>HTML ingestion preserves &amp; decodes visible text.</p>
            <a href="https://example.test/source">source</a>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    result = parse_file(path)

    assert result["parser"] == "html_text_v1"
    assert result["mime_type"] == "text/html"
    assert result["title"] == "Mucaro Source Brief"
    assert result["link_count"] == 1
    assert result["parse_status"] == "OK"
    assert "Planning Gate" in result["text"]
    assert "HTML ingestion preserves & decodes visible text." in result["text"]
    assert "ignore me" not in result["text"]
    assert result["text_length"] == len(result["text"])


def test_ingest_html_manifest_includes_parser_evidence(tmp_path: Path):
    cfg = make_settings(tmp_path / "proj")
    src = tmp_path / "brief.html"
    src.write_text("<html><head><title>Brief</title></head><body><p>Cited HTML source.</p></body></html>", encoding="utf-8")

    result = ingest_file(src, cfg)
    source = result["source"]

    assert source["mime_type"] == "text/html"
    assert source["parser"] == "html_text_v1"
    assert source["parser_metadata"]["title"] == "Brief"
    assert source["parser_metadata"]["text_length"] == len("Cited HTML source.")
    assert source["lineage_status"] == "VALID"
    assert result["chunks_added"] == 1


def test_docx_parser_extracts_paragraph_table_text_and_metadata(tmp_path: Path):
    path = tmp_path / "brief.docx"
    write_minimal_docx(path)

    result = parse_file(path)

    assert result["parser"] == "docx_text_v1"
    assert result["mime_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert result["parse_status"] == "OK"
    assert result["paragraph_count"] == 3
    assert result["table_count"] == 1
    assert result["text"] == "DOCX source lineage requires cited chunks.\nField | Evidence"
    assert result["text_length"] == len(result["text"])
    assert result["core_properties"]["title"] == "Mucaro DOCX Brief"
    assert result["core_properties"]["creator"] == "Mucaro Test"


def test_ingest_docx_manifest_includes_parser_evidence(tmp_path: Path):
    cfg = make_settings(tmp_path / "proj")
    src = tmp_path / "brief.docx"
    write_minimal_docx(src, title="Brief")

    result = ingest_file(src, cfg)
    source = result["source"]

    assert source["parser"] == "docx_text_v1"
    assert source["parser_metadata"]["paragraph_count"] == 3
    assert source["parser_metadata"]["table_count"] == 1
    assert source["parser_metadata"]["core_properties"]["title"] == "Brief"
    assert source["lineage_status"] == "VALID"
    assert result["chunks_added"] == 1


def test_eml_parser_extracts_plain_body_headers_and_attachment_metadata(tmp_path: Path):
    path = tmp_path / "brief.eml"
    path.write_text(
        "\n".join(
            [
                "From: sender@example.test",
                "To: reviewer@example.test",
                "Subject: Mucaro Email Brief",
                "Date: Sat, 05 Sep 2026 18:00:00 -0400",
                "Message-ID: <brief@example.test>",
                "MIME-Version: 1.0",
                'Content-Type: multipart/mixed; boundary="outer"',
                "",
                "--outer",
                "Content-Type: text/plain; charset=utf-8",
                "",
                "Email source lineage requires cited chunks.",
                "--outer",
                "Content-Type: application/pdf",
                'Content-Disposition: attachment; filename="evidence.pdf"',
                "",
                "placeholder",
                "--outer--",
            ]
        ),
        encoding="utf-8",
    )

    result = parse_file(path)

    assert result["parser"] == "email_rfc822_v1"
    assert result["mime_type"] == "message/rfc822"
    assert result["headers"]["subject"] == "Mucaro Email Brief"
    assert result["headers"]["message_id"] == "<brief@example.test>"
    assert result["body_part_count"] == 1
    assert result["attachment_count"] == 1
    assert result["extracted_attachment_count"] == 0
    assert result["skipped_attachment_count"] == 1
    assert result["attachments"] == [
        {
            "filename": "evidence.pdf",
            "content_type": "application/pdf",
            "size_bytes": len("placeholder"),
            "extracted": False,
            "skip_reason": "UNSUPPORTED_ATTACHMENT_TYPE",
        }
    ]
    assert "EMAIL_ATTACHMENTS_SKIPPED:1" in result["warnings"]
    assert result["parse_status"] == "PARTIAL"
    assert "Email source lineage requires cited chunks." in result["text"]
    assert "placeholder" not in result["text"]
    assert result["text_length"] == len(result["text"])


def test_eml_parser_uses_html_body_when_plain_text_is_missing(tmp_path: Path):
    path = tmp_path / "html-only.eml"
    path.write_text(
        "\n".join(
            [
                "From: sender@example.test",
                "To: reviewer@example.test",
                "Subject: HTML Email Brief",
                "MIME-Version: 1.0",
                "Content-Type: text/html; charset=utf-8",
                "",
                "<html><body><h1>HTML Mail</h1><p>Cited &amp; decoded body.</p></body></html>",
            ]
        ),
        encoding="utf-8",
    )

    result = parse_file(path)

    assert result["parser"] == "email_rfc822_v1"
    assert result["body_part_count"] == 1
    assert result["attachment_count"] == 0
    assert result["extracted_attachment_count"] == 0
    assert result["skipped_attachment_count"] == 0
    assert "EMAIL_HTML_BODY_USED" in result["warnings"]
    assert "HTML Mail" in result["text"]
    assert "Cited & decoded body." in result["text"]


def test_eml_parser_extracts_text_like_attachments_with_explicit_markers(tmp_path: Path):
    path = tmp_path / "brief-with-attachments.eml"
    path.write_text(
        "\n".join(
            [
                "From: sender@example.test",
                "To: reviewer@example.test",
                "Subject: Mucaro Attachment Brief",
                "MIME-Version: 1.0",
                'Content-Type: multipart/mixed; boundary="outer"',
                "",
                "--outer",
                "Content-Type: text/plain; charset=utf-8",
                "",
                "Email body cites the attached local note.",
                "--outer",
                "Content-Type: text/plain; charset=utf-8",
                'Content-Disposition: attachment; filename="field-note.txt"',
                "",
                "Attachment alpha records pump station inspection evidence.",
                "--outer",
                "Content-Type: application/json",
                'Content-Disposition: attachment; filename="facts.json"',
                "",
                '{"status":"verified","source":"operator"}',
                "--outer",
                "Content-Type: application/pdf",
                'Content-Disposition: attachment; filename="scan.pdf"',
                "",
                "binary placeholder",
                "--outer--",
            ]
        ),
        encoding="utf-8",
    )

    result = parse_file(path)

    assert result["parser"] == "email_rfc822_v1"
    assert result["attachment_count"] == 3
    assert result["extracted_attachment_count"] == 2
    assert result["skipped_attachment_count"] == 1
    assert "EMAIL_ATTACHMENTS_EXTRACTED:2" in result["warnings"]
    assert "EMAIL_ATTACHMENTS_SKIPPED:1" in result["warnings"]
    assert "[EMAIL ATTACHMENT filename=field-note.txt content_type=text/plain]" in result["text"]
    assert "Attachment alpha records pump station inspection evidence." in result["text"]
    assert "[EMAIL ATTACHMENT filename=facts.json content_type=application/json]" in result["text"]
    assert '"source": "operator"' in result["text"]
    assert "binary placeholder" not in result["text"]
    assert result["attachments"][0]["extracted"] is True
    assert result["attachments"][0]["extraction_parser"] == "email_attachment_text_v1"
    assert result["attachments"][1]["extracted"] is True
    assert result["attachments"][1]["extraction_parser"] == "email_attachment_json_v1"
    assert result["attachments"][2]["extracted"] is False
    assert result["attachments"][2]["skip_reason"] == "UNSUPPORTED_ATTACHMENT_TYPE"


def test_ingest_eml_manifest_includes_parser_evidence(tmp_path: Path):
    cfg = make_settings(tmp_path / "proj")
    src = tmp_path / "brief.eml"
    src.write_text(
        "From: sender@example.test\nTo: reviewer@example.test\nSubject: Brief\n\nCited email source.",
        encoding="utf-8",
    )

    result = ingest_file(src, cfg)
    source = result["source"]

    assert source["mime_type"] == "message/rfc822"
    assert source["parser"] == "email_rfc822_v1"
    assert source["parser_metadata"]["headers"]["subject"] == "Brief"
    assert source["parser_metadata"]["body_part_count"] == 1
    assert source["parser_metadata"]["attachment_count"] == 0
    assert source["parser_metadata"]["extracted_attachment_count"] == 0
    assert source["parser_metadata"]["skipped_attachment_count"] == 0
    assert source["lineage_status"] == "VALID"
    assert result["chunks_added"] == 1


def test_msg_parser_extracts_bounded_binary_text_probe(tmp_path: Path):
    path = tmp_path / "brief.msg"
    path.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\x00" * 128
        + "Subject: Mucaro MSG Brief".encode("utf-16le")
        + b"\x00" * 16
        + "Body: Outlook MSG source lineage probe text.".encode("utf-16le")
    )

    result = parse_file(path)

    assert result["parser"] == "outlook_msg_text_probe_v1"
    assert result["mime_type"] == "application/vnd.ms-outlook"
    assert result["parse_status"] == "PARTIAL"
    assert "MSG_HEURISTIC_TEXT_EXTRACTION" in result["warnings"]
    assert "MSG_COMPOUND_MAGIC_MISSING" not in result["warnings"]
    assert "Subject: Mucaro MSG Brief" in result["text"]
    assert "Body: Outlook MSG source lineage probe text." in result["text"]
    assert result["text_length"] == len(result["text"])
    assert result["extracted_text_segments"] >= 2
    assert result["structured_properties_extracted"] is False


def test_ingest_msg_manifest_includes_probe_scope(tmp_path: Path):
    cfg = make_settings(tmp_path / "proj")
    src = tmp_path / "brief.msg"
    src.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\x00" * 128
        + "Subject: Manifest MSG Brief".encode("utf-16le")
        + b"\x00" * 16
        + "Body: MSG manifest source text.".encode("utf-16le")
    )

    result = ingest_file(src, cfg)
    source = result["source"]

    assert source["mime_type"] == "application/vnd.ms-outlook"
    assert source["parser"] == "outlook_msg_text_probe_v1"
    assert source["parser_metadata"]["structured_properties_extracted"] is False
    assert source["parser_metadata"]["extracted_text_segments"] >= 2
    assert source["lineage_status"] == "VALID"
    assert result["chunks_added"] >= 1


def test_invalid_json_raises_parse_error(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text('{"broken": ', encoding="utf-8")

    with pytest.raises(ParseError) as exc:
        parse_file(path)

    assert "PARSE_FAILED:bad.json" in str(exc.value)


def test_empty_file_reports_partial_parse(tmp_path: Path):
    path = tmp_path / "empty.md"
    path.write_text("", encoding="utf-8")

    result = parse_file(path)

    assert result["parse_status"] == "PARTIAL"
    assert "EMPTY_FILE_NO_TEXT" in result["warnings"]
    assert result["bytes"] == 0


def test_pdf_parser_unavailable_reports_warning(tmp_path: Path, monkeypatch):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = _parse_pdf(path)

    assert result["parse_status"] == "PARTIAL"
    assert result["parser"] == "pdf_unavailable"
    assert "PDF_PARSER_UNAVAILABLE_INSTALL_PYPDF" in result["warnings"]


def test_png_parser_records_metadata_text_and_dimensions(tmp_path: Path):
    path = tmp_path / "sample.png"
    path.write_bytes(minimal_png(width=2, height=3))

    result = parse_file(path)

    assert result["parser"] == "image_metadata_v1"
    assert result["media_kind"] == "image"
    assert result["mime_type"] == "image/png"
    assert result["width"] == 2
    assert result["height"] == 3
    assert result["parse_status"] == "OK"
    assert "Multimodal source: sample.png" in result["text"]


def test_wav_parser_records_metadata_duration_and_channels(tmp_path: Path):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 800)

    result = parse_file(path)

    assert result["parser"] == "audio_metadata_v1"
    assert result["media_kind"] == "audio"
    assert result["mime_type"] == "audio/wav"
    assert result["duration_seconds"] == 0.1
    assert result["sample_rate"] == 8000
    assert result["channels"] == 1
    assert result["parse_status"] == "OK"


def test_mp4_parser_records_container_and_duration_warning(tmp_path: Path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2")

    result = parse_file(path)

    assert result["parser"] == "video_metadata_v1"
    assert result["media_kind"] == "video"
    assert result["mime_type"] == "video/mp4"
    assert result["container"] == "iso_base_media"
    assert "VIDEO_DURATION_UNAVAILABLE" in result["warnings"]
    assert result["parse_status"] == "PARTIAL"
