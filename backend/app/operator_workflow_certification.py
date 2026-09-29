from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from .answer_trace_eval import current_git_head
from .config import Settings
from .db import list_payloads
from .ingestion import get_chunks, get_index_status, get_sources, ingest_folder
from .memory import (
    delete_memory_item,
    export_memory,
    list_memory_items,
    remember_memory_item,
    set_memory_enabled,
)
from .permissions import check_directory_permission, grant_directory
from .release_receipts import write_release_receipt
from .services import answer_question
from .tools import (
    ToolRuntimeError,
    approve_tool_call,
    list_tool_audit_events,
    request_tool_call,
    run_tool_call,
)

OPERATOR_WORKFLOW_OK = "OPERATOR_WORKFLOW_CERT_OK"
OPERATOR_WORKFLOW_FAILED = "OPERATOR_WORKFLOW_CERT_FAILED"
OPERATOR_WORKFLOW_PROFILE = "v0.1.23-local-release"
OPERATOR_WORKFLOW_RECEIPT_TYPE = "operator_workflow"


def _temp_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "operator_workflow.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def _check(name: str, status: str, detail: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "status": status, "detail": detail}
    if evidence is not None:
        payload["evidence"] = evidence
    return payload


def _export_paths_exist(answer: dict[str, Any]) -> bool:
    export_paths = answer.get("export_paths")
    if not isinstance(export_paths, dict) or not export_paths:
        return False
    return all(Path(str(path)).exists() for path in export_paths.values())


