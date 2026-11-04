"""Bounded research DAG on the existing checkpoint and model-routing engines.

Development API only. A completed run is an AUDIT_ONLY artifact, never a release
certificate. Source text and model responses cannot add tasks or grant permissions.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path

from . import agent_checkpoints as checkpoints
from . import model_router, model_routing_policy
from .config import Settings
from .injection_guard import scan_chunks
from .research_evidence import audit_packet, verify_snapshot
from .utils import canonical_sha256, stable_id

SCHEMA = "mucaro-research-plan/1"
CLAIM_CLASSES = {"FACT", "COMPUTED", "BINDING", "INFERENCE", "ASSUMPTION", "HYPOTHESIS", "UNKNOWN"}


def validate_plan(plan: dict, packet: dict) -> list[dict]:
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA:
        raise ValueError("unsupported research plan")
    if not isinstance(plan.get("objective"), str) or not plan["objective"].strip():
        raise ValueError("objective required")
    for field in ("provider", "model"):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            raise ValueError(f"explicit {field} required")
    if type(plan.get("allow_live")) is not bool:
        raise ValueError("explicit allow_live required")
    for field, ceiling in (("max_model_calls", 20), ("max_input_chars", 60000), ("deadline_seconds", 86400)):
        value = plan.get(field)
        if type(value) is not int or not 1 <= value <= ceiling:
            raise ValueError(f"invalid {field}")
    tasks = plan.get("questions")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
        raise ValueError("one to twenty questions required")
    claim_ids = {claim["claim_id"] for claim in packet["claims"]}
    ids = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise TypeError("question must be an object")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in ids:
            raise ValueError("missing or duplicate question ID")
        ids.add(task_id)
        if not isinstance(task.get("query"), str) or not task["query"].strip():
            raise ValueError("query required")
        for field in ("depends_on", "claim_ids"):
            values = task.get(field)
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError(f"invalid {field}")
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {field}")
        if not task["claim_ids"] or not set(task["claim_ids"]) <= claim_ids:
            raise ValueError("missing or unknown claim binding")
    if any(not set(task["depends_on"]) <= ids for task in tasks):
        raise ValueError("unknown question dependency")
    ordered, done = [], set()
    while len(ordered) < len(tasks):
        ready = [t for t in tasks if t["id"] not in done and set(t["depends_on"]) <= done]
        if not ready:
            raise ValueError("cyclic research plan")
        for task in ready:
            ordered.append(copy.deepcopy(task))
            done.add(task["id"])
    return ordered


def claim_ledger(root: Path, packet: dict) -> dict:
    audit = audit_packet(root, packet)
    if audit["integrity_status"] != "PASS":
        raise ValueError("research evidence integrity failed")
    by_id = {c["claim_id"]: c for c in packet["claims"]}
    rows, edges = [], []
    for claim in packet["claims"]:
        epistemic_class = claim.get("epistemic_class", "UNKNOWN")
        if epistemic_class not in CLAIM_CLASSES:
            raise ValueError("unknown claim class")
        contradicts = claim.get("contradicts", [])
        if not isinstance(contradicts, list) or any(not isinstance(c, str) for c in contradicts):
            raise ValueError("invalid contradiction list")
        if len(set(contradicts)) != len(contradicts):
            raise ValueError("duplicate contradiction")
        for target in contradicts:
            if target not in by_id or target == claim["claim_id"]:
                raise ValueError("invalid contradiction endpoint")
            edges.append({"from": claim["claim_id"], "to": target, "state": "UNRESOLVED"})
        rows.append(
            {
                **copy.deepcopy(claim),
                "declared_class": epistemic_class,
                "entailment": "OPEN",
                "identity": "UNRESOLVED",
                "integrity": "PASS",
            }
        )
    return {
        "schema_version": "mucaro-research-claims/1",
        "packet_sha256": canonical_sha256(packet),
        "claims": rows,
        "contradictions": edges,
        "audit": audit,
        "release_certification": "WITHHELD",
    }


def create_research(plan: dict, packet: dict, root: Path, *, source_sha: str, cfg: Settings) -> dict:
    """Validate before writing. Persist one immutable plan/packet per new checkpoint."""
    ledger = claim_ledger(root, packet)
    questions = validate_plan(plan, packet)
    frozen = {"plan": copy.deepcopy(plan), "packet": copy.deepcopy(packet), "root": str(root.resolve())}
    digest = canonical_sha256(frozen)
    pending = [{"id": "claims"}] + [{"id": "question:" + q["id"]} for q in questions] + [{"id": "report"}]
    contradictions = [
        {
            "id": stable_id("contradiction", edge["from"], edge["to"]),
            "claims": [edge["from"], edge["to"]],
            "state": "UNRESOLVED",
        }
        for edge in ledger["contradictions"]
    ]
    return checkpoints.create_checkpoint(
        session_id="research",
        run_id=stable_id("research", digest),
        objective=plan["objective"],
        source_sha=source_sha,
        state="EXECUTING",
        pending_steps=pending,
        contradictions=contradictions,
        provider=plan["provider"],
        model=plan["model"],
        metadata={"research": frozen, "research_sha256": digest},
        cfg=cfg,
    )


def _result(checkpoint: dict, step: str) -> dict | None:
    row = next((row for row in checkpoint["completed_steps"] if row["id"] == step), None)
    if row is None:
        return None
    stored = row["result"]
    if stored.get("sha256") != canonical_sha256(stored.get("payload")):
        raise ValueError("stored research output hash mismatch")
    return stored["payload"]


def advance_research(checkpoint_id: str, *, source_sha: str, cfg: Settings, max_steps: int = 1) -> dict:
    """Run at most max_steps. In-flight uncertainty is never automatically replayed.

    Deadline is checked between operations, not a preemptive timeout. Existing
    provider transport timeouts still apply. Restart with the same source commit.
    """
    if type(max_steps) is not int or not 1 <= max_steps <= 22:
        raise ValueError("invalid step budget")
    current = checkpoints.get_checkpoint(checkpoint_id, cfg)
    if current is None:
        raise ValueError("unknown research checkpoint")
    if source_sha != current["source_sha"]:
        raise ValueError("research source commit changed")
    frozen = current["metadata"]["research"]
    if canonical_sha256(frozen) != current["metadata"]["research_sha256"]:
        raise ValueError("frozen research input changed")
    plan, packet, root = frozen["plan"], frozen["packet"], Path(frozen["root"])
    questions = validate_plan(plan, packet)
    # Revalidate evidence on every invocation, including completed runs.
    ledger = claim_ledger(root, packet)
    for row in current["completed_steps"]:
        _result(current, row["id"])
    if current["state"] != "EXECUTING":
        return current
    if current.get("in_flight_step"):
        raise ValueError("in-flight operation requires explicit outcome reconciliation")
    for _ in range(max_steps):
        age = (datetime.now(UTC) - datetime.fromisoformat(current["created_at"])).total_seconds()
        if age > plan["deadline_seconds"]:
            return checkpoints.transition_checkpoint(
                checkpoint_id,
                state="PAUSED_RESOURCE",
                pause_reason="RESEARCH_DEADLINE",
                expected_sequence=current["sequence"],
                cfg=cfg,
            )
        pending = current["pending_steps"]
        if not pending:
            if any(c["state"] != "ADJUDICATED" for c in current["contradictions"]):
                return checkpoints.transition_checkpoint(
                    checkpoint_id, state="BLOCKED_EXTERNAL", pause_reason="UNRESOLVED_RESEARCH_CONTRADICTIONS", cfg=cfg
                )
            return checkpoints.transition_checkpoint(checkpoint_id, state="COMPLETED", cfg=cfg)
        step = pending[0]["id"]
        if step.startswith("question:"):
            calls = sum(row["id"].startswith("question:") for row in current["completed_steps"])
            if calls >= plan["max_model_calls"]:
                return checkpoints.transition_checkpoint(
                    checkpoint_id, state="PAUSED_RESOURCE", pause_reason="RESEARCH_MODEL_CALL_BUDGET", cfg=cfg
                )
            task = next(q for q in questions if "question:" + q["id"] == step)
            if any(_result(current, "question:" + dep) is None for dep in task["depends_on"]):
                raise ValueError("dependency has no verified output")
            route = model_routing_policy.plan_model_route(
                task_type="cited_qa",
                preferred_provider=plan["provider"],
                preferred_model=plan["model"],
                allowed_providers=[plan["provider"]],
                allow_live=plan["allow_live"],
                require_offline=not plan["allow_live"],
                allow_offline_failover=False,
                cfg=cfg,
            )
            if route["status"] != "MODEL_ROUTE_READY":
                return checkpoints.transition_checkpoint(
                    checkpoint_id, state="PAUSED_PROVIDER", pause_reason="RESEARCH_ROUTE_BLOCKED", cfg=cfg
                )
            chunks = []
            selected = [c for c in packet["claims"] if c["claim_id"] in task["claim_ids"]]
            sources = {s["source_id"]: s for s in packet["sources"]}
            for claim in selected:
                _, raw = verify_snapshot(root, sources[claim["source_id"]])
                # Explicit UTF-8 only. OCR/legacy encodings need a separate transformation binding.
                text = raw[claim["byte_start"] : claim["byte_end"]].decode("utf-8", errors="strict")
                chunks.append(
                    {
                        "chunk_id": claim["claim_id"],
                        "source_id": claim["source_id"],
                        "text": text,
                        "trust": "UNTRUSTED_CONTENT",
                    }
                )
            if len(task["query"]) + sum(len(c["text"]) for c in chunks) > plan["max_input_chars"]:
                return checkpoints.transition_checkpoint(
                    checkpoint_id, state="PAUSED_RESOURCE", pause_reason="RESEARCH_INPUT_BUDGET", cfg=cfg
                )
        key = canonical_sha256({"research": current["metadata"]["research_sha256"], "step": step})
        checkpoints.begin_step(checkpoint_id, step_id=step, idempotency_key=key, cfg=cfg)
        try:
            if step == "claims":
                payload = ledger
            elif step == "report":
                payload = {
                    "schema_version": "mucaro-research-report/1",
                    "status": "AUDIT_ONLY",
                    "question_count": len(questions),
                    "answers": [{"id": q["id"], **_result(current, "question:" + q["id"])} for q in questions],
                    "claims": ledger["claims"],
                    "contradictions": ledger["contradictions"],
                    "source_sha": source_sha,
                    "research_sha256": current["metadata"]["research_sha256"],
                    "independent_verification": "OPEN",
                    "release_certification": "WITHHELD",
                }
            else:
                answer = model_router.generate_answer(
                    task["query"],
                    chunks,
                    model_provider=plan["provider"],
                    model_name=plan["model"],
                    allow_mock_fallback=False,
                    cfg=cfg,
                )
                if answer.get("model_provider") != plan["provider"] or answer.get("model_name") != plan["model"]:
                    raise ValueError("model identity differs from frozen plan")
                cited = answer.get("cited_chunk_ids")
                if (
                    not isinstance(cited, list)
                    or not cited
                    or any(not isinstance(c, str) for c in cited)
                    or len(set(cited)) != len(cited)
                    or not set(cited) <= set(task["claim_ids"])
                ):
                    raise ValueError("answer cites missing or ambiguous claim evidence")
                if not isinstance(answer.get("answer"), str) or not answer["answer"].strip():
                    raise ValueError("empty model answer")
                payload = {
                    "query": task["query"],
                    "answer": answer,
                    "route": route,
                    "injection_signals": scan_chunks(chunks),
                    "entailment": "OPEN",
                }
            current = checkpoints.complete_step(
                checkpoint_id,
                idempotency_key=key,
                result={"payload": payload, "sha256": canonical_sha256(payload)},
                cfg=cfg,
            )
        except Exception:
            # In-flight marker survives: a model could have charged before failure.
            checkpoints.transition_checkpoint(
                checkpoint_id,
                state="BLOCKED_EXTERNAL",
                pause_reason="RESEARCH_STEP_OUTCOME_REQUIRES_RECONCILIATION",
                cfg=cfg,
            )
            raise
    return current
