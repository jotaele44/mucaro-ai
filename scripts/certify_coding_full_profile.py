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
    checks=[
        CodingCheck("pytest","test",("pytest","-q"),True),
        CodingCheck("lint","lint",("ruff","check","."),True),
        CodingCheck("types","type",("mypy","backend"),True),
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
            "This receipt covers the frozen four-command Python coding profile; frontend-specific checks remain governed by the release gate.",
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
