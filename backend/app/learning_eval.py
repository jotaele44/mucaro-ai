"""Deterministic, synthetic held-out evaluation of reward-driven workflow selection.

No language model, external agent or user data participates. The explicitly bound
workflow functions below are evaluation-only consumers of procedure artifacts.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from . import learning
from .config import Settings


def fixtures() -> list[dict]:
    cases = []
    for index in range(8):
        split = "train" if index < 4 else "held_out"
        raw = [{"record_id": f"ID {index} A", "name": "Same name", "value": None},
               {"record_id": f"ID {index} B", "name": "Same name", "value": index}]
        cases.extend([
            {"id": f"identity-{index}", "split": split, "context": "eval/identity", "input": raw,
             "expected": [f"ID {index} A", f"ID {index} B"]},
            {"id": f"conservation-{index}", "split": split, "context": "eval/conservation", "input": raw,
             "expected": [f"ID {index} A", f"ID {index} B"]},
            {"id": f"ties-{index}", "split": split, "context": "eval/ties",
             "input": [{"id": f"A{index}", "score": index}, {"id": f"B{index}", "score": index}],
             "expected": {"status": "UNRESOLVED", "candidates": [f"A{index}", f"B{index}"]}},
        ])
    return cases


def execute(context: str, action: str, payload: list[dict]):
    if context == "eval/identity":
        if action == "preserve":
            return [row["record_id"] for row in payload]
        return list(dict.fromkeys(row["name"].casefold() for row in payload))
    if context == "eval/conservation":
        return [row["record_id"] for row in payload if action == "preserve" or row["value"] is not None]
    if context == "eval/ties":
        best = max(row["score"] for row in payload)
        matches = [row["id"] for row in payload if row["score"] == best]
        if action == "heuristic":
            return {"status": "BOUND", "candidates": matches[:1]}
        return {"status": "UNRESOLVED" if len(matches) > 1 else "BOUND", "candidates": matches}
    raise ValueError("Unknown evaluation context")


def _cfg(cfg: Settings, root: Path) -> Settings:
    return replace(cfg, data_dir=root, input_dir=root / "input", processed_dir=root / "processed",
                   index_dir=root / "indexes", export_dir=root / "exports", sqlite_path=root / "data.sqlite3")


def _run(cfg: Settings, cases: list[dict], *, invert_rewards: bool = False) -> dict:
    train = [case for case in cases if case["split"] == "train"]
    held_out = [case for case in cases if case["split"] == "held_out"]
    contexts = sorted({case["context"] for case in cases})
    mapping = {}
    for context in contexts:
        for action in ["preserve", "heuristic"]:
            evidence = [case for case in train if case["context"] == context]
            text = f"Evaluation-only {action} workflow for {context}."
            row = learning.recognize(cfg, text=text, source_id=f"fixture:{context}:{action}",
                                     source_bytes=json.dumps(evidence, sort_keys=True), permitted_use=True,
                                     purpose="Synthetic scoped bandit evaluation only", kind="skill", scope=context)
            row = learning.review(cfg, row["candidate_id"], version=row["version"], accept=True,
                                  rationale="Synthetic fixture artifact, not a deployed procedure", positive_case="Bound adapter",
                                  negative_case="Unbound adapter denied", conflict_ids=[], explicit_approval=True,
                                  verified_evidence=True, authority_safe=True)
            row = learning.apply(cfg, row["candidate_id"], version=row["version"])
            row = learning.verify(cfg, row["candidate_id"], version=row["version"], positive_pass=True,
                                  negative_pass=True, evaluation_receipt="Synthetic adapter binding verified by evaluator")
            mapping[row["candidate_id"]] = (context, action)
    untrained = {context: learning.recommend(cfg, context) for context in contexts}
    for case in train:
        for candidate_id, (context, action) in mapping.items():
            if context != case["context"]:
                continue
            observed = execute(context, action, case["input"])
            reward = 1.0 if observed == case["expected"] else -1.0
            if invert_rewards:
                reward *= -1
            learning.feedback(cfg, episode_id=f'{case["id"]}:{action}', candidate_id=candidate_id,
                              context=context, reward=reward, permitted_use=True,
                              evidence=json.dumps({"fixture_id": case["id"], "observed": observed,
                                                   "expected": case["expected"], "inverted_control": invert_rewards}, sort_keys=True))
    frozen = {context: learning.recommend(cfg, context) for context in contexts}
    before = learning.digest(learning.snapshot(cfg)["feedback"])
    results = []
    for case in held_out:
        policy = frozen[case["context"]]
        selected = policy["top_candidates"] if policy["status"] == "RECOMMENDATION" else []
        observed = execute(case["context"], mapping[selected[0]][1], case["input"]) if selected else None
        uniform_pass = sum(execute(case["context"], action, case["input"]) == case["expected"]
                           for action in ["preserve", "heuristic"]) / 2
        results.append({"fixture_id": case["id"], "selected": selected, "observed": observed,
                        "pass": observed == case["expected"], "uniform_expected_accuracy": uniform_pass})
    after = learning.digest(learning.snapshot(cfg)["feedback"])
    if before != after:
        raise ValueError("EVALUATION_HOLDOUT_CHANGED_TRAINING_FEEDBACK")
    return {"untrained_abstentions": sum(p["status"] == "UNRESOLVED" for p in untrained.values()),
            "policies": frozen, "results": results, "held_out_passed": sum(r["pass"] for r in results),
            "held_out_total": len(results), "uniform_expected_accuracy": sum(r["uniform_expected_accuracy"] for r in results) / len(results),
            "reward_episodes": len(learning.snapshot(cfg)["feedback"]), "training_feedback_sha256": before,
            "held_out_feedback_unchanged": before == after,
            "unseen_context_status": learning.recommend(cfg, "eval/unseen")["status"],
            "receipt_chain": learning.snapshot(cfg)["receipt_chain"]}


def evaluate(cfg: Settings) -> dict:
    cases = fixtures()
    train_ids = {case["id"] for case in cases if case["split"] == "train"}
    test_ids = {case["id"] for case in cases if case["split"] == "held_out"}
    if not train_ids.isdisjoint(test_ids):
        raise ValueError("EVALUATION_TRAIN_HELD_OUT_OVERLAP")
    with TemporaryDirectory(prefix="mucaro-learning-eval-") as directory:
        root = Path(directory)
        measured = _run(_cfg(cfg, root / "measured"), cases)
        poisoned = _run(_cfg(cfg, root / "inverted-reward-control"), cases, invert_rewards=True)
    passed = (measured["held_out_passed"] == measured["held_out_total"] and poisoned["held_out_passed"] == 0
              and measured["unseen_context_status"] == "UNRESOLVED" and measured["untrained_abstentions"] == 3)
    return {"schema": "mucaro-learning-effectiveness/1", "status": "PASS" if passed else "FAIL",
            "scope": "synthetic exact-context reward-conditioned workflow selection with evaluation-only adapters",
            "fixtures": cases, "fixtures_sha256": learning.digest(cases),
            "split_algebra": {"intersection": [], "train_only": sorted(train_ids), "held_out_only": sorted(test_ids),
                              "union_count": len(train_ids | test_ids), "symmetric_difference_count": len(train_ids ^ test_ids)},
            "measured": measured, "inverted_reward_control": poisoned,
            "effectiveness": {"trained_held_out_accuracy": measured["held_out_passed"] / measured["held_out_total"],
                              "uniform_expected_accuracy": measured["uniform_expected_accuracy"],
                              "difference_percentage_points": 100 * (measured["held_out_passed"] / measured["held_out_total"] - measured["uniform_expected_accuracy"])},
            "limitations": ["Synthetic finite fixtures, not user-task or real-world transfer evidence.",
                            "The evaluator binds workflow functions; production artifacts do not become executable agents.",
                            "Reward inversion demonstrates susceptibility to incorrect feedback, not resistance to poisoning.",
                            "No model-weight training, unseen-context generalization, or causal real-world improvement claim."]}
