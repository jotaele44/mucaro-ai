"""Which governed tools may ever be approved without a per-call human approval.

Autopilot and run-scoped permission leases both stand in for an operator's approval of one tool call. Neither may do
that implicitly for a tool that executes a command, mutates files or repositories, pushes, or reaches a remote. The
only tools that an Autopilot self-approval, or a lease that does not name its tools one by one, may cover are the
read-only local inspection tools listed here, and only while the registry still declares them non-mutating and local.

A tool added to the registry is outside this set until it is named here. Naming a mutating or remote tool here has no
effect, because the registry flags are checked as well.
"""
from __future__ import annotations

from typing import Any

READ_ONLY_LOCAL_INSPECTION_TOOLS = frozenset(
    {
        "source_library.status",
        "product.audit",
        "workspace_profiles.list",
        "memory.list",
        "coding.repo_status",
        "coding.read_file",
        "coding.diff",
        "coding.patch_check",
    }
)


def is_read_only_local_inspection_tool(tool_name: Any, registry: dict[str, Any]) -> bool:
    if not isinstance(tool_name, str):
        return False
    tool = registry.get(tool_name)
    return (
        tool is not None
        and tool_name in READ_ONLY_LOCAL_INSPECTION_TOOLS
        and not tool.mutates_state
        and tool.network == "none"
    )
