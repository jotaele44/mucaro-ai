from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import tomllib
from pathlib import Path
from typing import Any, Sequence

from .config import Settings, settings
from .conversations import (
    append_answer_turn,
    build_conversation_context,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
)
from .db import list_payloads
from .execution_gate import analyze_task
from .ingestion import (
    delete_source,
    get_index_status,
    get_sources,
    ingest_file,
    ingest_folder,
    reindex_sources,
)
from .permissions import (
    DirectoryPermissionError,
    check_directory_permission,
    grant_directory,
    load_directory_grants,
    revoke_directory,
)
from .retrieval import MAX_TOP_K, MIN_TOP_K, RETRIEVAL_STRATEGIES
from .services import answer_question
from .validation import validate_project_json


class MucaroCLIError(RuntimeError):
    def __init__(self, status: str, detail: str, *, exit_code: int = 2) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.exit_code = exit_code


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("PORT_MUST_BE_INTEGER") from exc
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("PORT_RANGE:1-65535")
    return port


def package_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.exists():
        try:
            return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
        except (KeyError, tomllib.TOMLDecodeError):
            pass
    try:
        return importlib.metadata.version("mucaro-ai")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.13-dev"


def parse_top_k(value: str) -> int:
    try:
        top_k = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("TOP_K_MUST_BE_INTEGER") from exc
    if top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        raise argparse.ArgumentTypeError(f"TOP_K_RANGE:{MIN_TOP_K}-{MAX_TOP_K}")
    return top_k


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mucaro",
        description="Command line interface for the local-first Mucaro AI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    ask = subcommands.add_parser("ask", help="Ask a cited retrieval question.")
    ask.add_argument("query", nargs="+", help="Question to answer from ingested local sources.")
    ask.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to use ({MIN_TOP_K}-{MAX_TOP_K}).")
    ask.add_argument("--strategy", choices=sorted(RETRIEVAL_STRATEGIES), default="hybrid", help="Retrieval strategy to use.")
    ask.add_argument("--provider", choices=["mock", "anthropic"], help="Model provider override.")
    ask.add_argument("--model", help="Model name override.")
    ask.add_argument("--allow-mock-fallback", action="store_true", help="Permit mock fallback when a live provider is unavailable.")
    ask.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    ask.add_argument("--no-persist", action="store_true", help="Do not write the run to SQLite.")
    ask.set_defaults(handler=run_ask)

    ingest = subcommands.add_parser("ingest", help="Ingest a supported file or folder.")
    ingest.add_argument("path", nargs="?", default="data/input", help="File or folder to ingest.")
    ingest.add_argument(
        "--grant-directory-access",
        action="store_true",
        help="Prompt to grant access when PATH is outside the project directory.",
    )
    ingest.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    ingest.set_defaults(handler=run_ingest)

    sources = subcommands.add_parser("sources", help="Inspect and manage ingested sources.")
    sources_subcommands = sources.add_subparsers(dest="sources_command", required=True)

    sources_list = sources_subcommands.add_parser("list", help="List ingested sources and index status.")
    sources_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_list.set_defaults(handler=run_sources_list)

    sources_reindex = sources_subcommands.add_parser("reindex", help="Rebuild the local persistent retrieval index.")
    sources_reindex.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_reindex.set_defaults(handler=run_sources_reindex)

    sources_delete = sources_subcommands.add_parser("delete", help="Delete one ingested source by source_id.")
    sources_delete.add_argument("source_id", help="Source identifier to remove from the local source library.")
    sources_delete.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_delete.set_defaults(handler=run_sources_delete)

    conversations = subcommands.add_parser("conversations", help="Manage persistent local chat sessions.")
    conversations_subcommands = conversations.add_subparsers(dest="conversations_command", required=True)

    conversations_list = conversations_subcommands.add_parser("list", help="List saved conversations.")
    conversations_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_list.set_defaults(handler=run_conversations_list)

    conversations_create = conversations_subcommands.add_parser("create", help="Create a saved conversation.")
    conversations_create.add_argument("--title", default=None, help="Optional conversation title.")
    conversations_create.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_create.set_defaults(handler=run_conversations_create)

    conversations_show = conversations_subcommands.add_parser("show", help="Show one saved conversation.")
    conversations_show.add_argument("conversation_id", help="Conversation identifier.")
    conversations_show.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_show.set_defaults(handler=run_conversations_show)

    conversations_rename = conversations_subcommands.add_parser("rename", help="Rename one saved conversation.")
    conversations_rename.add_argument("conversation_id", help="Conversation identifier.")
    conversations_rename.add_argument("title", nargs="+", help="New conversation title.")
    conversations_rename.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_rename.set_defaults(handler=run_conversations_rename)

    conversations_delete = conversations_subcommands.add_parser("delete", help="Delete one saved conversation.")
    conversations_delete.add_argument("conversation_id", help="Conversation identifier.")
    conversations_delete.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_delete.set_defaults(handler=run_conversations_delete)

    conversations_ask = conversations_subcommands.add_parser("ask", help="Ask inside a saved conversation.")
    conversations_ask.add_argument("conversation_id", help="Conversation identifier.")
    conversations_ask.add_argument("query", nargs="+", help="Question to answer from ingested local sources.")
    conversations_ask.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to use ({MIN_TOP_K}-{MAX_TOP_K}).")
    conversations_ask.add_argument("--strategy", choices=sorted(RETRIEVAL_STRATEGIES), default="hybrid", help="Retrieval strategy to use.")
    conversations_ask.add_argument("--provider", choices=["mock", "anthropic"], help="Model provider override.")
    conversations_ask.add_argument("--model", help="Model name override.")
    conversations_ask.add_argument("--allow-mock-fallback", action="store_true", help="Permit mock fallback when a live provider is unavailable.")
    conversations_ask.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_ask.set_defaults(handler=run_conversations_ask)

    runs = subcommands.add_parser("runs", help="Inspect persisted answer runs.")
    runs_subcommands = runs.add_subparsers(dest="runs_command", required=True)

    runs_list = runs_subcommands.add_parser("list", help="List persisted answer runs.")
    runs_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_list.set_defaults(handler=run_runs_list)

    runs_summary = runs_subcommands.add_parser("summary", help="Summarize persisted answer runs.")
    runs_summary.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_summary.set_defaults(handler=run_runs_summary)

    runs_show = runs_subcommands.add_parser("show", help="Show one persisted answer run.")
    runs_show.add_argument("run_id", help="Run identifier.")
    runs_show.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_show.set_defaults(handler=run_runs_show)

    index = subcommands.add_parser("index", help="Inspect retrieval index health.")
    index_subcommands = index.add_subparsers(dest="index_command", required=True)

    index_status = index_subcommands.add_parser("status", help="Show local persistent index status.")
    index_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    index_status.set_defaults(handler=run_index_status)

    permissions = subcommands.add_parser("permissions", help="Manage user-directory access grants.")
    permissions_subcommands = permissions.add_subparsers(dest="permission_command", required=True)

    permissions_grant = permissions_subcommands.add_parser("grant", help="Grant access to a file's containing folder or a folder.")
    permissions_grant.add_argument("path", help="File or directory path to authorize.")
    permissions_grant.add_argument("--yes", action="store_true", help="Confirm the grant without an interactive prompt.")
    permissions_grant.add_argument("--reason", help="Optional reason recorded in the local permission ledger.")
    permissions_grant.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_grant.set_defaults(handler=run_permissions_grant)

    permissions_list = permissions_subcommands.add_parser("list", help="List directory access grants.")
    permissions_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_list.set_defaults(handler=run_permissions_list)

    permissions_check = permissions_subcommands.add_parser("check", help="Check whether a path is currently accessible.")
    permissions_check.add_argument("path", help="File or directory path to check.")
    permissions_check.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_check.set_defaults(handler=run_permissions_check)

    permissions_revoke = permissions_subcommands.add_parser("revoke", help="Revoke an exact directory access grant.")
    permissions_revoke.add_argument("path", help="File or directory path whose containing grant should be revoked.")
    permissions_revoke.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_revoke.set_defaults(handler=run_permissions_revoke)

    plan = subcommands.add_parser("plan", help="Analyze a request with the Planning Gate.")
    plan.add_argument("request", nargs="+", help="Request to analyze.")
    plan.add_argument("--input", action="append", default=[], help="Available input, repeatable.")
    plan.add_argument("--approval-state", default="NOT_REQUESTED", help="Current approval state.")
    plan.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    plan.set_defaults(handler=run_plan)

    validate = subcommands.add_parser("validate", help="Validate project JSON artifacts.")
    validate.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    validate.set_defaults(handler=run_validate)

    serve = subcommands.add_parser("serve", help="Start the local API server.")
    serve.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    serve.add_argument("--port", type=parse_port, default=8000, help="Port to bind.")
    serve.add_argument("--reload", action="store_true", help="Restart when source files change.")
    serve.set_defaults(handler=run_serve)

    return parser


