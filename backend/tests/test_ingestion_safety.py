from __future__ import annotations

import json
import os
import socket
import stat
import threading
import zipfile
from dataclasses import replace
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.archive_safety import (
    ArchiveLimits,
    ArchiveSafetyError,
    inspect_zip,
    member_manifest,
    read_member_limited,
    safe_extract_zip,
)
from backend.app.cli import main
from backend.app.file_signatures import detect_signature
from backend.app.ingestion import IngestLimitError, get_chunks, ingest_file, ingest_folder
from backend.app.main import create_app
from backend.app.parser import ParseError, parse_file
from backend.app.web_browsing import WebBrowsingError, WebFetchPolicy, fetch_web_snapshot
from backend.tests.test_api import make_settings

ELF_HEADER = b"\x7fELF\x02\x01\x01" + b"\x00" * 57


def _pe_bytes() -> bytes:
    head = bytearray(b"MZ" + b"\x00" * 126)
    head[60:64] = (64).to_bytes(4, "little")
    head[64:68] = b"PE\x00\x00"
    return bytes(head)


def _zip(path: Path, members: list[tuple[str, bytes]], *, mode: int | None = None, flag_bits: int = 0) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode is not None:
                info.external_attr = mode << 16
            info.flag_bits |= flag_bits
            archive.writestr(info, data)
    return path


def _set_encrypted_flag(path: Path) -> Path:
    """zipfile cannot write encrypted members; set general-purpose bit 0 in both headers."""
    data = bytearray(path.read_bytes())
    local = data.find(b"PK\x03\x04")
    central = data.find(b"PK\x01\x02")
    data[local + 6] |= 0x1
    data[central + 8] |= 0x1
    path.write_bytes(bytes(data))
    return path


def _docx(path: Path, document_xml: bytes) -> Path:
    return _zip(
        path,
        [
            ("[Content_Types].xml", b"<Types/>"),
            ("word/document.xml", document_xml),
        ],
    )


# --- archive safety (TGT-023) -------------------------------------------------


def test_safe_zip_is_inventoried_and_extracts_text_members(tmp_path):
    archive = _zip(tmp_path / "bundle.zip", [("notes/a.md", b"# Alpha lineage"), ("data/b.csv", b"x,y\n1,2\n"), ("img.png", b"\x89PNG\r\n\x1a\n")])
    report = inspect_zip(archive)
    assert report["status"] == "ARCHIVE_SAFE"
    assert report["member_count"] == 3
    parsed = parse_file(archive)
    assert parsed["parser"] == "zip_archive_v1"
    assert "Alpha lineage" in parsed["text"]
    assert parsed["text_member_count"] == 2
    assert "ZIP_NON_TEXT_MEMBERS_NOT_EXTRACTED:1" in parsed["warnings"]
    manifest = member_manifest(archive)
    assert [row["path"] for row in manifest] == ["data/b.csv", "img.png", "notes/a.md"]
    assert all(len(row["sha256"]) == 64 for row in manifest)


@pytest.mark.parametrize(
    "member,finding",
    [
        ("../escape.txt", "PATH_TRAVERSAL"),
        ("a/../../escape.txt", "PATH_TRAVERSAL"),
        ("/etc/cron.d/evil", "ABSOLUTE_PATH"),
        ("C:/Windows/evil.txt", "ABSOLUTE_PATH"),
        ("dir\\evil.txt", "BACKSLASH_PATH"),
    ],
)
def test_traversal_members_make_archive_unsafe_and_nothing_escapes(tmp_path, member, finding):
    archive = _zip(tmp_path / "evil.zip", [(member, b"payload")])
    report = inspect_zip(archive)
    assert report["status"] == "ARCHIVE_UNSAFE"
    assert finding in {row["finding"] for row in report["findings"]}
    destination = tmp_path / "out"
    with pytest.raises(ArchiveSafetyError) as exc:
        safe_extract_zip(archive, destination)
    assert exc.value.status == "ERROR_ARCHIVE_UNSAFE"
    assert not (tmp_path / "escape.txt").exists()
    with pytest.raises(ParseError) as parse_exc:
        parse_file(archive)
    assert parse_exc.value.status == "ERROR_ARCHIVE_UNSAFE"


