"""Operator-approval plumbing shared by the CLI command groups and the coding agent loop.

Every approval decision and its outcome is appended to the existing hash-chained tool audit
log (tools.py), so CLI-driven changes leave the same evidence as the API approval queue.
An unaudited decision never proceeds: if the audit log cannot be written, the action is not run.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .tools import record_operator_decision, record_operator_outcome

CLI_ACTOR = "mucaro-cli"


class OperatorAuditError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.status = "ERROR_OPERATOR_AUDIT_FAILED"
        self.detail = detail


@dataclass(frozen=True)
class ApprovalDecision:
    """An operator's answer plus how it was reached (one of tools.APPROVAL_MODES)."""

    approved: bool
    mode: str


def _stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (ValueError, OSError):
        return False


def approval_mode(yes: bool) -> str:
    """The mode an approval prompt is (or would be) answered in, for the audit trail."""
    if yes:
        return "blanket_yes"
    return "interactive" if _stdin_is_tty() else "denied_non_tty"


def audit_decision(
    cfg: Settings,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    approved: bool,
    requested_by: str,
    mode: str,
    reason: str | None = None,
) -> str:
    """Record a decision before anything runs; any failure to record it stops the action."""
    try:
        return record_operator_decision(
            cfg, tool_name, arguments, approved=approved, requested_by=requested_by, approval_mode=mode, reason=reason
        )
    except Exception as exc:  # an unauditable decision must not proceed
        raise OperatorAuditError(f"decision for {tool_name} could not be recorded: {type(exc).__name__}: {exc}") from exc


def _record_outcome(
    cfg: Settings, call_id: str, tool_name: str, requested_by: str, *, ok: bool, status: str | None
) -> str | None:
    try:
        record_operator_outcome(cfg, call_id, tool_name, requested_by=requested_by, ok=ok, result_status=status)
    except Exception as exc:  # noqa: BLE001 - the action already ran; report the gap instead of masking its result
        return f"outcome event not recorded: {type(exc).__name__}: {exc}"
    return None


def run_audited(
    cfg: Settings,
    tool_name: str,
    call_id: str,
    requested_by: str,
    action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Run an approved action and record how it ended. The action's own result or error always wins.

    BaseException is caught (and re-raised) on purpose: a Ctrl-C during a long run, or a closed event
    stream, must still leave a FAILED outcome behind instead of an approved call with no ending.
    """
    try:
        result = action()
    except BaseException as exc:
        _record_outcome(cfg, call_id, tool_name, requested_by, ok=False, status=str(getattr(exc, "status", type(exc).__name__)))
        raise
    status = result.get("status") if isinstance(result, dict) else None
    warning = _record_outcome(cfg, call_id, tool_name, requested_by, ok=True, status=str(status) if status else None)
    if warning and isinstance(result, dict):
        return {**result, "audit_warning": warning}
    return result
