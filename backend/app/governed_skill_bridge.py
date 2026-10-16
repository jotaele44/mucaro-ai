"""Strict policy-to-worker-to-skill binding for a credential-free V4 pilot.

Only the explicitly reviewed built-in Mucaro health probe is eligible, and
it must already be active, no-capability, and IN_PROCESS. No unattended side-effecting, remote, training or promotion actions.
"""
from __future__ import annotations
from typing import Any
from time import time
from .capability_policy import CapabilityPolicy, evaluate_capabilities
from .config import Settings
from .governed_learning_loop import digest
from .leased_agent_queue import LeasedAgentQueue
from .skill_runtime import SKILL_REGISTRY, list_skill_mounts, execute_skill

# Bounded pilot ONLY. Zero declared capabilities do not prove a handler is pure:
# the built-in health handler creates local directories as part of setup.
# All other skill identities remain outside this pilot's execution authority.
PILOT_SKILL_ALLOWLIST = frozenset({"mucaro/intelligence-plane-health"})

class DispatchGateError(RuntimeError):
    pass

class GovernedSkillBridge:
    def __init__(self, *, queue: LeasedAgentQueue, cfg: Settings,
                 policy: CapabilityPolicy):
        self.queue, self.cfg, self.policy = queue, cfg, policy
        self.policy_digest = digest({
            "policy_id":policy.policy_id,
            "allowed":sorted(policy.allowed), "denied":sorted(policy.denied),
        })

    def _authorize(self, skill_id: str):
        if skill_id not in PILOT_SKILL_ALLOWLIST:
            raise DispatchGateError("PILOT_SKILL_NOT_ALLOWLISTED")
        skill = SKILL_REGISTRY.get(skill_id)
        if not skill:
            raise DispatchGateError("UNKNOWN_SKILL")
        if skill.runtime != "IN_PROCESS" or skill.capabilities:
            raise DispatchGateError("SIDE_EFFECT_OR_REMOTE_RUNTIME_DENIED")
        required = {"runtime.in_process"}
        decision = evaluate_capabilities(policy=self.policy,
               declared_capabilities=required, requested_capabilities=required)
        if not decision.authorized:
            raise DispatchGateError("POLICY_DENIED")
        matches=[m for m in list_skill_mounts(self.cfg)["mounts"]
                 if m.get("skill_id")==skill_id]
        if len(matches)!=1 or matches[0].get("state")!="ACTIVE" or matches[0].get("digest")!=skill.digest:
            raise DispatchGateError("SKILL_MOUNT_NOT_VERIFIED")
        existing=matches[0].get("policy_decision")
        if not isinstance(existing,dict) or existing.get("status")!="AUTHORIZED":
            raise DispatchGateError("SKILL_ACTIVATION_NOT_AUTHORIZED")
        return skill

    def execute_readonly(self, *, job_id: str, run_id: str,
                         skill_id: str, arguments: dict[str,Any],
                         worker_id: str, lease_seconds: int = 30) -> dict[str,Any]:
        if not isinstance(arguments,dict):
            raise DispatchGateError("INVALID_ARGUMENTS")
        skill=self._authorize(skill_id)
        inp=digest({"skill_id":skill_id,"skill_digest":skill.digest,"arguments":arguments})
        self.queue.submit(job_id,run_id,"EXECUTE",inp,self.policy_digest,max_attempts=2)
        claim=self.queue.claim(job_id,worker_id,int(time()),lease_seconds,permitted=True)
        if claim is None:
            return {"status":"BLOCKED","reason":"NO_AUTHORIZED_CLAIM"}
        if claim["input_digest"]!=inp or claim["policy_digest"]!=self.policy_digest:
            raise DispatchGateError("CLAIM_IDENTITY_MISMATCH")
        # Recheck authorization after claim, before invoking a real handler.
        self._authorize(skill_id)
        try:
            executed=execute_skill(skill_id,arguments,cfg=self.cfg,
                                   executed_by="agent:"+worker_id)
        except Exception as exc:
            return {"status":"FAILED","reason":type(exc).__name__,
                    "job_id":job_id,"fence":claim["fence"]}
        evidence=executed.get("execution")
        if (executed.get("status")!="SKILL_EXECUTED" or
            not isinstance(evidence,dict) or
            evidence.get("status")!="PASS" or
            evidence.get("skill_digest")!=skill.digest):
            return {"status":"FAILED","reason":"INVALID_EXECUTION_RECEIPT"}
        output={"status":"PASS","skill_id":skill_id,
                "execution_id":evidence.get("execution_id"),
                "skill_digest":skill.digest,
                "runtime_identity_digest":digest(evidence.get("runtime_identity")),
                "result_digest":digest(executed.get("result"))}
        completed=self.queue.finish(job_id,worker_id,claim["fence"],output,int(time()))
        if not completed:
            return {"status":"BLOCKED","reason":"STALE_FENCE_OR_LEASE"}
        return {"status":"PASS","job_id":job_id,"claim_fence":claim["fence"],
                "result":executed.get("result"),"output_digest":digest(output),
                "policy_digest":self.policy_digest}
