#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.env_file import merge_env_file  # noqa: E402
from scripts import native_window  # noqa: E402

BACKEND_APP = "backend.app.main:app"
WINDOW_PID_FILE = ROOT / ".mucaro-runtime" / "window.pid"
WINDOW_STORAGE_DIR = ROOT / ".mucaro-runtime" / "webview"

PHONE_ACCESS_ENV = "MUCARO_AI_PHONE_ACCESS"
PHONE_BIND_HOST = "0.0.0.0"
MIN_PHONE_TOKEN_CHARS = 32  # the backend's strong-token rule for any non-loopback bind
OVERLAY_NETWORK = ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT range, which Tailscale also uses


def build_env() -> dict[str, str]:
    # Load Mucaro settings from .env, then replace every host-controlled Python setting.
    merged = merge_env_file(os.environ, ROOT / ".env")
    env = {key: value for key, value in merged.items() if not key.startswith("PYTHON")}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "backend")])
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONSAFEPATH"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PhonePlan:
    token: str
    generated: bool
    scheme: str
    env: dict[str, str]  # the backend's environment: the caller's, plus the token and the public-bind opt-in


def plan_phone_access(env: Mapping[str, str], *, tls: bool = False) -> PhonePlan:
    """Phone access needs a token: keep a configured one that is long enough, otherwise mint one for this launch."""
    configured = env.get("MUCARO_AI_OPERATOR_TOKEN", "")
    generated = len(configured) < MIN_PHONE_TOKEN_CHARS
    token = secrets.token_urlsafe(32) if generated else configured
    backend_env = {**env, "MUCARO_AI_OPERATOR_TOKEN": token, "MUCARO_AI_ALLOW_PUBLIC_BIND": "1"}
    return PhonePlan(token=token, generated=generated, scheme="https" if tls else "http", env=backend_env)


def phone_link(scheme: str, host: str, port: int, token: str) -> str:
    # The token rides in the URL fragment: browsers never send it to the server, and the app removes it from the
    # address bar.
    return f"{scheme}://{host}:{port}/#token={quote(token, safe='')}"


def lan_addresses(candidates: Iterable[str]) -> list[str]:
    """Private or overlay-network IPv4 addresses a phone could reach: RFC 1918 and 100.64/10, never loopback."""
    found: list[str] = []
    for text in candidates:
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            continue
        if address.version != 4 or address.is_loopback or address.is_link_local:
            continue
        if (address.is_private or address in OVERLAY_NETWORK) and text not in found:
            found.append(text)
    return found


def discover_lan_addresses() -> list[str]:
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # a UDP "connect" sends nothing; it only picks the outbound interface
            candidates.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return lan_addresses(candidates)


def print_phone_links(plan: PhonePlan, port: int, *, show_token: bool = True) -> list[str]:
    """Print where a phone can reach Mucaro and return the full links (they carry the token).

    With show_token=False, for a launch with no Terminal whose output goes to a log file on disk, the printed lines
    carry no token: the caller hands the full link to the person another way.
    """
    source = "generated for this launch" if plan.generated else "from MUCARO_AI_OPERATOR_TOKEN"
    print(f"PHONE_ACCESS_ENABLED bind={PHONE_BIND_HOST} token={source}")
    addresses = discover_lan_addresses()
    links = [phone_link(plan.scheme, address, port, plan.token) for address in addresses]
    for address, link in zip(addresses, links):
        shown = link if show_token else f"{plan.scheme}://{address}:{port}/ (token withheld from this log)"
        print(f"PHONE_URL {shown}")
    if not addresses:
        print("PHONE_ACCESS_NO_LAN_ADDRESS no private network address was found; join Wi-Fi or a VPN and launch again")
    if plan.scheme == "http":
        print(
            "PHONE_ACCESS_CLEARTEXT_HTTP this link is not encrypted: use it only on a network you trust, "
            "or over a VPN (see docs/PHONE_ACCESS.md)"
        )
    print("PHONE_ACCESS_NOTE the link carries the access token; do not post or share it", flush=True)
    return links


