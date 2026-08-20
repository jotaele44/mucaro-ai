from __future__ import annotations

from typing import Any

from .utils import stable_id, utc_now

_EXECUTION_MODES = [
    "MANUAL_MODE",
    "TERMINAL_MODE",
    "LOCAL_IN_CHAT_MODE",
    "CLAUDE_CODE_MODE",
    "GITHUB_PR_MODE",
    "DESIGN_MODE",
    "AUTOMATION_MODE",
]

_DESTRUCTIVE_TERMS = {"delete", "remove", "drop", "overwrite", "merge", "deploy", "send", "file", "submit"}
_CODE_TERMS = {"code", "repo", "github", "branch", "test", "api", "backend", "frontend", "schema", "build", "cli"}
_DATA_TERMS = {"csv", "pdf", "json", "zip", "file", "dataset", "parse", "ingest", "rag"}
_DESIGN_TERMS = {"design", "prototype", "figma", "screen", "ui", "ux", "mockup"}
_AUTOMATION_TERMS = {"daily", "weekly", "monitor", "remind", "schedule", "recurring", "watch"}


def analyze_task(user_request: str, *, inputs_available: list[str] | None = None, approval_state: str = "NOT_REQUESTED") -> dict[str, Any]:
    request = user_request.strip()
    if not request:
        raise ValueError("EMPTY_TASK_REQUEST")
    inputs_available = inputs_available or []
    tokens = set(request.lower().replace("→", " ").replace("[", " ").replace("]", " ").split())
    active_vector = infer_active_vector(request, tokens)
    risks = infer_risks(request, tokens)
    gaps = infer_gaps(request, tokens, inputs_available)
    plan_options = generate_plan_options(tokens, risks)
    recommended_mode = recommend_mode(tokens, active_vector)
    readiness = score_readiness(request, inputs_available, gaps, risks, approval_state)
    task_id = stable_id("task", request, utc_now(), length=12)
    state = "READY_FOR_APPROVAL" if readiness >= 75 else "PLANNING"
    return {
        "task_id": task_id,
        "mission": infer_mission(request),
        "active_vector": active_vector,
        "state": state,
        "inputs_required": infer_required_inputs(tokens, active_vector),
        "inputs_available": inputs_available,
        "assumptions": infer_assumptions(active_vector),
        "gaps": gaps,
        "risks": risks,
        "plan_options": plan_options,
        "recommended_mode": recommended_mode,
        "readiness_percent": readiness,
        "approval_required": True,
        "approved_mode": None,
        "execution_trace_id": None,
        "created_at": utc_now(),
    }


def infer_mission(request: str) -> str:
    cleaned = request.replace("EXECUTE_NEXT_VECTOR:", "").strip()
    return cleaned[:280] if len(cleaned) > 280 else cleaned


def infer_active_vector(request: str, tokens: set[str]) -> str:
    lower = request.lower()
    if "execution_gate" in lower or "planning" in lower:
        return "planning_gate_execution_control"
    if tokens & _CODE_TERMS:
        return "codebase_build"
    if tokens & _DATA_TERMS:
        return "data_rag_ingestion"
    if tokens & _DESIGN_TERMS:
        return "design_prototype"
    if tokens & _AUTOMATION_TERMS:
        return "automation_watch"
    return "general_task_execution"


def infer_required_inputs(tokens: set[str], active_vector: str) -> list[str]:
    required = ["user_request"]
    if active_vector == "codebase_build":
        required.extend(["target_repo_or_local_project", "execution_mode"])
    if active_vector == "data_rag_ingestion":
        required.extend(["input_files_or_folder", "output_schema"])
    if active_vector == "planning_gate_execution_control":
        required.extend(["execution_policy", "mode_selector_rules"])
    if "github" in tokens:
        required.append("github_repository")
    return sorted(set(required))


def infer_assumptions(active_vector: str) -> list[str]:
    base = ["No irreversible action occurs during PLANNING state.", "Execution requires approved_mode unless task is explicitly marked automation-safe."]
    if active_vector == "planning_gate_execution_control":
        base.append("The Planning Gate is a first-class workflow controller, not only a UI panel.")
    return base


