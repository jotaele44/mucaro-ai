from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import backend.app.coding_mutation as mutation
from backend.app.coding_mutation import CodingMutationError, apply_patch_transaction
from backend.app.coding_patch import validate_patch_proposal
from backend.app.config import Settings

PROBE = Path("backend/tests/fixtures/coding_mutation_live_probe.txt")


def _patch(old: str, new: str) -> str:
    path = PROBE.as_posix()
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["apply","preimage-drift","rollback"], required=True)
    args=parser.parse_args()

    cfg=Settings.load()
    repo=cfg.project_root.resolve()
    patch=_patch("alpha","beta")
    proposal=validate_patch_proposal(repo,patch,cfg=cfg)
    head=proposal["head_sha"]
    patch_sha=hashlib.sha256(patch.encode("utf-8")).hexdigest()
    preimages=proposal["preimages"]

    if args.mode=="preimage-drift":
        bad=[dict(row) for row in preimages]
        bad[0]["sha256"]="0"*64
        try:
            apply_patch_transaction(
                repo,patch,
                expected_head_sha=head,
                expected_patch_sha256=patch_sha,
                expected_preimages=bad,
                cfg=cfg,
            )
        except CodingMutationError as exc:
            payload={"mode":args.mode,"status":exc.status,"expected_rejection":exc.status=="ERROR_CODING_MUTATION_PREIMAGE_DRIFT"}
            print(json.dumps(payload,sort_keys=True))
            return 0 if payload["expected_rejection"] else 2
        print(json.dumps({"mode":args.mode,"status":"UNEXPECTED_APPLY","expected_rejection":False},sort_keys=True))
        return 2

    if args.mode=="rollback":
        original_inspect=mutation.inspect_repository
        calls=0

        def inject_unexpected_dirty(*inspect_args, **inspect_kwargs):
            nonlocal calls
            calls += 1
            state=original_inspect(*inspect_args, **inspect_kwargs)
            if calls == 2:
                altered=dict(state)
                altered["dirty_paths"]=sorted(set(state.get("dirty_paths") or []) | {"unexpected.txt"})
                altered["dirty_status"]="DIRTY"
                return altered
            return state

        mutation.inspect_repository=inject_unexpected_dirty
        try:
            apply_patch_transaction(
                repo,patch,
                expected_head_sha=head,
                expected_patch_sha256=patch_sha,
                expected_preimages=preimages,
                cfg=cfg,
            )
        except CodingMutationError as exc:
            payload={
                "mode":args.mode,
                "status":exc.status,
                "expected_rollback":exc.status=="CODING_MUTATION_ROLLED_BACK",
                "postimage":PROBE.read_text(encoding="utf-8"),
                "receipt":exc.receipt,
            }
            print(json.dumps(payload,sort_keys=True))
            return 0 if payload["expected_rollback"] and payload["postimage"]=="alpha\n" else 2
        finally:
            mutation.inspect_repository=original_inspect
        print(json.dumps({"mode":args.mode,"status":"UNEXPECTED_SUCCESS","expected_rollback":False},sort_keys=True))
        return 2

    receipt=apply_patch_transaction(
        repo,patch,
        expected_head_sha=head,
        expected_patch_sha256=patch_sha,
        expected_preimages=preimages,
        cfg=cfg,
    )
    payload={
        "mode":args.mode,
        "status":receipt["status"],
        "head_sha":receipt["head_sha"],
        "patch_sha256":receipt["patch_sha256"],
        "patch_paths":receipt["patch_paths"],
        "newly_dirty_paths":receipt["newly_dirty_paths"],
        "postimage":PROBE.read_text(encoding="utf-8"),
        "invariants":receipt["invariants"],
    }
    print(json.dumps(payload,sort_keys=True))
    return 0 if receipt["status"]=="CODING_MUTATION_APPLIED" and payload["postimage"]=="beta\n" and all(receipt["invariants"].values()) else 2


if __name__=="__main__":
    raise SystemExit(main())
