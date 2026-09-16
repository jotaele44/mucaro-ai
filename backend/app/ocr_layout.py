from __future__ import annotations

from collections import defaultdict
from typing import Any


def derive_layout_receipt(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive engine-evidence layout groups without inventing semantic document structure."""
    groups: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        key = (int(span.get("page_index", 0)), int(span.get("block_num", 0)), int(span.get("paragraph_num", 0)), int(span.get("line_num", 0)))
        groups[key].append(span)
    lines = []
    for key in sorted(groups):
        words = sorted(groups[key], key=lambda row: (int(row.get("word_num", 0)), int(row.get("bbox", {}).get("x", 0))))
        text = " ".join(str(row.get("raw_text", "")) for row in words).strip()
        confidences = [float(row["confidence"]) for row in words if row.get("confidence") is not None]
        boxes = [row.get("bbox") or {} for row in words]
        x0 = min((int(b.get("x", 0)) for b in boxes), default=0); y0 = min((int(b.get("y", 0)) for b in boxes), default=0)
        x1 = max((int(b.get("x", 0)) + int(b.get("width", 0)) for b in boxes), default=0); y1 = max((int(b.get("y", 0)) + int(b.get("height", 0)) for b in boxes), default=0)
        lines.append({"page_index": key[0], "block_num": key[1], "paragraph_num": key[2], "line_num": key[3], "raw_text": text, "span_indices": [int(row["span_index"]) for row in words], "bbox": {"x": x0, "y": y0, "width": max(x1-x0,0), "height": max(y1-y0,0)}, "mean_confidence": sum(confidences)/len(confidences) if confidences else None})
    reading_order = [index for line in lines for index in line["span_indices"]]
    return {"method": "tesseract_page_block_paragraph_line_word_order", "lines": lines, "reading_order_span_indices": reading_order, "invariants": {"input_span_count": len(spans), "ordered_span_count": len(reading_order), "span_accounting_closed": sorted(reading_order) == list(range(len(spans))), "semantic_tables_inferred": False, "semantic_forms_inferred": False}, "limitations": ["Reading order is derived only from engine page/block/paragraph/line/word fields.", "Columns, tables and forms are not semantically reconstructed by this receipt."]}


def calibrate_confidence(spans: list[dict[str, Any]], *, low_threshold: float = 50.0, high_threshold: float = 85.0) -> dict[str, Any]:
    if not (0 <= low_threshold < high_threshold <= 100):
        raise ValueError("confidence thresholds must satisfy 0 <= low < high <= 100")
    counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "UNKNOWN": 0}
    for span in spans:
        value = span.get("confidence")
        state = "UNKNOWN" if value is None else "LOW" if float(value) < low_threshold else "HIGH" if float(value) >= high_threshold else "MEDIUM"
        counts[state] += 1
    return {"scale": "0-100", "low_threshold": low_threshold, "high_threshold": high_threshold, "counts": counts, "invariants": {"span_accounting_closed": sum(counts.values()) == len(spans)}, "limitations": ["Thresholds classify engine-reported confidence; they do not prove OCR correctness without a labeled corpus."]}
