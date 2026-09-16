from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

RETRIEVAL_CORPUS_SCHEMA_VERSION = "mucaro-retrieval-corpus/1.0"
RETRIEVAL_CORPUS_REPORT_VERSION = "mucaro-retrieval-corpus-report/1.0"
ALLOWED_REAL_WORLD_ORIGINS = {
    "operator_file",
    "web_snapshot",
    "public_record",
    "email_export",
    "field_capture",
    "library_document",
    "dataset_export",
}


class RetrievalCorpusError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _positive_thresholds(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_THRESHOLDS", "thresholds must be an object")
    result: dict[str, int] = {}
    for key in ("min_sources", "min_queries", "min_formats", "min_total_bytes"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_THRESHOLD", f"{key} must be a positive integer")
        result[key] = value
    return result


def _load_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_MANIFEST_READ", str(path)) from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_MANIFEST_PARSE", str(path)) from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != RETRIEVAL_CORPUS_SCHEMA_VERSION:
        raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SCHEMA", str(manifest.get("schema_version")))
    return manifest, raw


def certify_retrieval_corpus(
    manifest_path: str | Path,
    *,
    corpus_root: str | Path | None = None,
    evaluation_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest, manifest_bytes = _load_manifest(manifest_file)
    root = Path(corpus_root).expanduser().resolve() if corpus_root is not None else manifest_file.parent.resolve()
    thresholds = _positive_thresholds(manifest.get("thresholds"))
    sources = manifest.get("sources")
    queries = manifest.get("queries")
    if not isinstance(sources, list) or not isinstance(queries, list):
        raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_ROWS", "sources and queries must be arrays")

    source_rows: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    formats: set[str] = set()
    total_bytes = 0
    material_failures: list[str] = []

    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_ROW", f"sources[{index}]")
        source_id = source.get("source_id")
        relative_path = source.get("path")
        expected_sha = source.get("sha256")
        expected_size = source.get("byte_size")
        source_format = source.get("source_format")
        origin = source.get("origin")
        synthetic = source.get("synthetic")
        if not isinstance(source_id, str) or not source_id:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_ID", f"sources[{index}]")
        if source_id in source_ids:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_DUPLICATE", source_id)
        source_ids.add(source_id)
        if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_PATH", source_id)
        target = (root / relative_path).resolve()
        if not _within(target, root):
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_ESCAPE", source_id)
        if target.is_symlink():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_SYMLINK", source_id)
        if not isinstance(expected_sha, str) or len(expected_sha) != 64 or any(c not in "0123456789abcdef" for c in expected_sha):
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_SHA", source_id)
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_SIZE", source_id)
        if not isinstance(source_format, str) or not source_format.strip():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_SOURCE_FORMAT", source_id)
        if origin not in ALLOWED_REAL_WORLD_ORIGINS:
            material_failures.append(f"UNSUPPORTED_ORIGIN:{source_id}:{origin}")
        if synthetic is not False:
            material_failures.append(f"SYNTHETIC_OR_UNDECLARED:{source_id}")

        exists = target.is_file()
        observed_size = target.stat().st_size if exists else None
        observed_sha = _sha256(target) if exists else None
        if not exists:
            material_failures.append(f"SOURCE_MISSING:{source_id}")
        elif observed_size != expected_size:
            material_failures.append(f"SIZE_MISMATCH:{source_id}")
        elif observed_sha != expected_sha:
            material_failures.append(f"SHA256_MISMATCH:{source_id}")
        formats.add(source_format.strip().lower())
        total_bytes += expected_size
        source_rows.append(
            {
                "source_id": source_id,
                "path": relative_path,
                "source_format": source_format.strip().lower(),
                "origin": origin,
                "synthetic": synthetic,
                "expected_sha256": expected_sha,
                "observed_sha256": observed_sha,
                "expected_byte_size": expected_size,
                "observed_byte_size": observed_size,
                "identity_match": bool(exists and observed_size == expected_size and observed_sha == expected_sha),
            }
        )

    query_ids: set[str] = set()
    query_rows: list[dict[str, Any]] = []
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_ROW", f"queries[{index}]")
        query_id = query.get("query_id")
        text = query.get("query")
        expected_sources = query.get("expected_source_ids")
        if not isinstance(query_id, str) or not query_id:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_ID", f"queries[{index}]")
        if query_id in query_ids:
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_DUPLICATE", query_id)
        query_ids.add(query_id)
        if not isinstance(text, str) or not text.strip():
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_TEXT", query_id)
        if not isinstance(expected_sources, list) or not expected_sources or not all(isinstance(item, str) and item for item in expected_sources):
            raise RetrievalCorpusError("ERROR_RETRIEVAL_CORPUS_QUERY_BINDINGS", query_id)
        unknown = sorted(set(expected_sources) - source_ids)
        if unknown:
            material_failures.append(f"QUERY_UNKNOWN_SOURCE:{query_id}:{','.join(unknown)}")
        query_rows.append(
            {
                "query_id": query_id,
                "query": text.strip(),
                "expected_source_ids": sorted(set(expected_sources)),
                "category": query.get("category"),
            }
        )

    threshold_failures: list[str] = []
    if len(source_rows) < thresholds["min_sources"]:
        threshold_failures.append(f"MIN_SOURCES:{len(source_rows)}<{thresholds['min_sources']}")
    if len(query_rows) < thresholds["min_queries"]:
        threshold_failures.append(f"MIN_QUERIES:{len(query_rows)}<{thresholds['min_queries']}")
    if len(formats) < thresholds["min_formats"]:
        threshold_failures.append(f"MIN_FORMATS:{len(formats)}<{thresholds['min_formats']}")
    if total_bytes < thresholds["min_total_bytes"]:
        threshold_failures.append(f"MIN_TOTAL_BYTES:{total_bytes}<{thresholds['min_total_bytes']}")

    source_ids_sha = _stable_hash(sorted(source_ids))
    query_ids_sha = _stable_hash(sorted(query_ids))
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    evaluation_failures: list[str] = []
    if not isinstance(evaluation_receipt, dict):
        evaluation_failures.append("EVALUATION_RECEIPT_MISSING")
    else:
        if evaluation_receipt.get("status") not in {"RETRIEVAL_EVAL_OK", "RETRIEVAL_QUALITY_CERT_OK"}:
            evaluation_failures.append(f"EVALUATION_STATUS:{evaluation_receipt.get('status')}")
        if evaluation_receipt.get("corpus_manifest_sha256") != manifest_sha:
            evaluation_failures.append("EVALUATION_MANIFEST_IDENTITY_MISMATCH")
        if evaluation_receipt.get("source_ids_sha256") != source_ids_sha:
            evaluation_failures.append("EVALUATION_SOURCE_DENOMINATOR_MISMATCH")
        if evaluation_receipt.get("query_ids_sha256") != query_ids_sha:
            evaluation_failures.append("EVALUATION_QUERY_DENOMINATOR_MISMATCH")

    all_failures = material_failures + threshold_failures + evaluation_failures
    ready = not all_failures
    report: dict[str, Any] = {
        "schema_version": RETRIEVAL_CORPUS_REPORT_VERSION,
        "status": "RETRIEVAL_REAL_WORLD_CORPUS_CERT_READY" if ready else "RETRIEVAL_REAL_WORLD_CORPUS_CERT_BLOCKED",
        "certification_ready": ready,
        "manifest_path": str(manifest_file),
        "manifest_sha256": manifest_sha,
        "corpus_root": str(root),
        "corpus_id": manifest.get("corpus_id"),
        "frozen_at": manifest.get("frozen_at"),
        "thresholds": thresholds,
        "counts": {
            "sources": len(source_rows),
            "queries": len(query_rows),
            "formats": len(formats),
            "total_bytes": total_bytes,
        },
        "source_ids_sha256": source_ids_sha,
        "query_ids_sha256": query_ids_sha,
        "sources": source_rows,
        "queries": query_rows,
        "material_failures": material_failures,
        "threshold_failures": threshold_failures,
        "evaluation_failures": evaluation_failures,
        "residue_count": len(all_failures),
        "invariants": {
            "source_ids_unique": len(source_ids) == len(source_rows),
            "query_ids_unique": len(query_ids) == len(query_rows),
            "all_source_identities_match": all(row["identity_match"] for row in source_rows),
            "all_sources_explicitly_nonsynthetic": all(row["synthetic"] is False for row in source_rows),
            "all_origins_real_world": all(row["origin"] in ALLOWED_REAL_WORLD_ORIGINS for row in source_rows),
            "all_query_bindings_resolve": not any(item.startswith("QUERY_UNKNOWN_SOURCE:") for item in material_failures),
            "thresholds_closed": not threshold_failures,
            "evaluation_bound_to_exact_denominator": not evaluation_failures,
        },
        "allowed_denominator_event": "RT-09 OPEN->PROVISIONAL" if ready else None,
        "limitations": [
            "This gate proves corpus identity, real-world provenance declarations, threshold closure, and evaluation-denominator binding; it does not independently prove retrieval quality.",
            "Source provenance declarations must be backed by acquisition records elsewhere in the evidence plane; a false declaration invalidates certification.",
            "RT-09 PASS requires the project certification policy to accept the corpus scope and executed retrieval evidence; this module alone authorizes at most PROVISIONAL adjudication.",
        ],
        "falsification_criteria": [
            "any synthetic or undeclared source prevents readiness",
            "any source byte/hash mismatch prevents readiness",
            "any unknown expected-source binding prevents readiness",
            "any corpus threshold shortfall prevents readiness",
            "evaluation evidence must bind the exact manifest, source denominator, and query denominator",
        ],
    }
    report["report_sha256"] = _stable_hash(report)
    return report
