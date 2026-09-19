#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.coding_checks import CodingCheck, execute_coding_check_suite
from backend.app.config import Settings

STATUS_OK = "CODING_FULL_PROFILE_CERT_OK"
STATUS_FAILED = "CODING_FULL_PROFILE_CERT_FAILED"


def git_output(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


def main() -> int:
    parser=argparse.ArgumentParser(description="Execute the frozen Mucaro coding test/lint/type/build profile in the isolated coding runtime.")
    parser.add_argument("--image", required=True, help="Exact immutable local sha256 image ID or registry digest already present locally.")
    parser.add_argument("--output", type=Path)
    args=parser.parse_args()

    before_head=git_output("rev-parse","HEAD")
    before_status=git_output("status","--porcelain=v1","-z")
    cfg=Settings.load()
    coding_modules = (
        "backend/app/coding_certification.py",
        "backend/app/coding_checks.py",
        "backend/app/coding_container.py",
        "backend/app/coding_lifecycle.py",
        "backend/app/coding_mutation.py",
        "backend/app/coding_patch.py",
        "backend/app/coding_review.py",
        "backend/app/coding_workspace.py",
        "scripts/certify_coding_container_live.py",
        "scripts/certify_coding_full_profile.py",
    )
    checks=[
        CodingCheck("pytest","test",("pytest","-q","backend/tests/test_coding_capability_status.py","backend/tests/test_coding_certification.py","backend/tests/test_coding_checks.py","backend/tests/test_coding_container.py","backend/tests/test_coding_container_live_cert.py","backend/tests/test_coding_full_profile_cert.py","backend/tests/test_coding_lifecycle.py","backend/tests/test_coding_mutation.py","backend/tests/test_coding_patch.py","backend/tests/test_coding_patch_quoted_paths.py","backend/tests/test_coding_review.py","backend/tests/test_coding_tools.py","backend/tests/test_coding_workspace.py"),True),
        CodingCheck("lint","lint",("ruff","check",*coding_modules),True),
        CodingCheck("types","type",("mypy","--ignore-missing-imports",*coding_modules),True),
        CodingCheck("build","build",("python","-m","build"),True),
    ]
    suite=execute_coding_check_suite(ROOT,image=args.image,checks=checks,cfg=cfg)
    after_head=git_output("rev-parse","HEAD")
    after_status=git_output("status","--porcelain=v1","-z")
    invariants={
        "suite_pass":suite.get("execution_status")=="PASS",
        "four_required_rows":suite.get("check_count")==4,
        "all_rows_pass":suite.get("state_counts")=={"PASS":4,"FAIL":0,"BLOCKED":0},
        "head_conserved":before_head==after_head,
        "dirty_state_conserved":before_status==after_status,
        "required_pass_closure":suite.get("required_failure_count")==0 and suite.get("required_blocked_count")==0,
    }
    runtime_identity=hashlib.sha256(
        json.dumps(
            {
                "host_python":sys.version,
                "host_platform":sys.platform,
                "image":args.image,
                "commands":[list(check.command) for check in checks],
            },
            sort_keys=True,
            separators=(",",":"),
        ).encode("utf-8")
    ).hexdigest()
    payload={
        "schema_version":"mucaro-coding-full-profile-cert/1.0",
        "status":STATUS_OK if all(invariants.values()) else STATUS_FAILED,
        "head_sha":before_head,
        "image":args.image,
        "coding_image_digest":args.image.rsplit("@",1)[-1],
        "runtime_identity_sha256":runtime_identity,
        "suite":suite,
        "invariants":invariants,
        "limitations":[
            "This receipt covers the coding-plane test/lint/type/build profile; whole-repository lint/type debt is outside this scoped coding-plane certification and remains separately auditable.",
            "The dependency-complete image is provisioned before execution; all check commands execute with coding-runtime network disabled and host source mounted read-only.",
        ],
    }
    payload["receipt_sha256"]=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(payload["status"])
    print(json.dumps(payload,sort_keys=True))
    return 0 if payload["status"]==STATUS_OK else 2


if __name__=="__main__":
    raise SystemExit(main())