def emit_payload(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    status = payload.get("status") or payload.get("citation_status") or "OK"
    print(status)
    if "run_id" in payload:
        print(f"run_id={payload.get('run_id')}")
        print(f"citation_status={payload.get('citation_status')}")
        print(f"retrieval_strategy={payload.get('retrieval_strategy')}")
        if payload.get("answer"):
            print(payload.get("answer"))
    elif "answer" in payload:
        print(payload["answer"])
    elif "files_ingested" in payload:
        print(f"files_ingested={payload['files_ingested']}")
    elif "source" in payload:
        source = payload["source"]
        print(f"source_id={source.get('source_id')} chunks_added={payload.get('chunks_added', 0)}")
    elif "answer" in payload and "conversation" in payload:
        answer = payload.get("answer", {})
        conversation = payload.get("conversation", {})
        print(f"conversation_id={conversation.get('conversation_id')}")
        if isinstance(answer, dict):
            print(f"answer_status={answer.get('citation_status') or answer.get('status')}")
            if answer.get("run_id"):
                print(f"run_id={answer.get('run_id')}")
            if answer.get("answer"):
                print(answer.get("answer"))
    elif "conversations" in payload:
        print(f"conversations={len(payload.get('conversations', []))}")
        for conversation in payload.get("conversations", []):
            print(
                f"{conversation.get('conversation_id')} {conversation.get('title')} "
                f"turns={conversation.get('turn_count', 0)} status={conversation.get('last_status')}"
            )
    elif "conversation_id" in payload and "turns" in payload:
        print(f"conversation_id={payload.get('conversation_id')}")
        print(f"title={payload.get('title')}")
        print(f"turns={len(payload.get('turns', []))}")
        for turn in payload.get("turns", []):
            print(f"{turn.get('role')} {turn.get('status')} {turn.get('run_id') or ''}".rstrip())
    elif "runs" in payload:
        print(f"runs={len(payload.get('runs', []))}")
        for run in payload.get("runs", []):
            print(f"{run.get('run_id')} {run.get('citation_status') or run.get('status')} {run.get('retrieval_strategy')}")
    elif payload.get("status") == "RUN_HISTORY_OK":
        print(f"run_count={payload.get('run_count', 0)}")
        print(f"citation_ok={payload.get('citation_ok', 0)}")
        print(f"latest_run_id={payload.get('latest_run_id')}")
    elif "sources" in payload:
        print(f"sources={len(payload.get('sources', []))}")
        index = payload.get("index", {})
        if isinstance(index, dict):
            print(f"index_status={index.get('status')}")
            print(f"indexed_chunk_count={index.get('indexed_chunk_count', 0)}")
        for source in payload.get("sources", []):
            print(f"{source.get('source_id')} {source.get('file_name')} chunks={source.get('chunk_count', 0)}")
    elif "removed_chunks" in payload:
        print(f"source_id={payload.get('source_id')}")
        print(f"removed_chunks={payload.get('removed_chunks', 0)}")
        print(f"remaining_sources={payload.get('remaining_sources', 0)}")
    elif "index_status" in payload:
        print(f"index_status={payload.get('index_status')}")
        print(f"indexed_chunk_count={payload.get('indexed_chunk_count', 0)}")
    elif payload.get("status") in {"INDEX_OK", "INDEX_STALE", "INDEX_MISSING"}:
        print(f"chunk_count={payload.get('chunk_count', 0)}")
        print(f"indexed_chunk_count={payload.get('indexed_chunk_count', 0)}")
        print(f"stale={payload.get('stale')}")
    elif "readiness_percent" in payload:
        print(f"active_vector={payload.get('active_vector')}")
        print(f"recommended_mode={payload.get('recommended_mode')}")
        print(f"readiness_percent={payload.get('readiness_percent')}")
    elif "checked" in payload:
        print(f"checked={len(payload.get('checked', []))}")
    elif "grants" in payload:
        print(f"grants={len(payload.get('grants', []))}")
        for grant in payload.get("grants", []):
            print(grant.get("canonical_root"))
    elif "canonical_root" in payload:
        print(f"canonical_root={payload.get('canonical_root')}")
    elif payload.get("status") in {"CONVERSATION_DELETE_OK", "ERROR_CONVERSATION_NOT_FOUND"}:
        print(f"conversation_id={payload.get('conversation_id')}")
        print(f"removed={payload.get('removed')}")
    elif "path" in payload:
        print(f"path={payload.get('path')}")


def emit_error(status: str, detail: str, output_format: str = "json") -> None:
    payload = {"status": status, "detail": detail}
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
        return
    print(f"{status} {detail}", file=sys.stderr)


def run_ask(args: argparse.Namespace, cfg: Settings) -> int:
    result = answer_question(
        " ".join(args.query),
        top_k=args.top_k,
        retrieval_strategy=args.strategy,
        model_provider=args.provider,
        model_name=args.model,
        allow_mock_fallback=args.allow_mock_fallback,
        cfg=cfg,
        persist=not args.no_persist,
    )
    emit_payload(result, args.format)
    if result.get("status", "").startswith("ERROR_"):
        return 2
    return 0 if result.get("citation_status") == "CITATION_OK" else 3


def run_ingest(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.path)
    if not path.is_absolute():
        path = cfg.project_root / path
    if not path.exists():
        raise MucaroCLIError("ERROR_INPUT_PATH_NOT_FOUND", str(path))
    if not path.is_file() and not path.is_dir():
        raise MucaroCLIError("ERROR_INPUT_PATH_UNSUPPORTED", str(path))
    permission = check_directory_permission(path, cfg)
    if permission["status"] != "PERMISSION_OK":
        if not args.grant_directory_access:
            raise MucaroCLIError(str(permission["status"]), str(permission["detail"]))
        grant = prompt_for_directory_grant(Path(str(permission["canonical_root"])), cfg)
        permission = {"status": "PERMISSION_OK", "scope": "granted_directory", "grant": grant}
    result = ingest_folder(path, cfg) if path.is_dir() else ingest_file(path, cfg)
    result["directory_permission"] = permission
    emit_payload(result, args.format)
    return 0


def run_sources_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "SOURCES_OK", "sources": get_sources(cfg), "index": get_index_status(cfg)}
    emit_payload(result, args.format)
    return 0


