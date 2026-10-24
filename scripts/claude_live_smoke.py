#!/usr/bin/env python3
"""One real Claude turn through the chat agent, recorded as a test fixture (the live gate in docs/AGENT_CHAT.md).

Uses the API key saved in Settings → Claude (or ANTHROPIC_API_KEY), runs the turn in a throwaway data
directory so saved conversations are never touched, prints the progress lines as they arrive, and writes the
raw response stream to backend/tests/fixtures/agent_live/, where test_agent.py replays it through the engine.
The key is never printed or written; the fixture holds only what Anthropic sent back. Costs a few cents.

    python scripts/claude_live_smoke.py
    python scripts/claude_live_smoke.py --prompt "..." --name web_search
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Self

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "agent_live"
DEFAULT_PROMPT = "What is the latest stable release of Python? Search the web and cite the page you used."


class _Recording:
    """Wraps one streamed response: passes every line through and keeps a copy."""

    def __init__(self, manager: Any, sink: list[dict[str, Any]]) -> None:
        self.manager = manager
        response = manager.__enter__()
        self.response = response
        self.entry: dict[str, Any] = {"request_id": response.headers.get("request-id"), "status": response.status_code, "lines": []}
        sink.append(self.entry)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        self.manager.__exit__(*exc)
        return False

    def iter_lines(self):
        for line in self.response.iter_lines():
            self.entry["lines"].append(line)
            yield line

    def close(self) -> None:
        self.response.close()


class _RecordingClient:
    """Stands in for client.beta.messages.with_streaming_response, recording each response."""

    def __init__(self, client: Any, sink: list[dict[str, Any]], requests: list[dict[str, Any]]) -> None:
        self.client = client
        self.sink = sink
        self.requests = requests

    @property
    def beta(self) -> _RecordingClient:
        return self

    @property
    def messages(self) -> _RecordingClient:
        return self

    @property
    def with_streaming_response(self) -> _RecordingClient:
        return self

    def create(self, **kwargs: Any) -> Any:
        self.requests.append({"model": kwargs.get("model"), "betas": kwargs.get("betas"), "tools": [tool.get("name") for tool in kwargs.get("tools", [])],
                              "extra_body": kwargs.get("extra_body"), "messages": len(kwargs.get("messages", []))})
        return _Recording(self.client.beta.messages.with_streaming_response.create(**kwargs), self.sink)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--name", default="web_search", help="Fixture name (letters, digits, underscores).")
    parser.add_argument("--data-dir", help="Data directory whose Settings → Claude key to use (default: this checkout's).")
    parser.add_argument("--no-save", action="store_true", help="Run without writing a fixture.")
    args = parser.parse_args()
    if not args.name.replace("_", "").isalnum():
        parser.error("--name may contain only letters, digits and underscores")

    from backend.app import agent, work_runs
    from backend.app.app_settings import get_api_key, load_app_settings, save_app_settings
    from backend.app.config import Settings
    from backend.app.conversations import create_conversation

    def under(cfg: Settings, base: Path, sqlite_name: str) -> Settings:
        return dataclasses.replace(cfg, data_dir=base, input_dir=base / "input", processed_dir=base / "processed",
                                   export_dir=base / "exports", index_dir=base / "indexes", sqlite_path=base / "processed" / sqlite_name)

    user_cfg = Settings.load()
    if args.data_dir:
        user_cfg = under(user_cfg, Path(args.data_dir).expanduser().resolve(), user_cfg.sqlite_path.name)
    key, source = get_api_key(user_cfg)
    if not key:
        print("NO_KEY: add your Anthropic API key in Settings → Claude (or set ANTHROPIC_API_KEY), then run this again.")
        return 2
    chosen = load_app_settings(user_cfg)

    import anthropic

    with tempfile.TemporaryDirectory(prefix="mucaro-live-") as tmp:
        base = Path(tmp)
        cfg = under(user_cfg, base, "live.sqlite3")
        cfg.ensure_dirs()
        # The throwaway run reads the key from the environment, never from the Keychain or a file.
        os.environ.update({"MUCARO_AI_SECRET_STORE": "env", "ANTHROPIC_API_KEY": key})
        save_app_settings({"live_consent": True, "model": chosen["model"], "effort": chosen["effort"],
                           "web_tools": chosen["web_tools"], "code_sandbox": chosen["code_sandbox"]}, cfg)

        recorded: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        real = anthropic.Anthropic(api_key=key, max_retries=2)
        agent.CLIENT_FACTORY = lambda _key: _RecordingClient(real, recorded, requests)

        print(f"Model {chosen['model']} · effort {chosen['effort']} · key from {source}")
        print(f"Prompt: {args.prompt}\n")
        chat = create_conversation("Live smoke", cfg)["conversation_id"]
        run = work_runs.submit_message(chat, args.prompt, mode="agent", cfg=cfg)["run"]
        seen, started = 0, time.monotonic()
        while True:
            for event in work_runs.list_run_events(run["run_id"], after_seq=seen, cfg=cfg):
                seen = event["seq"]
                if event["type"] == "progress":
                    print(f"  … {event['data']['text']}  ({time.monotonic() - started:.1f} s)")
                elif event["type"] in {"warning", "error"}:
                    print(f"  ! {event['type']}: {event['data'].get('status')} {event['data'].get('detail') or ''}")
            state = work_runs.get_run(run["run_id"], cfg)
            if state["state"] in work_runs.TERMINAL_STATES | {"AWAITING_APPROVAL", "AWAITING_INPUT"}:
                break
            time.sleep(0.2)

        events = work_runs.list_run_events(run["run_id"], cfg=cfg)
        final = next((event["data"] for event in reversed(events) if event["type"] == "final"), {})
        usage = next((event["data"] for event in reversed(events) if event["type"] == "usage"), {})
        envelope = work_runs.run_envelope(run["run_id"], cfg)
        answer = next((part["text"] for part in reversed(envelope["parts"]) if part["kind"] == "text"), "")
        print(f"\nState {state['state']} · outcome {state.get('outcome')} · {len(answer)} characters")
        if state.get("error"):
            print(f"Error: {state['error']}")
        print(f"Usage: {json.dumps(usage)}")
        print(f"Request ids: {', '.join(str(row['request_id']) for row in recorded)}")
        print("\n" + answer[:1500])

        ok = state["state"] == "COMPLETED" and final.get("outcome") == "ANSWERED" and bool(answer.strip())
        if ok and not args.no_save:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            fixture = {
                "schema": "mucaro-agent-live-fixture/1",
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "prompt": args.prompt,
                "model": chosen["model"],
                "effort": chosen["effort"],
                "requests": requests,
                "responses": recorded,
                "outcome": final.get("outcome"),
                "progress": [event["data"]["text"] for event in events if event["type"] == "progress"],
            }
            text = json.dumps(fixture, ensure_ascii=False, indent=1)
            if key in text:
                print("REFUSED: the recording contains the API key; nothing was written.")
                return 3
            path = FIXTURES / f"{args.name}.json"
            path.write_text(text + "\n", encoding="utf-8")
            print(f"\nFixture written: {path.relative_to(ROOT)}")
        print("LIVE_SMOKE_OK" if ok else "LIVE_SMOKE_FAILED")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
