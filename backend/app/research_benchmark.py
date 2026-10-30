"""Paired, label-blinded research evaluation preparation. No model calls."""

from __future__ import annotations

import math
import secrets

from .artifact_identity import sha256_bytes


def prepare_blind_pairs(cases: list[dict]) -> tuple[dict, dict]:
    """Keep returned key private until reviewer scores are frozen.

    Only explicit identity metadata is removed. Answer prose/citations may reveal
    identity; do not rewrite evidence to hide that. Record leakage on review.
    """
    if not isinstance(cases, list) or not cases:
        raise ValueError("nonempty paired cases required")
    public, private, seen = [], [], set()
    for case in cases:
        if not isinstance(case, dict):
            raise TypeError("case must be an object")
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError("missing or duplicate case ID")
        seen.add(case_id)
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise ValueError("question required")
        responses = case["responses"]
        if not isinstance(responses, list) or any(not isinstance(r, dict) for r in responses):
            raise ValueError("responses must be a list of objects")
        if any(not isinstance(r.get("system_id"), str) or not r["system_id"].strip() for r in responses):
            raise ValueError("system IDs must be nonempty strings")
        if len(responses) != 2 or len({r["system_id"] for r in responses}) != 2:
            raise ValueError("exactly two distinct systems required")
        ordering = list(responses)
        secrets.SystemRandom().shuffle(ordering)
        answers, mapping = {}, {}
        for label, response in zip(("A", "B"), ordering):
            for field in ("system_id", "answer", "run_id"):
                if not isinstance(response.get(field), str) or not response[field].strip():
                    raise ValueError(f"missing {field}")
            digest = sha256_bytes(response["answer"].encode("utf-8"))
            answers[label] = {"answer": response["answer"], "answer_sha256": digest}
            mapping[label] = {"system_id": response["system_id"], "run_id": response["run_id"], "answer_sha256": digest}
        public.append({"case_id": case_id, "question": case["question"], "answers": answers})
        private.append({"case_id": case_id, "mapping": mapping})
    return (
        {"schema_version": "mucaro-blind-review/1", "cases": public},
        {"schema_version": "mucaro-blind-key/1", "cases": private},
    )


def summarize_reviews(review_packet: dict, reviews: list[dict]) -> dict:
    """Require exactly one complete paired review per case; missing work cannot disappear."""
    if not isinstance(review_packet, dict) or review_packet.get("schema_version") != "mucaro-blind-review/1":
        raise ValueError("unsupported review packet")
    cases = review_packet["cases"]
    if not isinstance(cases, list) or not isinstance(reviews, list):
        raise TypeError("cases and reviews must be lists")
    if any(not isinstance(case, dict) or not isinstance(case.get("case_id"), str) for case in cases):
        raise ValueError("invalid case shape")
    expected = {case["case_id"] for case in cases}
    if not expected or len(expected) != len(cases):
        raise ValueError("invalid case denominator")
    seen, deltas = set(), []
    for review in reviews:
        if not isinstance(review, dict) or not isinstance(review.get("case_id"), str):
            raise TypeError("invalid review shape")
        case_id = review["case_id"]
        if case_id not in expected or case_id in seen:
            raise ValueError("unknown or duplicate reviewed case")
        seen.add(case_id)
        if type(review.get("identity_leakage")) is not bool:
            raise ValueError("explicit leakage assessment required")
        scores = review["scores"]
        if not isinstance(scores, dict) or set(scores) != {"A", "B"}:
            raise ValueError("both scores required")
        for value in scores.values():
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("scores must be finite numbers from 0 to 100")
        deltas.append(scores["A"] - scores["B"])
    if seen != expected:
        raise ValueError("incomplete review denominator")
    # A/B are randomized per case: this aggregate is a workflow diagnostic, NOT a system comparison.
    return {
        "cases": len(cases),
        "reviews": len(reviews),
        "paired_reviews_complete": True,
        "identity_leakage_cases": sum(r["identity_leakage"] for r in reviews),
        "label_a_wins": sum(d > 0 for d in deltas),
        "ties": sum(d == 0 for d in deltas),
        "label_b_wins": sum(d < 0 for d in deltas),
        "comparative_superiority": "NOT_ASSESSED",
        "release_certification": "WITHHELD",
    }
