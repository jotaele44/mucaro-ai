#!/usr/bin/env python3
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path
from tarfile import TarFile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.packaging import CLI_ENTRYPOINT, PYTHON_PACKAGE_NAME  # noqa: E402
from backend.app.release_receipts import write_release_receipt  # noqa: E402

REQUIRED_SDIST_MEMBERS = [
    "pyproject.toml",
    "README.md",
    "backend/app/cli.py",
    "backend/app/packaging.py",
]


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


def package_metadata() -> dict[str, str]:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload.get("project")
    if not isinstance(project, dict):
        raise ValueError("project metadata not found in pyproject.toml")
    metadata = {
        "name": project.get("name"),
        "version": project.get("version"),
        "requires_python": project.get("requires-python"),
    }
    missing = [key for key, value in metadata.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        raise ValueError(f"project metadata missing: {','.join(missing)}")
    return {key: str(value) for key, value in metadata.items()}


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


def wheel_metadata(wheel: Path) -> dict[str, object]:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_point_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1:
            raise ValueError(f"expected one wheel METADATA file, found {len(metadata_names)}")
        if len(entry_point_names) != 1:
            raise ValueError(f"expected one wheel entry_points.txt file, found {len(entry_point_names)}")
        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
        entry_points = configparser.ConfigParser()
        entry_points.read_string(archive.read(entry_point_names[0]).decode("utf-8"))
        console_scripts = entry_points["console_scripts"] if entry_points.has_section("console_scripts") else {}
        return {
            "metadata_path": metadata_names[0],
            "entry_points_path": entry_point_names[0],
            "name": metadata.get("Name"),
            "version": metadata.get("Version"),
            "requires_python": metadata.get("Requires-Python"),
            "mucaro_entrypoint": console_scripts.get("mucaro"),
            "has_backend_cli": "backend/app/cli.py" in names,
        }


def verify_wheel_metadata(wheel: Path, expected: dict[str, str]) -> dict[str, object]:
    metadata = wheel_metadata(wheel)
    errors = []
    if metadata["name"] != expected["name"]:
        errors.append(f"name={metadata['name']} expected={expected['name']}")
    if metadata["version"] != expected["version"]:
        errors.append(f"version={metadata['version']} expected={expected['version']}")
    if metadata["requires_python"] != expected["requires_python"]:
        errors.append(f"requires_python={metadata['requires_python']} expected={expected['requires_python']}")
    if metadata["mucaro_entrypoint"] != CLI_ENTRYPOINT:
        errors.append(f"mucaro_entrypoint={metadata['mucaro_entrypoint']} expected={CLI_ENTRYPOINT}")
    if metadata["has_backend_cli"] is not True:
        errors.append("backend/app/cli.py missing from wheel")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "metadata": metadata}


def verify_sdist_members(sdist: Path, expected: dict[str, str]) -> dict[str, object]:
    expected_root = f"{expected['name'].replace('-', '_')}-{expected['version']}"
    with TarFile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        members = set(names)
        pkg_info_name = f"{expected_root}/PKG-INFO"
        pkg_info = archive.extractfile(pkg_info_name)
        if pkg_info is None:
            raise ValueError(f"{pkg_info_name} missing from sdist")
        metadata = Parser().parsestr(pkg_info.read().decode("utf-8"))
    roots = {name.split("/", 1)[0] for name in names if name}
    missing = sorted(path for path in REQUIRED_SDIST_MEMBERS if f"{expected_root}/{path}" not in members)
    errors = []
    if roots != {expected_root}:
        errors.append(f"root={','.join(sorted(roots)) or 'none'} expected={expected_root}")
    if metadata.get("Name") != expected["name"]:
        errors.append(f"name={metadata.get('Name')} expected={expected['name']}")
    if metadata.get("Version") != expected["version"]:
        errors.append(f"version={metadata.get('Version')} expected={expected['version']}")
    if missing:
        errors.append(f"missing={','.join(missing)}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "root": expected_root,
        "required_members": REQUIRED_SDIST_MEMBERS,
        "missing_members": missing,
        "metadata": {
            "name": metadata.get("Name"),
            "version": metadata.get("Version"),
        },
    }


def write_python_dist_manifest(
    output_dir: Path,
    *,
    head_sha: str,
    expected: dict[str, str],
    wheel: Path,
    sdist: Path,
    wheel_verification: dict[str, object],
    sdist_verification: dict[str, object],
) -> tuple[Path, dict[str, object]]:
    manifest = {
        "status": "PYTHON_DIST_ARTIFACTS_VERIFIED",
        "artifact_format": "python_wheel_sdist_manifest_v1",
        "package_name": expected["name"],
        "version": expected["version"],
        "head_sha": head_sha,
        "wheel": dist_summary(wheel),
        "sdist": dist_summary(sdist),
        "wheel_verification": wheel_verification,
        "sdist_verification": sdist_verification,
    }
    manifest_path = output_dir / f"{expected['name'].replace('-', '_')}-{expected['version']}-{head_sha[:12]}-python-dist-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path, manifest


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
    expected = package_metadata()
    if expected["name"] != PYTHON_PACKAGE_NAME:
        print(f"PYTHON_DIST_CERT_FAILED package_name={expected['name']} expected={PYTHON_PACKAGE_NAME}", file=sys.stderr)
        return 2
    version = expected["version"]
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
        wheel_verification = verify_wheel_metadata(wheel, expected)
        sdist_verification = verify_sdist_members(sdist, expected)
        failed_artifact_checks = [
            name
            for name, result in [("wheel_metadata", wheel_verification), ("sdist_members", sdist_verification)]
            if result.get("status") != "PASS"
        ]
        if failed_artifact_checks:
            raise ValueError(f"distribution artifact verification failed: {','.join(failed_artifact_checks)}")
        manifest_path, manifest = write_python_dist_manifest(
            output_dir,
            head_sha=head_sha,
            expected=expected,
            wheel=wheel,
            sdist=sdist,
            wheel_verification=wheel_verification,
            sdist_verification=sdist_verification,
        )

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
                "manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
                "wheel": manifest["wheel"],
                "sdist": manifest["sdist"],
                "wheel_verification": wheel_verification,
                "sdist_verification": sdist_verification,
                "kept": args.keep,
                "checks": [
                    "clean_worktree",
                    "build_env",
                    "python_build_sdist_wheel",
                    "wheel_exists",
                    "sdist_exists",
                    "wheel_metadata",
                    "sdist_members",
                    "python_dist_manifest",
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
