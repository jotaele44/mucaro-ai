import zipfile
from pathlib import Path

from backend.app.config import Settings
from backend.app.ingestion import ingest_file, ingest_folder


def minimal_png(width: int = 2, height: int = 3) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def write_minimal_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>DOCX folder ingest source.</w:t></w:r></w:p></w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document_xml)


def test_ingest_file_tmp(tmp_path: Path):
    root = tmp_path / "proj"
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
    src = tmp_path / "note.md"
    src.write_text("Execution Gate source lineage citations.", encoding="utf-8")
    result = ingest_file(src, cfg)
    assert result["status"] == "INGEST_OK"
    assert result["source"]["chunk_count"] >= 1
    assert result["index_status"] == "INDEX_OK"
    assert result["indexed_chunk_count"] >= 1
    assert (cfg.processed_dir / "source_manifest.json").exists()
    assert (cfg.index_dir / "tfidf_index.json").exists()


def test_ingest_file_preserves_image_metadata_in_manifest(tmp_path: Path):
    root = tmp_path / "proj"
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
    src = tmp_path / "sample.png"
    src.write_bytes(minimal_png())

    result = ingest_file(src, cfg)

    assert result["status"] == "INGEST_OK"
    assert result["source"]["parser"] == "image_metadata_v1"
    assert result["source"]["parser_metadata"]["media_kind"] == "image"
    assert result["source"]["parser_metadata"]["width"] == 2
    assert result["source"]["parser_metadata"]["height"] == 3
    assert result["source"]["chunk_count"] >= 1
    assert result["semantic_store_status"] == "SEMANTIC_STORE_OK"
    assert result["source"]["source_path"].startswith("data/input/")


def test_ingest_folder_accepts_media_and_html_files(tmp_path: Path):
    root = tmp_path / "proj"
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
    folder = tmp_path / "sources"
    folder.mkdir()
    (folder / "note.md").write_text("Source lineage remains text grounded.", encoding="utf-8")
    (folder / "brief.html").write_text("<html><body><p>HTML source lineage remains grounded.</p></body></html>", encoding="utf-8")
    write_minimal_docx(folder / "brief.docx")
    (folder / "brief.eml").write_text("From: sender@example.test\nSubject: EML Brief\n\nEmail folder ingest source.", encoding="utf-8")
    (folder / "brief.msg").write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 128 + "MSG folder ingest source.".encode("utf-16le")
    )
    (folder / "sample.png").write_bytes(minimal_png())

    result = ingest_folder(folder, cfg)

    assert result["status"] == "INGEST_FOLDER_OK"
    assert result["files_ingested"] == 6
    assert {item["source"]["parser"] for item in result["results"]} == {
        "text_utf8",
        "html_text_v1",
        "docx_text_v1",
        "email_rfc822_v1",
        "outlook_msg_text_probe_v1",
        "image_metadata_v1",
    }