def offer_phone_link(plan: PhonePlan, links: list[str]) -> None:
    """The native app has no Terminal, so say where the phone link is in a dialog; the link is copied on request."""
    if not links:
        native_window.show_phone_link(
            "Phone access is on, but this computer has no private network address (Wi-Fi or VPN). "
            "Join one, then open Mucaro AI again.",
            "",
        )
        return
    lines = [
        "Phone access is on.",
        "",
        "Copy the link, send it to your phone (for example by AirDrop, Messages or email) and open it there. "
        "It works only on this network, and only with the token inside the link.",
    ]
    if plan.scheme == "http":
        lines += [
            "",
            "This connection is not encrypted: use it only on a network you trust, or over a VPN. "
            "See docs/PHONE_ACCESS.md.",
        ]
    native_window.show_phone_link("\n".join(lines), links[0])


def url_for(port: int, scheme: str = "http") -> str:
    return f"{scheme}://127.0.0.1:{port}"


def health_url(port: int, scheme: str = "http") -> str:
    return f"{url_for(port, scheme)}/health"


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def health_ok(port: int, tls: bool = False) -> bool:
    context = None
    if tls:
        # A phone-access certificate is usually self-signed or issued for another name. This only asks the loopback
        # service whether it is up, so the certificate is not verified.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    url = health_url(port, "https" if tls else "http")
    try:
        with urllib.request.urlopen(url, timeout=1.5, context=context) as response:
            body = json.loads(response.read().decode("utf-8"))
            # A generic healthy service is not this checkout's backend. Loopback callers still get project_root.
            return (
                response.status == 200
                and isinstance(body, dict)
                and body.get("status") == "OK"
                and body.get("project_root") == str(ROOT)
            )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def run_checked(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("RUN", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env or build_env(), check=True)


def frontend_source_files(root: Path) -> list[Path]:
    """Every input the frontend build reads. Shared with scripts/one_click.py's build fingerprint."""
    frontend = root / "frontend"
    files = [frontend / name for name in ("index.html", "package.json", "package-lock.json", ".npmrc")]
    files.extend(sorted(frontend.glob("vite.config.*")))
    for folder in ("src", "public"):
        base = frontend / folder
        if base.is_dir():
            files.extend(sorted(path for path in base.rglob("*") if path.is_file()))
    return files


def frontend_dist_is_stale(dist_index: Path) -> bool:
    built_at = dist_index.stat().st_mtime
    return any(path.is_file() and path.stat().st_mtime > built_at for path in frontend_source_files(ROOT))


def ensure_frontend_dist(skip_build: bool = False, verified: bool = False) -> None:
    dist_index = ROOT / "frontend" / "dist" / "index.html"
    if dist_index.exists():
        # verified: the caller (the double-click setup) already fingerprinted the build inputs.
        if verified or not frontend_dist_is_stale(dist_index):
            print(f"OK_FRONTEND_DIST {dist_index}")
            return
        if skip_build or not (ROOT / "frontend" / "node_modules").exists() or shutil.which("npm") is None:
            print("WARN_FRONTEND_DIST_STALE frontend sources changed after the last build; run: cd frontend && npm ci && npm run build")
            print(f"OK_FRONTEND_DIST {dist_index}")
            return
        print("FRONTEND_DIST_STALE frontend sources changed after the last build; rebuilding")
        try:
            run_checked(["npm", "run", "build"], cwd=ROOT / "frontend")
        except (OSError, subprocess.CalledProcessError) as exc:
            # A stale interface is better than no interface: keep serving the previous build.
            print(f"WARN_FRONTEND_REBUILD_FAILED {exc}; serving the previous build")
            print(f"OK_FRONTEND_DIST {dist_index}")
            return
        print(f"FRONTEND_DIST_STALE_REBUILT {dist_index}")
        return
    if skip_build:
        raise SystemExit("ERROR_FRONTEND_DIST_MISSING run: cd frontend && npm ci && npm run build")
    node_modules = ROOT / "frontend" / "node_modules"
    package_json = ROOT / "frontend" / "package.json"
    if not package_json.exists():
        raise SystemExit("ERROR_FRONTEND_PACKAGE_MISSING frontend/package.json")
    if not node_modules.exists():
        raise SystemExit("ERROR_FRONTEND_DEPS_MISSING run: cd frontend && npm ci")
    run_checked(["npm", "run", "build"], cwd=ROOT / "frontend")
    if not dist_index.exists():
        raise SystemExit("ERROR_FRONTEND_BUILD_NO_DIST frontend/dist/index.html")
    print(f"OK_FRONTEND_DIST {dist_index}")


def _healthy(port: int, tls: bool) -> bool:
    # A plain-http check keeps the one-argument call that callers and tests rely on.
    return health_ok(port, tls=True) if tls else health_ok(port)


def wait_for_health(port: int, timeout_seconds: float, tls: bool = False) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _healthy(port, tls):
            return True
        time.sleep(0.25)
    return False


def start_backend(
    port: int,
    *,
    host: str = "127.0.0.1",
    env: dict[str, str] | None = None,
    tls: tuple[str, str] | None = None,
) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-s",
        "-P",
        "-B",
        "-m",
        "uvicorn",
        BACKEND_APP,
        "--host",
        host,
        "--port",
        str(port),
    ]
    if tls:
        cmd += ["--ssl-certfile", tls[0], "--ssl-keyfile", tls[1]]
    print("STARTING_DESKTOP_BACKEND", " ".join(cmd))
    return subprocess.Popen(cmd, cwd=ROOT, env=env if env is not None else build_env())


