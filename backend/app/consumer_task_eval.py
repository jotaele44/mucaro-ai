"""Operational task replay over a real frozen Mucaro repository, not generated files.

The measured outcome is audit-evidence completeness. This does not evaluate model
reasoning, code repair, external agent training, or general-purpose task transfer.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from . import agent_consumers as consumers
from . import learning
from .config import Settings
from .permissions import grant_directory
from .tools import list_tool_audit_events, list_tool_calls, verify_tool_audit_chain

SOURCE_HEAD = "2110eac33d4338a606e4e917507664e0a0135e95"
SOURCE_BASE = "404cf0afe4001c0dc6352db690f09f48dca600ae"
TASKS = [
    ("repository_review", "train", "backend/app/learning.py", "Review evidence retention and promotion guards"),
    ("repository_review", "train", "backend/app/services.py", "Review learned prompt-context consumption"),
    ("repository_review", "held_out", "backend/app/learning_api.py", "Review authenticated learning API boundaries"),
    ("repository_review", "held_out", "backend/app/security_gates.py", "Review current-process socket identity handling"),
    ("release_audit", "train", "docs/GOVERNED_LEARNING.md", "Review the declared implementation limits"),
    ("release_audit", "train", "docs/GOVERNED_LEARNING_CLOSEOUT.md", "Review source and release manifestation bindings"),
    ("release_audit", "held_out", "docs/evidence/governed-learning-closeout-2026-10-16/local-release-receipt.json", "Verify retained local release gate evidence"),
    ("release_audit", "held_out", "docs/evidence/governed-learning-closeout-2026-10-16/manifest.json", "Verify the retained closeout payload manifest"),
]


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], timeout=30)


def freeze_tasks(repo: Path) -> dict:
    if _git(repo, "rev-parse", "HEAD").decode().strip() != SOURCE_HEAD or _git(repo, "status", "--porcelain").strip():
        raise ValueError("REAL_TASK_SOURCE_NOT_FROZEN")
    diff = _git(repo, "diff", "--no-ext-diff", "--no-color", SOURCE_BASE, "--")
    tasks = []
    for index, (scope, split, path, objective) in enumerate(TASKS):
        raw = _git(repo, "show", f"{SOURCE_HEAD}:{path}")
        if (repo / path).read_bytes() != raw:
            raise ValueError("REAL_TASK_FILE_NOT_SOURCE_IDENTICAL")
        raw.decode("utf-8")
        if path.endswith(".json"):
            json.loads(raw)
        tasks.append({"task_id": f"mucaro-operational-{index + 1:02}", "scope": scope, "split": split,
                      "objective": objective, "file_path_raw": path, "file_sha256": hashlib.sha256(raw).hexdigest(),
                      "file_bytes": len(raw), "file_blob_sha": _git(repo, "rev-parse", f"{SOURCE_HEAD}:{path}").decode().strip(),
                      "source_url": f"https://github.com/jotaele44/mucaro-ai/blob/{SOURCE_HEAD}/{path}"})
    train = [t for t in tasks if t["split"] == "train"]
    held = [t for t in tasks if t["split"] == "held_out"]
    a, b = {t["file_sha256"] for t in train}, {t["file_sha256"] for t in held}
    if a & b or len(a) != len(train) or len(b) != len(held):
        raise ValueError("REAL_TASK_SPLIT_COLLISION")
    return {"schema": "mucaro-operational-task-manifest/1", "source_head": SOURCE_HEAD, "base_head": SOURCE_BASE,
            "source_tree": _git(repo, "rev-parse", "HEAD^{tree}").decode().strip(), "tasks": tasks,
            "diff_sha256": hashlib.sha256(diff).hexdigest(),
            "provenance": "User-authorized repository review and release-evidence review, replayed on actual public Mucaro source bytes. Task selection authored for this evaluation; not a naturally collected user-success dataset.",
            "split_algebra": {"intersection": [], "train_only": sorted(a), "held_out_only": sorted(b),
                              "union_count": len(a | b), "symmetric_difference_count": len(a ^ b)}}


def measure(run: dict, task: dict, manifest: dict, repo: Path) -> dict:
    results = [step["result"] for step in run["results"]]
    revision = any(r.get("status") == "CODING_REPOSITORY_STATUS_OK" and r.get("head_sha") == SOURCE_HEAD
                   and r.get("repo_root") == str(repo.resolve()) and r.get("dirty_status") == "CLEAN" for r in results)
    comparison = any(r.get("status") == "CODING_DIFF_OK" and r.get("base_commit_sha") == SOURCE_BASE
                     and r.get("head_sha") == SOURCE_HEAD and r.get("diff_sha256") == manifest["diff_sha256"]
                     and hashlib.sha256(r.get("diff", "").encode()).hexdigest() == manifest["diff_sha256"] for r in results)
    file_bytes = any(r.get("status") == "CODING_FILE_READ_OK" and r.get("relative_path") == task["file_path_raw"]
                     and r.get("sha256") == task["file_sha256"] and r.get("byte_size") == task["file_bytes"]
                     and hashlib.sha256(r.get("content", "").encode()).hexdigest() == task["file_sha256"] for r in results)
    criteria = {"exact_repository_revision": revision, "bound_comparison_bytes": comparison, "exact_requested_file_bytes": file_bytes}
    coverage = sum(criteria.values()) / len(criteria)
    return {"task_id": task["task_id"], "state": run["state"], "criteria": criteria, "coverage": coverage,
            "score": 2 * coverage - 1, "tool_calls": len(run["results"]), "run_id": run["run_id"],
            "plan_sha256": run["plan_sha256"], "output_sha256": run.get("output_sha256")}


def evaluate(cfg: Settings, repo: Path, evidence_dir: Path) -> dict:
    repo = repo.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    manifest = freeze_tasks(repo)
    manifest_hash = learning.digest(manifest)
    (evidence_dir / "task_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Explicit evaluator permission is separate from skill binding and run approval.
    grant_directory(repo, cfg, granted_by="operator_authorized_task_evaluator", reason="Read-only operational learning evaluation")
    bindings = {}
    for agent_id, agent in consumers.AGENTS.items():
        training = [t for t in manifest["tasks"] if t["scope"] == agent["scope"] and t["split"] == "train"]
        for recipe in consumers.RECIPES:
            text = f"For {agent['scope']} use the operator-reviewed {recipe} procedure."
            row = learning.recognize(cfg, text=text, source_id=f"operational-task-manifest:{manifest_hash}",
                                     source_bytes=json.dumps(training, sort_keys=True), permitted_use=True,
                                     purpose="Train and evaluate bounded local audit workflow selection", kind="skill", scope=agent["scope"])
            row = learning.review(cfg, row["candidate_id"], version=row["version"], accept=True,
                                  rationale="Evaluator-controlled binding to the fixed, read-only recipe; no inferred executable prose",
                                  positive_case="Exact approved tool plan emits source-bound receipts",
                                  negative_case="Unapproved, stale, revoked, dirty or mismatched source is rejected",
                                  conflict_ids=[], explicit_approval=True, verified_evidence=True, authority_safe=True)
            row = learning.apply(cfg, row["candidate_id"], version=row["version"])
            row = learning.verify(cfg, row["candidate_id"], version=row["version"], positive_pass=True, negative_pass=True,
                                  evaluation_receipt="Consumer regression corpus plus independent Git oracle in this task replay")
            binding = consumers.bind(cfg, agent_id=agent_id, candidate_id=row["candidate_id"], candidate_version=row["version"],
                                     artifact_sha256=row["artifact"]["sha256"], recipe_id=recipe, approved=True)
            bindings[(agent["scope"], recipe)] = binding
    initial = {agent["scope"]: learning.recommend(cfg, agent["scope"]) for agent in consumers.AGENTS.values()}
    def execute(task, binding, phase):
        plan = consumers.prepare(cfg, binding_id=binding["binding_id"], repo_root=str(repo), head_sha=SOURCE_HEAD,
                                 base_sha=SOURCE_BASE, file_path=task["file_path_raw"], file_sha256=task["file_sha256"],
                                 idempotency_key=f"{phase}:{task['task_id']}:{binding['recipe_id']}")
        run = consumers.execute(cfg, plan["run_id"], version=plan["version"], plan_sha256=plan["plan_sha256"], approved=True)
        if run["state"] != "COMPLETED":
            raise ValueError(f"REAL_TASK_RUN_FAILED:{task['task_id']}:{run['error']}")
        return run, measure(run, task, manifest, repo)
    training_results = []
    for task in [t for t in manifest["tasks"] if t["split"] == "train"]:
        for recipe in consumers.RECIPES:
            run, measured = execute(task, bindings[(task["scope"], recipe)], "train")
            consumers.reward(cfg, run["run_id"], output_sha256=run["output_sha256"], score=measured["score"],
                             criteria=json.dumps({"method": "independent_git_oracle_evidence_coverage", "criteria": measured["criteria"],
                                                  "task_manifest_sha256": manifest_hash}), permitted_use=True)
            training_results.append(measured)
    policies = {agent["scope"]: learning.recommend(cfg, agent["scope"]) for agent in consumers.AGENTS.values()}
    feedback_before = learning.digest(learning.snapshot(cfg)["feedback"])
    held_out = []
    for task in [t for t in manifest["tasks"] if t["split"] == "held_out"]:
        _baseline, base_measure = execute(task, bindings[(task["scope"], "summary/1")], "baseline")
        policy = policies[task["scope"]]
        eligible = [b for b in bindings.values() if b["scope"] == task["scope"] and b["candidate_id"] in policy["top_candidates"]]
        if policy["status"] != "RECOMMENDATION" or len(eligible) != 1:
            raise ValueError("REAL_TASK_POLICY_AMBIGUOUS_OR_UNBOUND")
        _learned, learned_measure = execute(task, eligible[0], "held-out")
        held_out.append({"task_id": task["task_id"], "baseline": base_measure, "learned": learned_measure})
    feedback_after = learning.digest(learning.snapshot(cfg)["feedback"])
    if feedback_before != feedback_after or learning.digest(freeze_tasks(repo)) != manifest_hash:
        raise ValueError("REAL_TASK_HELD_OUT_FEEDBACK_OR_SOURCE_DRIFT")
    state = learning.snapshot(cfg)
    with learning._store(cfg) as raw_state:
        frozen_state = json.loads(json.dumps(raw_state))
        learning._validate(frozen_state)
    (evidence_dir / "execution_snapshot.json").write_text(json.dumps(frozen_state, indent=2, ensure_ascii=False) + "\n")
    (evidence_dir / "tool_audit.json").write_text(json.dumps(list_tool_audit_events(cfg), indent=2, ensure_ascii=False) + "\n")
    calls = list_tool_calls(cfg)["tool_calls"]
    (evidence_dir / "tool_calls.json").write_text(json.dumps(calls, indent=2, ensure_ascii=False) + "\n")
    audit = verify_tool_audit_chain(cfg)
    baseline_coverage = sum(x["baseline"]["coverage"] for x in held_out) / len(held_out)
    learned_coverage = sum(x["learned"]["coverage"] for x in held_out) / len(held_out)
    passed = (all(x["learned"]["coverage"] == 1 for x in held_out) and audit["status"] == "TOOL_AUDIT_CHAIN_OK"
              and len(calls) == 40 and len(state["feedback"]) == 8 and len(state["consumer_runs"]) == 16)
    return {"schema": "mucaro-operational-task-learning/1", "status": "PASS" if passed else "FAIL",
            "task_manifest_sha256": manifest_hash, "task_count": len(manifest["tasks"]), "training_runs": training_results,
            "initial_policies": initial, "trained_policies": policies, "held_out": held_out,
            "metrics": {"training_feedback_episodes": len(state["feedback"]), "held_out_tasks": len(held_out),
                        "baseline_evidence_coverage": baseline_coverage, "learned_evidence_coverage": learned_coverage,
                        "gain_percentage_points": 100 * (learned_coverage - baseline_coverage),
                        "baseline_calls_per_task": 1, "learned_calls_per_task": 4, "total_actual_tool_calls": len(calls),
                        "cost_reduction_claimed": False},
            "held_out_feedback_unchanged": feedback_before == feedback_after, "feedback_sha256": feedback_after,
            "tool_audit": audit, "learning_receipt_chain": state["receipt_chain"],
            "limitations": ["Actual repository and retained release-evidence tasks, selected by this evaluator; not natural user-success observations.",
                            "Measures evidence acquisition, not semantic correctness of code reviews or model reasoning.",
                            "Workflow selection improves completeness at four versus one tool call; no latency/cost saving established.",
                            "Two known task categories only; no unseen-domain transfer or model-weight training claim."]}
