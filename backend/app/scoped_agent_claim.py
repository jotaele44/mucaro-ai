"""V6 signed execution-context claims; V5 remains immutable on its own branch.

This contract binds the issuer/verifier to an exact declared workspace, source
revision, accountable principal and per-claim budget ceiling. It does not prove
that a user-supplied source revision is authentic; the supervisor must establish
a trustworthy current source identity before constructing either endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import math
from pathlib import Path
import re
from typing import Any

from .agent_claim_authority import (
    ClaimAuthorityError, ClaimIssuer, ClaimVerifier, _sign, SCHEMA as V5_SCHEMA
)
from .governed_learning_loop import digest
from .runtime_identity import current_git_head

SCHEMA = "mucaro-agent-claim/2.0"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ExecutionScope:
    workspace_digest: str
    source_head: str
    principal_id: str
    skill_id: str
    runtime: str
    max_resource_units: float

    @classmethod
    def for_workspace(cls, *, project_root: Path, source_head: str,
                      principal_id: str, skill_id: str,
                      max_resource_units: float) -> "ExecutionScope":
        return cls(
            workspace_digest=digest(str(Path(project_root).resolve())),
            source_head=source_head,
            principal_id=principal_id, skill_id=skill_id,
            runtime="IN_PROCESS", max_resource_units=max_resource_units,
        )

    def __post_init__(self):
        if not isinstance(self.workspace_digest,str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}",self.workspace_digest):
            raise ClaimAuthorityError("INVALID_WORKSPACE_BINDING")
        if not isinstance(self.source_head,str) or not _SHA40.fullmatch(self.source_head):
            raise ClaimAuthorityError("SOURCE_HEAD_REQUIRED")
        if (not isinstance(self.principal_id,str) or not self.principal_id.strip()
                or not isinstance(self.skill_id,str) or not self.skill_id.strip()
                or self.runtime!="IN_PROCESS"):
            raise ClaimAuthorityError("INVALID_SCOPE_IDENTITY")
        units=self.max_resource_units
        if (isinstance(units,bool) or not isinstance(units,(int,float))
                or not math.isfinite(units) or units<=0):
            raise ClaimAuthorityError("INVALID_RESOURCE_LIMIT")

    def as_dict(self) -> dict[str,Any]:
        return {
            "workspace_digest":self.workspace_digest,
            "source_head":self.source_head,
            "principal_id":self.principal_id,
            "skill_id":self.skill_id,
            "runtime":self.runtime,
            "max_resource_units":self.max_resource_units,
        }

    def matches_runtime(self, project_root: Path) -> bool:
        path=Path(project_root)
        return (self.workspace_digest==digest(str(path.resolve()))
                and current_git_head(path)==self.source_head)


class ScopedClaimIssuer(ClaimIssuer):
    """Issuer holds both a trusted V5 capability policy and a fixed V6 scope."""
    def __init__(self, issuer_id, secret, policy, reviewed_skills, scope: ExecutionScope):
        super().__init__(issuer_id,secret,policy,reviewed_skills)
        if not isinstance(scope,ExecutionScope) or (
                self.reviewed_skills.get(scope.skill_id) is None):
            raise ClaimAuthorityError("REVIEWED_SCOPE_REQUIRED")
        object.__setattr__(self,"scope",scope)

    def issue(self, **kwargs):
        if kwargs.get("skill_id")!=self.scope.skill_id:
            raise ClaimAuthorityError("SKILL_OUTSIDE_SCOPE")
        v5=super().issue(**kwargs)
        payload={k:v for k,v in v5.items() if k!="signature"}
        payload["schema"]=SCHEMA
        payload["scope"]=self.scope.as_dict()
        return {**payload,"signature":_sign(self.secret,payload)}


class ScopedClaimVerifier(ClaimVerifier):
    """Fail closed when the signed context differs from configured trust."""
    def __init__(self, issuer_id, secret, policy, reviewed_skills, scope: ExecutionScope):
        super().__init__(issuer_id,secret,policy,reviewed_skills)
        if not isinstance(scope,ExecutionScope) or (
                self.reviewed_skills.get(scope.skill_id) is None):
            raise ClaimAuthorityError("REVIEWED_SCOPE_REQUIRED")
        object.__setattr__(self,"scope",scope)

    def verify(self, grant, *, job_id, run_id, phase, input_digest, policy_hash,
               owner, now, lease_seconds):
        if (not isinstance(grant,dict) or len(grant)!=15 or
            not isinstance(grant.get("signature"),str) or
            not isinstance(grant.get("scope"),dict)):
            return False
        payload={k:v for k,v in grant.items() if k!="signature"}
        signature=grant["signature"]
        try:
            if (len(signature)!=64 or payload.get("schema")!=SCHEMA or
                    payload.get("scope")!=self.scope.as_dict() or
                    not hmac.compare_digest(signature,_sign(self.secret,payload))):
                return False
            # Authentication of v2 is required BEFORE constructing the
            # in-memory V5 representation for its strict job/time/policy checks.
            v5={k:v for k,v in payload.items() if k!="scope"}
            v5["schema"]=V5_SCHEMA
            v5["signature"]=_sign(self.secret,v5)
            return super().verify(v5,job_id=job_id,run_id=run_id,phase=phase,
                input_digest=input_digest,policy_hash=policy_hash,
                owner=owner,now=now,lease_seconds=lease_seconds)
        except (ValueError,TypeError,KeyError,OverflowError):
            return False