def stop_backend(proc: subprocess.Popen, port: int) -> bool:
    if proc.poll() is None:
        print("STOPPING_DESKTOP_BACKEND")
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)
    deadline = time.time() + 4
    while time.time() < deadline:
        if not port_open(port):
            print("DESKTOP_BACKEND_PORT_CLOSED")
            return True
        time.sleep(0.2)
    print(f"ERROR_BACKEND_PORT_STILL_OPEN port={port}", file=sys.stderr)
    return False


def find_fallback_port(port: int, span: int = 20, tls: bool = False) -> int | None:
    """Return the first later port that is free or already serves a healthy Mucaro AI backend."""
    for candidate in range(port + 1, port + span + 1):
        if not port_open(candidate) or _healthy(candidate, tls):
            return candidate
    return None


def _raise_keyboard_interrupt(signum, frame) -> None:
    del frame
    if signum == getattr(signal, "SIGHUP", None):
        # The terminal is gone; printing to it would fail before the backend is stopped.
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    raise KeyboardInterrupt


def install_stop_signals() -> dict[int, object]:
    # Closing the Terminal window sends SIGHUP; treat it (and SIGTERM) like Ctrl+C so the backend is stopped.
    previous: dict[int, object] = {}
    for name in ("SIGHUP", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous[sig] = signal.signal(sig, _raise_keyboard_interrupt)
        except (OSError, ValueError):
            continue
    return previous


def restore_signals(previous: dict[int, object]) -> None:
    for sig, handler in previous.items():
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError, TypeError):
            continue


