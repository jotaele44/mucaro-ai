from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

try:  # POSIX advisory locks; macOS and Linux are the supported platforms.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback keeps in-process locking only
    fcntl = None  # type: ignore[assignment]

T = TypeVar("T")
_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "should",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stable_id(prefix: str, *parts: str, length: int = 16) -> str:
    raw = "||".join(parts).encode("utf-8", errors="replace")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:length]}"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Any) -> None:
    """Atomically replace path. Each writer uses its own temp file, fsynced before rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive lock for a read-modify-write of path, across threads and processes (SD-008)."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(str(lock_path.resolve()), threading.Lock())
    with thread_lock:
        with lock_path.open("a+") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def update_json(path: Path, default: Any, mutate: Callable[[Any], T]) -> T:
    """Locked read-modify-write: mutate() edits the loaded value in place and returns a result."""
    with file_lock(path):
        data = read_json(path, default)
        result = mutate(data)
        write_json(path, data)
        return result


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9_\-]+", text.lower()) if len(t) > 1 and t not in STOPWORDS]
