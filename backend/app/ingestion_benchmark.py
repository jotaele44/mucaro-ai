from __future__ import annotations

import hashlib
import io
import json
import wave
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import Settings
from .format_registry import declared_format_registry
from .ingestion import IngestLimitError, ingest_file, ingest_folder
from .parser import ParseError
from .utils import utc_now

BENCHMARK_SCHEMA_VERSION = "mucaro-matrix010-ingestion-cert/1.0"
STATUS_OK = "MATRIX010_INGESTION_CERT_OK"
STATUS_FAILED = "MATRIX010_INGESTION_CERT_FAILED"


class IngestionBenchmarkError(RuntimeError):
    pass


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _sha256_bytes(raw)


def _settings(root: Path, *, max_ingest_bytes: int | None = None) -> Settings:
    data = root / "data"
    cfg = Settings(
        project_root=root,
        data_dir=data,
        input_dir=data / "input",
        processed_dir=data / "processed",
        export_dir=data / "exports",
        index_dir=data / "indexes",
        sqlite_path=data / "processed" / "matrix010.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    return replace(cfg, max_ingest_bytes=max_ingest_bytes) if max_ingest_bytes is not None else cfg


def _minimal_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii"))
        out.extend(payload)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def _minimal_jpeg(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\xff\xd8"
        + b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + b"\xff\xd9"
    )


def _write_docx(path: Path) -> None:
    document = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        "<w:body><w:p><w:r><w:t>MATRIX-010 DOCX benchmark source.</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 800)


def _write_fixture(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        path.write_text("MATRIX-010 plain text benchmark source.", encoding="utf-8")
    elif suffix == ".md":
        path.write_text("# MATRIX-010\nMarkdown benchmark source.", encoding="utf-8")
    elif suffix == ".csv":
        path.write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")
    elif suffix == ".json":
        path.write_text(json.dumps({"matrix": 10, "status": "benchmark"}), encoding="utf-8")
    elif suffix in {".html", ".htm"}:
        path.write_text("<html><title>MATRIX-010</title><body><p>HTML benchmark source.</p></body></html>", encoding="utf-8")
    elif suffix == ".docx":
        _write_docx(path)
    elif suffix == ".zip":
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("notes/source.md", "# MATRIX-010 ZIP benchmark source.")
    elif suffix == ".eml":
        path.write_text(
            "From: source@example.test\nTo: reviewer@example.test\nSubject: MATRIX-010\n\nEmail benchmark source.",
            encoding="utf-8",
        )
    elif suffix == ".msg":
        path.write_bytes(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
            + b"\x00" * 128
            + "Subject: MATRIX-010 MSG".encode("utf-16le")
            + b"\x00" * 16
            + "Body: Outlook benchmark source.".encode("utf-16le")
        )
    elif suffix == ".pdf":
        path.write_bytes(_minimal_pdf("MATRIX-010 PDF benchmark source."))
    elif suffix == ".png":
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (3).to_bytes(4, "big") + (2).to_bytes(4, "big")
        )
    elif suffix in {".jpg", ".jpeg"}:
        path.write_bytes(_minimal_jpeg())
    elif suffix == ".gif":
        path.write_bytes(b"GIF89a" + (3).to_bytes(2, "little") + (2).to_bytes(2, "little") + b"\x00" * 8)
    elif suffix == ".webp":
        path.write_bytes(b"RIFF" + (8).to_bytes(4, "little") + b"WEBPVP8 ")
    elif suffix == ".wav":
        _write_wav(path)
    elif suffix == ".mp3":
        path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00MATRIX010")
    elif suffix == ".m4a":
        path.write_bytes(b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom")
    elif suffix == ".mp4":
        path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2")
    elif suffix == ".mov":
        path.write_bytes(b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00qt  ")
    elif suffix == ".webm":
        path.write_bytes(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01MATRIX010")
    else:
        raise IngestionBenchmarkError(f"no benchmark fixture writer for {suffix}")


def _set_encrypted_flag(path: Path) -> None:
    data = bytearray(path.read_bytes())
    local = data.find(b"PK\x03\x04")
    central = data.find(b"PK\x01\x02")
    if local < 0 or central < 0:
        raise IngestionBenchmarkError("encrypted fixture missing ZIP headers")
    data[local + 6] |= 0x1
    data[central + 8] |= 0x1
    path.write_bytes(bytes(data))


def _positive_rows(registry: dict[str, Any], workspace: Path) -> list[dict[str, Any]]:
    cfg = _settings(workspace / "positive-runtime")
    fixtures = workspace / "positive-fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for declared in registry["formats"]:
        suffix = str(declared["suffix"])
        path = fixtures / f"matrix010-{suffix.lstrip('.')}{suffix}"
        _write_fixture(path)
        fixture_sha = _sha256_file(path)
        result = ingest_file(path, cfg)
        source = result["source"]
        checks = {
            "status": result.get("status") == "INGEST_OK",
            "parser": source.get("parser") == declared["parser"],
            "mime_type": source.get("mime_type") == declared["mime_type"],
            "source_sha256": source.get("source_sha256") == fixture_sha,
            "provenance_sha256": source.get("provenance", {}).get("raw_byte_sha256") == fixture_sha,
            "input_path_binding": str(source.get("source_path") or "").startswith("data/input/"),
            "index_status": result.get("index_status") == "INDEX_OK",
            "semantic_store_status": result.get("semantic_store_status") == "SEMANTIC_STORE_OK",
        }
        rows.append(
            {
                "suffix": suffix,
                "declared_parser": declared["parser"],
                "observed_parser": source.get("parser"),
                "declared_mime_type": declared["mime_type"],
                "observed_mime_type": source.get("mime_type"),
                "fixture_bytes": path.stat().st_size,
                "fixture_sha256": fixture_sha,
                "parse_status": source.get("parse_status"),
                "lineage_status": source.get("lineage_status"),
                "chunk_count": source.get("chunk_count"),
                "warnings": source.get("warnings", []),
                "checks": checks,
                "passed": all(checks.values()),
            }
        )
    return rows


def _status_from_call(call) -> str:
    try:
        call()
    except (ParseError, IngestLimitError) as exc:
        return exc.status
    return "NO_ERROR"


def _adversarial_rows(workspace: Path) -> list[dict[str, Any]]:
    root = workspace / "adversarial"
    root.mkdir(parents=True, exist_ok=True)
    cfg = _settings(root / "runtime")
    rows: list[dict[str, Any]] = []

    malformed = root / "malformed.json"
    malformed.write_text('{"broken": ', encoding="utf-8")
    observed = _status_from_call(lambda: ingest_file(malformed, cfg))
    rows.append({"case_id": "MALFORMED", "expected": "ERROR_PARSE_FAILED", "observed": observed, "passed": observed == "ERROR_PARSE_FAILED"})

    mislabeled = root / "mislabeled.png"
    mislabeled.write_bytes(_minimal_pdf("mislabeled"))
    observed = _status_from_call(lambda: ingest_file(mislabeled, cfg))
    rows.append(
        {
            "case_id": "MISLABELED",
            "expected": "ERROR_SIGNATURE_EXTENSION_MISMATCH",
            "observed": observed,
            "passed": observed == "ERROR_SIGNATURE_EXTENSION_MISMATCH",
        }
    )

    empty = root / "empty.md"
    empty.write_bytes(b"")
    empty_result = ingest_file(empty, cfg)
    empty_source = empty_result["source"]
    empty_safe = (
        empty_result["status"] == "INGEST_OK"
        and empty_source.get("parse_status") == "PARTIAL"
        and empty_source.get("lineage_status") == "PARTIAL"
        and "EMPTY_FILE_NO_TEXT" in empty_source.get("warnings", [])
        and "NO_TEXT_CHUNKS_EXTRACTED" in empty_source.get("warnings", [])
    )
    rows.append({"case_id": "EMPTY", "expected": "SAFE_PARTIAL", "observed": "SAFE_PARTIAL" if empty_safe else "UNSAFE_OR_UNCLASSIFIED", "passed": empty_safe})

    good_zip = root / "good.zip"
    with zipfile.ZipFile(good_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.txt", b"hello world" * 100)
    truncated = root / "truncated.zip"
    truncated.write_bytes(good_zip.read_bytes()[:40])
    observed = _status_from_call(lambda: ingest_file(truncated, cfg))
    rows.append({"case_id": "TRUNCATED", "expected": "ERROR_ARCHIVE_MALFORMED", "observed": observed, "passed": observed == "ERROR_ARCHIVE_MALFORMED"})

    encrypted = root / "encrypted.zip"
    with zipfile.ZipFile(encrypted, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("secret.txt", b"secret")
    _set_encrypted_flag(encrypted)
    observed = _status_from_call(lambda: ingest_file(encrypted, cfg))
    rows.append({"case_id": "PASSWORD_ACCESS_BLOCKED", "expected": "ERROR_ARCHIVE_UNSAFE", "observed": observed, "passed": observed == "ERROR_ARCHIVE_UNSAFE"})

    oversized = root / "oversized.md"
    oversized.write_bytes(b"x" * 11)
    tiny_cfg = _settings(root / "oversized-runtime", max_ingest_bytes=10)
    observed = _status_from_call(lambda: ingest_file(oversized, tiny_cfg))
    rows.append({"case_id": "OVERSIZED", "expected": "ERROR_INGEST_FILE_TOO_LARGE", "observed": observed, "passed": observed == "ERROR_INGEST_FILE_TOO_LARGE"})

    traversal = root / "traversal.zip"
    with zipfile.ZipFile(traversal, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", b"escape")
    observed = _status_from_call(lambda: ingest_file(traversal, cfg))
    rows.append({"case_id": "ARCHIVE_ATTACK", "expected": "ERROR_ARCHIVE_UNSAFE", "observed": observed, "passed": observed == "ERROR_ARCHIVE_UNSAFE"})

    unsupported_folder = root / "unsupported-folder"
    unsupported_folder.mkdir()
    (unsupported_folder / "tool.exe").write_bytes(b"not executable content")
    unsupported = ingest_folder(unsupported_folder, cfg)
    unsupported_safe = (
        unsupported.get("entries_seen") == 1
        and unsupported.get("files_ingested") == 0
        and unsupported.get("failed") == []
        and unsupported.get("skipped") == [{"name": "tool.exe", "reason": "UNSUPPORTED_FORMAT"}]
    )
    rows.append(
        {
            "case_id": "UNSUPPORTED",
            "expected": "UNSUPPORTED_FORMAT",
            "observed": "UNSUPPORTED_FORMAT" if unsupported_safe else "UNSAFE_OR_UNCLASSIFIED",
            "passed": unsupported_safe,
        }
    )
    return rows


def certify_ingestion_benchmark(
    project_root: Path,
    workspace: Path,
    *,
    head_sha: str | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    benchmark_path = project_root / "_architecture" / "ingestion_benchmark_v1.json"
    registry_path = project_root / "_architecture" / "ingest_format_registry_v1.json"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    frozen_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    current_registry = declared_format_registry()

    expected_cases = [row["case_id"] for row in benchmark["adversarial_cases"]]
    registry_match = frozen_registry == current_registry
    denominator_checks = {
        "benchmark_schema": benchmark.get("schema_version") == "mucaro-matrix010-ingestion-benchmark/1.0",
        "registry_schema": frozen_registry.get("schema_version") == "mucaro-ingest-format-registry/1.0",
        "registry_manifest_matches_runtime": registry_match,
        "declared_format_count": frozen_registry.get("format_count") == benchmark.get("required_format_count") == 22,
        "format_rows_count": len(frozen_registry.get("formats", [])) == 22,
        "adversarial_case_count": len(expected_cases) == benchmark.get("required_adversarial_case_count") == 8,
        "adversarial_case_uniqueness": len(set(expected_cases)) == len(expected_cases),
    }
    if not all(denominator_checks.values()):
        receipt = {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "status": STATUS_FAILED,
            "head_sha": head_sha,
            "executed_utc": utc_now(),
            "denominator_checks": denominator_checks,
            "format_rows": [],
            "adversarial_rows": [],
            "failed_checks": [name for name, passed in denominator_checks.items() if not passed],
        }
        receipt["receipt_sha256"] = _stable_hash(receipt)
        return receipt

    format_rows = _positive_rows(frozen_registry, workspace)
    adversarial_rows = _adversarial_rows(workspace)
    observed_case_ids = [row["case_id"] for row in adversarial_rows]
    format_pass = sum(1 for row in format_rows if row["passed"])
    adversarial_pass = sum(1 for row in adversarial_rows if row["passed"])
    arithmetic = {
        "format_count_expected": 22,
        "format_count_observed": len(format_rows),
        "format_pass": format_pass,
        "format_fail": len(format_rows) - format_pass,
        "adversarial_count_expected": 8,
        "adversarial_count_observed": len(adversarial_rows),
        "adversarial_pass": adversarial_pass,
        "adversarial_fail": len(adversarial_rows) - adversarial_pass,
        "format_arithmetic_closes": format_pass + (len(format_rows) - format_pass) == len(format_rows) == 22,
        "adversarial_arithmetic_closes": (
            adversarial_pass + (len(adversarial_rows) - adversarial_pass) == len(adversarial_rows) == 8
        ),
    }
    terminal_checks = {
        "denominator_checks_pass": all(denominator_checks.values()),
        "all_declared_formats_pass": format_pass == 22,
        "all_adversarial_cases_pass": adversarial_pass == 8,
        "adversarial_case_identity_matches": observed_case_ids == expected_cases,
        "zero_unexpected_outcomes": all(row["passed"] for row in format_rows + adversarial_rows),
        "arithmetic_closes": arithmetic["format_arithmetic_closes"] and arithmetic["adversarial_arithmetic_closes"],
    }
    receipt = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "status": STATUS_OK if all(terminal_checks.values()) else STATUS_FAILED,
        "benchmark_id": benchmark["benchmark_id"],
        "head_sha": head_sha,
        "executed_utc": utc_now(),
        "benchmark_manifest_path": str(benchmark_path.relative_to(project_root)),
        "benchmark_manifest_sha256": _sha256_file(benchmark_path),
        "format_registry_path": str(registry_path.relative_to(project_root)),
        "format_registry_sha256": _sha256_file(registry_path),
        "format_registry_rows_sha256": _stable_hash(frozen_registry["formats"]),
        "denominator_checks": denominator_checks,
        "arithmetic": arithmetic,
        "terminal_checks": terminal_checks,
        "format_rows": format_rows,
        "adversarial_rows": adversarial_rows,
        "limitations": [
            "Generated fixtures prove deterministic local ingestion behavior for the declared format registry; they are not a corpus-quality benchmark for arbitrary real-world files.",
            "Image/audio/video formats certify local metadata ingestion only, matching the registry declaration; semantic multimodal understanding remains independent.",
            "A later registry change requires a new frozen MATRIX-010 denominator and receipt.",
        ],
    }
    receipt["receipt_sha256"] = _stable_hash(receipt)
    return receipt
