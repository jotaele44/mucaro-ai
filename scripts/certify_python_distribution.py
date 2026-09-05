#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from tarfile import TarFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.release_receipts import write_release_receipt  # noqa: E402


def run(cmd: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("RUN", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_output(args: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode == 0, proc.stdout.strip()


def package_version() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in pyproject.splitlines():
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise ValueError("project version not found in pyproject.toml")


def dist_summary(path: Path) -> dict[str, object]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        return {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size, "member_count": len(names)}
    if path.name.endswith(".tar.gz"):
        with TarFile.open(path, "r:gz") as archive:
            names = archive.getnames()
        return {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size, "member_count": len(names)}
    raise ValueError(f"unsupported distribution artifact: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and certify Mucaro AI Python wheel/sdist distribution artifacts.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "exports" / "python_dist",
        help="Directory for generated Python distribution artifacts.",
    )
    parser.add_argument("--keep", action="store_true", help="Keep temporary build/install directories for debugging.")
    args = parser.parse_args()

    head_ok, head_sha = git_output(["rev-parse", "HEAD"])
    if not head_ok:
        print(f"PYTHON_DIST_CERT_FAILED git_head_unavailable {head_sha}", file=sys.stderr)
        return 2
    status_ok, status_output = git_output(["status", "--short"])
    if not status_ok or status_output:
        print(f"PYTHON_DIST_CERT_FAILED dirty_worktree {status_output or 'git status failed'}", file=sys.stderr)
        return 2

    temp_root = Path(tempfile.mkdtemp(prefix="mucaro-ai-python-dist-"))
    build_env = temp_root / "build-env"
    install_env = temp_root / "install-env"
    output_dir = args.output_dir.expanduser()
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    version = package_version()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for old_artifact in output_dir.glob(f"mucaro_ai-{version}*"):
            old_artifact.unlink()
        run([sys.executable, "-m", "venv", str(build_env)], cwd=ROOT)
        build_python = build_env / "bin" / "python"
        run([str(build_python), "-m", "pip", "install", "--upgrade", "pip", "build"], cwd=ROOT)
        run([str(build_python), "-m", "build", "--sdist", "--wheel", "--outdir", str(output_dir)], cwd=ROOT)

        wheel = output_dir / f"mucaro_ai-{version}-py3-none-any.whl"
        sdist = output_dir / f"mucaro_ai-{version}.tar.gz"
        if not wheel.exists() or not sdist.exists():
            raise FileNotFoundError(f"expected wheel/sdist missing: wheel={wheel.exists()} sdist={sdist.exists()}")

        run([sys.executable, "-m", "venv", str(install_env)], cwd=ROOT)
        install_python = install_env / "bin" / "python"
        install_mucaro = install_env / "bin" / "mucaro"
        run([str(install_python), "-m", "pip", "install", "--upgrade", "pip"], cwd=ROOT)
        run([str(install_python), "-m", "pip", "install", str(wheel)], cwd=ROOT)
        run([str(install_mucaro), "--version"], cwd=ROOT)
        run([str(install_mucaro), "--help"], cwd=ROOT)
        run([str(install_python), "-c", "import backend.app.cli; import backend.app.packaging"], cwd=ROOT)

        receipt = write_release_receipt(
            ROOT,
            receipt_type="python_dist",
            status="PYTHON_DIST_CERT_OK",
            head_sha=head_sha,
            command=[sys.executable, "scripts/certify_python_distribution.py"],
            payload={
                "output_dir": str(output_dir),
                "wheel": dist_summary(wheel),
                "sdist": dist_summary(sdist),
                "kept": args.keep,
                "checks": [
                    "clean_worktree",
                    "build_env",
                    "python_build_sdist_wheel",
                    "wheel_exists",
                    "sdist_exists",
                    "install_env",
                    "pip_install_wheel",
                    "mucaro_version",
                    "mucaro_help",
                    "package_imports",
                ],
            },
        )
        print(f"PYTHON_DIST_RECEIPT {receipt.relative_to(ROOT)}")
        print(f"PYTHON_DIST_CERT_OK wheel={wheel} sdist={sdist}")
        return 0
    except Exception as exc:
        receipt = write_release_receipt(
            ROOT,
            receipt_type="python_dist",
            status="PYTHON_DIST_CERT_FAILED",
            head_sha=head_sha,
            command=[sys.executable, "scripts/certify_python_distribution.py"],
            payload={
                "output_dir": str(output_dir),
                "kept": args.keep,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        print(f"PYTHON_DIST_RECEIPT {receipt.relative_to(ROOT)}")
        print(f"PYTHON_DIST_CERT_FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"PYTHON_DIST_KEPT {temp_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
