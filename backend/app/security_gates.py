"""Measurable security gates for the release (INV-016, MATRIX-014, TGT-024, TGT-025).

These checks never claim the absence of unknown vulnerabilities. Each one measures
something specific (a lock is hash-pinned, a service listens only on loopback, a
static finding has a recorded disposition) and reports what it measured.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

_ENV_KEY = "ANTHROPIC" + "_API" + "_KEY"
SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9_]{20,}|"
    r"xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY|"
    + _ENV_KEY
    + r"\s*=\s*[^#\s]+)"
)
# Evidence may name these fields but must never carry their values.
SECRET_FIELD_PATTERN = re.compile(r"(^|_)(token|secret|password|passwd|api_key|apikey|private_key|credential)s?$", re.IGNORECASE)
LOOPBACK_ADDRESSES = {"127.0.0.1", "::1"}


# --- dependency integrity and SBOM ------------------------------------------------------


def parse_hashed_lock(path: Path) -> list[dict[str, Any]]:
    """Components of a pip-compile/uv lock with --generate-hashes."""
    text = path.read_text(encoding="utf-8")
    components: list[dict[str, Any]] = []
    for block in re.split(r"\n(?=[A-Za-z0-9])", text):
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", block.strip())
        if not match:
            continue
        components.append(
            {
                "name": match.group(1).lower(),
                "version": match.group(2),
                "sha256": sorted(set(re.findall(r"--hash=sha256:([0-9a-f]{64})", block))),
            }
        )
    return components


def _bom(name: str, version: str, components: list[dict[str, Any]], *, source: Path, root: Path) -> dict[str, Any]:
    digest = hashlib.sha256(json.dumps(components, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        # Deterministic: the same inputs always yield the same serial number.
        "serialNumber": f"urn:uuid:{uuid.UUID(digest[:32])}",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": name, "version": version},
            "properties": [{"name": "mucaro:source", "value": source.relative_to(root).as_posix()}],
        },
        "components": components,
    }


def python_sbom(lock: Path, *, root: Path, app_version: str) -> dict[str, Any]:
    components = [
        {
            "type": "library",
            "name": row["name"],
            "version": row["version"],
            "purl": f"pkg:pypi/{row['name']}@{row['version']}",
            "scope": "required",
            "hashes": [{"alg": "SHA-256", "content": value} for value in row["sha256"]],
        }
        for row in parse_hashed_lock(lock)
    ]
    return _bom("Mucaro AI (macOS arm64 Python runtime)", app_version, components, source=lock, root=root)


def _npm_runtime_closure(packages: dict[str, Any], roots: list[str]) -> set[str]:
    seen: set[str] = set()
    stack = [f"node_modules/{name}" for name in roots]
    while stack:
        key = stack.pop()
        if key in seen or key not in packages:
            continue
        seen.add(key)
        for dependency in (packages[key].get("dependencies") or {}):
            nested = f"{key}/node_modules/{dependency}"
            stack.append(nested if nested in packages else f"node_modules/{dependency}")
    return seen


def npm_sbom(package_lock: Path, *, root: Path, runtime_roots: list[str], app_version: str) -> dict[str, Any]:
    """Frontend SBOM: packages bundled into the GUI are `required`; build tooling is `excluded`."""
    packages = json.loads(package_lock.read_text(encoding="utf-8"))["packages"]
    runtime = _npm_runtime_closure(packages, runtime_roots)
    components = []
    for key, meta in sorted(packages.items()):
        if not key:
            continue
        name = key.split("node_modules/")[-1]
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": meta.get("version", ""),
            "purl": f"pkg:npm/{name.replace('@', '%40')}@{meta.get('version', '')}",
            "scope": "required" if key in runtime else "excluded",
        }
        integrity = str(meta.get("integrity") or "")
        if integrity.startswith("sha512-"):
            import base64

            component["hashes"] = [{"alg": "SHA-512", "content": base64.b64decode(integrity[7:]).hex()}]
        components.append(component)
    return _bom("Mucaro AI (GUI bundle)", app_version, components, source=package_lock, root=root)


def lock_integrity(lock: Path, package_lock: Path) -> dict[str, Any]:
    python_rows = parse_hashed_lock(lock)
    unhashed_python = [row["name"] for row in python_rows if not row["sha256"]]
    packages = json.loads(package_lock.read_text(encoding="utf-8"))["packages"]
    unhashed_npm = [key for key, meta in packages.items() if key and not str(meta.get("integrity", "")).startswith("sha512-")]
    return {
        "python_components": len(python_rows),
        "python_unhashed": unhashed_python,
        "npm_components": len(packages) - 1,
        "npm_without_sha512_integrity": unhashed_npm,
        "ok": bool(python_rows) and not unhashed_python and not unhashed_npm,
    }


# --- secrets ---------------------------------------------------------------------------


def scan_secret_patterns(paths: list[Path], root: Path) -> list[str]:
    hits = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in SECRET_PATTERN.finditer(text):
            if path.name == ".env.example" and match.group(0).startswith(f"{_ENV_KEY}="):
                continue
            hits.append(f"{path.relative_to(root).as_posix()}:{match.start()}")
    return hits


def scan_evidence_secret_fields(paths: list[Path], root: Path) -> list[str]:
    """Generated evidence (JSON receipts) must not carry values under secret-like keys (TGT-024)."""
    hits: list[str] = []

    def walk(value: Any, where: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if SECRET_FIELD_PATTERN.search(str(key)) and isinstance(child, str) and len(child) >= 12:
                    hits.append(f"{where}:{key}")
                walk(child, where)
        elif isinstance(value, list):
            for child in value:
                walk(child, where)

    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        walk(payload, path.relative_to(root).as_posix())
    return hits


# --- static analysis triage -------------------------------------------------------------


def static_security_findings(root: Path, targets: list[str]) -> list[dict[str, Any]]:
    """Ruff's flake8-bandit (S) rules over shipped code."""
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "S", "--output-format", "json", "--no-cache", *targets],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or f"ruff exited {proc.returncode}")
    findings = []
    for row in json.loads(proc.stdout or "[]"):
        path = Path(row["filename"]).resolve().relative_to(root.resolve()).as_posix()
        line = int(row["location"]["row"])
        source = (root / path).read_text(encoding="utf-8").splitlines()[line - 1].strip()
        findings.append({"rule": row["code"], "path": path, "line": line, "code": source})
    return findings


