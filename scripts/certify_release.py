#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.release_receipts import write_release_receipt  # noqa: E402

ENV_KEY_PATTERN = "ANTHROPIC" + "_API" + "_KEY"
SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY|"
    + ENV_KEY_PATTERN
    + r"\s*=\s*[^#\s]+)"
)
PRIOR_NAME_TOKENS = [
    "custom" + "-llm" + "-lab",
    "Custom " + "LLM Lab",
    "CUSTOM" + "_LLM" + "_LAB",
    "custom" + "_llm" + "_lab",
    "Custom " + "LLM",
    "CUSTOM" + "_LLM",
]
PRIOR_NAME_PATTERN = re.compile("|".join(re.escape(token) for token in PRIOR_NAME_TOKENS))
GENERATED_PATH_PARTS = {
    ".env",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".serena",
}
GENERATED_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".pyc"}


@dataclass
class Gate:
    name: str
    status: str
    detail: str


def run(cmd: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("RUN", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def tracked_files() -> list[Path]:
    proc = run(["git", "ls-files"])
    return [ROOT / line for line in proc.stdout.splitlines() if line.strip()]


def gate_worktree_clean() -> Gate:
    proc = run(["git", "status", "--short"], check=False)
    status = "PASS" if not proc.stdout.strip() else "FAIL"
    return Gate("worktree_clean", status, proc.stdout.strip() or "clean")


def gate_no_prior_names(files: list[Path]) -> Gate:
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if PRIOR_NAME_PATTERN.search(text):
            hits.append(str(path.relative_to(ROOT)))
    return Gate("prior_name_residue", "PASS" if not hits else "FAIL", ",".join(hits) or "none")


def gate_no_secret_patterns(files: list[Path]) -> Gate:
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in SECRET_PATTERN.finditer(text):
            if path.name == ".env.example" and match.group(0).startswith(f"{ENV_KEY_PATTERN}="):
                continue
            hits.append(f"{path.relative_to(ROOT)}:{match.start()}")
    return Gate("secret_pattern_scan", "PASS" if not hits else "FAIL", ",".join(hits) or "none")


def gate_no_large_files(files: list[Path], *, max_bytes: int = 1_000_000) -> Gate:
    hits = [f"{path.relative_to(ROOT)}:{path.stat().st_size}" for path in files if path.stat().st_size > max_bytes]
    return Gate("large_file_scan", "PASS" if not hits else "FAIL", ",".join(hits) or f"<= {max_bytes} bytes")


def gate_no_generated_tracked(files: list[Path]) -> Gate:
    hits = []
    for path in files:
        rel = path.relative_to(ROOT)
        parts = set(rel.parts)
        if parts.intersection(GENERATED_PATH_PARTS) or path.suffix in GENERATED_SUFFIXES:
            hits.append(str(rel))
        if len(rel.parts) >= 3 and rel.parts[:2] == ("data", "input") and rel.name.startswith("src_"):
            hits.append(str(rel))
    return Gate("generated_artifact_scan", "PASS" if not hits else "FAIL", ",".join(sorted(set(hits))) or "none")


def gate_command(name: str, cmd: list[str], *, cwd: Path = ROOT) -> Gate:
    proc = run(cmd, cwd=cwd, check=False)
    status = "PASS" if proc.returncode == 0 else "FAIL"
    lines = proc.stdout.strip().splitlines()
    if not lines:
        detail = f"returncode={proc.returncode}"
    elif status == "PASS":
        detail = lines[-1]
    else:
        detail = " | ".join(lines[-80:])
    return Gate(name, status, detail)


def _node_candidates() -> list[str]:
    candidates = []
    env_node = os.environ.get("MUCARO_NODE")
    if env_node:
        candidates.append(env_node)
    bundled_node = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node"
    candidates.append(str(bundled_node))
    system_node = shutil.which("node")
    if system_node:
        candidates.append(system_node)
    seen = set()
    return [candidate for candidate in candidates if not (candidate in seen or seen.add(candidate))]


def _working_node() -> str | None:
    for candidate in _node_candidates():
        if not Path(candidate).exists():
            continue
        proc = subprocess.run(
            [candidate, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if proc.returncode == 0:
            return candidate
    return None


def _npm_cli_candidates() -> list[Path]:
    env_npm_cli = os.environ.get("MUCARO_NPM_CLI")
    candidates = [Path(env_npm_cli)] if env_npm_cli else []
    candidates.extend(
        [
            Path("/usr/local/lib/node_modules/npm/bin/npm-cli.js"),
            Path("/opt/homebrew/lib/node_modules/npm/bin/npm-cli.js"),
        ]
    )
    return [candidate for candidate in candidates if candidate.exists()]


def gate_npm_audit() -> Gate:
    primary = gate_command("npm_audit", ["npm", "audit", "--audit-level=moderate"], cwd=ROOT / "frontend")
    if primary.status == "PASS":
        return primary
    node = _working_node()
    npm_cli = next(iter(_npm_cli_candidates()), None)
    if not node or npm_cli is None:
        return primary
    proc = run([node, str(npm_cli), "audit", "--audit-level=moderate"], cwd=ROOT / "frontend", check=False)
    if proc.returncode != 0:
        detail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else f"returncode={proc.returncode}"
        return Gate("npm_audit", "FAIL", f"{primary.detail}; fallback_failed={detail}")
    detail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "fallback npm audit passed"
    return Gate("npm_audit", "PASS", f"fallback_node={node} npm_cli={npm_cli} detail={detail}")


def gate_frontend_build() -> Gate:
    primary = gate_command("frontend_build", ["npm", "run", "build"], cwd=ROOT / "frontend")
    if primary.status == "PASS":
        return primary
    node = _working_node()
    vite_bin = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not vite_bin.exists():
        return primary
    proc = run([node, str(vite_bin), "build"], cwd=ROOT / "frontend", check=False)
    if proc.returncode != 0:
        detail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else f"returncode={proc.returncode}"
        return Gate("frontend_build", "FAIL", f"{primary.detail}; fallback_failed={detail}")
    detail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "fallback frontend build passed"
    return Gate("frontend_build", "PASS", f"fallback_node={node} vite={vite_bin.relative_to(ROOT)} detail={detail}")


def gate_package_artifact() -> tuple[Gate, dict | None]:
    proc = run([sys.executable, "scripts/mucaro.py", "package", "build", "--format", "json"], check=False)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    if proc.returncode != 0:
        detail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else f"returncode={proc.returncode}"
        return Gate("package_artifact", "FAIL", detail), payload
    if not isinstance(payload, dict):
        return Gate("package_artifact", "FAIL", "package artifact JSON invalid"), None
    manifest = payload.get("manifest", {})
    member_count = manifest.get("member_count") if isinstance(manifest, dict) else None
    return Gate("package_artifact", "PASS", f"manifest_member_count={member_count}"), payload


def gate_package_artifact_verification(package_artifact: dict | None) -> Gate:
    if not isinstance(package_artifact, dict):
        return Gate("package_artifact_verify", "FAIL", "package artifact payload unavailable")
    manifest = package_artifact.get("manifest")
    manifest_path = manifest.get("path") if isinstance(manifest, dict) else None
    if not isinstance(manifest_path, str) or not manifest_path.strip():
        return Gate("package_artifact_verify", "FAIL", "package artifact manifest path unavailable")
    proc = run(
        [
            sys.executable,
            "scripts/mucaro.py",
            "package",
            "verify",
            manifest_path,
            "--require-current-head",
            "--format",
            "json",
        ],
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    if proc.returncode != 0:
        detail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else f"returncode={proc.returncode}"
        return Gate("package_artifact_verify", "FAIL", detail)
    if not isinstance(payload, dict):
        return Gate("package_artifact_verify", "FAIL", "package artifact verification JSON invalid")
    manifest_summary = payload.get("manifest", {})
    member_count = manifest_summary.get("member_count") if isinstance(manifest_summary, dict) else None
    return Gate("package_artifact_verify", "PASS", f"manifest_member_count={member_count}")


def git_head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "UNKNOWN_HEAD"


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'backend'}"
    head_sha = git_head()
    files = tracked_files()
    gates = [
        gate_worktree_clean(),
        gate_no_prior_names(files),
        gate_no_secret_patterns(files),
        gate_no_large_files(files),
        gate_no_generated_tracked(files),
        gate_command("python_tests", [sys.executable, "-m", "pytest", "backend/tests", "-q"]),
        gate_command("answer_trace_quality", [sys.executable, "scripts/certify_answer_trace_quality.py"]),
        gate_command("operator_workflow", [sys.executable, "scripts/certify_operator_workflow.py"]),
        gate_command("retrieval_eval", [sys.executable, "scripts/evaluate_retrieval.py"]),
        gate_command("retrieval_quality", [sys.executable, "scripts/certify_retrieval_quality.py"]),
        gate_command("gis_quality", [sys.executable, "scripts/certify_gis_quality.py"]),
        gate_command(
            "retrieval_eval_large",
            [
                sys.executable,
                "scripts/evaluate_retrieval.py",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
            ],
        ),
        gate_command("json_validation", [sys.executable, "scripts/validate_json.py"]),
        gate_command("desktop_package_self_test", [sys.executable, "scripts/package_desktop.py", "--self-test"]),
    ]
    package_gate, package_artifact = gate_package_artifact()
    gates.append(package_gate)
    gates.append(gate_package_artifact_verification(package_artifact))
    gates.extend(
        [
            gate_command(
                "python_audit",
                [
                    sys.executable,
                    "-m",
                    "pip_audit",
                    "-r",
                    "backend/requirements.txt",
                    "--progress-spinner",
                    "off",
                ],
            ),
            gate_npm_audit(),
            gate_frontend_build(),
        ]
    )
    failed = [gate for gate in gates if gate.status != "PASS"]
    for gate in gates:
        print(f"{gate.status} {gate.name} {gate.detail}")
    status = "CERTIFY_RELEASE_FAILED" if failed else "CERTIFY_RELEASE_OK"
    receipt = write_release_receipt(
        ROOT,
        receipt_type="local_release",
        status=status,
        head_sha=head_sha,
        command=[sys.executable, "scripts/certify_release.py"],
        payload={
            "gates": [{"name": gate.name, "status": gate.status, "detail": gate.detail} for gate in gates],
            "failed_gates": [gate.name for gate in failed],
            "package_artifact": package_artifact,
        },
    )
    print(f"RELEASE_RECEIPT {receipt.relative_to(ROOT)}")
    if failed:
        print("CERTIFY_RELEASE_FAILED")
        return 2
    print("CERTIFY_RELEASE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