def self_test() -> int:
    checks = {
        "ROOT_PRESENT": ROOT.exists(),
        "BACKEND_MAIN_PRESENT": (ROOT / "backend" / "app" / "main.py").exists(),
        "FRONTEND_PACKAGE_PRESENT": (ROOT / "frontend" / "package.json").exists(),
        "SCRIPTS_PRESENT": (ROOT / "scripts").exists(),
    }
    for name, ok in checks.items():
        print(("OK_" if ok else "ERROR_") + name)
    if not all(checks.values()):
        return 2
    print("LAUNCH_APP_SELF_TEST_OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch Mucaro AI as a desktop-style local app.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--native",
        action="store_true",
        help="Show the app in its own native window instead of the system browser, and report failures in a dialog "
        "(what Mucaro AI.app runs; there is no Terminal to read them in).",
    )
    parser.add_argument("--skip-build", action="store_true", help="Do not build frontend/dist if missing.")
    parser.add_argument(
        "--frontend-verified",
        action="store_true",
        help="The caller already verified frontend/dist is current (the double-click setup fingerprints it); skip the staleness check.",
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help="Start, verify, stop, and exit without opening a long-running session.")
    parser.add_argument("--hold-seconds", type=float, default=0.5, help="Seconds to hold a successful smoke-test backend before stopping.")
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="If the port is used by another program, use the next free port (up to 20 higher) instead of failing.",
    )
    parser.add_argument(
        "--exit-if-running",
        action="store_true",
        help="If Mucaro AI is already running, activate its native window or open the browser in browser mode.",
    )
    parser.add_argument(
        "--phone",
        action="store_true",
        help=(
            "Let a phone on the same private network (or VPN) use Mucaro: bind all interfaces, require an access token "
            f"(generated for this launch unless {MIN_PHONE_TOKEN_CHARS}+ characters are set in "
            "MUCARO_AI_OPERATOR_TOKEN) "
            f"and print the links. Also switched on by {PHONE_ACCESS_ENV}=1 in .env."
        ),
    )
    parser.add_argument(
        "--tls-cert", help="Certificate file (PEM) to serve phone access over https. Needs --tls-key and --phone."
    )
    parser.add_argument("--tls-key", help="Private key file (PEM) for --tls-cert.")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    previous_signals = install_stop_signals()
    try:
        code = _launch(args)
    except KeyboardInterrupt:
        if not args.native:
            raise
        code = 0
    except (Exception, SystemExit) as exc:
        if not args.native:
            raise
        print(f"ERROR_NATIVE_LAUNCH {exc}", file=sys.stderr)
        code = 10
    finally:
        restore_signals(previous_signals)
    if code != 0 and args.native and os.environ.get("MUCARO_SILENT_LAUNCH") != "1":
        native_window.show_dialog(_failure_message(code))
    return code


def _failure_message(code: int) -> str:
    log = os.environ.get("MUCARO_SETUP_LOG") or str(ROOT / ".mucaro-runtime" / "logs")
    if code == 11:
        return (
            "Mucaro AI needs the Microsoft Edge WebView2 Runtime to show its window.\n\n"
            f"Install it from {native_window.WEBVIEW2_DOWNLOAD}, then open Mucaro AI again."
        )
    return (
        f"Mucaro AI could not start (code {code}).\n\n"
        f"Details are in: {log}\n\n"
        "Open Mucaro AI again to retry; setup resumes where it stopped."
    )


