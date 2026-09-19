from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from backend.app.chunker import chunk_text
from backend.app.parser import parse_file
from backend.app.retrieval import retrieve
from backend.app.retrieval_corpus_certification import certify_retrieval_corpus
from backend.app.retrieval_corpus_materializer import build_real_world_corpus_manifest

ROOT = Path(__file__).resolve().parents[1]
DENOMINATOR = ROOT / "_architecture" / "retrieval_real_world_corpus_v1.json"
STATUS_OK = "RT09_REAL_WORLD_CORPUS_CERT_OK"
STATUS_FAILED = "RT09_REAL_WORLD_CORPUS_CERT_FAILED"


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def acquire(url: str, *, allowed_hosts: set[str], timeout: int, max_bytes: int) -> tuple[bytes, str, int | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mucaro-RT09-Cert/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        host = urlparse(final_url).hostname or ""
        if host not in allowed_hosts:
            raise RuntimeError(f"FINAL_HOST_NOT_ALLOWED:{host}")
        status = getattr(response, "status", None)
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise RuntimeError(f"SOURCE_TOO_LARGE:{len(raw)}>{max_bytes}")
    if not raw:
        raise RuntimeError("SOURCE_EMPTY")
    return raw, final_url, status


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire and certify the frozen RT-09 real-world retrieval corpus.")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    denominator = json.loads(DENOMINATOR.read_text(encoding="utf-8"))
    allowed_hosts = set(denominator["acquisition"]["allowed_hosts"])
    timeout = int(denominator["acquisition"]["timeout_seconds"])
    max_bytes = int(denominator["acquisition"]["max_source_bytes"])
    out = args.output_dir.resolve()
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)

    acquisition_rows = []
    materializer_sources = []
    for spec in denominator["sources"]:
        raw, final_url, status = acquire(spec["url"], allowed_hosts=allowed_hosts, timeout=timeout, max_bytes=max_bytes)
        target = corpus / spec["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        acquisition_rows.append({
            "source_id": spec["source_id"], "requested_url": spec["url"], "final_url": final_url,
            "http_status": status, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
            "byte_size": len(raw), "sha256": sha, "path": spec["path"],
        })
        materializer_sources.append({
            "source_id": spec["source_id"], "path": spec["path"], "source_format": spec["source_format"],
            "origin": spec["origin"], "synthetic": False,
            "provenance_ref": f"acquisition:{spec['source_id']}:{sha}",
        })

    manifest = build_real_world_corpus_manifest(
        corpus,
        corpus_id=denominator["corpus_id"],
        frozen_at=datetime.now(timezone.utc).isoformat(),
        sources=materializer_sources,
        queries=denominator["queries"],
        thresholds=denominator["thresholds"],
    )
    manifest_path = out / "corpus_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    chunks = []
    parse_rows = []
    for spec in denominator["sources"]:
        path = corpus / spec["path"]
        parsed = parse_file(path)
        text = str(parsed.get("text") or "")
        source_chunks = chunk_text(spec["source_id"], text)
        for chunk in source_chunks:
            chunk["source_format"] = spec["source_format"]
        chunks.extend(source_chunks)
        parse_rows.append({
            "source_id": spec["source_id"], "parser": parsed.get("parser"),
            "parse_status": parsed.get("parse_status"), "chunk_count": len(source_chunks),
            "warnings": parsed.get("warnings", []),
        })

    eval_rows = []
    hits = 0
    for query in denominator["queries"]:
        result = retrieve(query["query"], chunks, strategy="hybrid", top_k=5)
        retrieved_sources = []
        for chunk in result.get("retrieved_chunks", []):
            sid = chunk.get("source_id")
            if isinstance(sid, str) and sid not in retrieved_sources:
                retrieved_sources.append(sid)
        expected = set(query["expected_source_ids"])
        hit = bool(expected.intersection(retrieved_sources))
        hits += int(hit)
        eval_rows.append({
            "query_id": query["query_id"], "query": query["query"], "expected_source_ids": sorted(expected),
            "retrieved_source_ids": retrieved_sources, "hit": hit, "status": result.get("status"),
        })

    source_ids = sorted(spec["source_id"] for spec in denominator["sources"])
    query_ids = sorted(query["query_id"] for query in denominator["queries"])
    evaluation = {
        "schema_version": "mucaro-rt09-live-eval/1.0",
        "status": "RETRIEVAL_EVAL_OK" if hits == len(eval_rows) else "RETRIEVAL_EVAL_FAILED",
        "corpus_manifest_sha256": manifest_sha,
        "source_ids_sha256": stable_hash(source_ids),
        "query_ids_sha256": stable_hash(query_ids),
        "strategy": "hybrid", "top_k": 5,
        "query_count": len(eval_rows), "hit_count": hits,
        "hit_rate": round(hits / len(eval_rows), 4) if eval_rows else 0.0,
        "rows": eval_rows,
    }
    evaluation_path = out / "evaluation.json"
    evaluation_path.write_text(json.dumps(evaluation, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    certification = certify_retrieval_corpus(manifest_path, corpus_root=corpus, evaluation_receipt=evaluation)
    acquisition = {
        "schema_version": "mucaro-rt09-acquisition/1.0",
        "denominator_sha256": hashlib.sha256(DENOMINATOR.read_bytes()).hexdigest(),
        "source_count": len(acquisition_rows), "sources": acquisition_rows,
        "all_hosts_allowed": all((urlparse(row["final_url"]).hostname or "") in allowed_hosts for row in acquisition_rows),
    }
    (out / "acquisition.json").write_text(json.dumps(acquisition, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "parse_receipts.json").write_text(json.dumps(parse_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "certification.json").write_text(json.dumps(certification, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    ok = (
        evaluation["status"] == "RETRIEVAL_EVAL_OK"
        and certification.get("status") == "RETRIEVAL_REAL_WORLD_CORPUS_CERT_READY"
        and certification.get("certification_ready") is True
        and len(chunks) > 0
        and acquisition["all_hosts_allowed"] is True
    )
    summary = {
        "schema_version": "mucaro-rt09-live-cert/1.0",
        "status": STATUS_OK if ok else STATUS_FAILED,
        "corpus_id": denominator["corpus_id"],
        "counts": certification.get("counts"),
        "chunk_count": len(chunks),
        "evaluation_hit_rate": evaluation["hit_rate"],
        "manifest_sha256": manifest_sha,
        "certification_report_sha256": certification.get("report_sha256"),
        "limitations": [
            "This frozen corpus is authoritative standards/public-record material, not a proxy for every private enterprise corpus.",
            "The evidence certifies this exact acquired manifestation; later upstream source changes require a new frozen manifestation.",
            "RT-10 production-scale stress remains independent.",
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(summary["status"])
    print(json.dumps(summary, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