def test_symlink_executable_and_encrypted_members_are_flagged(tmp_path):
    link = _zip(tmp_path / "link.zip", [("link", b"/etc/passwd")], mode=stat.S_IFLNK | 0o777)
    exe_mode = _zip(tmp_path / "exe_mode.zip", [("run", b"echo hi")], mode=stat.S_IFREG | 0o755)
    exe_suffix = _zip(tmp_path / "exe_suffix.zip", [("setup.exe", b"data")])
    encrypted = _set_encrypted_flag(_zip(tmp_path / "enc.zip", [("secret.txt", b"data")]))
    assert "SYMLINK_MEMBER" in {row["finding"] for row in inspect_zip(link)["findings"]}
    assert "EXECUTABLE_MEMBER" in {row["finding"] for row in inspect_zip(exe_mode)["findings"]}
    assert "EXECUTABLE_MEMBER" in {row["finding"] for row in inspect_zip(exe_suffix)["findings"]}
    assert "ENCRYPTED_MEMBER" in {row["finding"] for row in inspect_zip(encrypted)["findings"]}


def test_decompression_bomb_is_rejected_by_ratio_and_by_actual_bytes(tmp_path):
    bomb = _zip(tmp_path / "bomb.zip", [("big.txt", b" " * (8 * 1024 * 1024))])
    report = inspect_zip(bomb)
    assert report["status"] == "ARCHIVE_UNSAFE"
    assert "COMPRESSION_RATIO_EXCEEDED" in {row["finding"] for row in report["findings"]}
    with zipfile.ZipFile(bomb) as archive, pytest.raises(ArchiveSafetyError) as exc:
        read_member_limited(archive, "big.txt", 1024 * 1024)
    assert exc.value.status == "ERROR_ARCHIVE_MEMBER_TOO_LARGE"


def test_member_count_and_total_limits_are_enforced(tmp_path):
    archive = _zip(tmp_path / "many.zip", [(f"f{i}.txt", b"x") for i in range(5)])
    report = inspect_zip(archive, ArchiveLimits(max_members=3))
    assert "TOO_MANY_MEMBERS" in {row["finding"] for row in report["findings"]}


def test_extracted_executable_payload_is_refused(tmp_path):
    archive = _zip(tmp_path / "hidden.zip", [("readme.txt", ELF_HEADER)])
    with pytest.raises(ArchiveSafetyError) as exc:
        safe_extract_zip(archive, tmp_path / "out")
    assert exc.value.status == "ERROR_ARCHIVE_EXECUTABLE_PAYLOAD"


def test_safe_extract_writes_non_executable_files_inside_destination(tmp_path):
    archive = _zip(tmp_path / "ok.zip", [("a/b.txt", b"hello")])
    result = safe_extract_zip(archive, tmp_path / "out")
    written = tmp_path / "out" / "a" / "b.txt"
    assert result["status"] == "ARCHIVE_EXTRACTED"
    assert written.read_bytes() == b"hello"
    assert not os.access(written, os.X_OK)


def test_malformed_and_truncated_archives_fail_closed(tmp_path):
    good = _zip(tmp_path / "good.zip", [("a.txt", b"hello world" * 100)])
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(good.read_bytes()[:40])
    assert inspect_zip(truncated)["status"] == "ARCHIVE_MALFORMED"
    with pytest.raises(ParseError) as exc:
        parse_file(truncated)
    assert exc.value.status == "ERROR_ARCHIVE_MALFORMED"


def test_docx_decompression_bomb_is_rejected(tmp_path):
    body = b"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body>" + b" " * (6 * 1024 * 1024) + b"</w:body></w:document>"
    path = _docx(tmp_path / "bomb.docx", body)
    with pytest.raises(ParseError) as exc:
        parse_file(path)
    assert exc.value.status == "ERROR_ARCHIVE_UNSAFE"


# --- signatures and mislabeled content (INV-007) ------------------------------


@pytest.mark.parametrize(
    "name,content,status",
    [
        ("photo.png", b"%PDF-1.7\n", "ERROR_SIGNATURE_EXTENSION_MISMATCH"),
        ("notes.txt", b"%PDF-1.4\nbinary", "ERROR_SIGNATURE_EXTENSION_MISMATCH"),
        ("report.pdf", b"PK\x03\x04rest", "ERROR_SIGNATURE_EXTENSION_MISMATCH"),
        ("readme.md", ELF_HEADER, "ERROR_EXECUTABLE_PAYLOAD"),
        ("invoice.pdf", _pe_bytes(), "ERROR_EXECUTABLE_PAYLOAD"),
        ("clip.mp4", b"\xcf\xfa\xed\xfe" + b"\x00" * 60, "ERROR_EXECUTABLE_PAYLOAD"),
    ],
)
def test_mislabeled_and_executable_payloads_fail_closed(tmp_path, name, content, status):
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(ParseError) as exc:
        parse_file(path)
    assert exc.value.status == status