def _launch(args: argparse.Namespace) -> int:
    print(f"DESKTOP_LAUNCH_START root={ROOT}")
    env = build_env()
    phone = args.phone or truthy(env.get(PHONE_ACCESS_ENV))
    tls: tuple[str, str] | None = None
    if args.tls_cert or args.tls_key:
        if not (args.tls_cert and args.tls_key):
            print("ERROR_TLS_CERT_AND_KEY_REQUIRED pass both --tls-cert and --tls-key", file=sys.stderr)
            return 2
        if not phone:
            print(
                "ERROR_TLS_REQUIRES_PHONE_ACCESS --tls-cert and --tls-key are for phone access: add --phone",
                file=sys.stderr,
            )
            return 2
        for path in (args.tls_cert, args.tls_key):
            if not Path(path).is_file():
                print(f"ERROR_TLS_FILE_MISSING {path}", file=sys.stderr)
                return 2
        tls = (args.tls_cert, args.tls_key)
    ensure_frontend_dist(skip_build=args.skip_build, verified=args.frontend_verified)

    scheme = "https" if tls else "http"
    port = args.port
    if args.auto_port and port_open(port) and not _healthy(port, bool(tls)):
        fallback = find_fallback_port(port, tls=True) if tls else find_fallback_port(port)
        if fallback is None:
            print(f"ERROR_PORT_IN_USE_NON_MUCARO_AI port={port} no free port up to {port + 20}", file=sys.stderr)
            return 5
        print(f"PORT_FALLBACK from={port} to={fallback}")
        port = fallback

    url = url_for(port, scheme)
    # Phone mode starts the backend itself with a token and on all interfaces. The default launch keeps the
    # original call shapes: loopback only, no token.
    plan = plan_phone_access(env, tls=bool(tls)) if phone else None
    backend_kwargs = {"host": PHONE_BIND_HOST, "env": plan.env, "tls": tls} if plan is not None else {}
    phone_links: list[str] = []
    proc: subprocess.Popen | None = None
    try:
        if port_open(port):
            if _healthy(port, bool(tls)):
                print(f"REUSING_EXISTING_BACKEND {health_url(port, scheme)}")
                if plan is not None:
                    print(
                        "PHONE_ACCESS_UNAVAILABLE a Mucaro backend is already running on this port; "
                        "stop it and launch again to enable phone access"
                    )
                    plan = None  # no links for a backend this launch did not start: its token is unknown
            else:
                print(f"ERROR_PORT_IN_USE_NON_MUCARO_AI port={port}", file=sys.stderr)
                return 5
        else:
            proc = start_backend(port, **backend_kwargs)
            if tls:
                ready = wait_for_health(port, args.startup_timeout, tls=True)
            else:
                ready = wait_for_health(port, args.startup_timeout)
            if not ready:
                if proc.poll() is not None:
                    print(f"ERROR_BACKEND_EXITED_EARLY returncode={proc.returncode}", file=sys.stderr)
                    return proc.returncode or 6
                print(f"ERROR_BACKEND_HEALTH_TIMEOUT url={health_url(port, scheme)}", file=sys.stderr)
                return 7
            print(f"BACKEND_READY {health_url(port, scheme)}")
            if plan is not None:
                phone_links = print_phone_links(plan, port, show_token=not args.native)

        if args.native and not args.smoke_test:
            if proc is None and args.exit_if_running and native_window.activate_existing_window(WINDOW_PID_FILE):
                print(f"DESKTOP_LAUNCH_REUSED {url}")
                return 0
            print(f"OPENING_NATIVE_WINDOW {url}", flush=True)
            window_url = url
            if plan is not None:
                offer_phone_link(plan, phone_links)
                # In phone mode every client needs the token, this computer's own window included.
                window_url = f"{url}/#token={quote(plan.token, safe='')}"
            try:
                native_window.run_window(window_url, storage_dir=WINDOW_STORAGE_DIR, pid_file=WINDOW_PID_FILE)
            except ImportError as exc:
                # Never fall back to a browser: the app is meant to be self-contained.
                print(f"ERROR_NATIVE_WINDOW_UNAVAILABLE {exc}", file=sys.stderr)
                return 10
            except native_window.WebView2Missing as exc:
                # Distinct code: scripts/native_start.ps1 turns it into a dialog with the download link.
                print(f"ERROR_WEBVIEW2_MISSING {exc}", file=sys.stderr)
                return 11
            print("DESKTOP_WINDOW_CLOSED")
            return 0

        if not args.no_browser and not args.native:
            print(f"OPENING_BROWSER {url}")
            # In phone mode every client needs the token, this computer's own window included.
            webbrowser.open(url if plan is None else f"{url}/#token={quote(plan.token, safe='')}")

        if proc is None and args.exit_if_running and not args.smoke_test:
            print(f"DESKTOP_LAUNCH_REUSED {url}")
            print(f"Mucaro AI is already running at {url} (in another window). This window can be closed.")
            return 0

        print("DESKTOP_LAUNCH_OK press Ctrl+C to stop")
        if args.smoke_test:
            time.sleep(max(0.0, args.hold_seconds))
            print("DESKTOP_SMOKE_READY")
            if proc is not None:
                ok = stop_backend(proc, port)
                proc = None
                if not ok:
                    return 9
            print("DESKTOP_SMOKE_OK")
            return 0

        print("")
        print(f"Mucaro AI is running at {url}")
        print("Keep this window open while you use it. To stop, close this window or press Ctrl+C.")
        print("", flush=True)
        try:
            while True:
                if proc is not None and proc.poll() is not None:
                    print(f"ERROR_BACKEND_EXITED returncode={proc.returncode}", file=sys.stderr)
                    return proc.returncode or 8
                time.sleep(1)
        except KeyboardInterrupt:
            print("DESKTOP_STOP_REQUESTED")
            return 0
        finally:
            if proc is not None and proc.poll() is None:
                stop_backend(proc, port)
                proc = None
            print("DESKTOP_STOP_OK")
    finally:
        # Covers Ctrl+C, a closed window, or an error while the backend was still starting.
        if proc is not None and proc.poll() is None:
            stop_backend(proc, port)


if __name__ == "__main__":
    raise SystemExit(main())
