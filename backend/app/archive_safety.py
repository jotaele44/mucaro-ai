"""ZIP archive safety (TGT-023, INV-015, INV-019).

Inspection never trusts declared sizes: member reads are bounded by the bytes
actually produced by decompression. An archive is UNSAFE if any member is a
path traversal, absolute path, symlink, executable, encrypted, duplicated, or
exceeds the configured size, ratio, or member-count limits.
"""

from __future__ import annotations

import hashlib
import stat
import tempfile
import unicodedata
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .file_signatures import detect_signature

EXECUTABLE_SUFFIXES = {
    ".app", ".bat", ".bin", ".cmd", ".com", ".command", ".dll", ".dylib", ".exe", ".jar", ".msi", ".pkg",
    ".ps1", ".scr", ".sh", ".so", ".vbs",
}


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = 2000
    max_member_bytes: int = 50 * 1024 * 1024
    max_total_bytes: int = 200 * 1024 * 1024
    max_ratio: float = 100.0


DEFAULT_LIMITS = ArchiveLimits()


class ArchiveSafetyError(ValueError):
    def __init__(self, status: str, detail: str, findings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(f"{status}:{detail}")
        self.status = status
        self.detail = detail
        self.findings = findings or []


def _member_path_problem(name: str) -> str | None:
    if not name:
        return "EMPTY_NAME"
    if "\\" in name:
        return "BACKSLASH_PATH"
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return "ABSOLUTE_PATH"
    parts = PurePosixPath(name).parts
    if any(part == ".." for part in parts):
        return "PATH_TRAVERSAL"
    if "\x00" in name:
        return "NUL_IN_NAME"
    # Windows treats these as streams, device names, or aliases of other paths.
    # Reject them on every platform so a SAFE receipt is portable.
    for part in name.rstrip("/").split("/"):
        if not part or part == "." or part.endswith((".", " ")):
            return "AMBIGUOUS_PATH"
        if any(ord(char) < 32 or char in ':<>"|?*' for char in part):
            return "INVALID_PATH_CHARACTER"
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or (
            len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789¹²³"
        ):
            return "RESERVED_DEVICE_NAME"
    return None


def _unix_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF


def inspect_zip(path: Path | zipfile.ZipFile, limits: ArchiveLimits = DEFAULT_LIMITS) -> dict[str, Any]:
    """Inventory members and classify the archive as SAFE or UNSAFE without extracting."""
    findings: list[dict[str, Any]] = []
    try:
        archive = path if isinstance(path, zipfile.ZipFile) else zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return {"status": "ARCHIVE_MALFORMED", "detail": str(exc), "members": [], "findings": [{"finding": "MALFORMED"}]}
    with nullcontext(archive) if isinstance(path, zipfile.ZipFile) else archive:
        infos = archive.infolist()
        if len(infos) > limits.max_members:
            findings.append({"finding": "TOO_MANY_MEMBERS", "count": len(infos), "limit": limits.max_members})
        seen: set[str] = set()
        declared_total = 0
        members = []
        for info in infos:
            name = info.filename
            problem = _member_path_problem(name)
            if problem:
                findings.append({"finding": problem, "member": name})
            identity = unicodedata.normalize("NFC", name.rstrip("/")).casefold()
            if identity in seen:
                findings.append({"finding": "DUPLICATE_MEMBER", "member": name})
            seen.add(identity)
            mode = _unix_mode(info)
            if stat.S_ISLNK(mode):
                findings.append({"finding": "SYMLINK_MEMBER", "member": name})
            elif stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                findings.append({"finding": "SPECIAL_FILE_MEMBER", "member": name})
            is_dir = info.is_dir()
            if not is_dir and (mode & 0o111 or PurePosixPath(name).suffix.lower() in EXECUTABLE_SUFFIXES):
                findings.append({"finding": "EXECUTABLE_MEMBER", "member": name})
            if info.flag_bits & 0x1:
                findings.append({"finding": "ENCRYPTED_MEMBER", "member": name})
            declared_total += info.file_size
            if info.file_size > limits.max_member_bytes:
                findings.append({"finding": "MEMBER_TOO_LARGE", "member": name, "declared_bytes": info.file_size})
            if info.compress_size > 0 and info.file_size / info.compress_size > limits.max_ratio:
                findings.append({"finding": "COMPRESSION_RATIO_EXCEEDED", "member": name,
                                 "ratio": round(info.file_size / info.compress_size, 1)})
            members.append({"path": name, "is_dir": is_dir, "declared_bytes": info.file_size,
                            "compressed_bytes": info.compress_size})
        if declared_total > limits.max_total_bytes:
            findings.append({"finding": "TOTAL_TOO_LARGE", "declared_bytes": declared_total})
    return {
        "status": "ARCHIVE_SAFE" if not findings else "ARCHIVE_UNSAFE",
        "member_count": len(members),
        "declared_total_bytes": declared_total,
        "members": members,
        "findings": findings,
    }


def read_member_limited(archive: zipfile.ZipFile, name: str, max_bytes: int) -> bytes:
    """Read one member, stopping as soon as actual decompressed output exceeds max_bytes."""
    problem = _member_path_problem(name)
    if problem:
        raise ArchiveSafetyError("ERROR_ARCHIVE_UNSAFE_MEMBER", f"{name}:{problem}")
    chunks: list[bytes] = []
    total = 0
    with archive.open(name) as handle:
        while True:
            block = handle.read(64 * 1024)
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise ArchiveSafetyError("ERROR_ARCHIVE_MEMBER_TOO_LARGE", f"{name}:>{max_bytes}")
            chunks.append(block)
    return b"".join(chunks)


def safe_extract_zip(path: Path, destination: Path, limits: ArchiveLimits = DEFAULT_LIMITS) -> dict[str, Any]:
    """Extract only a SAFE archive into destination; files are written without execute bits."""
    report = inspect_zip(path, limits)
    if report["status"] != "ARCHIVE_SAFE":
        raise ArchiveSafetyError("ERROR_ARCHIVE_UNSAFE", path.name, report["findings"])
    # Extract privately, then publish the complete tree. Never follow existing
    # destination links or overwrite files/hardlinks belonging to the operator.
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ArchiveSafetyError("ERROR_ARCHIVE_DESTINATION_EXISTS", destination.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    extracted = []
    total = 0
    with tempfile.TemporaryDirectory(prefix=".mucaro-extract-", dir=destination.parent) as staging:
        root = Path(staging)
        with zipfile.ZipFile(path) as archive:
            # Validate the handle actually read, even if the source pathname was
            # replaced after the initial inventory.
            opened_report = inspect_zip(archive, limits)
            if opened_report["status"] != "ARCHIVE_SAFE":
                raise ArchiveSafetyError("ERROR_ARCHIVE_UNSAFE", path.name, opened_report["findings"])
            for info in archive.infolist():
                target = root / info.filename
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                data = read_member_limited(archive, info.filename, limits.max_member_bytes)
                total += len(data)
                if total > limits.max_total_bytes:
                    raise ArchiveSafetyError("ERROR_ARCHIVE_TOTAL_TOO_LARGE", f">{limits.max_total_bytes}")
                detected = detect_signature(data[:4096])
                if detected and detected.endswith(("_executable", "_java_class")):
                    raise ArchiveSafetyError("ERROR_ARCHIVE_EXECUTABLE_PAYLOAD", f"{info.filename}:{detected}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(data)
                target.chmod(0o644)
                extracted.append({"path": info.filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        root.rename(destination)
    return {"status": "ARCHIVE_EXTRACTED", "destination": str(destination), "members": extracted}


def member_manifest(path: Path, limits: ArchiveLimits = DEFAULT_LIMITS) -> list[dict[str, Any]]:
    """INV-019 member identity: path, uncompressed size, SHA-256 (bounded reads)."""
    report = inspect_zip(path, limits)
    if report["status"] != "ARCHIVE_SAFE":
        raise ArchiveSafetyError("ERROR_ARCHIVE_UNSAFE", path.name, report["findings"])
    rows = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        opened_report = inspect_zip(archive, limits)
        if opened_report["status"] != "ARCHIVE_SAFE":
            raise ArchiveSafetyError("ERROR_ARCHIVE_UNSAFE", path.name, opened_report["findings"])
        for info in archive.infolist():
            if info.is_dir():
                continue
            data = read_member_limited(archive, info.filename, limits.max_member_bytes)
            total += len(data)
            if total > limits.max_total_bytes:
                raise ArchiveSafetyError("ERROR_ARCHIVE_TOTAL_TOO_LARGE", f">{limits.max_total_bytes}")
            rows.append({"path": info.filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return sorted(rows, key=lambda row: row["path"])
