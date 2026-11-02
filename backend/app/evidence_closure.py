"""Fail-closed evidence reconciliation primitives.

An additive, side-effect-free candidate module; it performs no promotion,
GitHub writes, source acquisition or runtime activation. Evidence grades are
inputs requiring an independently authenticated upstream producer.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping

EVIDENCE_RANK = {
    "stable_id": 80,
    "authoritative_binding": 70,
    "certified_geometry": 60,
    "pip_and_independent_alias": 50,
    "point_in_polygon": 40,
    "authoritative_alias_with_support": 30,
    "historical_continuity": 20,
    "proximity": 10,
}
NON_IDENTITY = {"name_only", "normalized_name_only", "count_equality", "nearest_only", "same_category", "source_absence"}
VALID_STATES = {"PASS", "FAIL", "OPEN", "BLOCKED", "PROVISIONAL", "AUDIT_ONLY", "NONCANONICAL", "CANDIDATE_NOT_IDENTITY", "UNRESOLVED", "SUPERSEDED"}


class EvidenceInvariantError(ValueError):
    """Unclosed evidence invariant; caller must not certify."""


def canonical_bytes(value: Any) -> bytes:
    """Canonical JSON only (never claim equivalence to other serializations)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def logical_digest(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def capability_delta(a: Iterable[str], b: Iterable[str]) -> dict[str, list[str]]:
    aa, bb = set(a), set(b)
    if any(not isinstance(v, str) or not v for v in aa | bb):
        raise EvidenceInvariantError("capability identifiers must be nonempty strings")
    return {"intersection": sorted(aa & bb), "a_only": sorted(aa - bb),
            "b_only": sorted(bb - aa), "union": sorted(aa | bb),
            "symmetric_difference": sorted(aa ^ bb)}


def adjudicate_identity(candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return candidate classification, never silently choose by name/distance.

    Each candidate must carry a unique stable *candidate key*. Grades supplied by
    upstream are NOT proof that the stated evidence has been validated.
    """
    rows = list(candidates)
    if not rows:
        return {"state": "UNRESOLVED", "winner": None, "candidates": [], "reason": "no candidates"}
    ids = [r.get("candidate_key") for r in rows]
    if any(not isinstance(k, str) or not k for k in ids) or len(set(ids)) != len(ids):
        raise EvidenceInvariantError("missing or duplicate candidate_key")
    assessed = []
    for r in rows:
        kinds = r.get("evidence", [])
        if not isinstance(kinds, (list, tuple, set)) or any(not isinstance(k, str) for k in kinds):
            raise EvidenceInvariantError("evidence must be a sequence of strings")
        unknown = set(kinds) - set(EVIDENCE_RANK) - NON_IDENTITY
        if unknown:
            raise EvidenceInvariantError("unknown evidence classes: " + ",".join(sorted(unknown)))
        grade = max((EVIDENCE_RANK[k] for k in kinds if k in EVIDENCE_RANK), default=0)
        assessed.append({"candidate_key": r["candidate_key"], "rank": grade,
                         "evidence": sorted(set(kinds)), "contradiction": bool(r.get("contradiction", False))})
    assessed.sort(key=lambda r: r["candidate_key"])
    eligible = [r for r in assessed if not r["contradiction"]]
    best = max((r["rank"] for r in eligible), default=0)
    top = [r for r in eligible if r["rank"] == best and best > EVIDENCE_RANK["proximity"]]
    if len(top) == 1:
        return {"state": "CANDIDATE_NOT_IDENTITY", "winner": top[0]["candidate_key"],
                "candidates": assessed, "reason": "unique evidence leader; upstream validation required"}
    return {"state": "UNRESOLVED", "winner": None, "candidates": assessed,
            "reason": "tie, insufficient identity evidence or contradictory evidence"}


def reconcile_source_rows(source_ids: Iterable[str], dispositions: Iterable[Mapping[str, Any]],
                          allowed_states: Iterable[str] = ("RETAINED", "COMPANION", "EXCLUDED", "UNRESOLVED")) -> dict[str, Any]:
    """One disposition per source record; duplicate source keys fail closed.

    Source IDs must be qualified by source manifestation + record key upstream.
    Multiple output entities may be encoded in disposition payloads, not by
    duplicating source records.
    """
    keys = list(source_ids)
    if any(not isinstance(k, str) or not k for k in keys) or len(keys) != len(set(keys)):
        raise EvidenceInvariantError("invalid/duplicate source identity")
    rows = list(dispositions)
    routed = [r.get("source_id") for r in rows]
    if any(not isinstance(k, str) or not k for k in routed) or len(routed) != len(set(routed)):
        raise EvidenceInvariantError("missing/duplicate disposition source_id")
    allowed = set(allowed_states)
    if not allowed or any(r.get("disposition") not in allowed for r in rows):
        raise EvidenceInvariantError("unknown disposition")
    missing, extra = sorted(set(keys) - set(routed)), sorted(set(routed) - set(keys))
    counts = dict(sorted(Counter(r["disposition"] for r in rows).items()))
    closed = not missing and not extra and counts.get("UNRESOLVED", 0) == 0
    return {"state": "PASS" if closed else "OPEN", "source_count": len(keys),
            "routed_count": len(rows), "counts": counts, "missing": missing, "extra": extra,
            "arithmetic_closed": len(rows) == sum(counts.values())}


def certify(required: Iterable[str], observed: Mapping[str, str],
            unresolved_material: Iterable[str] = ()) -> dict[str, Any]:
    """Certification is PASS only when every required named gate passes."""
    req = list(required)
    if not req or len(req) != len(set(req)) or any(not isinstance(k, str) or not k for k in req):
        raise EvidenceInvariantError("required gates must be nonempty unique strings")
    unknown = sorted(set(observed) - set(req))
    invalid = sorted(k for k, v in observed.items() if v not in VALID_STATES)
    if invalid:
        raise EvidenceInvariantError("invalid certification states: " + ",".join(invalid))
    residues = sorted(set(unresolved_material))
    pending = {k: observed.get(k, "OPEN") for k in req if observed.get(k) != "PASS"}
    return {"state": "PASS" if not pending and not residues and not unknown else "WITHHELD",
            "required": sorted(req), "pending": dict(sorted(pending.items())),
            "unexpected_gates": unknown, "unresolved_material": residues,
            "receipt_sha256": logical_digest({"required": sorted(req), "observed": dict(sorted(observed.items())),
                                               "unresolved_material": residues})}
