"""V14 local checkout provenance gate for bounded benchmark execution.

Checks actual Git HEAD, tree, tracked blob, filesystem bytes and clean worktree.
This is local integrity evidence; it is NOT external source notarization,
independent process attestation or proof of deployed binary equivalence.
"""
from __future__ import annotations
import hashlib
import re
import subprocess
from pathlib import Path
from .governed_learning_loop import digest

_SHA=re.compile(r"^[a-f0-9]{40}$")
class SourceIntegrityError(RuntimeError):
    pass

def _git(root:Path,*args:str)->bytes:
    try:
        run=subprocess.run(["git","-C",str(root),*args],capture_output=True,
                           timeout=10,check=True)
        return run.stdout
    except (OSError,subprocess.SubprocessError) as exc:
        raise SourceIntegrityError("GIT_SOURCE_IDENTITY_UNAVAILABLE") from exc

def attest_local_source(*,project_root:Path,expected_head:str,
                        source_path:str,expected_sha256:str)->dict:
    if (not isinstance(expected_head,str) or not _SHA.fullmatch(expected_head)
        or not isinstance(expected_sha256,str) or not re.fullmatch(
            r"sha256:[a-f0-9]{64}",expected_sha256)):
        raise SourceIntegrityError("INVALID_EXPECTED_SOURCE_IDENTITY")
    root=Path(project_root).resolve()
    if not isinstance(source_path,str) or not source_path or source_path.startswith("/"):
        raise SourceIntegrityError("INVALID_SOURCE_PATH")
    path=(root/source_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise SourceIntegrityError("SOURCE_PATH_OUTSIDE_OR_MISSING")
    relative=path.relative_to(root).as_posix()
    git_root=_git(root,"rev-parse","--show-toplevel").decode().strip()
    if Path(git_root).resolve()!=root:
        raise SourceIntegrityError("PROJECT_GIT_ROOT_MISMATCH")
    actual_head=_git(root,"rev-parse","HEAD").decode().strip()
    if actual_head!=expected_head:
        raise SourceIntegrityError("SOURCE_HEAD_MISMATCH")
    if _git(root,"status","--porcelain","--untracked-files=all"):
        raise SourceIntegrityError("DIRTY_SOURCE_CHECKOUT")
    tracked=_git(root,"ls-files","--error-unmatch","--",relative)
    if relative not in tracked.decode().splitlines():
        raise SourceIntegrityError("UNTRACKED_SOURCE_MODULE")
    file_bytes=path.read_bytes()
    actual_sha="sha256:"+hashlib.sha256(file_bytes).hexdigest()
    if actual_sha!=expected_sha256:
        raise SourceIntegrityError("SOURCE_FILE_HASH_MISMATCH")
    git_blob=_git(root,"show",f"HEAD:{relative}")
    if git_blob!=file_bytes:
        raise SourceIntegrityError("SOURCE_BYTES_DO_NOT_MATCH_GIT_BLOB")
    tree=_git(root,"rev-parse","HEAD^{tree}").decode().strip()
    if not _SHA.fullmatch(tree):
        raise SourceIntegrityError("INVALID_GIT_TREE")
    evidence={"schema":"mucaro-local-source-integrity/1.0",
              "repository_root":str(root),"head":actual_head,
              "tree":tree,"path":relative,
              "sha256":actual_sha,"size_bytes":len(file_bytes),
              "worktree_clean":True,
              "tracked_blob_equivalent":True,
              "independent_remote_attestation":False}
    return {**evidence,"receipt_hash":digest(evidence)}
