from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .config import Settings, settings
from .execution_gate import analyze_task
from .ingestion import ingest_file, ingest_folder
from .retrieval import RETRIEVAL_STRATEGIES
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mucaro",
        description="Command line interface for the local-first Mucaro AI.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    ask = subcommands.add_parser("ask", help="Ask a cited retrieval question.")
    ask.add_argument("query", nargs="+", help="Question to answer from ingested local sources.")
    ask.add_argument("--top-k", type=int, default=5, help="Maximum retrieved chunks to use.")
    ask.add_argument("--strategy", choices=sorted(RETRIEVAL_STRATEGIES), default="hybrid", help="Retrieval strategy to use.")
    ask.add_argument("--provider", choices=["mock", "anthropic"], help="Model provider override.")
    ask.add_argument("--model", help="Model name override.")
    ask.add_argument("--allow-mock-fallback", action="store_true", help="Permit mock fallback when a live provider is unavailable.")
    ask.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    ask.add_argument("--no-persist", action="store_true", help="Do not write the run to SQLite.")
    ask.set_defaults(handler=run_ask)

    ingest = subcommands.add_parser("ingest", help="Ingest a supported file or folder.")
    ingest.add_argument("path", nargs="?", default="data/input", help="File or folder to ingest.")
    ingest.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    ingest.set_defaults(handler=run_ingest)

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
    if "answer" in payload:
        print(payload["answer"])
    elif "files_ingested" in payload:
        print(f"files_ingested={payload['files_ingested']}")
    elif "source" in payload:
        source = payload["source"]
        print(f"source_id={source.get('source_id')} chunks_added={payload.get('chunks_added', 0)}")
    elif "readiness_percent" in payload:
        print(f"active_vector={payload.get('active_vector')}")
        print(f"recommended_mode={payload.get('recommended_mode')}")
        print(f"readiness_percent={payload.get('readiness_percent')}")
    elif "checked" in payload:
        print(f"checked={len(payload.get('checked', []))}")


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
    result = ingest_folder(path, cfg) if path.is_dir() else ingest_file(path, cfg)
    emit_payload(result, args.format)
    return 0


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
    except ValueError as exc:
        emit_error("ERROR_INVALID_INPUT", str(exc), getattr(args, "format", "json"))
        return 2
    except OSError as exc:
        emit_error("ERROR_FILESYSTEM", str(exc), getattr(args, "format", "json"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
