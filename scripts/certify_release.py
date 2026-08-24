#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
    detail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else f"returncode={proc.returncode}"
    return Gate(name, status, detail)


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'backend'}"
    files = tracked_files()
    gates = [
        gate_worktree_clean(),
        gate_no_prior_names(files),
        gate_no_secret_patterns(files),
        gate_no_large_files(files),
        gate_no_generated_tracked(files),
        gate_command("python_tests", [sys.executable, "-m", "pytest", "backend/tests", "-q"]),
        gate_command("retrieval_eval", [sys.executable, "scripts/evaluate_retrieval.py"]),
        gate_command("json_validation", [sys.executable, "scripts/validate_json.py"]),
        gate_command("npm_audit", ["npm", "audit", "--audit-level=moderate"], cwd=ROOT / "frontend"),
        gate_command("frontend_build", ["npm", "run", "build"], cwd=ROOT / "frontend"),
    ]
    failed = [gate for gate in gates if gate.status != "PASS"]
    for gate in gates:
        print(f"{gate.status} {gate.name} {gate.detail}")
    if failed:
        print("CERTIFY_RELEASE_FAILED")
        return 2
    print("CERTIFY_RELEASE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