def triage_findings(findings: list[dict[str, Any]], dispositions: dict[str, Any]) -> dict[str, Any]:
    """Every finding needs a recorded disposition: rule-wide, or per (rule, path, code line)."""
    rule_wide = {row["rule"]: row for row in dispositions.get("rule_dispositions", [])}
    specific = {(row["rule"], row["path"], row["code"]): row for row in dispositions.get("finding_dispositions", [])}
    untriaged, matched_specific = [], set()
    by_classification: dict[str, int] = {}
    for finding in findings:
        key = (finding["rule"], finding["path"], finding["code"])
        record = specific.get(key) or rule_wide.get(finding["rule"])
        if record is None:
            untriaged.append(finding)
            continue
        if key in specific:
            matched_specific.add(key)
        by_classification[record["classification"]] = by_classification.get(record["classification"], 0) + 1
    stale = [f"{rule}:{path}" for (rule, path, code) in specific if (rule, path, code) not in matched_specific]
    blocking = [finding for finding in findings if (specific.get((finding["rule"], finding["path"], finding["code"])) or rule_wide.get(finding["rule"]) or {}).get("classification") in {"CRITICAL", "HIGH"}]
    return {
        "findings": len(findings),
        "by_classification": dict(sorted(by_classification.items())),
        "untriaged": untriaged,
        "stale_dispositions": stale,
        "blocking_high_or_critical": blocking,
        "ok": not untriaged and not blocking,
    }


def shell_true_uses(root: Path, targets: list[str]) -> list[str]:
    hits = []
    for target in targets:
        base = root / target
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"shell\s*=\s*True", line):
                    hits.append(f"{path.relative_to(root).as_posix()}:{number}")
    return hits


# --- runtime exposure -------------------------------------------------------------------


def _linux_listening(pid: int) -> list[str]:
    inodes = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[8:-1])
    addresses = []
    for table, width in (("tcp", 8), ("tcp6", 32)):
        path = Path(f"/proc/{pid}/net/{table}")
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A" or fields[9] not in inodes:  # 0A = LISTEN
                continue
            raw, port = fields[1].split(":")
            if width == 8:
                address = ".".join(str(int(raw[i : i + 2], 16)) for i in (6, 4, 2, 0))
            else:
                words = [raw[i : i + 8] for i in range(0, 32, 8)]
                packed = b"".join(bytes.fromhex(word)[::-1] for word in words)
                import ipaddress

                address = str(ipaddress.IPv6Address(packed))
            addresses.append(f"{address}:{int(port, 16)}")
    return sorted(addresses)


def _darwin_listening(pid: int) -> list[str]:
    proc = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"], capture_output=True, text=True, check=False)
    addresses = []
    for line in proc.stdout.splitlines():
        if line.startswith("n"):
            value = line[1:]
            host, _, port = value.rpartition(":")
            addresses.append(f"{host.strip('[]')}:{port}")
    return sorted(addresses)


def listening_addresses(pid: int) -> list[str]:
    return _darwin_listening(pid) if sys.platform == "darwin" else _linux_listening(pid)


def loopback_only(addresses: list[str]) -> bool:
    return bool(addresses) and all(address.rpartition(":")[0] in LOOPBACK_ADDRESSES for address in addresses)


def xml_amplification_protected() -> dict[str, Any]:
    """ElementTree over expat >= 2.4.1 refuses billion-laughs/quadratic-blowup amplification."""
    import pyexpat

    version = tuple(pyexpat.version_info)
    return {"expat": ".".join(map(str, version)), "ok": version >= (2, 4, 1)}
