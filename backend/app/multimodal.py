from __future__ import annotations

import struct
import wave
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
MULTIMODAL_SUFFIXES = IMAGE_SUFFIXES | AUDIO_SUFFIXES | VIDEO_SUFFIXES


def media_kind_for_suffix(suffix: str) -> str | None:
    normalized = suffix.lower()
    if normalized in IMAGE_SUFFIXES:
        return "image"
    if normalized in AUDIO_SUFFIXES:
        return "audio"
    if normalized in VIDEO_SUFFIXES:
        return "video"
    return None


def parse_multimodal_metadata(path: Path, *, mime_type: str) -> dict[str, Any]:
    kind = media_kind_for_suffix(path.suffix)
    if kind is None:
        raise ValueError(f"Unsupported multimodal suffix: {path.suffix}")
    warnings: list[str] = []
    metadata: dict[str, Any] = {
        "media_kind": kind,
        "mime_type": mime_type,
        "file_name": path.name,
        "bytes": path.stat().st_size,
    }
    if kind == "image":
        metadata.update(_parse_image_metadata(path, warnings))
    elif kind == "audio":
        metadata.update(_parse_audio_metadata(path, warnings))
    elif kind == "video":
        metadata.update(_parse_video_metadata(path, warnings))
    text = _metadata_text(metadata, warnings)
    return {
        "text": text,
        "parser": f"{kind}_metadata_v1",
        "mime_type": mime_type,
        "warnings": warnings,
        "parser_warnings": warnings,
        "parse_status": "PARTIAL" if warnings else "OK",
        "bytes": metadata["bytes"],
        **metadata,
    }


def _parse_image_metadata(path: Path, warnings: list[str]) -> dict[str, Any]:
    data = path.read_bytes()[:64]
    suffix = path.suffix.lower()
    if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return {"image_format": "png", "width": width, "height": height}
    if suffix in {".jpg", ".jpeg"}:
        dimensions = _jpeg_dimensions(path)
        if dimensions is not None:
            width, height = dimensions
            return {"image_format": "jpeg", "width": width, "height": height}
    if suffix == ".gif" and data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return {"image_format": "gif", "width": width, "height": height}
    warnings.append("MEDIA_DIMENSIONS_UNAVAILABLE")
    return {"image_format": suffix.lstrip(".") or "unknown"}


def _jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if index + 7 > len(data):
                return None
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            return width, height
        index += segment_length
    return None


def _parse_audio_metadata(path: Path, warnings: list[str]) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                frame_count = audio.getnframes()
                frame_rate = audio.getframerate()
                duration_seconds = round(frame_count / frame_rate, 3) if frame_rate else 0.0
                return {
                    "audio_format": "wav",
                    "duration_seconds": duration_seconds,
                    "sample_rate": frame_rate,
                    "channels": audio.getnchannels(),
                    "sample_width": audio.getsampwidth(),
                }
        except wave.Error:
            warnings.append("AUDIO_WAV_METADATA_UNAVAILABLE")
    warnings.append("AUDIO_DURATION_UNAVAILABLE")
    return {"audio_format": suffix.lstrip(".") or "unknown"}


def _parse_video_metadata(path: Path, warnings: list[str]) -> dict[str, Any]:
    suffix = path.suffix.lower()
    data = path.read_bytes()[:64]
    metadata = {"video_format": suffix.lstrip(".") or "unknown"}
    if suffix in {".mp4", ".mov"} and b"ftyp" in data[:16]:
        metadata["container"] = "iso_base_media"
    elif suffix == ".webm" and data.startswith(b"\x1a\x45\xdf\xa3"):
        metadata["container"] = "webm"
    else:
        warnings.append("VIDEO_CONTAINER_UNCONFIRMED")
    warnings.append("VIDEO_DURATION_UNAVAILABLE")
    return metadata


def _metadata_text(metadata: dict[str, Any], warnings: list[str]) -> str:
    lines = [
        f"Multimodal source: {metadata.get('file_name')}",
        f"Media kind: {metadata.get('media_kind')}",
        f"MIME type: {metadata.get('mime_type')}",
        f"Bytes: {metadata.get('bytes')}",
    ]
    if metadata.get("width") and metadata.get("height"):
        lines.append(f"Dimensions: {metadata['width']} x {metadata['height']}")
    if metadata.get("duration_seconds") is not None:
        lines.append(f"Duration seconds: {metadata['duration_seconds']}")
    if metadata.get("sample_rate"):
        lines.append(f"Sample rate: {metadata['sample_rate']}")
    if metadata.get("channels"):
        lines.append(f"Channels: {metadata['channels']}")
    if metadata.get("container"):
        lines.append(f"Container: {metadata['container']}")
    if warnings:
        lines.append(f"Parser warnings: {', '.join(warnings)}")
    lines.append(
        "Content note: local parser records media metadata only; "
        "semantic image, audio, or video understanding requires a multimodal model adapter."
    )
    return "\n".join(lines)
