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

STATUS_OK = "CODING_CONTAINER_LIVE_CERT_OK"
STATUS_FAILED = "CODING_CONTAINER_LIVE_CERT_FAILED"


def git_output(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


def main() -> int:
    parser=argparse.ArgumentParser(description="Execute the live isolated coding-container boundary.")
    parser.add_argument("--image", required=True, help="Exact immutable image in name@sha256:<digest> form, already present locally.")
    parser.add_argument("--output", type=Path)
    args=parser.parse_args()

    before_head=git_output("rev-parse","HEAD")
    before_status=git_output("status","--porcelain=v1","-z")
    marker=ROOT/"container-proof.txt"
    if marker.exists():
        marker.unlink()

    cfg=Settings.load()
    checks=[
        CodingCheck("live_python","test",("python","--version"),True),
        CodingCheck(
            "ephemeral_write",
            "build",
            ("python","-c","from pathlib import Path; Path('container-proof.txt').write_text('container-only',encoding='utf-8'); print(Path('container-proof.txt').read_text())"),
            True,
        ),
        CodingCheck(
            "network_denied",
            "security",
            ("python","-c","import socket,sys;\ntry:\n socket.create_connection(('1.1.1.1',53),timeout=1); sys.exit(9)\nexcept OSError:\n print('NETWORK_DENIED'); sys.exit(0)"),
            True,
        ),
    ]
    suite=execute_coding_check_suite(ROOT,image=args.image,checks=checks,cfg=cfg)
    after_head=git_output("rev-parse","HEAD")
    after_status=git_output("status","--porcelain=v1","-z")
    host_marker_absent=not marker.exists()
    identity={
        "source_head_sha":before_head,
        "runtime_identity_sha256":hashlib.sha256(
            json.dumps(
                {
                    "python":sys.version,
                    "platform":sys.platform,
                    "image":args.image,
                    "engine":suite.get("checks",[{}])[0].get("execution_receipt",{}).get("engine"),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        "coding_image_digest":args.image.rsplit("@",1)[1],
    }
    invariants={
        "suite_pass":suite.get("execution_status")=="PASS",
        "head_conserved":before_head==after_head,
        "dirty_state_conserved":before_status==after_status,
        "ephemeral_write_not_on_host":host_marker_absent,
        "all_required_terminal_pass":suite.get("required_failure_count")==0 and suite.get("required_blocked_count")==0,
    }
    payload={
        "schema_version":"mucaro-coding-container-live-cert/1.0",
        "status":STATUS_OK if all(invariants.values()) else STATUS_FAILED,
        "image":args.image,
        "identity":identity,
        "suite":suite,
        "invariants":invariants,
        "limitations":[
            "This probe certifies the isolation/execution substrate with bounded representative commands; it does not by itself certify every repository test/lint/type/build command.",
            "The image must be pulled or otherwise provisioned before execution; the coding runtime itself remains --pull=never and network=none.",
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
