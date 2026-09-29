#!/usr/bin/env python3
"""End-to-end check of the double-clickable ``Mucaro AI.app`` in a checkout (macOS CI).

The CI job first opens the app through LaunchServices (``open "Mucaro AI.app"``, the path a Finder
double-click takes) on a clean runner and waits until the first-run setup has created ``.venv``.
This script then runs with ``.venv/bin/python`` (whose pywebview dependencies provide Quartz for the
window check) and proves, against the app that is already running:

- the Mucaro AI window was shown and the GUI loaded with the launch token (``instance.json``);
- the service runs under the production profile, loopback only;
- the sample documents are loaded, and a representative workflow works
  (ingest -> ask -> cited answer -> export; ``scripts/verify_macos_app.py:workflow``);
- quitting stops the service and removes ``instance.json``;
- a second open is fast (no setup work) and finds the earlier conversation.

Reuses the helpers of ``scripts/verify_macos_app.py``. The launch token is read from the private
instance file and never written to the receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_macos_app import (  # noqa: E402
    VerificationError,
    call,
    persisted,
    read_instance,
    screenshot,
    wait_exit,
    wait_field,
    wait_instance,
    wait_security,
    window_check,
    workflow,
)

SCHEMA_VERSION = "mucaro-checkout-app-e2e/1.0"
SAMPLE_FILES = {"sample_mucaro_ai.txt", "sample_source_lineage.md"}
FAST_PATH_SECONDS = 60.0


def bundle_id(app: Path) -> str:
    return str(plistlib.loads((app / "Contents" / "Info.plist").read_bytes())["CFBundleIdentifier"])


def port_closed(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def open_app(app: Path) -> None:
    subprocess.run(["/usr/bin/open", str(app)], check=True, timeout=60)


def quit_app(app: Path, pid: int, port: int, data_dir: Path) -> dict[str, Any]:
    """Quit the way the Dock's Quit menu does (an Apple Event); SIGTERM only as a fallback."""
    event = subprocess.run(
        ["/usr/bin/osascript", "-e", f'tell application id "{bundle_id(app)}" to quit'],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    result: dict[str, Any] = {"method": "APPLE_EVENT_QUIT"}
    if not wait_exit(pid, 30):
        os.kill(pid, signal.SIGTERM)
        result = {"method": "SIGTERM_AFTER_QUIT_EVENT_FAILED", "quit_event_stderr": event.stderr.strip()[-300:]}
    result["exited"] = wait_exit(pid, 30)
    result["instance_removed"] = not (data_dir / "instance.json").exists()
    result["port_closed"] = port_closed(port)
    result["ok"] = result["exited"] and result["instance_removed"] and result["port_closed"]
    return result


def samples_loaded(base: str, token: str) -> dict[str, Any]:
    status, payload = call(base, "GET", "/sources", token=token)
    names = sorted({str(row.get("file_name")) for row in (payload or {}).get("sources", [])}) if status == 200 else []
    return {"status": status, "file_names": names, "ok": SAMPLE_FILES <= set(names)}


def setup_logs(runtime: Path) -> dict[str, Any]:
    """The markers of the first launch (full setup) and the second (fast path) in the setup logs."""
    logs = sorted((runtime / "logs").glob("setup-*.log"))
    texts = [path.read_text(encoding="utf-8", errors="replace") for path in logs]
    desktop = [text for text in texts if "DESKTOP_APP_MODE" in text]
    first, last = (desktop[0], desktop[-1]) if desktop else ("", "")
    return {
        "desktop_launch_logs": len(desktop),
        "first_setup_ok": "ONE_CLICK_SETUP_OK" in first and "DESKTOP_APP_READY" in first,
        "last_fast_path": "ONE_CLICK_FAST_PATH" in last and "DESKTOP_APP_READY" in last,
        "ok": len(desktop) >= 2 and "ONE_CLICK_SETUP_OK" in first and "ONE_CLICK_FAST_PATH" in last,
    }


def verify(app: Path, data_dir: Path, runtime: Path, output: Path, first_timeout: float) -> dict[str, Any]:
    receipt: dict[str, Any] = {"mode": "MACOS_LAUNCHSERVICES_CHECKOUT", "app": str(app), "data_dir": str(data_dir)}
    instance = wait_instance(data_dir, timeout=first_timeout)
    pid, port, token = instance["pid"], instance["port"], str(instance["token"])
    base = f"http://127.0.0.1:{port}"
    receipt["launch"] = {"pid": pid, "launcher": instance.get("launcher"), "ok": instance.get("launcher") == "MUCARO_AI_APP_CHECKOUT"}
    receipt["security"] = wait_security(data_dir)
    receipt["window_event"] = wait_field(data_dir, "window", "WINDOW_SHOWN")
    # Checked before this script makes any authorized call, so only the window can have made it.
    receipt["gui"] = wait_field(data_dir, "ui", "GUI_CONNECTED")
    receipt["window"] = window_check(pid)
    main_windows = [row for row in receipt["window"].get("windows", []) if row["layer"] == 0]
    receipt["screenshot"] = screenshot(main_windows[0]["number"], output.parent / "window.png") if main_windows else {"ok": False, "status": "NO_WINDOW"}
    receipt["samples"] = samples_loaded(base, token)
    receipt["workflow"] = workflow(base, token)
    receipt["quit"] = quit_app(app, pid, port, data_dir)

    started = time.monotonic()
    open_app(app)
    again = wait_instance(data_dir, not_pid=pid, timeout=120)
    gui = wait_field(data_dir, "ui", "GUI_CONNECTED", timeout=120)
    seconds = round(time.monotonic() - started, 1)
    receipt["reopen"] = {
        "seconds_to_gui": seconds,
        "gui": gui.get("status"),
        "ok": gui.get("status") == "GUI_CONNECTED" and seconds < FAST_PATH_SECONDS,
    }
    again_base = f"http://127.0.0.1:{again['port']}"
    receipt["persisted"] = persisted(again_base, str(again["token"]), receipt["workflow"])
    receipt["quit_again"] = quit_app(app, again["pid"], again["port"], data_dir)
    receipt["setup_logs"] = setup_logs(runtime)
    return receipt


def blocking(receipt: dict[str, Any]) -> list[str]:
    names = ["launch", "security", "window", "screenshot", "samples", "workflow", "quit", "reopen", "persisted", "quit_again", "setup_logs"]
    failures = [name for name in names if not receipt.get(name, {}).get("ok")]
    if receipt.get("window_event", {}).get("status") != "WINDOW_SHOWN":
        failures.append("window_event")
    if receipt.get("gui", {}).get("status") != "GUI_CONNECTED":
        failures.append("gui")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", type=Path, default=ROOT / "Mucaro AI.app")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data", help="The library the app opens (default: data/).")
    parser.add_argument("--runtime", type=Path, default=ROOT / ".mucaro-runtime")
    parser.add_argument("--first-timeout", type=float, default=600.0, help="Seconds to wait for the already opened app.")
    parser.add_argument("--head", default="UNKNOWN_HEAD")
    parser.add_argument("--output", type=Path, required=True, help="Receipt path (JSON).")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    try:
        if sys.platform != "darwin":
            raise VerificationError("Mucaro AI.app verification needs macOS")
        receipt = verify(args.app.resolve(), args.data_dir.resolve(), args.runtime.resolve(), args.output, args.first_timeout)
        failures = blocking(receipt)
    except (VerificationError, subprocess.SubprocessError, OSError, KeyError) as exc:
        receipt, failures = {"error": f"{type(exc).__name__}: {exc}", "instance": bool(read_instance(args.data_dir))}, ["exception"]
    status = "CHECKOUT_APP_E2E_PASS" if not failures else "CHECKOUT_APP_E2E_FAIL"
    receipt.update({"schema_version": SCHEMA_VERSION, "created_utc": started, "head_sha": args.head, "status": status, "blocking_checks": failures})
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "blocking_checks": failures, "receipt": str(args.output)}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
