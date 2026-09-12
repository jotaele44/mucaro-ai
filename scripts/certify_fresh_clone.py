#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.release_receipts import write_release_receipt  # noqa: E402
from scripts import certify_release as release_certification  # noqa: E402

COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def run(
    cmd: list[str],
    *,
    cwd: Path,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print("RUN", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check, env=env)


def node_first_env(node: str) -> dict[str, str]:
    env = os.environ.copy()
    node_dir = str(Path(node).parent)
    existing_path = env.get("PATH", "")
    env["PATH"] = f"{node_dir}{os.pathsep}{existing_path}" if existing_path else node_dir
    env["NODE"] = node
    env["npm_config_node"] = node
    return env


def run_npm_ci(frontend_dir: Path) -> None:
    cmd = ["npm", "ci", "--registry=https://registry.npmjs.org/"]
    primary = run(cmd, cwd=frontend_dir, check=False)
    if primary.returncode == 0:
        return

    node = release_certification._working_node()
    npm_cli = next(iter(release_certification._npm_cli_candidates()), None)
    if not node or npm_cli is None:
        primary.check_returncode()

    fallback = run(
        [node, str(npm_cli), "ci", "--registry=https://registry.npmjs.org/"],
        cwd=frontend_dir,
        check=False,
        env=node_first_env(node),
    )
    if fallback.returncode != 0:
        fallback.check_returncode()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def origin_url() -> str:
    proc = run(["git", "remote", "get-url", "origin"], cwd=ROOT)
    return proc.stdout.strip()


def git_head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "UNKNOWN_HEAD"


def assert_clean_worktree(repo: Path) -> None:
    proc = run(["git", "status", "--short"], cwd=repo, check=False)
    if proc.stdout.strip():
        raise RuntimeError(f"FRESH_CLONE_DIRTY_WORKTREE\n{proc.stdout}")


def clone_ref(source: str, ref: str, temp_root: Path, clone_dir: Path) -> None:
    if COMMIT_SHA_RE.fullmatch(ref):
        run(["git", "clone", "--no-checkout", source, str(clone_dir)], cwd=temp_root)
        run(["git", "fetch", "--depth", "1", "origin", ref], cwd=clone_dir)
        run(["git", "checkout", "--detach", ref], cwd=clone_dir)
        return
    run(["git", "clone", "--depth", "1", "--branch", ref, source, str(clone_dir)], cwd=temp_root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Mucaro AI from a fresh clone and smoke-test desktop launch.")
    parser.add_argument("--source", default=None, help="Git clone source. Defaults to origin URL.")
    parser.add_argument("--ref", default="main", help="Branch, tag, or commit to checkout after clone.")
    parser.add_argument("--keep", action="store_true", help="Keep temporary clone for debugging.")
    args = parser.parse_args()

    source = args.source or origin_url()
    temp_root = Path(tempfile.mkdtemp(prefix="mucaro-ai-fresh-clone-"))
    clone_dir = temp_root / "mucaro-ai"
    checked_out_sha = args.ref if COMMIT_SHA_RE.fullmatch(args.ref) else git_head(ROOT)
    try:
        clone_ref(source, args.ref, temp_root, clone_dir)
        checked_out_sha = git_head(clone_dir)
        python = sys.executable
        venv = clone_dir / ".venv"
        run([python, "-m", "venv", str(venv)], cwd=clone_dir)
        venv_python = venv / "bin" / "python"
        venv_mucaro = venv / "bin" / "mucaro"
        run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], cwd=clone_dir)
        run([str(venv_python), "-m", "pip", "install", "-e", ".[dev]"], cwd=clone_dir)
        run_npm_ci(clone_dir / "frontend")
        run([str(venv_python), "scripts/release_check.py"], cwd=clone_dir)
        run([str(venv_mucaro), "--help"], cwd=clone_dir)
        run([str(venv_python), "scripts/launch_app.py", "--self-test"], cwd=clone_dir)
        port = free_port()
        run([
            str(venv_python),
            "scripts/launch_app.py",
            "--smoke-test",
            "--no-browser",
            "--port",
            str(port),
            "--startup-timeout",
            "30",
        ], cwd=clone_dir)
        assert_clean_worktree(clone_dir)
        receipt = write_release_receipt(
            ROOT,
            receipt_type="fresh_clone",
            status="FRESH_CLONE_CERT_OK",
            head_sha=checked_out_sha,
            command=[sys.executable, "scripts/certify_fresh_clone.py", "--ref", args.ref],
            payload={
                "source": source,
                "ref": args.ref,
                "clone_dir": str(clone_dir),
                "kept": args.keep,
                "checks": [
                    "clone_ref",
                    "venv",
                    "pip_install",
                    "npm_ci",
                    "release_check",
                    "cli_help",
                    "launcher_self_test",
                    "launcher_smoke_test",
                    "clean_clone_worktree",
                ],
            },
        )
        print(f"FRESH_CLONE_RECEIPT {receipt.relative_to(ROOT)}")
        print(f"FRESH_CLONE_CERT_OK path={clone_dir}")
        return 0
    except Exception as exc:
        receipt = write_release_receipt(
            ROOT,
            receipt_type="fresh_clone",
            status="FRESH_CLONE_CERT_FAILED",
            head_sha=checked_out_sha,
            command=[sys.executable, "scripts/certify_fresh_clone.py", "--ref", args.ref],
            payload={
                "source": source,
                "ref": args.ref,
                "clone_dir": str(clone_dir),
                "kept": args.keep,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        print(f"FRESH_CLONE_RECEIPT {receipt.relative_to(ROOT)}")
        print(f"FRESH_CLONE_CERT_FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"FRESH_CLONE_KEPT {clone_dir}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
