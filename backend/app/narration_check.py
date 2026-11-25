"""Compare what an answer says was done with the call records (MOB-F15).

The model writes the answer; the call records say what happened. When the prose claims an action, such as a
commit, a push, a pull request, an applied change, a test run or a web fetch, and no call of that kind EXECUTED in
this run, the claim contradicts the records. The run then carries `WARN_NARRATION_CONTRADICTS_RECORDS`, so the person
sees that the call records, not the answer, are what to trust. The check is deliberately narrow: first-person or
passive completion claims only, so plans ("I will push"), refusals ("I did not commit") and questions do not trip it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

NARRATION_WARNING = "WARN_NARRATION_CONTRADICTS_RECORDS"

_SUBJECT = r"\b(?:i|we)(?:\s+have|'ve|\s+just|\s+now|\s+successfully|\s+also)*\s+"
_PASSIVE = r"\b(?:it|this|that|they|the\s+changes?|your\s+changes?|changes?|the\s+fix|the\s+branch|the\s+commit)\s+(?:has|have|was|were|is|are)\s+(?:now\s+|been\s+|successfully\s+)*"


@dataclass(frozen=True)
class Claim:
    claim: str
    pattern: re.Pattern[str]
    tools: tuple[str, ...]


def _claim(claim: str, active: str, passive: str | None, tools: tuple[str, ...]) -> Claim:
    parts = [_SUBJECT + active]
    if passive:
        parts.append(_PASSIVE + passive)
    return Claim(claim, re.compile("|".join(f"(?:{part})" for part in parts), re.IGNORECASE), tools)


CLAIMS: tuple[Claim, ...] = (
    _claim("COMMIT", r"committed\b(?!\s+to\b)", r"committed\b", ("coding.commit",)),
    _claim("PUSH", r"pushed\b", r"pushed\b", ("coding.push",)),
    _claim(
        "PULL_REQUEST",
        r"(?:opened|created|submitted|updated|raised)\s+(?:a|an|the|your)?\s*(?:new\s+|draft\s+)?(?:pull\s+request|PR)\b",
        None,
        ("github.pr_open_update",),
    ),
    _claim("BRANCH", r"created\s+(?:a|the|your)?\s*(?:new\s+)?(?:work\s+)?branch\b", None, ("coding.branch_create",)),
    _claim(
        "PATCH",
        r"applied\s+(?:a|an|the|your|this|that)?\s*(?:patch|change|changes|edit|edits|fix|diff)\b",
        r"applied\b",
        ("coding.patch_apply",),
    ),
    _claim("CLONE", r"cloned\b", r"cloned\b", ("repo.clone",)),
    _claim(
        "EXECUTION",
        r"(?:ran|executed|re-ran|reran)\s+(?:a|the|your|all\s+the|all)?\s*(?:unit\s+)?(?:tests?|test\s+suite|suite|checks?|command|script|regression)\b",
        None,
        ("coding.exec", "coding.regression_run"),
    ),
    _claim(
        "WEB",
        r"(?:fetched|downloaded|retrieved|visited|browsed|rendered)\s+(?:a|the|that|this|your)?\s*(?:web\s*)?(?:page|url|site|website|article|link)\b"
        r"|searched\s+the\s+web\b",
        None,
        ("web.fetch", "web.fetch_session", "web.render", "web.search"),
    ),
)


def narration_conflicts(answer: str, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each action the answer claims without an EXECUTED call of that kind in this run."""
    conflicts: list[dict[str, Any]] = []
    text = str(answer or "")
    for claim in CLAIMS:
        match = claim.pattern.search(text)
        if not match:
            continue
        related = [call for call in calls if call.get("tool_name") in claim.tools]
        if any(call.get("status") == "EXECUTED" for call in related):
            continue
        latest = related[-1] if related else None
        conflicts.append({
            "claim": claim.claim,
            "phrase": match.group(0).strip()[:80],
            "tools": list(claim.tools),
            "reason": f"RECORD_{latest.get('status')}" if latest else "NO_RECORD",
            "tool_call_ids": [str(call.get("tool_call_id")) for call in related],
        })
    return conflicts
