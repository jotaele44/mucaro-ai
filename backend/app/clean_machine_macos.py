from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .portable_install import install_portable_macos_app, rollback_portable_macos_app
from .portable_runtime import PortableRuntimeError, verify_portable_macos_app

EXTERNAL_EVIDENCE_SCHEMA = "mucaro-external-release-evidence/1.0"
Runner = Callable[[list[str], Path, dict[str, str]], subprocess.CompletedProcess[str]]


class CleanMachineMacOSError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _default_runner(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=180,
    )


def _manifest(app: Path) -> dict[str, Any]:
    path = app / "Contents/Resources/portable-manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanMachineMacOSError("ERROR_CLEAN_MACHINE_MANIFEST", str(path)) from exc
    if not isinstance(payload, dict) or not payload.get("head_sha"):
        raise CleanMachineMacOSError("ERROR_CLEAN_MACHINE_MANIFEST", str(path))
    return payload


def _run(command: list[str], *, cwd: Path, env: dict[str, str], runner: Runner) -> dict[str, Any]:
    try:
        proc = runner(command, cwd, env)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "FAIL", "returncode": None, "command": command, "output": str(exc)[:65536]}
    return {
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "command": command,
        "output": str(proc.stdout or "")[:65536],
    }


def certify_clean_machine_macos(
    current_app: str | Path,
    previous_app: str | Path,
    *,
    expected_head: str,
    install_root: str | Path,
    output_path: str | Path | None = None,
    port: int = 8765,
    platform_name: str = sys.platform,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    if platform_name != "darwin":
        raise CleanMachineMacOSError("ERROR_CLEAN_MACHINE_PLATFORM", "clean-machine certification must execute on macOS")
    current = Path(current_app).expanduser().resolve()
    previous = Path(previous_app).expanduser().resolve()
    root = Path(install_root).expanduser().resolve()
    if not isinstance(expected_head, str) or len(expected_head) != 40:
        raise CleanMachineMacOSError("ERROR_CLEAN_MACHINE_HEAD", "expected_head must be a full 40-character Git SHA")

    try:
        current_verify = verify_portable_macos_app(current, expected_head=expected_head)
        previous_verify = verify_portable_macos_app(previous)
    except PortableRuntimeError as exc:
        raise CleanMachineMacOSError(exc.status, exc.detail) from exc
    if current_verify.get("status") != "PORTABLE_APP_VERIFIED" or previous_verify.get("status") != "PORTABLE_APP_VERIFIED":
        raise CleanMachineMacOSError("ERROR_CLEAN_MACHINE_PORTABLE_VERIFY", "portable source bundle verification failed")

    current_manifest = _manifest(current)
    previous_manifest = _manifest(previous)
    current_version = current_manifest.get("release_version")
    previous_version = previous_manifest.get("release_version")
    if not isinstance(current_version, str) or not isinstance(previous_version, str) or not current_version or not previous_version:
        raise CleanMachineMacOSError("ERROR_CLEAN_MACHINE_VERSION_MISSING", "both portable apps need a bound release version")
    if current_version == previous_version:
        raise CleanMachineMacOSError("ERROR_CLEAN_MACHINE_SAME_VERSION", "upgrade and rollback require distinct release versions")
    previous_head = str(previous_manifest.get("head_sha"))
    if previous_head == expected_head:
        raise CleanMachineMacOSError("ERROR_CLEAN_MACHINE_PREVIOUS_HEAD", "previous app must bind to a distinct Git head")

    root.mkdir(parents=True, exist_ok=True)
    previous_install = install_portable_macos_app(previous, install_root=root)
    current_install = install_portable_macos_app(current, install_root=root)
    installed = Path(current_install["installation"]["app_path"])
    launcher = installed / "Contents/MacOS/mucaro-ai"
    app_root = installed / "Contents/Resources/app"
    embedded_python = installed / "Contents/Resources/runtime/bin/python3"
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    env["PYTHONPATH"] = os.pathsep.join([str(app_root), str(app_root / "backend")])
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONSAFEPATH"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MUCARO_AI_DATA_DIR"] = str(root / "user-data")

    launch = _run(
        [str(launcher), "--smoke-test", "--no-browser", "--skip-build", "--port", str(port)],
        cwd=app_root,
        env=env,
        runner=runner,
    )
    workflow = _run(
        [str(embedded_python), "-s", "-P", "-B", str(app_root / "scripts/certify_operator_workflow.py")],
        cwd=app_root,
        env=env,
        runner=runner,
    )
    try:
        post_launch_verify = verify_portable_macos_app(installed, expected_head=expected_head)
    except PortableRuntimeError as exc:
        post_launch_verify = {
            "status": "PORTABLE_APP_VERIFY_FAILED",
            "failed_checks": [exc.status],
            "detail": exc.detail,
        }
    rollback = rollback_portable_macos_app(install_root=root)
    rollback_head = str(rollback.get("installation", {}).get("head_sha") or "")
    reinstall = install_portable_macos_app(current, install_root=root)
    reinstalled_head = str(reinstall.get("installation", {}).get("head_sha") or "")

    evidence = {
        "install": current_install.get("status") == "PORTABLE_INSTALL_OK",
        "launch": launch["status"] == "PASS",
        "representative_workflow": workflow["status"] == "PASS",
        "post_launch_bundle_immutable": post_launch_verify.get("status") == "PORTABLE_APP_VERIFIED",
        "reinstall": reinstall.get("status") == "PORTABLE_INSTALL_OK" and reinstalled_head == expected_head,
        "rollback": rollback.get("status") == "PORTABLE_ROLLBACK_OK" and rollback_head == previous_head,
    }
    receipt: dict[str, Any] = {
        "schema_version": EXTERNAL_EVIDENCE_SCHEMA,
        "head_sha": expected_head,
        "evidence": evidence,
        "clean_machine": {
            "install_root": str(root),
            "previous_head": previous_head,
            "previous_version": previous_version,
            "current_version": current_version,
            "current_head": expected_head,
            "previous_install": previous_install,
            "current_install": current_install,
            "launch": launch,
            "representative_workflow": workflow,
            "post_launch_bundle_verification": post_launch_verify,
            "rollback": rollback,
            "reinstall": reinstall,
        },
        "basis": "distinct-version install/launch/workflow/bundle-integrity/rollback/reinstall executed with the embedded portable runtime on macOS",
        "limitations": [
            "The operator must run this on a genuinely clean macOS machine for the receipt to qualify as clean-machine evidence.",
            "This function cannot independently prove the machine was clean before execution; release procedure must preserve host identity and setup evidence.",
            "Signing/notarization is a separate external gate.",
        ],
    }
    receipt["status"] = "CLEAN_MACHINE_MACOS_VERIFIED" if all(evidence.values()) else "CLEAN_MACHINE_MACOS_BLOCKED"
    if output_path is not None:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipt["output_path"] = str(target)
    return receipt
