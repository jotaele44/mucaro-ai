#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.release_receipts import write_release_receipt  # noqa: E402
from scripts import certify_release as release_certification  # noqa: E402


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


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object in {path}")
    return payload


def _zip_path_safe(path: str) -> bool:
    parsed = PurePosixPath(path)
    return not parsed.is_absolute() and ".." not in parsed.parts and bool(parsed.parts)


def extract_source_archive(manifest: dict[str, Any], temp_root: Path) -> Path:
    package_name = manifest.get("package_name")
    version = manifest.get("version")
    archive_path = Path(str(manifest.get("archive_path", ""))).expanduser()
    expected_root = f"{package_name}-{version}"
    if not archive_path.is_absolute():
        archive_path = ROOT / archive_path
    if not archive_path.exists():
        raise FileNotFoundError(f"archive not found: {archive_path}")
    with zipfile.ZipFile(archive_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        unsafe = [info.filename for info in infos if not _zip_path_safe(info.filename)]
        if unsafe:
            raise ValueError(f"unsafe archive paths: {','.join(unsafe)}")
        roots = {PurePosixPath(info.filename).parts[0] for info in infos}
        if roots != {expected_root}:
            raise ValueError(f"unexpected archive root: {','.join(sorted(roots)) or 'none'} expected={expected_root}")
        archive.extractall(temp_root)
    source_dir = temp_root / expected_root
    if not source_dir.is_dir():
        raise FileNotFoundError(f"extracted source root missing: {source_dir}")
    return source_dir


def certify_source_dir(source_dir: Path) -> None:
    python = sys.executable
    venv = source_dir / ".venv"
    run([python, "-m", "venv", str(venv)], cwd=source_dir)
    venv_python = venv / "bin" / "python"
    venv_mucaro = venv / "bin" / "mucaro"
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], cwd=source_dir)
    run([str(venv_python), "-m", "pip", "install", "-e", ".[dev]"], cwd=source_dir)
    if (source_dir / "frontend" / "package-lock.json").exists():
        run_npm_ci(source_dir / "frontend")
    run([str(venv_python), "scripts/release_check.py"], cwd=source_dir)
    run([str(venv_mucaro), "--help"], cwd=source_dir)
    run([str(venv_python), "scripts/package_desktop.py", "--self-test"], cwd=source_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify installability from a verified Mucaro AI source artifact.")
    parser.add_argument("manifest", type=Path, help="Artifact manifest path to verify and install from.")
    parser.add_argument("--keep", action="store_true", help="Keep temporary extracted source tree for debugging.")
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser()
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    checked_out_sha = "UNKNOWN_HEAD"
    temp_root = Path(tempfile.mkdtemp(prefix="mucaro-ai-source-artifact-"))
    source_dir = temp_root / "unresolved"
    try:
        verify_proc = run(
            [
                sys.executable,
                "scripts/mucaro.py",
                "package",
                "verify",
                str(manifest_path),
                "--require-current-head",
                "--format",
                "json",
            ],
            cwd=ROOT,
            check=False,
        )
        if verify_proc.returncode != 0:
            raise RuntimeError(verify_proc.stdout.strip() or f"package verify returncode={verify_proc.returncode}")
        verify_payload = json.loads(verify_proc.stdout)
        manifest_summary = verify_payload.get("manifest") if isinstance(verify_payload, dict) else None
        checked_out_sha = str(manifest_summary.get("head_sha")) if isinstance(manifest_summary, dict) else "UNKNOWN_HEAD"
        manifest = _load_manifest(manifest_path)
        source_dir = extract_source_archive(manifest, temp_root)
        certify_source_dir(source_dir)
        receipt = write_release_receipt(
            ROOT,
            receipt_type="source_artifact",
            status="SOURCE_ARTIFACT_CERT_OK",
            head_sha=checked_out_sha,
            command=[sys.executable, "scripts/certify_source_artifact.py", str(args.manifest)],
            payload={
                "manifest_path": str(manifest_path),
                "archive_path": manifest.get("archive_path"),
                "source_dir": str(source_dir),
                "kept": args.keep,
                "checks": [
                    "package_verify_current_head",
                    "extract_source_archive",
                    "venv",
                    "pip_install",
                    "npm_ci",
                    "release_check",
                    "cli_help",
                    "desktop_package_self_test",
                ],
            },
        )
        print(f"SOURCE_ARTIFACT_RECEIPT {receipt.relative_to(ROOT)}")
        print(f"SOURCE_ARTIFACT_CERT_OK path={source_dir}")
        return 0
    except Exception as exc:
        receipt = write_release_receipt(
            ROOT,
            receipt_type="source_artifact",
            status="SOURCE_ARTIFACT_CERT_FAILED",
            head_sha=checked_out_sha,
            command=[sys.executable, "scripts/certify_source_artifact.py", str(args.manifest)],
            payload={
                "manifest_path": str(manifest_path),
                "source_dir": str(source_dir),
                "kept": args.keep,
                "error_type": type(exc).__name__,
                "error": str(exc),
                # The failing step's own output, so a failure can be diagnosed from the receipt alone.
                "error_output_tail": str(getattr(exc, "output", "") or "")[-4000:],
            },
        )
        print(f"SOURCE_ARTIFACT_RECEIPT {receipt.relative_to(ROOT)}")
        print(f"SOURCE_ARTIFACT_CERT_FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"SOURCE_ARTIFACT_KEPT {source_dir}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
