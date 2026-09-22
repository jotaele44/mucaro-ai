from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

REPAIRABLE = {"TEST_FAILURE","LINT_FAILURE","TYPE_FAILURE","BUILD_FAILURE","IMPORT_FAILURE","SCHEMA_FAILURE","PACKAGE_FAILURE"}
NON_CODE = {"PYTHON_DEPENDENCY_FAILURE","NPM_DEPENDENCY_FAILURE","VULNERABILITY_GATE_FAILURE","RUNTIME_MISSING","GIT_UNAVAILABLE","DOCKER_UNAVAILABLE","NETWORK_UNAVAILABLE","EXTERNAL_SERVICE_UNAVAILABLE","SECRET_REQUIRED","PERMISSION_DENIED","AUTHENTICATION_REQUIRED"}

@dataclass(frozen=True)
class MutationBudget:
    max_iterations: int = 3
    max_files: int = 5
    max_patch_bytes: int = 524288
    allow_dependency: bool = False
    allow_schema: bool = False
    allow_security: bool = False
    allow_ci_policy: bool = False

def classify_failure(name: str, detail: str = "") -> str:
    text=(name+" "+detail).lower()
    rules=(
      ("NETWORK_UNAVAILABLE",("network","dns","registry unreachable","connection refused")),
      ("SECRET_REQUIRED",("secret required","api key required","missing secret")),
      ("DOCKER_UNAVAILABLE",("docker unavailable","docker: command not found")),
      ("RUNTIME_MISSING",("command not found","runtime missing")),
      ("TYPE_FAILURE",("mypy","typecheck","type error")),
      ("LINT_FAILURE",("ruff","lint")),
      ("TEST_FAILURE",("pytest","test failed","assertionerror")),
      ("BUILD_FAILURE",("build failed","vite build","python -m build")),
      ("IMPORT_FAILURE",("modulenotfounderror","importerror")),
      ("SCHEMA_FAILURE",("schema","json validation")),
      ("PACKAGE_FAILURE",("package verify","package artifact")),
    )
    for state,needles in rules:
        if any(n in text for n in needles): return state
    return "UNCLASSIFIED_FAILURE"

def mutation_allowed(failure: str, *, files: int, patch_bytes: int, iteration: int, budget: MutationBudget = MutationBudget()) -> tuple[bool,str]:
    if failure not in REPAIRABLE: return False, failure if failure in NON_CODE else "UNCLASSIFIED_FAILURE"
    if iteration > budget.max_iterations or files > budget.max_files or patch_bytes > budget.max_patch_bytes:
        return False,"MUTATION_BUDGET_EXCEEDED"
    return True,"AUTHORIZED"

def decide_after_patch(before: Iterable[str], after: Iterable[str], *, targeted_pass: bool, full_pass: bool, history: Iterable[frozenset[str]] = ()) -> str:
    b=frozenset(before); a=frozenset(after)
    if not targeted_pass or not full_pass: return "ROLLBACK_REGRESSION"
    if a in set(history): return "ROLLBACK_OSCILLATION"
    if not a < b: return "ROLLBACK_NO_IMPROVEMENT"
    return "KEEP"
