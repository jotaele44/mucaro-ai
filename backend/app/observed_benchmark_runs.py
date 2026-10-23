"""V12 observed executable benchmark trace contract (no autonomous promotion).

Executes real callable handlers against immutable-invocation input snapshots,
records their observed outputs and elapsed monotonic time, and fails closed
on unbound handlers or incomplete results. Caller must independently attest
real-task acquisition, trusted process identity and source bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from time import perf_counter_ns
from typing import Any, Callable, Mapping

from .governed_learning_loop import canonical_bytes, digest

_SHA40=re.compile(r"^[a-f0-9]{40}$")
class ObservedRunError(RuntimeError):
    pass

@dataclass(frozen=True)
class RunBinding:
    source_head: str
    implementation_digest: str
    runner_principal: str
    runner_manifest_digest: str

    def __post_init__(self):
        if not isinstance(self.source_head,str) or not _SHA40.fullmatch(self.source_head):
            raise ObservedRunError("UNVERIFIED_SOURCE_HEAD")
        for value in (self.implementation_digest,self.runner_manifest_digest):
            if not isinstance(value,str) or not re.fullmatch(r"sha256:[a-f0-9]{64}",value):
                raise ObservedRunError("INVALID_IMPLEMENTATION_BINDING")
        if not isinstance(self.runner_principal,str) or not self.runner_principal.strip():
            raise ObservedRunError("RUNNER_IDENTITY_REQUIRED")

    def manifest(self):
        return {"source_head":self.source_head,
                "implementation_digest":self.implementation_digest,
                "runner_principal":self.runner_principal,
                "runner_manifest_digest":self.runner_manifest_digest}

def observed_execution(*,run_id:str,benchmark_id:str,
                       cases:Mapping[str,str],binding:RunBinding,
                       handler:Callable[[str],str],
                       expected_handler_digest:str,
                       declared_max_cases:int=100)->dict[str,Any]:
    """Run a bounded supplied handler. Never give it holdout answer labels."""
    if not isinstance(run_id,str) or not run_id or not benchmark_id:
        raise ObservedRunError("INVALID_RUN_ID")
    if (not isinstance(cases,dict) or not 4<=len(cases)<=declared_max_cases
        or isinstance(declared_max_cases,bool) or not isinstance(declared_max_cases,int)
        or not 4<=declared_max_cases<=10000):
        raise ObservedRunError("UNBOUNDED_OR_INVALID_CASES")
    if any(not isinstance(k,str) or not k or not isinstance(v,str)
           for k,v in cases.items()):
        raise ObservedRunError("INVALID_CASE_INPUT")
    if (not callable(handler) or expected_handler_digest!=binding.implementation_digest):
        raise ObservedRunError("UNBOUND_IMPLEMENTATION")
    outputs={}
    samples=[]
    for case_id in sorted(cases):
        input_text=cases[case_id]
        before=perf_counter_ns()
        try:
            output=handler(input_text)
        except Exception as exc:
            raise ObservedRunError("HANDLER_EXECUTION_FAILED:"+type(exc).__name__) from exc
        elapsed=perf_counter_ns()-before
        if not isinstance(output,str) or elapsed<0:
            raise ObservedRunError("INVALID_HANDLER_OUTPUT_OR_CLOCK")
        outputs[case_id]=output
        samples.append({"case_id":case_id,"input_digest":digest(input_text),
                        "output_digest":digest(output),"elapsed_ns":elapsed})
    manifest={"schema":"mucaro-observed-benchmark-run/1.0",
        "run_id":run_id,"benchmark_id":benchmark_id,
        "binding":binding.manifest(),"case_ids":sorted(cases),
        "input_manifest_digest":digest(cases),
        "output_manifest_digest":digest(outputs),
        "samples":samples,
        "duration_ns":sum(item["elapsed_ns"] for item in samples),
        "observed_outputs":outputs,
        "cost_units_kind":"UNMETERED",
        "actual_provider_charges":None,
        "independent_runtime_attestation":False,
        "source_byte_attestation":False,
        "promotion_authorized":False}
    return {**manifest,"receipt_hash":digest(manifest)}

def verify_observed_receipt(receipt:dict[str,Any],*,cases:Mapping[str,str],
                            binding:RunBinding)->bool:
    """Validate internal trace conservation; does not prove handler attribution."""
    try:
        if (not isinstance(receipt,dict) or
            receipt.get("schema")!="mucaro-observed-benchmark-run/1.0" or
            receipt.get("binding")!=binding.manifest() or
            receipt.get("case_ids")!=sorted(cases) or
            receipt.get("input_manifest_digest")!=digest(cases) or
            receipt.get("output_manifest_digest")!=digest(receipt["observed_outputs"]) or
            receipt.get("receipt_hash")!=digest({k:v for k,v in receipt.items()
                                                 if k!="receipt_hash"}) or
            receipt["cost_units_kind"]!="UNMETERED" or
            receipt["promotion_authorized"] is not False):
            return False
        samples=receipt["samples"]
        outputs=receipt["observed_outputs"]
        if (len(samples)!=len(cases) or set(outputs)!=set(cases) or
            receipt["duration_ns"]!=sum(s["elapsed_ns"] for s in samples)):
            return False
        for cid,s in zip(sorted(cases),samples):
            if (s["case_id"]!=cid or s["input_digest"]!=digest(cases[cid])
                or s["output_digest"]!=digest(outputs[cid])
                or isinstance(s["elapsed_ns"],bool) or
                not isinstance(s["elapsed_ns"],int) or s["elapsed_ns"]<0):
                return False
        return True
    except (KeyError,TypeError,ValueError,OverflowError):
        return False

def compare_observed_runs(*,baseline:dict,candidate:dict,
                          sealed_holdout:dict[str,str],cases:dict[str,str],
                          baseline_binding:RunBinding,
                          candidate_binding:RunBinding)->dict[str,Any]:
    if (not verify_observed_receipt(baseline,cases=cases,binding=baseline_binding)
        or not verify_observed_receipt(candidate,cases=cases,binding=candidate_binding)
        or set(sealed_holdout)!=set(cases)):
        raise ObservedRunError("INVALID_COMPARISON_MANIFEST")
    base=baseline["observed_outputs"]
    new=candidate["observed_outputs"]
    fixed=sorted(k for k in cases if base[k]!=sealed_holdout[k] and new[k]==sealed_holdout[k])
    regression=sorted(k for k in cases if base[k]==sealed_holdout[k] and new[k]!=sealed_holdout[k])
    denom=len(cases)
    bsum=sum(base[k]==sealed_holdout[k] for k in cases)
    nsum=sum(new[k]==sealed_holdout[k] for k in cases)
    row={"schema":"mucaro-observed-run-comparison/1.0",
         "baseline_receipt_hash":baseline["receipt_hash"],
         "candidate_receipt_hash":candidate["receipt_hash"],
         "heldout_truth_digest":digest(sealed_holdout),
         "denominator":denom,"baseline_pass":bsum,"candidate_pass":nsum,
         "accuracy_delta":(nsum-bsum)/denom,"fixed_cases":fixed,
         "regressed_cases":regression,
         "baseline_elapsed_ns":baseline["duration_ns"],
         "candidate_elapsed_ns":candidate["duration_ns"],
         "classification":"FIXTURE_REVIEW_ONLY" if nsum>bsum and not regression else "REJECTED",
         "promoted":False,
         "independence_confirmed":False}
    return {**row,"receipt_hash":digest(row)}
