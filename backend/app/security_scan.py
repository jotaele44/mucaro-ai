"""Local-only, fail-closed repository security candidate scanner. No raw source in results."""
from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from pathlib import Path

from .coding_workspace import CodingWorkspaceError, _git_output, inspect_repository
from .config import Settings, settings

VERSION = "mucaro-security-scan/1.0"
PATTERNS = (
    ("DYNAMIC_EVAL", "MEDIUM", re.compile(r"\beval\s*\(")),
    ("DYNAMIC_EXEC", "MEDIUM", re.compile(r"\bexec\s*\(")),
    ("SHELL_TRUE", "HIGH", re.compile(r"\bshell\s*=\s*True\b")),
    ("OS_SYSTEM", "MEDIUM", re.compile(r"\bos\.system\s*\(")),
    ("TLS_VERIFY_DISABLED", "HIGH", re.compile(r"\bverify\s*=\s*False\b")),
)
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".mp4", ".zip", ".gz", ".sqlite", ".tif", ".woff", ".ico", ".icns"}
HEAD_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BYTE_LIMIT = 262144


class SecurityScanError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def preflight_repository(path: str, *, cfg: Settings = settings) -> dict:
    try:
        state = inspect_repository(path, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise SecurityScanError(exc.status, exc.detail) from exc
    if state["dirty_status"] != "CLEAN":
        raise SecurityScanError("ERROR_SCAN_DIRTY", "Clean the checkout before scanning.")
    if not HEAD_PATTERN.fullmatch(state["head_sha"]):
        raise SecurityScanError("ERROR_SCAN_HEAD", "Expected a full Git commit SHA.")
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise SecurityScanError("ERROR_SCAN_UNSUPPORTED_PLATFORM", "Race-resistant file access unavailable.")
    return {
        "status": "SCAN_TARGET_READY", "schema_version": VERSION,
        "repo_root": state["repo_root"], "head_sha": state["head_sha"],
        "tracked_file_count": state["tracked_file_count"], "permission_scope": state["permission_scope"],
    }


def _read_file(root: Path, relative: str) -> tuple[str, bytes | None]:
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in ("", ".", "..", ".git") for p in parts):
        raise SecurityScanError("ERROR_SCAN_PATH", "Unsafe tracked path.")
    fds = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(fd)
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            fds.append(fd)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        fds.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return "NONREGULAR", None
        if before.st_size > BYTE_LIMIT:
            return "OVERSIZE", None
        raw = os.read(fd, BYTE_LIMIT + 1)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ) or len(raw) != before.st_size:
            raise SecurityScanError("ERROR_SCAN_CHANGED", "File changed during read.")
        return "READ", raw
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return "NONREGULAR", None
        raise SecurityScanError("ERROR_SCAN_READ", "Tracked file is unreadable.") from exc
    finally:
        for fd in reversed(fds):
            os.close(fd)


def scan_repository(path: str, expected_head: str, *, cfg: Settings = settings, max_files: int = 1000) -> dict:
    if not isinstance(max_files, int) or isinstance(max_files, bool) or not 1 <= max_files <= 1000:
        raise SecurityScanError("ERROR_SCAN_LIMIT", "max_files must be 1..1000.")
    if not isinstance(expected_head, str) or not HEAD_PATTERN.fullmatch(expected_head):
        raise SecurityScanError("ERROR_SCAN_EXPECTED_HEAD", "Full lowercase expected HEAD required.")
    initial = preflight_repository(path, cfg=cfg)
    if initial["head_sha"] != expected_head:
        raise SecurityScanError("ERROR_SCAN_STALE_HEAD", "HEAD changed since preflight.")
    root = Path(initial["repo_root"])
    try:
        names = [x for x in _git_output(root, ["ls-files", "--cached", "-z", "--"]).split("\0") if x]
    except CodingWorkspaceError as exc:
        raise SecurityScanError(exc.status, exc.detail) from exc
    if len(names) != len(set(names)):
        raise SecurityScanError("ERROR_SCAN_DUPLICATE", "Duplicate Git index path.")
    if len(names) != initial["tracked_file_count"]:
        raise SecurityScanError("ERROR_SCAN_COUNT", "Git index count drift.")
    if len(names) > max_files:
        raise SecurityScanError("ERROR_SCAN_FILE_LIMIT", "Scan target exceeds file limit.")
    counts = {"SCANNED": 0, "NONREGULAR": 0, "BINARY": 0, "OVERSIZE": 0, "SUFFIX": 0}
    findings = []
    digest = hashlib.sha256()
    for name in sorted(names):
        if Path(name).suffix.lower() in SKIP_SUFFIXES:
            counts["SUFFIX"] += 1
            continue
        kind, raw = _read_file(root, name)
        if kind != "READ":
            counts[kind] += 1
            continue
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            counts["BINARY"] += 1
            continue
        counts["SCANNED"] += 1
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(raw).digest())
        for line_number, line in enumerate(decoded.splitlines(), 1):
            for rule, severity, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append({
                        "path": name, "line": line_number, "rule": rule, "severity": severity,
                        "source_line_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                        "classification": "CANDIDATE_NOT_IDENTITY",
                    })
                    if len(findings) > 100:
                        raise SecurityScanError("ERROR_SCAN_FINDING_LIMIT", "Too many candidates; no partial result.")
    final = preflight_repository(path, cfg=cfg)
    if final["head_sha"] != expected_head or final["tracked_file_count"] != len(names):
        raise SecurityScanError("ERROR_SCAN_TARGET_CHANGED", "Target changed during scan.")
    if sum(counts.values()) != len(names):
        raise SecurityScanError("ERROR_SCAN_ACCOUNTING", "Unclassified tracked file.")
    excluded = len(names) - counts["SCANNED"]
    return {
        "status": "SCAN_BOUNDED" if excluded == 0 else "SCAN_AUDIT_ONLY_PARTIAL",
        "schema_version": VERSION, "head_sha": expected_head,
        "tracked_file_count": len(names), "counts": counts, "excluded_count": excluded,
        "finding_count": len(findings), "findings": findings,
        "scanned_content_manifest_sha256": digest.hexdigest(),
        "invariants": {"tracked_count_closed": True, "finding_count_closed": True},
        "limitations": ["Heuristic findings are not proven vulnerabilities; zero findings proves no security property.",
                        "Tracked clean regular UTF-8 files only; ignored, binary, symlink and excluded files not scanned.",
                        "Results contain no source excerpts or secret values; not a complete security assessment."],
    }
