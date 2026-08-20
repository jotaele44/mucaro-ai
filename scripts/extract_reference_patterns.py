#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFS = [
    ("anthropics/claude-cookbooks", "Claude API examples, prompting, tool-use patterns"),
    ("anthropics/anthropic-retrieval-demo", "Reference retrieval flow and cited context assembly"),
    ("anthropics/claude-quickstarts", "Starter app/deployment structures"),
    ("anthropics/anthropic-sdk-python", "Claude Python SDK integration patterns"),
    ("run-llama/llama_index", "RAG ingestion, indexing, retrieval abstractions"),
    ("chroma-core/chroma", "Vector database abstraction"),
    ("BerriAI/litellm", "Model router abstraction"),
    ("microsoft/graphrag", "Graph/entity retrieval phase 2"),
    ("modelcontextprotocol/servers", "External tool connector phase 2"),
]


def main() -> int:
    arch = ROOT / "_architecture"
    arch.mkdir(exist_ok=True)
    findings = ["# Reference Findings", "", "Network clone status is recorded separately. This file is safe for clean-room architecture planning.", ""]
    matrix_rows = []
    for repo, purpose in REFS:
        local = ROOT / "_references" / repo.split("/")[-1]
        status = "LOCAL_PRESENT" if local.exists() and any(local.iterdir()) else "NOT_CLONED_IN_SANDBOX"
        findings.extend([
            f"## {repo}",
            f"- Claimed purpose: {purpose}",
            f"- Local status: {status}",
            "- Adopt: architecture concepts only; no direct code copying by default.",
            "- Not adopted: repository-specific platform assumptions and deployment defaults.",
            "- Security caution: inspect auth, upload, and tool-execution surfaces before production use.",
            "",
        ])
        matrix_rows.append({
            "repo": repo,
            "pattern": purpose,
            "component": "reference_architecture",
            "adopt_status": "CLEAN_ROOM_REIMPLEMENT",
            "reason": "Use concepts while preserving custom source-lineage schema.",
            "risk_level": "MEDIUM",
            "notes": status,
        })
    (arch / "REFERENCE_FINDINGS.md").write_text("\n".join(findings), encoding="utf-8")
    with (arch / "PATTERN_MATRIX.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(matrix_rows[0].keys()))
        writer.writeheader()
        writer.writerows(matrix_rows)
    (arch / "LICENSE_AUDIT.md").write_text(
        "# License Audit\n\nDefault policy: clean-room implementation only until every referenced file license is reviewed locally.\n",
        encoding="utf-8",
    )
    (arch / "DO_NOT_COPY_LEDGER.md").write_text(
        "# Do Not Copy Ledger\n\nNo external repository code was copied into this snapshot. Network cloning was unavailable in the sandbox.\n",
        encoding="utf-8",
    )
    print("REFERENCE_EXTRACTION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
