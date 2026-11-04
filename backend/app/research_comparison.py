"""Freeze reviewer bytes before revealing paired system identities."""

from __future__ import annotations

from .artifact_identity import sha256_bytes
from .research_benchmark import summarize_reviews
from .research_evidence import read_json_strict
from .utils import canonical_sha256, utc_now


def freeze_scores(packet_bytes: bytes, scores_bytes: bytes, rubric_bytes: bytes) -> dict:
    packet, scores, rubric = [read_json_strict(raw) for raw in (packet_bytes, scores_bytes, rubric_bytes)]
    summary = summarize_reviews(packet, scores)
    if not isinstance(packet.get("key_commitment_sha256"), str) or len(packet["key_commitment_sha256"]) != 64:
        raise ValueError("blind packet must commit to private key before review")
    if not isinstance(rubric, dict) or rubric.get("status") != "FROZEN_BEFORE_RUNS":
        raise ValueError("a predeclared rubric is required")
    for row in scores:
        if not isinstance(row.get("reviewer_id"), str) or not row["reviewer_id"].strip():
            raise ValueError("reviewer attribution required")
    return {
        "schema_version": "mucaro-review-seal/1",
        "frozen_utc": utc_now(),
        "packet_sha256": sha256_bytes(packet_bytes),
        "scores_sha256": sha256_bytes(scores_bytes),
        "rubric_sha256": sha256_bytes(rubric_bytes),
        "summary": summary,
        "reviewer_independence": "NOT_ATTESTED",
        "release_certification": "WITHHELD",
    }


def compare_frozen(
    packet_bytes: bytes,
    scores_bytes: bytes,
    rubric_bytes: bytes,
    key_bytes: bytes,
    seal_bytes: bytes,
    expected_seal_sha256: str,
) -> dict:
    if sha256_bytes(seal_bytes) != expected_seal_sha256:
        raise ValueError("review seal changed")
    seal = read_json_strict(seal_bytes)
    if seal.get("schema_version") != "mucaro-review-seal/1":
        raise ValueError("invalid review seal")
    for name, raw in (("packet", packet_bytes), ("scores", scores_bytes), ("rubric", rubric_bytes)):
        if seal.get(name + "_sha256") != sha256_bytes(raw):
            raise ValueError(f"frozen {name} bytes changed")
    validated = freeze_scores(packet_bytes, scores_bytes, rubric_bytes)
    packet, scores, key = [read_json_strict(raw) for raw in (packet_bytes, scores_bytes, key_bytes)]
    if not isinstance(key, dict) or key.get("schema_version") != "mucaro-blind-key/1":
        raise ValueError("invalid blind key")
    if canonical_sha256(key) != packet["key_commitment_sha256"]:
        raise ValueError("private key differs from pre-review commitment")
    mappings = {}
    for case in key["cases"]:
        if case["case_id"] in mappings:
            raise ValueError("duplicate key case")
        mappings[case["case_id"]] = case["mapping"]
    expected = {case["case_id"] for case in packet["cases"]}
    if set(mappings) != expected:
        raise ValueError("key case denominator mismatch")
    systems, rows = None, []
    score_index = {row["case_id"]: row for row in scores}
    for case in packet["cases"]:
        mapping, answers = mappings[case["case_id"]], case["answers"]
        if set(mapping) != {"A", "B"} or set(answers) != {"A", "B"}:
            raise ValueError("invalid paired labels")
        values = {}
        for label in ("A", "B"):
            identity = mapping[label]
            if any(not isinstance(identity.get(k), str) or not identity[k].strip() for k in ("system_id", "run_id")):
                raise ValueError("missing system/run identity")
            digest = sha256_bytes(answers[label]["answer"].encode("utf-8"))
            if digest != answers[label]["answer_sha256"] or digest != identity["answer_sha256"]:
                raise ValueError("answer/key binding mismatch")
            if identity["system_id"] in values:
                raise ValueError("paired systems must differ")
            values[identity["system_id"]] = score_index[case["case_id"]]["scores"][label]
        if systems is None:
            systems = sorted(values)
        elif set(values) != set(systems):
            raise ValueError("system pair changed across cases")
        rows.append(
            {
                "case_id": case["case_id"],
                "scores": values,
                "delta_first_minus_second": values[systems[0]] - values[systems[1]],
            }
        )
    deltas = [r["delta_first_minus_second"] for r in rows]
    return {
        "schema_version": "mucaro-paired-comparison/1",
        "status": "DESCRIPTIVE_ONLY",
        "systems": systems,
        "case_count": len(rows),
        "rows": rows,
        "mean_delta_first_minus_second": sum(deltas) / len(deltas),
        "first_wins": sum(d > 0 for d in deltas),
        "ties": sum(d == 0 for d in deltas),
        "second_wins": sum(d < 0 for d in deltas),
        "identity_leakage_cases": validated["summary"]["identity_leakage_cases"],
        "seal_sha256": expected_seal_sha256,
        "key_sha256": sha256_bytes(key_bytes),
        "superiority": "NOT_ESTABLISHED",
        "reviewer_independence": "NOT_ATTESTED",
        "release_certification": "WITHHELD",
    }