def _write_operator_sources(source_dir: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "lineage.md").write_text(
        "\n".join(
            [
                "# Source Lineage",
                "",
                "Release-grade workflow certification records operator evidence.",
                "It requires cited chunk IDs, source IDs, trace exports, and approval logs.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (source_dir / "controls.txt").write_text(
        "Mucaro AI keeps memory explicit and local. Tool execution is deny-by-default until approved.\n",
        encoding="utf-8",
    )


def build_operator_workflow_certification(
    root: Path,
    *,
    profile: str = OPERATOR_WORKFLOW_PROFILE,
    command: list[str] | None = None,
) -> dict[str, Any]:
    head_sha = current_git_head(root)
    checks: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="mucaro-operator-workflow-") as temp_dir:
        temp_root = Path(temp_dir)
        project_root = temp_root / "project"
        external_sources = temp_root / "external_sources"
        cfg = _temp_settings(project_root)
        cfg.ensure_dirs()
        _write_operator_sources(external_sources)

        try:
            permission_before = check_directory_permission(external_sources, cfg)
            checks.append(
                _check(
                    "permission_required_before_grant",
                    "PASS" if permission_before.get("status") == "ERROR_DIRECTORY_PERMISSION_REQUIRED" else "FAIL",
                    str(permission_before.get("status")),
                    {"canonical_root": permission_before.get("canonical_root")},
                )
            )

            grant = grant_directory(
                external_sources,
                cfg,
                granted_by="operator_workflow_certifier",
                reason="certify isolated external-source ingestion boundary",
            )
            checks.append(
                _check(
                    "permission_grant",
                    "PASS" if grant.get("status") == "DIRECTORY_PERMISSION_GRANTED" else "FAIL",
                    str(grant.get("status")),
                    {"canonical_root": grant.get("canonical_root")},
                )
            )

            permission_after = check_directory_permission(external_sources / "lineage.md", cfg)
            checks.append(
                _check(
                    "permission_ok_after_grant",
                    "PASS" if permission_after.get("status") == "PERMISSION_OK" else "FAIL",
                    str(permission_after.get("status")),
                    {"scope": permission_after.get("scope")},
                )
            )

            ingest = ingest_folder(external_sources, cfg)
            sources = get_sources(cfg)
            chunks = get_chunks(cfg)
            index = get_index_status(cfg)
            semantic = index.get("semantic_store", {})
            ingest_passed = (
                ingest.get("status") == "INGEST_FOLDER_OK"
                and ingest.get("files_ingested") == 2
                and len(sources) == 2
                and len(chunks) >= 2
                and index.get("status") == "INDEX_OK"
                and isinstance(semantic, dict)
                and semantic.get("status") == "SEMANTIC_STORE_OK"
            )
            checks.append(
                _check(
                    "source_ingestion_and_indexes",
                    "PASS" if ingest_passed else "FAIL",
                    f"files={ingest.get('files_ingested')} sources={len(sources)} chunks={len(chunks)} index={index.get('status')}",
                    {
                        "source_count": len(sources),
                        "chunk_count": len(chunks),
                        "index_status": index.get("status"),
                        "semantic_store_status": semantic.get("status") if isinstance(semantic, dict) else None,
                    },
                )
            )

            memory = remember_memory_item(
                "Prefer release-grade answers with explicit source IDs.",
                cfg,
                kind="preference",
                source="operator_workflow_certifier",
            )["memory"]
            memory_id = str(memory["memory_id"])
            checks.append(_check("memory_save", "PASS", "MEMORY_SAVED", {"memory_id": memory_id}))

            answer = answer_question(
                "What does release-grade workflow certification record?",
                top_k=5,
                retrieval_strategy="persistent",
                model_provider="mock",
                model_name="mock",
                cfg=cfg,
                persist=True,
            )
            answer_passed = (
                answer.get("citation_status") == "CITATION_OK"
                and answer.get("memory_context_count") == 1
                and memory_id in answer.get("memory_ids", [])
                and bool(answer.get("cited_chunks"))
                and _export_paths_exist(answer)
            )
            checks.append(
                _check(
                    "ask_trace_memory_and_exports",
                    "PASS" if answer_passed else "FAIL",
                    str(answer.get("citation_status") or answer.get("status")),
                    {
                        "run_id": answer.get("run_id"),
                        "memory_context_count": answer.get("memory_context_count"),
                        "cited_chunks": len(answer.get("cited_chunks", [])),
                        "export_paths": answer.get("export_paths"),
                    },
                )
            )

            runs = list_payloads("runs", cfg.sqlite_path)
            checks.append(
                _check(
                    "run_history_persisted",
                    "PASS" if len(runs) == 1 and runs[0].get("run_id") == answer.get("run_id") else "FAIL",
                    f"runs={len(runs)}",
                    {"run_count": len(runs), "latest_run_id": runs[-1].get("run_id") if runs else None},
                )
            )

            disabled = set_memory_enabled(memory_id, False, cfg)
            active = list_memory_items(cfg, include_disabled=False)
            exported = export_memory(cfg)
            deleted = delete_memory_item(memory_id, cfg)
            memory_controls_passed = (
                disabled.get("status") == "MEMORY_UPDATED"
                and active.get("memory_count") == 0
                and exported.get("status") == "MEMORY_EXPORT_OK"
                and deleted.get("status") == "MEMORY_DELETE_OK"
            )
            checks.append(
                _check(
                    "memory_disable_export_delete",
                    "PASS" if memory_controls_passed else "FAIL",
                    (
                        f"disable={disabled.get('status')} active={active.get('memory_count')} "
                        f"export={exported.get('status')} delete={deleted.get('status')}"
                    ),
                )
            )

            requested = request_tool_call(
                "source_library.status",
                {},
                cfg=cfg,
                requested_by="operator_workflow_certifier",
                reason="certify approved local tool boundary",
            )
            tool_call_id = str(requested["tool_call"]["tool_call_id"])
            blocked_before_approval = False
            try:
                run_tool_call(tool_call_id, cfg=cfg, executed_by="operator_workflow_certifier")
            except ToolRuntimeError as exc:
                blocked_before_approval = exc.status == "ERROR_TOOL_APPROVAL_REQUIRED"
            checks.append(
                _check(
                    "tool_denied_before_approval",
                    "PASS" if blocked_before_approval else "FAIL",
                    "ERROR_TOOL_APPROVAL_REQUIRED" if blocked_before_approval else "tool executed before approval",
                    {"tool_call_id": tool_call_id},
                )
            )

            approved = approve_tool_call(
                tool_call_id,
                cfg=cfg,
                approved_by="operator_workflow_certifier",
                reason="certify approved execution",
            )
            executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="operator_workflow_certifier")
            audit = list_tool_audit_events(cfg)
            tool_events = [event.get("event") for event in audit.get("events", [])]
            tool_passed = (
                approved.get("status") == "TOOL_CALL_APPROVED"
                and executed.get("status") == "TOOL_CALL_EXECUTED"
                and executed.get("result", {}).get("status") == "SOURCE_LIBRARY_STATUS_OK"
                and tool_events == ["TOOL_CALL_REQUESTED", "TOOL_CALL_APPROVED", "TOOL_CALL_STARTED", "TOOL_CALL_EXECUTED"]
            )
            checks.append(
                _check(
                    "tool_approval_execution_audit",
                    "PASS" if tool_passed else "FAIL",
                    f"events={','.join(str(event) for event in tool_events)}",
                    {
                        "tool_call_id": tool_call_id,
                        "result_status": executed.get("result", {}).get("status"),
                        "event_count": len(tool_events),
                    },
                )
            )

            summary = {
                "source_count": len(sources),
                "chunk_count": len(chunks),
                "run_count": len(runs),
                "tool_audit_event_count": len(tool_events),
                "trace_export_count": len(answer.get("export_paths", {})) if isinstance(answer.get("export_paths"), dict) else 0,
            }
        except Exception as exc:  # pragma: no cover - defensive receipt evidence
            checks.append(_check("operator_workflow_unhandled_exception", "FAIL", f"{type(exc).__name__}: {exc}"))

    failed = [check for check in checks if check.get("status") != "PASS"]
    status = OPERATOR_WORKFLOW_FAILED if failed else OPERATOR_WORKFLOW_OK
    report = {
        "status": status,
        "profile": profile,
        "head_sha": head_sha,
        "summary": {
            "checks": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            **summary,
        },
        "checks": checks,
        "failed_checks": [str(check.get("name")) for check in failed],
    }
    receipt = write_release_receipt(
        root,
        receipt_type=OPERATOR_WORKFLOW_RECEIPT_TYPE,
        status=status,
        head_sha=head_sha,
        command=command or [sys.executable, "scripts/certify_operator_workflow.py"],
        payload=report,
    )
    report["receipt"] = str(receipt.relative_to(root) if receipt.is_relative_to(root) else receipt)
    return report


def format_operator_workflow_certification_text(report: dict[str, Any]) -> list[str]:
    summary = report.get("summary", {})
    lines = [
        str(report.get("status")),
        f"profile={report.get('profile')}",
        f"head_sha={report.get('head_sha')}",
        f"checks={summary.get('checks', 0)} passed={summary.get('passed', 0)} failed={summary.get('failed', 0)}",
        f"sources={summary.get('source_count', 0)} chunks={summary.get('chunk_count', 0)} runs={summary.get('run_count', 0)}",
        f"tool_audit_events={summary.get('tool_audit_event_count', 0)} trace_exports={summary.get('trace_export_count', 0)}",
    ]
    if report.get("receipt"):
        lines.append(f"receipt={report.get('receipt')}")
    for check in report.get("checks", []):
        if isinstance(check, dict):
            lines.append(f"{check.get('status')} {check.get('name')} {check.get('detail')}")
    return lines