def test_plain_text_starting_with_mz_is_not_a_false_positive(tmp_path):
    path = tmp_path / "memo.txt"
    path.write_text("MZ Holdings quarterly memo. " * 20, encoding="utf-8")
    assert detect_signature(path.read_bytes()[:4096]) is None
    assert parse_file(path)["parser"] == "text_utf8"


# --- folder ingestion boundaries (SD-001) -------------------------------------


def test_symlink_inside_granted_folder_is_not_followed(tmp_path):
    cfg = make_settings(tmp_path / "proj")
    outside = tmp_path / "outside_secret.md"
    outside.write_text("TOP-SECRET-OUTSIDE-GRANT", encoding="utf-8")
    folder = tmp_path / "granted"
    folder.mkdir()
    (folder / "ok.md").write_text("Visible note", encoding="utf-8")
    (folder / "leak.md").symlink_to(outside)
    (folder / "nested").mkdir()
    (folder / "tool.exe").write_bytes(b"data")

    result = ingest_folder(folder, cfg)

    assert result["files_ingested"] == 1
    reasons = {row["name"]: row["reason"] for row in result["skipped"]}
    assert reasons == {"leak.md": "SYMLINK_NOT_FOLLOWED", "nested": "SUBDIRECTORY_NOT_RECURSED", "tool.exe": "UNSUPPORTED_FORMAT"}
    assert result["entries_seen"] == result["files_ingested"] + len(result["skipped"]) + len(result["failed"])
    assert not any("TOP-SECRET" in chunk["text"] for chunk in get_chunks(cfg))


def test_folder_ingest_accounts_for_failures_without_aborting(tmp_path):
    cfg = make_settings(tmp_path / "proj")
    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "good.md").write_text("Good source", encoding="utf-8")
    (folder / "fake.png").write_bytes(b"%PDF-1.4")
    result = ingest_folder(folder, cfg)
    assert result["status"] == "INGEST_FOLDER_PARTIAL"
    assert result["files_ingested"] == 1
    assert result["failed"][0]["name"] == "fake.png"
    assert result["failed"][0]["status"] == "ERROR_SIGNATURE_EXTENSION_MISMATCH"
    assert result["entries_seen"] == 2


def test_oversized_file_is_rejected_before_hashing(tmp_path):
    cfg = replace(make_settings(tmp_path / "proj"), max_ingest_bytes=10)
    path = tmp_path / "big.md"
    path.write_text("x" * 11, encoding="utf-8")
    with pytest.raises(IngestLimitError) as exc:
        ingest_file(path, cfg)
    assert exc.value.status == "ERROR_INGEST_FILE_TOO_LARGE"


def test_api_upload_of_mislabeled_file_reports_status_and_discards_upload(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    response = client.post("/ingest", files={"upload": ("photo.png", b"%PDF-1.7 not a png", "image/png")})
    assert response.status_code == 422
    assert response.json()["detail"]["status"] == "ERROR_SIGNATURE_EXTENSION_MISMATCH"
    assert not (tmp_path / "data" / "input" / "photo.png").exists()


def test_cli_ingest_of_executable_payload_exits_with_status(tmp_path, capsys):
    cfg = make_settings(tmp_path)
    path = tmp_path / "notes.md"
    path.write_bytes(ELF_HEADER)
    assert main(["ingest", str(path), "--format", "json"], cfg=cfg) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "ERROR_EXECUTABLE_PAYLOAD"


# --- DNS rebinding (SD-011) ---------------------------------------------------


class _Quiet(SimpleHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body>internal only</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


def test_dns_rebinding_to_private_address_is_blocked_at_connect_time(tmp_path, monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    calls = {"count": 0}
    real = socket.getaddrinfo

    def rebinding_getaddrinfo(host, *args, **kwargs):
        if host == "rebind.example":
            calls["count"] += 1
            address = "93.184.215.14" if calls["count"] == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    try:
        with pytest.raises(WebBrowsingError) as exc:
            fetch_web_snapshot(
                f"http://rebind.example:{port}/page",
                cfg=make_settings(tmp_path),
                policy=WebFetchPolicy(respect_robots=False),
            )
    finally:
        server.shutdown()
    assert exc.value.status == "ERROR_WEB_PRIVATE_HOST_BLOCKED"
    assert "connect time" in exc.value.detail
    assert calls["count"] >= 2
