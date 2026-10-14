from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .utils import write_json

OCR_STRUCTURE_SCHEMA_VERSION = "mucaro-ocr-structure/1.0"


class OCRStructureError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _bbox(row: dict[str, Any]) -> dict[str, int]:
    raw = row.get("bbox")
    if not isinstance(raw, dict):
        raise OCRStructureError("ERROR_OCR_STRUCTURE_BBOX", "span bbox must be an object")
    result: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise OCRStructureError("ERROR_OCR_STRUCTURE_BBOX", f"invalid bbox {key}")
        result[key] = value
    if result["width"] <= 0 or result["height"] <= 0:
        raise OCRStructureError("ERROR_OCR_STRUCTURE_BBOX", "bbox width and height must be positive")
    return result


def _validated_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(spans, list) or not spans:
        raise OCRStructureError("ERROR_OCR_STRUCTURE_SPANS", "non-empty spans array required")
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for position, raw in enumerate(spans):
        if not isinstance(raw, dict):
            raise OCRStructureError("ERROR_OCR_STRUCTURE_SPAN", f"spans[{position}] must be an object")
        span_index = raw.get("span_index")
        if not isinstance(span_index, int) or isinstance(span_index, bool) or span_index < 0:
            raise OCRStructureError("ERROR_OCR_STRUCTURE_SPAN_INDEX", f"spans[{position}]")
        if span_index in seen:
            raise OCRStructureError("ERROR_OCR_STRUCTURE_DUPLICATE_SPAN", str(span_index))
        seen.add(span_index)
        text = raw.get("raw_text")
        if not isinstance(text, str) or not text.strip():
            raise OCRStructureError("ERROR_OCR_STRUCTURE_TEXT", f"span {span_index}")
        row = dict(raw)
        row["span_index"] = span_index
        row["raw_text"] = text
        row["bbox"] = _bbox(raw)
        try:
            row["page_index"] = int(raw.get("page_index", 0))
            row["block_num"] = int(raw.get("block_num", 0))
            row["paragraph_num"] = int(raw.get("paragraph_num", 0))
            row["line_num"] = int(raw.get("line_num", 0))
            row["word_num"] = int(raw.get("word_num", position + 1))
        except (TypeError, ValueError) as exc:
            raise OCRStructureError(
                "ERROR_OCR_STRUCTURE_HIERARCHY",
                f"span {span_index} has malformed page/block/paragraph/line/word fields",
            ) from exc
        normalized.append(row)
    return normalized


