"""Deterministic, fail-closed candidate learning loop.

This module records observations and proposes improvements. It never executes tools,
trains model weights, modifies instructions, or grants capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any

SCHEMA = "mucaro-governed-learning-loop/1.0"
PHASES = (
    "PERCEIVE", "UNDERSTAND", "PLAN", "EXECUTE", "OBSERVE", "EVALUATE",
    "LEARN", "OPTIMIZE", "VERIFY", "PROMOTE", "MONITOR", "RECOVER",
)
TERMINAL = frozenset({"PASS", "FAIL", "BLOCKED", "OPEN"})
DESTINATIONS = frozenset({
    "memory", "skill", "workflow", "agent_policy", "project_instructions",
    "evaluation_dataset", "training_candidate", "code_patch",
})
PROTECTED = frozenset({"core_policy", "capability_authority", "credentials", "production"})


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + sha256(canonical_bytes(value)).hexdigest()


@dataclass
class LearningRun:
    run_id: str
    objective: str
    input_digest: str
    events: list[dict[str, Any]] = field(default_factory=list)
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(isinstance(x, str) and x.strip() for x in (
            self.run_id, self.objective, self.input_digest
        )):
            raise ValueError("run identity, objective and input digest required")

    def observe(self, *, phase: str, state: str, evidence_digest: str,
                details: dict[str, Any] | None = None) -> dict[str, Any]:
        if phase not in PHASES or state not in TERMINAL:
            raise ValueError("unknown phase or state")
        if not isinstance(evidence_digest, str) or not evidence_digest.startswith("sha256:"):
            raise ValueError("evidence must be digest-bound")
        if self.events and PHASES.index(phase) < PHASES.index(self.events[-1]["phase"]):
            raise ValueError("out-of-order phase")
        data = {"seq": len(self.events) + 1, "phase": phase, "state": state,
                "evidence_digest": evidence_digest, "details": details or {},
                "prev_hash": self.events[-1]["event_hash"] if self.events else None}
        data["event_hash"] = digest(data)
        self.events.append(data)
        return data.copy()

    def propose(self, *, proposal_id: str, destination: str,
                source_event_hashes: list[str], candidate_digest: str,
                benchmark_digest: str) -> dict[str, Any]:
        if not proposal_id or proposal_id in self.proposals:
            raise ValueError("invalid or duplicate proposal")
        if destination not in DESTINATIONS or destination in PROTECTED:
            raise ValueError("unsupported destination")
        known = {e["event_hash"] for e in self.events if e["state"] == "PASS"}
        if not source_event_hashes or len(source_event_hashes) != len(set(source_event_hashes)):
            raise ValueError("missing or repeated evidence")
        if not set(source_event_hashes) <= known:
            raise ValueError("proposals require passed local evidence")
        if not candidate_digest.startswith("sha256:") or not benchmark_digest.startswith("sha256:"):
            raise ValueError("candidate and benchmark hashes required")
        proposal = {"proposal_id": proposal_id, "destination": destination,
                    "source_events": sorted(source_event_hashes),
                    "candidate_digest": candidate_digest, "benchmark_digest": benchmark_digest,
                    "status": "CANDIDATE", "promoted": False}
        self.proposals[proposal_id] = proposal
        return proposal.copy()

    def evaluate_proposal(self, proposal_id: str, *, baseline_score: float,
                          candidate_score: float, regression_pass: bool,
                          independent_evaluator: bool, permitted_use: bool,
                          operator_approved: bool) -> dict[str, Any]:
        proposal = self.proposals[proposal_id]
        if proposal["status"] != "CANDIDATE":
            raise ValueError("proposal already decided")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and 0 <= v <= 1 for v in (baseline_score, candidate_score)):
            raise ValueError("invalid normalized benchmark")
        passed = (candidate_score > baseline_score and regression_pass and
                  independent_evaluator and permitted_use and operator_approved)
        proposal.update({"status": "PROMOTION_ELIGIBLE" if passed else "REJECTED",
                         "baseline_score": baseline_score,
                         "candidate_score": candidate_score,
                         "regression_pass": regression_pass,
                         "independent_evaluator": independent_evaluator,
                         "permitted_use": permitted_use,
                         "operator_approved": operator_approved})
        return proposal.copy()

    def receipt(self) -> dict[str, Any]:
        events = [dict(e) for e in self.events]
        result = {"schema": SCHEMA, "run_id": self.run_id,
                  "objective": self.objective, "input_digest": self.input_digest,
                  "events": events, "proposals": sorted(
                      (dict(p) for p in self.proposals.values()), key=lambda p: p["proposal_id"])}
        return {**result, "receipt_hash": digest(result)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    try:
        data = {k: v for k, v in receipt.items() if k != "receipt_hash"}
        if receipt["receipt_hash"] != digest(data) or data["schema"] != SCHEMA:
            return False
        prior = None
        last_index = -1
        for index, event in enumerate(data["events"], 1):
            body = {k: v for k, v in event.items() if k != "event_hash"}
            if (event["seq"] != index or event["prev_hash"] != prior or
                event["event_hash"] != digest(body) or
                event["phase"] not in PHASES or event["state"] not in TERMINAL or
                PHASES.index(event["phase"]) < last_index):
                return False
            last_index = PHASES.index(event["phase"])
            prior = event["event_hash"]
        allowed = {e["event_hash"] for e in data["events"] if e["state"] == "PASS"}
        for p in data["proposals"]:
            if (p["destination"] not in DESTINATIONS or p.get("promoted") is not False or
                not p["source_events"] or not set(p["source_events"]) <= allowed):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False
