"""Persistent, bounded, deterministic task selection for Mucaro.

Read-only prioritization never confers authority. Dispatch is a separate
capability-policy + leased-worker operation. No implicit new objectives.
"""
from __future__ import annotations
import json
import math
import sqlite3
from pathlib import Path
from typing import Any
from .governed_learning_loop import digest

SCHEMA = "mucaro-goal-reactor/1.0"
TERMINAL = {"PASS", "FAILED", "BLOCKED", "CANCELLED"}
ALLOWED = TERMINAL | {"READY", "RUNNING"}

class GoalReactorError(RuntimeError):
    pass

def _positive(value: float, name: str, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GoalReactorError("INVALID_" + name)
    if value < 0 or (not allow_zero and value == 0):
        raise GoalReactorError("INVALID_" + name)
    return float(value)

def _dump(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)

class GoalReactor:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=5)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS goals(
            goal_id TEXT PRIMARY KEY, description TEXT NOT NULL,
            authority_digest TEXT NOT NULL, limit_cost REAL NOT NULL,
            spent_cost REAL NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'ACTIVE'
          );
          CREATE TABLE IF NOT EXISTS tasks(
            task_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, phase TEXT NOT NULL,
            dependencies TEXT NOT NULL, source_digest TEXT NOT NULL,
            skill_id TEXT NOT NULL, requested_capabilities TEXT NOT NULL,
            expected_value REAL NOT NULL, confidence REAL NOT NULL,
            relevance REAL NOT NULL, cost REAL NOT NULL, risk REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'READY', result_digest TEXT,
            FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
          );
        """)
    def add_goal(self, *, goal_id: str, description: str,
                 authority_digest: str, limit_cost: float):
        limit = _positive(limit_cost, "BUDGET")
        if not goal_id or not description or not authority_digest.startswith("sha256:"):
            raise GoalReactorError("INVALID_GOAL_IDENTITY")
        identity = (description, authority_digest, limit)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT description,authority_digest,limit_cost FROM goals "
                                  "WHERE goal_id=?", (goal_id,)).fetchone()
            if old:
                if tuple(old) != identity:
                    raise GoalReactorError("GOAL_IDENTITY_CONFLICT")
            else:
                self.db.execute("INSERT INTO goals(goal_id,description,authority_digest,limit_cost) "
                                "VALUES(?,?,?,?)", (goal_id, *identity))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def add_task(self, *, task_id: str, goal_id: str, phase: str,
                 dependencies: tuple[str, ...], source_digest: str,
                 skill_id: str, requested_capabilities: tuple[str, ...],
                 expected_value: float, confidence: float, relevance: float,
                 cost: float, risk: float):
        if not all(isinstance(x, str) and x.strip() for x in (task_id, goal_id, phase, skill_id)):
            raise GoalReactorError("INVALID_TASK_IDENTITY")
        from .governed_learning_loop import PHASES
        if phase not in PHASES or phase in ("PROMOTE", "MONITOR", "RECOVER"):
            raise GoalReactorError("UNAUTHORIZED_TASK_PHASE")
        if not source_digest.startswith("sha256:"):
            raise GoalReactorError("MISSING_SOURCE_DIGEST")
        if not isinstance(dependencies, tuple) or task_id in dependencies or len(set(dependencies)) != len(dependencies):
            raise GoalReactorError("INVALID_DEPENDENCIES")
        if not isinstance(requested_capabilities, tuple) or len(set(requested_capabilities)) != len(requested_capabilities):
            raise GoalReactorError("INVALID_CAPABILITIES")
        value = _positive(expected_value, "VALUE")
        conf, rel = _positive(confidence, "CONFIDENCE"), _positive(relevance, "RELEVANCE")
        if conf > 1 or rel > 1:
            raise GoalReactorError("INVALID_PROBABILITY")
        amount, loss = _positive(cost, "COST", False), _positive(risk, "RISK")
        cap_json, deps_json = _dump(sorted(requested_capabilities)), _dump(sorted(dependencies))
        identity = (goal_id, phase, deps_json, source_digest, skill_id, cap_json,
                    value, conf, rel, amount, loss)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if not self.db.execute("SELECT 1 FROM goals WHERE goal_id=?", (goal_id,)).fetchone():
                raise GoalReactorError("GOAL_NOT_FOUND")
            for parent in dependencies:
                row = self.db.execute("SELECT goal_id FROM tasks WHERE task_id=?", (parent,)).fetchone()
                if not row or row[0] != goal_id:
                    raise GoalReactorError("UNKNOWN_OR_CROSS_GOAL_DEPENDENCY")
            old = self.db.execute(
                "SELECT goal_id,phase,dependencies,source_digest,skill_id,requested_capabilities,"
                "expected_value,confidence,relevance,cost,risk FROM tasks WHERE task_id=?",
                (task_id,)).fetchone()
            if old:
                if tuple(old) != identity:
                    raise GoalReactorError("TASK_IDENTITY_CONFLICT")
            else:
                self.db.execute(
                    "INSERT INTO tasks(task_id,goal_id,phase,dependencies,source_digest,skill_id,"
                    "requested_capabilities,expected_value,confidence,relevance,cost,risk)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (task_id, *identity))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def select(self, goal_id: str, *, risk_weight: float = 1.0) -> dict[str, Any]:
        weight = _positive(risk_weight, "RISK_WEIGHT")
        goal = self.db.execute("SELECT state,limit_cost,spent_cost FROM goals WHERE goal_id=?",
                               (goal_id,)).fetchone()
        if not goal:
            raise GoalReactorError("GOAL_NOT_FOUND")
        states = {r[0]:r[1] for r in self.db.execute(
            "SELECT task_id,state FROM tasks WHERE goal_id=?", (goal_id,))}
        tasks = self.db.execute(
            "SELECT task_id,phase,dependencies,source_digest,skill_id,requested_capabilities,"
            "expected_value,confidence,relevance,cost,risk FROM tasks "
            "WHERE goal_id=? AND state='READY' ORDER BY task_id", (goal_id,)).fetchall()
        eligible = []
        reasons = {"dependency": 0, "budget": 0}
        for task_id, phase, deps, source, skill, caps, value, conf, rel, cost, risk in tasks:
            if any(states.get(x) != "PASS" for x in json.loads(deps)):
                reasons["dependency"] += 1
                continue
            if cost > goal[1] - goal[2] + 1e-10:
                reasons["budget"] += 1
                continue
            score = (value * conf * rel) / (cost + weight * risk)
            eligible.append((score, task_id, {"task_id":task_id, "phase":phase,
                "skill_id":skill, "source_digest":source,
                "requested_capabilities":json.loads(caps), "cost":cost,
                "priority_score":score, "goal_id":goal_id}))
        if goal[0] != "ACTIVE":
            return {"status":"BLOCKED", "reason":"GOAL_INACTIVE"}
        if eligible:
            eligible.sort(key=lambda x:(-x[0], x[1]))
            return {"status":"READY", "candidate":eligible[0][2], "eligible_count":len(eligible)}
        if any(x=="RUNNING" for x in states.values()):
            return {"status":"OPEN", "reason":"IN_PROGRESS"}
        if states and all(x=="PASS" for x in states.values()):
            return {"status":"PASS", "reason":"ALL_TASKS_PASSED"}
        if not states:
            return {"status":"OPEN", "reason":"NO_DECLARED_TASKS"}
        return {"status":"BLOCKED", "reason":"NO_ELIGIBLE_TASKS", "counts":reasons}

    def record_result(self, *, task_id: str, state: str, result_digest: str,
                      policy_digest: str, cost_observed: float):
        if state not in TERMINAL or not result_digest.startswith("sha256:"):
            raise GoalReactorError("INVALID_RESULT")
        actual = _positive(cost_observed, "ACTUAL_COST")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT t.goal_id,t.state,t.cost,g.authority_digest,g.limit_cost,g.spent_cost "
                "FROM tasks t JOIN goals g ON t.goal_id=g.goal_id WHERE t.task_id=?",
                (task_id,)).fetchone()
            if not row:
                raise GoalReactorError("TASK_NOT_FOUND")
            goal, old, reserved, authority, limit, spent = row
            if policy_digest != authority:
                raise GoalReactorError("AUTHORITY_MISMATCH")
            if old == state:
                previous = self.db.execute("SELECT result_digest FROM tasks WHERE task_id=?",
                                           (task_id,)).fetchone()[0]
                if previous == result_digest:
                    self.db.execute("COMMIT")
                    return
            if old != "READY":
                raise GoalReactorError("TERMINAL_OR_STATE_CONFLICT")
            if actual > reserved or spent + actual > limit:
                raise GoalReactorError("BUDGET_EXCEEDED")
            self.db.execute("UPDATE tasks SET state=?,result_digest=? WHERE task_id=?",
                            (state,result_digest,task_id))
            self.db.execute("UPDATE goals SET spent_cost=spent_cost+? WHERE goal_id=?",
                            (actual,goal))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def summary(self, goal_id: str) -> dict[str, Any]:
        goal = self.db.execute("SELECT description,authority_digest,limit_cost,spent_cost,state "
                               "FROM goals WHERE goal_id=?", (goal_id,)).fetchone()
        if not goal:
            raise GoalReactorError("GOAL_NOT_FOUND")
        tasks = [dict(zip(("task_id","state","result_digest"),row))
                 for row in self.db.execute(
                     "SELECT task_id,state,result_digest FROM tasks WHERE goal_id=? ORDER BY task_id",
                     (goal_id,))]
        result = {"schema":SCHEMA,"goal_id":goal_id,"goal":list(goal),"tasks":tasks}
        return {**result,"receipt_hash":digest(result)}

    def close(self):
        self.db.close()