def infer_risks(request: str, tokens: set[str]) -> list[str]:
    risks = []
    if tokens & _DESTRUCTIVE_TERMS:
        risks.append("Potential file/repo mutation; approval lock required.")
    if "api" in tokens or "key" in tokens:
        risks.append("Secret handling risk; never log API keys.")
    if "github" in tokens or "merge" in tokens or "pr" in tokens:
        risks.append("Repository write risk; branch/PR target must be explicit.")
    if len(request) < 40:
        risks.append("Task may be underspecified.")
    return risks


def infer_gaps(request: str, tokens: set[str], inputs_available: list[str]) -> list[str]:
    gaps = []
    if "github" in tokens and not any("repo" in item.lower() for item in inputs_available):
        gaps.append("Missing target GitHub repository.")
    if "file" in tokens and not inputs_available:
        gaps.append("No input files confirmed available.")
    if "execute" in request.lower() and not any("mode" in item.lower() for item in inputs_available):
        gaps.append("Execution mode must be selected or inferred from explicit vector.")
    return gaps


def generate_plan_options(tokens: set[str], risks: list[str]) -> list[dict[str, str]]:
    options = []
    def add(mode: str, reason: str) -> None:
        options.append({"mode": mode, "reason": reason})
    add("MANUAL_MODE", "Return a plan/instructions without executing changes.")
    if tokens & _CODE_TERMS or tokens & _DATA_TERMS:
        add("TERMINAL_MODE", "Provide copy-paste commands for the user's machine.")
        add("LOCAL_IN_CHAT_MODE", "Generate local files/artifacts in the current sandbox where possible.")
    if tokens & _CODE_TERMS:
        add("CLAUDE_CODE_MODE", "Use a coding agent workflow for repo scaffolding, patches, and tests.")
    if "github" in tokens:
        add("GITHUB_PR_MODE", "Create a branch, patch files, and open a PR after approval.")
    if tokens & _DESIGN_TERMS:
        add("DESIGN_MODE", "Create or refine a UI/UX prototype.")
    if tokens & _AUTOMATION_TERMS:
        add("AUTOMATION_MODE", "Schedule or monitor repeated future checks.")
    if risks:
        add("APPROVAL_LOCK", "Required because risk flags are present.")
    return options


def recommend_mode(tokens: set[str], active_vector: str) -> str:
    if "github" in tokens:
        return "GITHUB_PR_MODE"
    if active_vector == "codebase_build":
        return "CLAUDE_CODE_MODE"
    if active_vector == "data_rag_ingestion":
        return "LOCAL_IN_CHAT_MODE"
    if active_vector == "design_prototype":
        return "DESIGN_MODE"
    if active_vector == "automation_watch":
        return "AUTOMATION_MODE"
    return "MANUAL_MODE"


def score_readiness(request: str, inputs_available: list[str], gaps: list[str], risks: list[str], approval_state: str) -> int:
    score = 0
    score += 20 if len(request.strip()) >= 20 else 10
    score += 20 if inputs_available else 10
    score += 15 if "→" in request or "EXECUTE_NEXT_VECTOR" in request else 8
    score += 15  # local tool path available by default
    score += max(0, 15 - 5 * len(risks))
    score += 10 if not gaps else max(0, 10 - 4 * len(gaps))
    score += 5 if approval_state in {"APPROVED", "EXPLICIT_VECTOR"} else 0
    return max(0, min(100, score))


def approve_execution(gate: dict[str, Any], approved_mode: str) -> dict[str, Any]:
    if approved_mode not in _EXECUTION_MODES and approved_mode != "APPROVAL_LOCK":
        raise ValueError(f"INVALID_EXECUTION_MODE:{approved_mode}")
    updated = dict(gate)
    updated["approved_mode"] = approved_mode
    updated["state"] = "EXECUTING"
    updated["execution_trace_id"] = stable_id("trace", gate["task_id"], approved_mode, utc_now(), length=12)
    return updated
