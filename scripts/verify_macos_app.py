#!/usr/bin/env python3
"""End-to-end verification of the packaged Mucaro app (GOAL-001, TGT-001, TGT-017, TGT-026, TGT-029).

macOS (``--zip``): installs the app the way a user does (unzip into an Applications folder),
launches it through LaunchServices (``open``, the path a Finder double-click takes), and proves:
native window shown, GUI running with the launch token, private API refused without it, the
representative workflow (ingest -> ask -> cited answer -> export), quit and relaunch with data
persisted, one instance per data directory, graceful SIGTERM, and reinstall keeping user data.

Any OS (``--executable``): the same API workflow, restart and lock checks against a frozen tree
started headless (``--no-window``); no window or LaunchServices claims are made in this mode.

The launch token is read from the private instance file and never written to the receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_NAME = "Mucaro AI"
BUNDLE_ID = "ai.mucaro.app"
SCHEMA_VERSION = "mucaro-macos-app-e2e/1.0"
FACT = "The Guajataca test reservoir gauge was recalibrated on 14 March 2031 by team Alder."
QUESTION = "When was the Guajataca test reservoir gauge recalibrated?"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class VerificationError(RuntimeError):
    pass


def macos_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def call(base: str, method: str, path: str, *, token: str | None = None, body: Any = None, files: dict[str, tuple[str, bytes, str]] | None = None) -> tuple[int, Any]:
    headers: dict[str, str] = {}
    data: bytes | None = None
    if token:
        headers["X-Mucaro-Token"] = token
    if files:
        boundary = uuid.uuid4().hex
        parts = []
        for field, (name, content, mime) in files.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
                + content
                + b"\r\n"
            )
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with _OPENER.open(request, timeout=30) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw or b"null")
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", errors="replace")


def read_instance(data_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((data_dir / "instance.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def wait_instance(data_dir: Path, *, not_pid: int | None = None, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        instance = read_instance(data_dir)
        if instance and instance.get("pid") != not_pid:
            status, _ = call(f"http://127.0.0.1:{instance['port']}", "GET", "/health")
            if status == 200:
                return instance
        time.sleep(0.25)
    log = data_dir / "logs" / "startup-error.log"
    raise VerificationError(f"app did not start within {timeout}s" + (f": {log.read_text(encoding='utf-8')}" if log.exists() else ""))


def wait_field(data_dir: Path, field: str, status: str, timeout: float = 90.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = (read_instance(data_dir) or {}).get(field) or {}
        if value.get("status") == status:
            return value
        time.sleep(0.25)
    return {"status": "NOT_OBSERVED", "expected": status}


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_exit(pid: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    return False


def workflow(base: str, token: str) -> dict[str, Any]:
    """The representative user workflow through the same API the GUI uses."""
    result: dict[str, Any] = {}
    status, page = call(base, "GET", "/")
    result["gui_served"] = status == 200 and '<div id="root">' in str(page)
    status, _ = call(base, "GET", "/conversations")
    result["private_route_without_token"] = status
    status, ingested = call(base, "POST", "/ingest", token=token, files={"upload": ("reservoir-notes.md", f"# Field notes\n\n{FACT}\n".encode(), "text/markdown")})
    if status != 200:
        raise VerificationError(f"ingest failed: {status} {ingested}")
    source_id = ingested.get("source_id") or (ingested.get("source") or {}).get("source_id")
    result["source_id"] = source_id
    status, conversation = call(base, "POST", "/conversations", token=token, body={"title": "Packaged app check"})
    conversation_id = conversation["conversation_id"]
    result["conversation_id"] = conversation_id
    status, submitted = call(
        base, "POST", f"/conversations/{conversation_id}/messages", token=token,
        body={"prompt": QUESTION, "mode": "ask", "idempotency_key": uuid.uuid4().hex},
    )
    if status != 200:
        raise VerificationError(f"message failed: {status} {submitted}")
    run_id = submitted["run"]["run_id"]
    deadline = time.monotonic() + 60
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        _, payload = call(base, "GET", f"/work-runs/{run_id}/events", token=token)
        run = payload["run"]
        if run["state"] in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            break
        time.sleep(0.25)
    events = payload["events"]
    citations = [event["data"] for event in events if event["type"] == "citation"]
    final: dict[str, Any] = next((event["data"] for event in events if event["type"] == "final"), {})
    answer = "".join(str(event["data"].get("text", "")) for event in events if event["type"] == "text_delta")
    result.update(
        {
            "run_state": run.get("state"),
            "answer_chars": len(answer),
            "citations": len(citations),
            "cites_ingested_source": any(citation.get("source_id") == source_id for citation in citations),
            "citation_status": final.get("citation_status"),
        }
    )
    status, manifest = call(base, "GET", "/offline-manifest", token=token)
    capabilities: dict[str, list[str]] = {}
    for row in (manifest or {}).get("capabilities", []) if status == 200 else []:
        capabilities.setdefault(str(row.get("state_now")), []).append(str(row.get("capability")))
    result["offline_manifest"] = {state: sorted(names) for state, names in sorted(capabilities.items())}
    answer_run_id = final.get("answer_run_id")
    status, exported = call(base, "GET", f"/exports/{answer_run_id}", token=token) if answer_run_id else (0, None)
    result["export_status"] = status
    result["export_has_trace"] = bool(status == 200 and answer_run_id and answer_run_id in str((exported or {}).get("content", "")))
    result["ok"] = (
        result["gui_served"]
        and result["private_route_without_token"] == 401
        and result["run_state"] == "COMPLETED"
        and result["answer_chars"] > 0
        and result["cites_ingested_source"]
        and result["export_has_trace"]
    )
    return result


def persisted(base: str, token: str, first: dict[str, Any]) -> dict[str, Any]:
    conversation_status, conversation = call(base, "GET", f"/conversations/{first['conversation_id']}", token=token)
    source_status, _ = call(base, "GET", f"/sources/{first['source_id']}", token=token)
    turns = len((conversation or {}).get("turns") or []) if conversation_status == 200 else 0
    return {"conversation_status": conversation_status, "turns": turns, "source_status": source_status, "ok": conversation_status == 200 and turns >= 2 and source_status == 200}


def headless(executable: Path, data_dir: Path) -> dict[str, Any]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MUCARO_AI_")}
    command = [str(executable), "--no-window", "--data-dir", str(data_dir)]
    receipt: dict[str, Any] = {"mode": "HEADLESS_FROZEN_TREE", "window_claims": "NONE"}
    first = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        instance = wait_instance(data_dir)
        base = f"http://127.0.0.1:{instance['port']}"
        receipt["workflow"] = workflow(base, instance["token"])
        second = subprocess.run([str(executable), "--smoke", "--data-dir", str(data_dir)], env=env, capture_output=True, text=True, timeout=60, check=False)
        receipt["single_instance"] = {"second_exit": second.returncode, "ok": second.returncode == 3}
    finally:
        first.send_signal(signal.SIGTERM)
        first.wait(timeout=30)
    receipt["sigterm"] = {"exit": first.returncode, "instance_removed": not (data_dir / "instance.json").exists(), "ok": first.returncode == 0 and not (data_dir / "instance.json").exists()}
    relaunch = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        again = wait_instance(data_dir)
        receipt["relaunch"] = {
            "new_port_and_token": again["port"] != instance["port"] or again["token"] != instance["token"],
            **persisted(f"http://127.0.0.1:{again['port']}", again["token"], receipt["workflow"]),
        }
    finally:
        relaunch.send_signal(signal.SIGTERM)
        relaunch.wait(timeout=30)
    return receipt


def window_check(pid: int) -> dict[str, Any]:
    try:
        import Quartz  # type: ignore[import-not-found]  # pyobjc-framework-Quartz (pywebview dependency)
    except ImportError:
        return {"ok": False, "status": "QUARTZ_UNAVAILABLE"}
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    owned: list[dict[str, Any]] = []
    for window in windows:
        if int(window.get("kCGWindowOwnerPID", -1)) != pid:
            continue
        bounds = window.get("kCGWindowBounds") or {}
        owned.append({"number": int(window.get("kCGWindowNumber", 0)), "layer": int(window.get("kCGWindowLayer", 0)), "width": float(bounds.get("Width", 0)), "height": float(bounds.get("Height", 0)), "owner": str(window.get("kCGWindowOwnerName", ""))})
    main = [row for row in owned if row["layer"] == 0 and row["width"] >= 420 and row["height"] >= 400]
    return {"ok": bool(main), "windows": owned}


def screenshot(window_number: int, path: Path) -> dict[str, Any]:
    result = subprocess.run(["screencapture", "-x", "-o", "-l", str(window_number), str(path)], capture_output=True, text=True, timeout=30, check=False)
    ok = result.returncode == 0 and path.exists() and path.stat().st_size > 10_000
    return {"ok": ok, "path": str(path) if ok else None, "bytes": path.stat().st_size if path.exists() else 0, "stderr": result.stderr.strip()[-300:]}


def launch(app: Path, *, new_instance: bool = False) -> None:
    subprocess.run(["open", *(["-n"] if new_instance else []), str(app)], check=True, timeout=60)


def quit_app(pid: int) -> dict[str, Any]:
    quit_event = subprocess.run(["osascript", "-e", f'tell application id "{BUNDLE_ID}" to quit'], capture_output=True, text=True, timeout=30, check=False)
    if wait_exit(pid, 30):
        return {"method": "APPLE_EVENT_QUIT", "ok": True}
    os.kill(pid, signal.SIGTERM)
    return {"method": "SIGTERM_AFTER_QUIT_EVENT_FAILED", "ok": wait_exit(pid, 30), "quit_event_stderr": quit_event.stderr.strip()[-300:]}


def macos(zip_path: Path, install_root: Path, output: Path) -> dict[str, Any]:
    data_dir = macos_data_dir()
    receipt: dict[str, Any] = {"mode": "MACOS_LAUNCHSERVICES", "data_dir": str(data_dir), "data_dir_preexisting": data_dir.exists()}
    install_root.mkdir(parents=True, exist_ok=True)
    app = install_root / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    subprocess.run(["ditto", "-x", "-k", str(zip_path), str(install_root)], check=True)
    receipt["install"] = {"app": str(app), "ok": (app / "Contents" / "Info.plist").exists()}

    started = time.monotonic()
    launch(app)
    instance = wait_instance(data_dir)
    receipt["launch"] = {"seconds_to_service": round(time.monotonic() - started, 3), "pid": instance["pid"]}
    receipt["window_event"] = wait_field(data_dir, "window", "WINDOW_SHOWN")
    # Checked before this script makes any authorized call, so only the window can have made it.
    receipt["gui"] = wait_field(data_dir, "ui", "GUI_CONNECTED")
    receipt["window"] = window_check(instance["pid"])
    main_windows = [row for row in receipt["window"].get("windows", []) if row["layer"] == 0]
    receipt["screenshot"] = screenshot(main_windows[0]["number"], output.parent / "window.png") if main_windows else {"ok": False, "status": "NO_WINDOW"}
    base = f"http://127.0.0.1:{instance['port']}"
    receipt["workflow"] = workflow(base, instance["token"])

    launch(app, new_instance=True)
    time.sleep(8)
    survivors = subprocess.run(["pgrep", "-f", f"{APP_NAME}.app/Contents/MacOS/"], capture_output=True, text=True, check=False).stdout.split()
    current = read_instance(data_dir) or {}
    receipt["single_instance"] = {"processes": len(survivors), "instance_pid_unchanged": current.get("pid") == instance["pid"], "ok": len(survivors) == 1 and current.get("pid") == instance["pid"]}

    receipt["quit"] = quit_app(instance["pid"])
    receipt["quit"]["instance_removed"] = not (data_dir / "instance.json").exists()

    launch(app)
    again = wait_instance(data_dir, not_pid=instance["pid"])
    receipt["relaunch"] = {"new_port_and_token": again["port"] != instance["port"] or again["token"] != instance["token"], **persisted(f"http://127.0.0.1:{again['port']}", again["token"], receipt["workflow"])}
    os.kill(again["pid"], signal.SIGTERM)
    receipt["sigterm"] = {"ok": wait_exit(again["pid"]), "instance_removed": not (data_dir / "instance.json").exists()}
    receipt["sigterm"]["ok"] = receipt["sigterm"]["ok"] and receipt["sigterm"]["instance_removed"]

    shutil.rmtree(app)
    subprocess.run(["ditto", "-x", "-k", str(zip_path), str(install_root)], check=True)
    launch(app)
    reinstalled = wait_instance(data_dir, not_pid=again["pid"])
    receipt["reinstall"] = persisted(f"http://127.0.0.1:{reinstalled['port']}", reinstalled["token"], receipt["workflow"])
    receipt["reinstall"]["note"] = "Cross-version upgrade needs a previous release artifact; schema upgrade is covered by backend/tests/test_db_upgrade.py."
    quit_app(reinstalled["pid"])
    return receipt


def blocking(receipt: dict[str, Any]) -> list[str]:
    names = ["workflow", "single_instance", "relaunch", "sigterm"]
    if receipt["mode"] == "MACOS_LAUNCHSERVICES":
        names += ["install", "window", "quit", "reinstall"]
        failures = [name for name in names if not receipt.get(name, {}).get("ok")]
        if receipt.get("window_event", {}).get("status") != "WINDOW_SHOWN":
            failures.append("window_event")
        if receipt.get("gui", {}).get("status") != "GUI_CONNECTED":
            failures.append("gui")
        return failures
    return [name for name in names if not receipt.get(name, {}).get("ok")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--zip", type=Path, help="macOS: the built app archive (ditto zip).")
    target.add_argument("--executable", type=Path, help="Any OS: a frozen tree's launcher, run headless.")
    parser.add_argument("--install-root", type=Path, default=Path.home() / "Applications")
    parser.add_argument("--data-dir", type=Path, help="Headless mode only (default: a fresh temporary directory).")
    parser.add_argument("--head", default="UNKNOWN_HEAD")
    parser.add_argument("--output", type=Path, required=True, help="Receipt path (JSON).")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    try:
        if args.zip:
            if sys.platform != "darwin":
                raise VerificationError("--zip verification needs macOS")
            receipt = macos(args.zip.resolve(), args.install_root.expanduser(), args.output)
        else:
            data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="mucaro-e2e-"))
            receipt = headless(args.executable.resolve(), data_dir)
        failures = blocking(receipt)
        status = "MACOS_APP_E2E_PASS" if not failures and receipt["mode"] == "MACOS_LAUNCHSERVICES" else "HEADLESS_E2E_PASS" if not failures else "E2E_FAIL"
    except (VerificationError, subprocess.SubprocessError, OSError, KeyError) as exc:
        receipt, failures, status = {"error": f"{type(exc).__name__}: {exc}"}, ["exception"], "E2E_FAIL"
    receipt.update({"schema_version": SCHEMA_VERSION, "created_utc": started, "head_sha": args.head, "status": status, "blocking_checks": failures})
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "blocking_checks": failures, "receipt": str(args.output)}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
