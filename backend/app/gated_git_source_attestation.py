"""Main-native, permission-gated read-only Git source attestation.

Unlike the historical V14 branch this uses main's permission_engine and
does not bypass git.read, distribution-channel or approval rules. A supplied
operator origin alone is not independently authenticated; the caller remains
responsible for binding that origin to an actual operator session.
"""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
from pathlib import Path

from .config import Settings
from .permission_engine import PermissionRequest, require, PermissionEngineError

_SHA40=re.compile(r"^[0-9a-f]{40}$")
_SHA256=re.compile(r"^sha256:[0-9a-f]{64}$")

class SourceAttestationError(RuntimeError):
    pass

def _git(root:Path, arguments:tuple[str,...], *,cfg:Settings,
         request:PermissionRequest)->bytes:
    # Authorization is required on EVERY subprocess invocation.
    # Only these fixed read-only Git forms are accepted.
    allowed=(
        ("rev-parse","--show-toplevel"),("rev-parse","HEAD"),
        ("rev-parse","HEAD^{tree}"),
        ("status","--porcelain","--untracked-files=all"),
    )
    safe=(arguments in allowed or
          (len(arguments)==4 and arguments[:3]==(
              "ls-files","--error-unmatch","--")) or
          (len(arguments)==2 and arguments[0]=="show" and
           arguments[1].startswith("HEAD:") and
           "\x00" not in arguments[1]))
    if not safe:
        raise SourceAttestationError("GIT_ARGUMENTS_OUTSIDE_READ_ONLY_ALLOWLIST")
    try:
        require(request,cfg=cfg)
    except PermissionEngineError as exc:
        raise SourceAttestationError("GIT_READ_PERMISSION_DENIED") from exc
    try:
        proc=subprocess.run(["git","-C",str(root),*arguments],
            check=True,capture_output=True,timeout=10)
        return proc.stdout
    except (OSError,subprocess.SubprocessError) as exc:
        raise SourceAttestationError("GIT_PROVENANCE_UNAVAILABLE") from exc

def attest_source(*,root:Path,source_path:str,expected_head:str,
                  expected_sha256:str,cfg:Settings,
                  request:PermissionRequest)->dict:
    if (not isinstance(expected_head,str) or not _SHA40.fullmatch(expected_head)
        or not isinstance(expected_sha256,str) or
        not _SHA256.fullmatch(expected_sha256)):
        raise SourceAttestationError("INVALID_SOURCE_IDENTITY")
    if (not isinstance(source_path,str) or not source_path or
        source_path.startswith("/")):
        raise SourceAttestationError("INVALID_SOURCE_PATH")
    if request.capability!="git.read" or not request.subject.strip():
        raise SourceAttestationError("GIT_READ_CAPABILITY_REQUIRED")
    if request.origin!="operator":
        raise SourceAttestationError("GIT_READ_PERMISSION_DENIED")
    root=Path(root).resolve()
    path=(root/source_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise SourceAttestationError("SOURCE_OUTSIDE_OR_MISSING")
    relative=path.relative_to(root).as_posix()
    go=lambda *args:_git(root,args,cfg=cfg,request=request)
    returned_root=go("rev-parse","--show-toplevel").decode().strip()
    if Path(returned_root).resolve()!=root:
        raise SourceAttestationError("GIT_ROOT_MISMATCH")
    actual_head=go("rev-parse","HEAD").decode().strip()
    if actual_head!=expected_head:
        raise SourceAttestationError("HEAD_MISMATCH")
    if go("status","--porcelain","--untracked-files=all"):
        raise SourceAttestationError("WORKTREE_NOT_CLEAN")
    tracked=go("ls-files","--error-unmatch","--",relative).decode().splitlines()
    if relative not in tracked:
        raise SourceAttestationError("SOURCE_NOT_TRACKED")
    raw=path.read_bytes()
    actual_digest="sha256:"+hashlib.sha256(raw).hexdigest()
    if actual_digest!=expected_sha256:
        raise SourceAttestationError("SOURCE_BYTES_HASH_MISMATCH")
    if go("show","HEAD:"+relative)!=raw:
        raise SourceAttestationError("GIT_BLOB_BYTES_MISMATCH")
    tree=go("rev-parse","HEAD^{tree}").decode().strip()
    if not _SHA40.fullmatch(tree):
        raise SourceAttestationError("INVALID_TREE_ID")
    evidence={"schema":"mucaro-gated-local-source/1.0",
        "head":actual_head,"tree":tree,"relative_path":relative,
        "file_sha256":actual_digest,"file_length":len(raw),
        "permission_capability":"git.read",
        "distribution_channel":cfg.distribution_channel,
        "clean":True,"tracked_blob_equivalent":True,
        "external_source_attestation":False,
        "operator_authentication_attested":False}
    canonical=json.dumps(evidence,sort_keys=True,separators=(",",":")).encode()
    return {**evidence,"receipt_sha256":"sha256:"+hashlib.sha256(canonical).hexdigest()}
