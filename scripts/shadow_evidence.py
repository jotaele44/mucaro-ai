#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "mucaro-shadow-vector-evidence/1.0"
EXECUTION_RECEIPT_SCHEMA_VERSION = "mucaro-shadow-execution-receipt/1.0"
TERMINAL_STATES = {"PASS", "NOT_APPLICABLE", "BLOCKED", "UNRESOLVED"}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ShadowEvidenceError(RuntimeError):
    pass


def _require_sha(value: object, *, label: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(text):
        raise ShadowEvidenceError(f"{label} must be a full lowercase 40-character Git SHA")
    return text


def _git_show_text(checkpoint_sha: str, path: str) -> str:
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise ShadowEvidenceError(f"invalid evidence path: {path!r}")
    completed = subprocess.run(
        ["git", "show", f"{checkpoint_sha}:{path}"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ShadowEvidenceError(f"evidence path absent from checkpoint: {path}")
    return completed.stdout


def _load_execution_receipt(path: str, *, base_sha: str, checkpoint_sha: str) -> None:
    target = (ROOT / path).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError as exc:
        raise ShadowEvidenceError(f"execution receipt escapes repository root: {path}") from exc
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowEvidenceError(f"unable to read execution receipt {path}: {exc}") from exc
    if payload.get("schema_version") != EXECUTION_RECEIPT_SCHEMA_VERSION:
        raise ShadowEvidenceError(f"invalid execution receipt schema: {path}")
    if _require_sha(payload.get("base_sha"), label="execution receipt base_sha") != base_sha:
        raise ShadowEvidenceError(f"execution receipt base mismatch: {path}")
    if _require_sha(payload.get("checkpoint_sha"), label="execution receipt checkpoint_sha") != checkpoint_sha:
        raise ShadowEvidenceError(f"execution receipt checkpoint mismatch: {path}")
    if payload.get("status") != "PASS":
        raise ShadowEvidenceError(f"execution receipt is not PASS: {path}")


def _normalize_evidence(
    entry: object,
    *,
    base_sha: str,
    checkpoint_sha: str,
    seen_ids: set[str],
) -> dict[str, str]:
    if not isinstance(entry, dict):
        raise ShadowEvidenceError("each evidence entry must be an object")
    evidence_id = str(entry.get("evidence_id") or "").strip()
    path = str(entry.get("path") or "").strip()
    selector = str(entry.get("selector") or "").strip()
    kind = str(entry.get("kind") or "CHECKPOINT_FILE").strip()
    if not evidence_id:
        raise ShadowEvidenceError("evidence_id is required")
    if evidence_id in seen_ids:
        raise ShadowEvidenceError(f"duplicate evidence_id: {evidence_id}")
    seen_ids.add(evidence_id)
    if kind not in {"CHECKPOINT_FILE", "CHECKPOINT_TEST", "EXECUTION_RECEIPT"}:
        raise ShadowEvidenceError(f"unsupported evidence kind for {evidence_id}: {kind}")
    if kind == "EXECUTION_RECEIPT":
        _load_execution_receipt(path, base_sha=base_sha, checkpoint_sha=checkpoint_sha)
        text = ""
    else:
        text = _git_show_text(checkpoint_sha, path)
    if kind == "CHECKPOINT_TEST":
        if not selector:
            raise ShadowEvidenceError(f"test evidence {evidence_id} requires selector")
        symbol = selector.split("::")[-1].strip()
        definitions = {f"def {symbol}(", f"async def {symbol}("}
        if not symbol or not any(token in text for token in definitions):
            raise ShadowEvidenceError(
                f"exact test definition for {evidence_id} is absent from checkpoint file: {selector}"
            )
    elif selector and selector not in text:
        raise ShadowEvidenceError(
            f"selector for {evidence_id} is absent from checkpoint file: {selector}"
        )
    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "path": path,
        "selector": selector,
    }


def validate_manifest(
    payload: object,
    *,
    base_sha: str,
    checkpoint_sha: str,
    required_vectors: Iterable[str],
) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict):
        raise ShadowEvidenceError("evidence manifest must be a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ShadowEvidenceError(f"schema_version must be {SCHEMA_VERSION}")

    expected_base = _require_sha(base_sha, label="base_sha")
    expected_checkpoint = _require_sha(checkpoint_sha, label="checkpoint_sha")
    manifest_base = _require_sha(payload.get("base_sha"), label="manifest base_sha")
    manifest_checkpoint = _require_sha(payload.get("checkpoint_sha"), label="manifest checkpoint_sha")
    if manifest_base != expected_base:
        raise ShadowEvidenceError(
            f"manifest base_sha mismatch: expected={expected_base} actual={manifest_base}"
        )
    if manifest_checkpoint != expected_checkpoint:
        raise ShadowEvidenceError(
            "manifest checkpoint_sha mismatch: "
            f"expected={expected_checkpoint} actual={manifest_checkpoint}"
        )

    rows = payload.get("vectors")
    if not isinstance(rows, list):
        raise ShadowEvidenceError("vectors must be a list")

    required = list(required_vectors)
    required_set = set(required)
    seen_vectors: set[str] = set()
    seen_evidence_ids: set[str] = set()
    normalized: dict[str, dict[str, object]] = {}

    for row in rows:
        if not isinstance(row, dict):
            raise ShadowEvidenceError("each vector disposition must be an object")
        vector = str(row.get("vector") or "").strip()
        state = str(row.get("state") or "").strip()
        rationale = str(row.get("rationale") or "").strip()
        if vector not in required_set:
            raise ShadowEvidenceError(f"unknown vector: {vector}")
        if vector in seen_vectors:
            raise ShadowEvidenceError(f"duplicate vector disposition: {vector}")
        seen_vectors.add(vector)
        if state not in TERMINAL_STATES:
            raise ShadowEvidenceError(f"invalid state for {vector}: {state}")

        evidence_rows = row.get("evidence", [])
        if not isinstance(evidence_rows, list):
            raise ShadowEvidenceError(f"evidence for {vector} must be a list")
        evidence = [
            _normalize_evidence(
                item,
                base_sha=expected_base,
                checkpoint_sha=expected_checkpoint,
                seen_ids=seen_evidence_ids,
            )
            for item in evidence_rows
        ]

        if state == "PASS":
            if not evidence:
                raise ShadowEvidenceError(f"PASS vector requires executable/checkpoint evidence: {vector}")
            if not any(item["kind"] == "EXECUTION_RECEIPT" for item in evidence):
                raise ShadowEvidenceError(f"PASS vector requires exact-checkpoint execution receipt: {vector}")
        if state in {"NOT_APPLICABLE", "BLOCKED", "UNRESOLVED"} and not rationale:
            raise ShadowEvidenceError(f"{state} vector requires rationale: {vector}")
        if state == "NOT_APPLICABLE" and evidence:
            raise ShadowEvidenceError(
                f"NOT_APPLICABLE vector must not carry positive evidence: {vector}"
            )

        normalized[vector] = {
            "state": state,
            "reason": rationale,
            "evidence": evidence,
        }

    missing = sorted(required_set - seen_vectors)
    extra = sorted(seen_vectors - required_set)
    if missing or extra or len(rows) != len(required):
        raise ShadowEvidenceError(
            f"vector denominator mismatch missing={missing} extra={extra} "
            f"expected={len(required)} actual={len(rows)}"
        )

    return normalized


def load_manifest(
    path: Path,
    *,
    base_sha: str,
    checkpoint_sha: str,
    required_vectors: Iterable[str],
) -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowEvidenceError(f"unable to read evidence manifest {path}: {exc}") from exc
    return validate_manifest(
        payload,
        base_sha=base_sha,
        checkpoint_sha=checkpoint_sha,
        required_vectors=required_vectors,
    )
