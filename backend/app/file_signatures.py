"""Content-signature sniffing for ingestion (INV-007, TGT-012, TGT-023).

Extensions are claims, not evidence. Before a parser trusts a file, the leading
bytes are compared with the claimed type. Two outcomes fail closed:

- an executable payload (PE, ELF, Mach-O) under any supported extension;
- a positively identified *different* known container than the extension claims
  (for example a ZIP labelled ``.png`` or a PDF labelled ``.txt``).

An absent or unrecognized signature is not treated as a mismatch; the format
parsers keep reporting their own ``*_UNCONFIRMED`` warnings in that case.
"""

from __future__ import annotations

from pathlib import Path

SNIFF_BYTES = 4096

EXECUTABLE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x7fELF", "elf_executable"),
    (b"\xfe\xed\xfa\xce", "macho_executable"),
    (b"\xfe\xed\xfa\xcf", "macho_executable"),
    (b"\xce\xfa\xed\xfe", "macho_executable"),
    (b"\xcf\xfa\xed\xfe", "macho_executable"),
    (b"\xca\xfe\xba\xbe", "macho_universal_or_java_class"),
)

# Signature -> detected kind. Order matters: more specific checks first.
_KIND_BY_SUFFIX: dict[str, set[str]] = {
    ".pdf": {"pdf"},
    ".docx": {"zip"},
    ".zip": {"zip"},
    ".png": {"png"},
    ".jpg": {"jpeg"},
    ".jpeg": {"jpeg"},
    ".gif": {"gif"},
    ".webp": {"webp"},
    ".wav": {"wav"},
    ".mp3": {"mp3"},
    ".m4a": {"iso_base_media"},
    ".mp4": {"iso_base_media"},
    ".mov": {"iso_base_media", "quicktime"},
    ".webm": {"ebml"},
    ".msg": {"ole_compound"},
}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".eml", ".geojson"}


class SignatureError(ValueError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(f"{status}:{detail}")
        self.status = status
        self.detail = detail


def _is_pe_executable(head: bytes) -> bool:
    # "MZ" alone is too weak (plain text may start with it); require the PE header
    # that e_lfanew points at.
    if not head.startswith(b"MZ") or len(head) < 64:
        return False
    offset = int.from_bytes(head[60:64], "little")
    return 64 <= offset <= len(head) - 4 and head[offset:offset + 4] == b"PE\x00\x00"


def detect_signature(head: bytes) -> str | None:
    """Return a coarse container kind for well-known signatures, else None."""
    if _is_pe_executable(head):
        return "pe_executable"
    for magic, kind in EXECUTABLE_SIGNATURES:
        if head.startswith(magic):
            return kind
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"ID3") or head[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "mp3"
    if head[4:8] == b"ftyp":
        return "quicktime" if head[8:10] == b"qt" else "iso_base_media"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "ebml"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole_compound"
    if head.startswith((b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")):
        return "compressed_archive"
    return None


def check_signature(path: Path) -> dict[str, str | None]:
    """Raise SignatureError when content contradicts the claimed extension."""
    suffix = path.suffix.lower()
    with path.open("rb") as handle:
        head = handle.read(SNIFF_BYTES)
    detected = detect_signature(head)
    if detected and detected.endswith(("_executable", "_java_class")):
        raise SignatureError("ERROR_EXECUTABLE_PAYLOAD", f"{path.name}:{detected}")
    expected = _KIND_BY_SUFFIX.get(suffix)
    if expected is not None and detected is not None and detected not in expected:
        raise SignatureError("ERROR_SIGNATURE_EXTENSION_MISMATCH", f"{path.name}:claimed={suffix}:detected={detected}")
    if suffix in TEXT_SUFFIXES and detected is not None:
        raise SignatureError("ERROR_SIGNATURE_EXTENSION_MISMATCH", f"{path.name}:claimed={suffix}:detected={detected}")
    return {"claimed_suffix": suffix, "detected_signature": detected}
