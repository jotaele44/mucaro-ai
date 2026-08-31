from __future__ import annotations

import builtins
import wave
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
