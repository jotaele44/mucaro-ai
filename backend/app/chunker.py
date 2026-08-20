from __future__ import annotations

from typing import Any

from .utils import stable_id, utc_now


def chunk_text(source_id: str, text: str, *, chunk_size: int = 900, overlap: int = 150) -> list[dict[str, Any]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size")
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    chunks: list[dict[str, Any]] = []
    start = 0
    idx = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        chunk_text_value = normalized[start:end].strip()
        if chunk_text_value:
            chunk_id = stable_id("chunk", source_id, str(idx), str(start), str(end), chunk_text_value[:80])
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "source_id": source_id,
                    "chunk_index": idx,
                    "text": chunk_text_value,
                    "char_start": start,
                    "char_end": end,
                    "metadata": {"created_at": utc_now()},
                }
            )
            idx += 1
        if end >= len(normalized):
            break
        start = max(0, end - overlap)
    return chunks
