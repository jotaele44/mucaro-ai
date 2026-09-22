from __future__ import annotations
import hashlib
from pathlib import Path
from backend.app.self_build_controller import decide_after_patch

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def governed_fixture_cycle(path: Path, candidate: bytes, *, before_failures: set[str], after_failures: set[str], targeted_pass: bool, full_pass: bool, history=()) -> dict:
    preimage=path.read_bytes(); pre_sha=sha(path)
    path.write_bytes(candidate)
    decision=decide_after_patch(before_failures,after_failures,targeted_pass=targeted_pass,full_pass=full_pass,history=history)
    if decision != "KEEP":
        path.write_bytes(preimage)
    post_sha=sha(path)
    return {"decision":decision,"preimage_sha256":pre_sha,"postimage_sha256":post_sha,"restored": decision=="KEEP" or post_sha==pre_sha}