def run_sources_reindex(args: argparse.Namespace, cfg: Settings) -> int:
    result = reindex_sources(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "REINDEX_OK" else 2


def run_sources_delete(args: argparse.Namespace, cfg: Settings) -> int:
    result = delete_source(args.source_id, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "SOURCE_DELETE_OK" else 2


def run_conversations_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "CONVERSATIONS_OK", "conversations": list_conversations(cfg)}
    emit_payload(result, args.format)
    return 0


def run_conversations_create(args: argparse.Namespace, cfg: Settings) -> int:
    result = create_conversation(args.title, cfg)
    emit_payload(result, args.format)
    return 0


def run_conversations_show(args: argparse.Namespace, cfg: Settings) -> int:
    conversation = get_conversation(args.conversation_id, cfg)
    if conversation is None:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    emit_payload(conversation, args.format)
    return 0


def run_conversations_rename(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = rename_conversation(args.conversation_id, " ".join(args.title), cfg)
    except KeyError:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    except ValueError as exc:
        raise MucaroCLIError(str(exc), "Conversation title is required.") from exc
    emit_payload(result, args.format)
    return 0


def run_conversations_delete(args: argparse.Namespace, cfg: Settings) -> int:
    result = delete_conversation(args.conversation_id, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "CONVERSATION_DELETE_OK" else 2


def run_conversations_ask(args: argparse.Namespace, cfg: Settings) -> int:
    conversation = get_conversation(args.conversation_id, cfg)
    if conversation is None:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    answer = answer_question(
        " ".join(args.query),
        top_k=args.top_k,
        retrieval_strategy=args.strategy,
        model_provider=args.provider,
        model_name=args.model,
        allow_mock_fallback=args.allow_mock_fallback,
        conversation_context=build_conversation_context(conversation),
        cfg=cfg,
    )
    updated = append_answer_turn(args.conversation_id, " ".join(args.query), answer, cfg)
    emit_payload({"status": "CONVERSATION_ASK_OK", "answer": answer, "conversation": updated}, args.format)
    if answer.get("status", "").startswith("ERROR_"):
        return 2
    return 0 if answer.get("citation_status") == "CITATION_OK" else 3


def run_runs_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "RUNS_OK", "runs": list_payloads("runs", cfg.sqlite_path)}
    emit_payload(result, args.format)
    return 0


def run_runs_summary(args: argparse.Namespace, cfg: Settings) -> int:
    rows = list_payloads("runs", cfg.sqlite_path)
    citation_ok = sum(1 for run in rows if run.get("citation_status") == "CITATION_OK")
    by_strategy: dict[str, int] = {}
    for run in rows:
        strategy = str(run.get("retrieval_strategy") or "unknown")
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
    result = {
        "status": "RUN_HISTORY_OK",
        "run_count": len(rows),
        "citation_ok": citation_ok,
        "citation_error": len(rows) - citation_ok,
        "by_retrieval_strategy": by_strategy,
        "latest_run_id": rows[-1].get("run_id") if rows else None,
    }
    emit_payload(result, args.format)
    return 0


def run_runs_show(args: argparse.Namespace, cfg: Settings) -> int:
    for run in list_payloads("runs", cfg.sqlite_path):
        if run.get("run_id") == args.run_id:
            emit_payload(run, args.format)
            return 0
    emit_payload({"status": "ERROR_RUN_NOT_FOUND", "run_id": args.run_id}, args.format)
    return 2


def run_index_status(args: argparse.Namespace, cfg: Settings) -> int:
    result = get_index_status(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "INDEX_OK" else 2


def prompt_for_directory_grant(root: Path, cfg: Settings) -> dict[str, Any]:
    if not sys.stdin.isatty():
        raise MucaroCLIError(
            "ERROR_PERMISSION_CONFIRMATION_REQUIRED",
            f"Run `mucaro permissions grant {root} --yes` before ingesting this path.",
        )
    print(f"Allow Mucaro AI to access files under: {root}? [y/N] ", end="", file=sys.stderr)
    answer = sys.stdin.readline().strip().lower()
    if answer not in {"y", "yes"}:
        raise MucaroCLIError("ERROR_DIRECTORY_PERMISSION_DENIED", str(root))
    try:
        return grant_directory(root, cfg, granted_by="cli_prompt", reason="ingest --grant-directory-access")
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc


def run_plan(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    result = analyze_task(
        " ".join(args.request),
        inputs_available=args.input,
        approval_state=args.approval_state,
    )
    emit_payload(result, args.format)
    return 0


def run_validate(args: argparse.Namespace, cfg: Settings) -> int:
    result = validate_project_json(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "JSON_OK" else 2


def run_permissions_grant(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        path = Path(args.path).expanduser().resolve()
        if not args.yes:
            if not sys.stdin.isatty():
                raise MucaroCLIError(
                    "ERROR_PERMISSION_CONFIRMATION_REQUIRED",
                    f"Run `mucaro permissions grant {path} --yes` after confirming this directory should be accessible.",
                )
            print(f"Allow Mucaro AI to access files under: {path}? [y/N] ", end="", file=sys.stderr)
            if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
                raise MucaroCLIError("ERROR_DIRECTORY_PERMISSION_DENIED", str(path))
        result = grant_directory(path, cfg, granted_by="cli", reason=args.reason)
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc
    emit_payload(result, args.format)
    return 0


def run_permissions_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "DIRECTORY_PERMISSIONS_OK", "grants": load_directory_grants(cfg)}
    emit_payload(result, args.format)
    return 0


def run_permissions_check(args: argparse.Namespace, cfg: Settings) -> int:
    result = check_directory_permission(args.path, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "PERMISSION_OK" else 2


def run_permissions_revoke(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = revoke_directory(args.path, cfg)
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc
    emit_payload(result, args.format)
    return 0


def run_serve(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    import uvicorn

    uvicorn.run("backend.app.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: Sequence[str] | None = None, cfg: Settings = settings) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args, cfg)
    except MucaroCLIError as exc:
        emit_error(exc.status, exc.detail, getattr(args, "format", "json"))
        return exc.exit_code
    except DirectoryPermissionError as exc:
        emit_error(exc.status, exc.detail, getattr(args, "format", "json"))
        return 2
    except ValueError as exc:
        emit_error("ERROR_INVALID_INPUT", str(exc), getattr(args, "format", "json"))
        return 2
    except OSError as exc:
        emit_error("ERROR_FILESYSTEM", str(exc), getattr(args, "format", "json"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