def _union_bbox(rows: list[dict[str, Any]]) -> dict[str, int]:
    boxes = [row["bbox"] for row in rows]
    x0 = min(box["x"] for box in boxes)
    y0 = min(box["y"] for box in boxes)
    x1 = max(box["x"] + box["width"] for box in boxes)
    y1 = max(box["y"] + box["height"] for box in boxes)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _group_lines(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        key = (
            span["page_index"],
            span["block_num"],
            span["paragraph_num"],
            span["line_num"],
        )
        groups[key].append(span)
    lines: list[dict[str, Any]] = []
    for key, rows in groups.items():
        ordered = sorted(rows, key=lambda row: (row["bbox"]["x"], row["word_num"], row["span_index"]))
        lines.append(
            {
                "key": key,
                "page_index": key[0],
                "bbox": _union_bbox(ordered),
                "raw_text": " ".join(row["raw_text"] for row in ordered),
                "span_indices": [row["span_index"] for row in ordered],
                "spans": ordered,
            }
        )
    return sorted(lines, key=lambda row: (row["page_index"], row["bbox"]["y"], row["bbox"]["x"], row["key"]))


def _one_page(spans: list[dict[str, Any]]) -> bool:
    return len({row["page_index"] for row in spans}) == 1


def _unresolved(kind: str, spans: list[dict[str, Any]], reasons: list[str]) -> dict[str, Any]:
    return {
        "schema_version": OCR_STRUCTURE_SCHEMA_VERSION,
        "kind": kind,
        "status": "OCR_STRUCTURE_UNRESOLVED",
        "epistemic_classification": "INFERENCE",
        "certification_state": "UNRESOLVED",
        "span_count": len(spans),
        "residue": sorted(set(reasons)),
        "invariants": {
            "all_input_spans_classified": False,
            "semantic_structure_claimed": False,
        },
    }


def _vertical_overlap(left: dict[str, int], right: dict[str, int]) -> int:
    return max(0, min(left["y"] + left["height"], right["y"] + right["height"]) - max(left["y"], right["y"]))


def _spatial_column_components(
    spans: list[dict[str, Any]],
    *,
    overlap_ratio: float,
) -> tuple[list[list[dict[str, Any]]], float]:
    """Partition spans into conservative x-runs before grouping their lines.

    Tesseract may reuse one line key for words that sit on the same visual row
    in different columns. Grouping by that key first therefore destroys the
    very column boundary this reconstruction needs to infer. A bounded x-run
    partition keeps raw spans intact and only joins nearby horizontal words;
    wide or ambiguous gaps remain separate candidates and are still rejected
    later when each candidate lacks enough lines or vertical support.
    """
    heights = [row["bbox"]["height"] for row in spans]
    widths = [row["bbox"]["width"] for row in spans]
    gap_tolerance = max(
        8.0,
        statistics.median(heights) * (1.0 + overlap_ratio),
        statistics.median(widths) * overlap_ratio,
    )
    ordered = sorted(spans, key=lambda row: (row["bbox"]["x"], row["bbox"]["y"], row["span_index"]))
    components: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_right = 0
    for row in ordered:
        box = row["bbox"]
        if current and box["x"] - current_right > gap_tolerance:
            components.append(current)
            current = []
        current.append(row)
        current_right = max(current_right, box["x"] + box["width"])
    if current:
        components.append(current)
    return components, gap_tolerance


def reconstruct_multicolumn(
    spans: list[dict[str, Any]],
    *,
    min_columns: int = 2,
    min_lines_per_column: int = 2,
    overlap_ratio: float = 0.20,
    min_vertical_overlap_ratio: float = 0.25,
) -> dict[str, Any]:
    rows = _validated_spans(spans)
    if not _one_page(rows):
        return _unresolved("MULTI_COLUMN", rows, ["MULTI_PAGE_SCOPE_NOT_SUPPORTED"])
    components, gap_tolerance = _spatial_column_components(rows, overlap_ratio=overlap_ratio)
    columns = []
    for component in components:
        ordered = sorted(component, key=lambda row: (row["bbox"]["y"], row["bbox"]["x"], row["span_index"]))
        lines = _group_lines(ordered)
        bbox = _union_bbox(ordered)
        columns.append(
            {
                "bbox": bbox,
                "line_count": len(lines),
                "raw_lines": [line["raw_text"] for line in lines],
                "span_indices": [index for line in lines for index in line["span_indices"]],
            }
        )
    columns.sort(key=lambda row: (row["bbox"]["x"], row["bbox"]["y"]))

    reasons: list[str] = []
    if len(columns) < min_columns:
        reasons.append("COLUMN_COUNT_BELOW_MINIMUM")
    if any(column["line_count"] < min_lines_per_column for column in columns):
        reasons.append("COLUMN_LINE_COUNT_BELOW_MINIMUM")
    for left, right in zip(columns, columns[1:]):
        if left["bbox"]["x"] + left["bbox"]["width"] > right["bbox"]["x"]:
            reasons.append("COLUMN_BOUNDS_OVERLAP")
        vertical_overlap = _vertical_overlap(left["bbox"], right["bbox"])
        minimum_height = min(left["bbox"]["height"], right["bbox"]["height"])
        if minimum_height <= 0 or vertical_overlap / minimum_height < min_vertical_overlap_ratio:
            reasons.append("COLUMN_VERTICAL_OVERLAP_BELOW_MINIMUM")

    assigned = [index for column in columns for index in column["span_indices"]]
    expected = sorted(row["span_index"] for row in rows)
    if sorted(assigned) != expected or len(assigned) != len(set(assigned)):
        reasons.append("SPAN_ACCOUNTING_MISMATCH")
    if reasons:
        return _unresolved("MULTI_COLUMN", rows, reasons)

    return {
        "schema_version": OCR_STRUCTURE_SCHEMA_VERSION,
        "kind": "MULTI_COLUMN",
        "status": "OCR_MULTI_COLUMN_OK",
        "epistemic_classification": "INFERENCE",
        "certification_state": "CANDIDATE_NOT_IDENTITY",
        "page_index": rows[0]["page_index"],
        "column_count": len(columns),
        "columns": columns,
        "reading_order_span_indices": assigned,
        "geometry": {
            "x_gap_tolerance_px": gap_tolerance,
            "partition": "conservative_spatial_x_runs",
        },
        "invariants": {
            "input_span_count": len(rows),
            "assigned_span_count": len(assigned),
            "all_input_spans_classified": sorted(assigned) == expected,
            "span_assignment_unique": len(assigned) == len(set(assigned)),
            "columns_disjoint": all(
                left["bbox"]["x"] + left["bbox"]["width"] <= right["bbox"]["x"]
                for left, right in zip(columns, columns[1:])
            ),
            "semantic_structure_claimed": True,
        },
        "limitations": [
            "This bounded reconstruction partitions nearby word boxes into conservative spatial x-runs before grouping OCR lines.",
            "Large intentional intra-column gaps, spanning headings and nested columns may remain unresolved rather than being synthesized.",
            "Spanning headings, nested columns, rotated text and multi-page column continuity remain unresolved.",
        ],
    }


def _cluster_rows(spans: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    heights = [row["bbox"]["height"] for row in spans]
    tolerance = max(4.0, statistics.median(heights) * 0.75)
    ordered = sorted(spans, key=lambda row: (row["bbox"]["y"], row["bbox"]["x"], row["span_index"]))
    clusters: list[list[dict[str, Any]]] = []
    anchors: list[float] = []
    for row in ordered:
        y = float(row["bbox"]["y"])
        candidates = [(abs(y - anchor), index) for index, anchor in enumerate(anchors) if abs(y - anchor) <= tolerance]
        if not candidates:
            clusters.append([row])
            anchors.append(y)
            continue
        distance, index = min(candidates)
        tied = [item for item in candidates if abs(item[0] - distance) < 1e-9]
        if len(tied) != 1:
            raise OCRStructureError("ERROR_OCR_STRUCTURE_ROW_TIE", str(row["span_index"]))
        clusters[index].append(row)
        anchors[index] = sum(item["bbox"]["y"] for item in clusters[index]) / len(clusters[index])
    return [
        sorted(cluster, key=lambda row: (row["bbox"]["x"], row["word_num"], row["span_index"]))
        for cluster in clusters
    ]


def reconstruct_table(
    spans: list[dict[str, Any]],
    *,
    min_rows: int = 2,
    min_columns: int = 2,
) -> dict[str, Any]:
    rows = _validated_spans(spans)
    if not _one_page(rows):
        return _unresolved("TABLE", rows, ["MULTI_PAGE_SCOPE_NOT_SUPPORTED"])
    try:
        row_groups = _cluster_rows(rows)
    except OCRStructureError as exc:
        return _unresolved("TABLE", rows, [exc.status])

    if len(row_groups) < min_rows:
        return _unresolved("TABLE", rows, ["ROW_COUNT_BELOW_MINIMUM"])
    header = row_groups[0]
    if len(header) < min_columns:
        return _unresolved("TABLE", rows, ["COLUMN_COUNT_BELOW_MINIMUM"])

    anchors = [float(span["bbox"]["x"]) for span in header]
    if len(set(anchors)) != len(anchors):
        return _unresolved("TABLE", rows, ["HEADER_ANCHOR_COLLISION"])
    median_height = statistics.median(span["bbox"]["height"] for span in rows)
    if len(anchors) > 1:
        min_gap = min(right - left for left, right in zip(anchors, anchors[1:]))
        tolerance = min(max(8.0, median_height * 1.25), max(8.0, min_gap * 0.40))
    else:
        tolerance = max(8.0, median_height * 1.25)

    grid: list[list[list[dict[str, Any]]]] = []
    reasons: list[str] = []
    assigned_indices: list[int] = []
    for row_index, row_group in enumerate(row_groups):
        cells: list[list[dict[str, Any]]] = [[] for _ in anchors]
        for span in row_group:
            distances = [abs(float(span["bbox"]["x"]) - anchor) for anchor in anchors]
            best = min(distances)
            nearest = [index for index, distance in enumerate(distances) if abs(distance - best) < 1e-9]
            if len(nearest) != 1:
                reasons.append(f"COLUMN_ASSIGNMENT_TIE:span={span['span_index']}")
                continue
            column_index = nearest[0]
            if best > tolerance:
                reasons.append(f"COLUMN_ASSIGNMENT_OUT_OF_TOLERANCE:span={span['span_index']}")
                continue
            cells[column_index].append(span)
            assigned_indices.append(span["span_index"])
        for column_index, cell in enumerate(cells):
            if not cell:
                reasons.append(f"EMPTY_CELL:row={row_index}:column={column_index}")
        grid.append(cells)

    expected_indices = sorted(span["span_index"] for span in rows)
    if sorted(assigned_indices) != expected_indices or len(assigned_indices) != len(set(assigned_indices)):
        reasons.append("SPAN_ACCOUNTING_MISMATCH")
    if reasons:
        return _unresolved("TABLE", rows, reasons)

    raw_grid: list[list[str]] = []
    cell_span_indices: list[list[list[int]]] = []
    for row in grid:
        raw_grid.append(
            [
                " ".join(span["raw_text"] for span in sorted(cell, key=lambda item: item["bbox"]["x"]))
                for cell in row
            ]
        )
        cell_span_indices.append(
            [
                [span["span_index"] for span in sorted(cell, key=lambda item: item["bbox"]["x"])]
                for cell in row
            ]
        )

    headers = raw_grid[0]
    canonical_headers = [" ".join(value.split()).casefold() for value in headers]
    if any(not value for value in canonical_headers) or len(set(canonical_headers)) != len(canonical_headers):
        return _unresolved("TABLE", rows, ["HEADER_IDENTITY_NOT_UNIQUE"])

    records = [
        {headers[column]: row[column] for column in range(len(headers))}
        for row in raw_grid[1:]
    ]
    return {
        "schema_version": OCR_STRUCTURE_SCHEMA_VERSION,
        "kind": "TABLE",
        "status": "OCR_TABLE_RECONSTRUCTION_OK",
        "epistemic_classification": "INFERENCE",
        "certification_state": "CANDIDATE_NOT_IDENTITY",
        "page_index": rows[0]["page_index"],
        "row_count": len(raw_grid),
        "column_count": len(headers),
        "headers_raw": headers,
        "grid_raw": raw_grid,
        "cell_span_indices": cell_span_indices,
        "records_raw": records,
        "geometry": {
            "header_x_anchors": anchors,
            "assignment_tolerance_px": tolerance,
        },
        "invariants": {
            "input_span_count": len(rows),
            "assigned_span_count": len(assigned_indices),
            "all_input_spans_classified": sorted(assigned_indices) == expected_indices,
            "span_assignment_unique": len(assigned_indices) == len(set(assigned_indices)),
            "rectangular_grid": all(len(row) == len(headers) for row in raw_grid),
            "header_identity_unique": len(set(canonical_headers)) == len(canonical_headers),
            "semantic_structure_claimed": True,
        },
        "limitations": [
            "This is a bounded rectangular table reconstruction using first-row x anchors and OCR word boxes.",
            "Merged cells, nested tables, rotated tables, border-only empty cells and headerless tables remain unresolved.",
            "Raw cell strings are preserved; normalization is not used as identity proof.",
        ],
    }


def reconstruct_form(spans: list[dict[str, Any]], *, min_fields: int = 2) -> dict[str, Any]:
    rows = _validated_spans(spans)
    if not _one_page(rows):
        return _unresolved("FORM", rows, ["MULTI_PAGE_SCOPE_NOT_SUPPORTED"])
    lines = _group_lines(rows)
    fields: list[dict[str, Any]] = []
    reasons: list[str] = []
    used_indices: list[int] = []
    labels: set[str] = set()

    for line_index, line in enumerate(lines):
        tokens = line["spans"]
        delimiters = [
            index
            for index, span in enumerate(tokens)
            if span["raw_text"].rstrip().endswith(":")
        ]
        if len(delimiters) != 1:
            reasons.append(f"FORM_DELIMITER_COUNT:line={line_index}:count={len(delimiters)}")
            continue
        delimiter = delimiters[0]
        label_tokens = tokens[: delimiter + 1]
        value_tokens = tokens[delimiter + 1 :]
        if not value_tokens:
            reasons.append(f"FORM_VALUE_MISSING:line={line_index}")
            continue
        raw_label = " ".join(span["raw_text"] for span in label_tokens).strip()
        raw_value = " ".join(span["raw_text"] for span in value_tokens).strip()
        canonical_label = " ".join(raw_label.rstrip(":").split()).casefold()
        if not canonical_label:
            reasons.append(f"FORM_LABEL_EMPTY:line={line_index}")
            continue
        if canonical_label in labels:
            reasons.append(f"FORM_LABEL_DUPLICATE:{canonical_label}")
            continue
        labels.add(canonical_label)
        span_indices = [span["span_index"] for span in tokens]
        used_indices.extend(span_indices)
        fields.append(
            {
                "field_index": len(fields),
                "label_raw": raw_label,
                "label_canonical_for_uniqueness_only": canonical_label,
                "value_raw": raw_value,
                "label_span_indices": [span["span_index"] for span in label_tokens],
                "value_span_indices": [span["span_index"] for span in value_tokens],
                "span_indices": span_indices,
                "bbox": line["bbox"],
            }
        )

    expected_indices = sorted(span["span_index"] for span in rows)
    if len(fields) < min_fields:
        reasons.append("FORM_FIELD_COUNT_BELOW_MINIMUM")
    if sorted(used_indices) != expected_indices or len(used_indices) != len(set(used_indices)):
        reasons.append("SPAN_ACCOUNTING_MISMATCH")
    if reasons:
        return _unresolved("FORM", rows, reasons)

    return {
        "schema_version": OCR_STRUCTURE_SCHEMA_VERSION,
        "kind": "FORM",
        "status": "OCR_FORM_RECONSTRUCTION_OK",
        "epistemic_classification": "INFERENCE",
        "certification_state": "CANDIDATE_NOT_IDENTITY",
        "page_index": rows[0]["page_index"],
        "field_count": len(fields),
        "fields": fields,
        "invariants": {
            "input_span_count": len(rows),
            "assigned_span_count": len(used_indices),
            "all_input_spans_classified": sorted(used_indices) == expected_indices,
            "span_assignment_unique": len(used_indices) == len(set(used_indices)),
            "field_labels_unique": len(labels) == len(fields),
            "semantic_structure_claimed": True,
        },
        "limitations": [
            "This bounded form reconstruction requires exactly one colon-terminated label token sequence per OCR line.",
            "Checkbox semantics, radio groups, unlabeled fields, multiline values and spatial label/value pairing without a colon remain unresolved.",
            "Raw label and value strings are preserved; canonical labels are used only for duplicate detection.",
        ],
    }


def reconstruct_ocr_report(
    report_path: str | Path,
    *,
    kind: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    source_path = Path(report_path).expanduser().resolve()
    try:
        raw = source_path.read_bytes()
        report = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OCRStructureError("ERROR_OCR_STRUCTURE_REPORT_READ", str(source_path)) from exc
    if not isinstance(report, dict) or report.get("schema_version") != "mucaro_ocr_report_v1":
        raise OCRStructureError(
            "ERROR_OCR_STRUCTURE_REPORT_SCHEMA",
            "structure input must be a saved mucaro_ocr_report_v1 image OCR receipt",
        )
    spans = report.get("spans")
    if not isinstance(spans, list) or not spans:
        raise OCRStructureError("ERROR_OCR_STRUCTURE_REPORT_SPANS", "saved OCR receipt contains no structure spans")

    dispatch = {
        "multi_column": reconstruct_multicolumn,
        "table": reconstruct_table,
        "form": reconstruct_form,
    }
    if kind not in dispatch:
        raise OCRStructureError("ERROR_OCR_STRUCTURE_KIND", f"unsupported structure kind: {kind}")
    structure = dispatch[kind](spans)
    report_sha256 = hashlib.sha256(raw).hexdigest()
    structure_identity = hashlib.sha256(
        f"{report_sha256}:{kind}:{OCR_STRUCTURE_SCHEMA_VERSION}".encode("utf-8")
    ).hexdigest()[:24]
    cfg.ensure_dirs()
    out_dir = cfg.export_dir / "ocr_structures"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"ocrstructure_{structure_identity}.json"
    receipt = {
        "schema_version": "mucaro-ocr-structure-receipt/1.0",
        "structure_id": f"ocrstructure_{structure_identity}",
        "status": structure.get("status"),
        "kind": kind,
        "input_ocr_report": {
            "path": str(source_path),
            "sha256": report_sha256,
            "report_id": report.get("report_id"),
            "source_sha256": report.get("source", {}).get("sha256")
            if isinstance(report.get("source"), dict)
            else None,
            "engine": report.get("engine"),
        },
        "structure": structure,
        "invariants": {
            "input_report_sha256_present": len(report_sha256) == 64,
            "input_report_schema_bound": True,
            "structure_kind_exact": structure.get("kind") == kind.upper(),
            "structure_span_count_matches_input": (
                structure.get("invariants", {}).get("input_span_count", structure.get("span_count")) == len(spans)
            ),
        },
        "limitations": [
            "The structure receipt is derived from an existing OCR receipt and does not rerun OCR.",
            "A reconstruction status records bounded algorithm resolution, not universal semantic correctness.",
            "CANDIDATE_NOT_IDENTITY and UNRESOLVED states remain binding until independent executed benchmark evidence supports promotion.",
        ],
    }
    write_json(output_path, receipt)
    receipt_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return {
        **receipt,
        "receipt_path": str(output_path),
        "receipt_sha256": receipt_sha256,
    }
