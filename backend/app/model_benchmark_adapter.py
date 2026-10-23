"""V13 source-bound calibration against the actual Mucaro mock provider.

This is a deterministic MOCK provider, not an independently acquired real
model or real-task benchmark. It confers no model-improvement authority.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from .model_router import generate_answer
from .observed_benchmark_runs import RunBinding,observed_execution,ObservedRunError
from .governed_learning_loop import digest

MODULE="backend/app/model_router.py"

def source_file_sha256(root:Path)->str:
    project=Path(root).resolve()
    path=(project/MODULE).resolve()
    if not path.is_relative_to(project) or not path.is_file():
        raise ObservedRunError("SOURCE_UNAVAILABLE")
    return "sha256:"+hashlib.sha256(path.read_bytes()).hexdigest()

def execute_mock_calibration(*,project_root:Path,source_head:str,
    inputs:dict[str,str],run_id:str,runner_id:str,
    expected_file_digest:str)->dict:
    actual=source_file_sha256(project_root)
    if actual!=expected_file_digest:
        raise ObservedRunError("SOURCE_BYTES_MISMATCH")
    if not runner_id:
        raise ObservedRunError("PRINCIPAL_REQUIRED")
    binding=RunBinding(source_head=source_head,
        implementation_digest=digest(["mucaro","mock",MODULE,actual]),
        runner_principal=runner_id,
        runner_manifest_digest=digest(["python-mucaro-mock",source_head,actual]))
    def handler(query:str)->str:
        response=generate_answer(query,[{"chunk_id":"fixture-1",
            "text":"Synthetic context: not real independent evaluation"}],
            model_provider="mock",allow_mock_fallback=False)
        if (response.get("model_provider")!="mock" or
            response.get("cited_chunk_ids")!=["fixture-1"] or
            not isinstance(response.get("answer"),str)):
            raise ObservedRunError("PROVIDER_CONTRACT_DRIFT")
        return response["answer"]
    result=observed_execution(run_id=run_id,
        benchmark_id="mucaro-mock-calibration/1.0",cases=inputs,
        binding=binding,handler=handler,
        expected_handler_digest=binding.implementation_digest)
    payload={**{k:v for k,v in result.items() if k!="receipt_hash"},
        "provider":"mock","source_file_digest":actual,
        "classification":"SYNTHETIC_PROVIDER_CALIBRATION_ONLY",
        "independent_model_improvement":False}
    return {**payload,"receipt_hash":digest(payload)}

def execute_git_attested_mock_calibration(*,project_root:Path,
        source_head:str,inputs:dict[str,str],run_id:str,runner_id:str,
        expected_file_digest:str)->dict:
    """Allow mock calibration only after verifying exact HEAD, tree and blob."""
    from .local_source_integrity import attest_local_source
    from inspect import getfile
    runtime_module=Path(getfile(generate_answer)).resolve()
    expected_module=(Path(project_root).resolve()/MODULE).resolve()
    if runtime_module!=expected_module:
        raise ObservedRunError("IMPORTED_MODEL_ROUTER_OUTSIDE_CHECKOUT")
    attestation=attest_local_source(project_root=project_root,
        expected_head=source_head,source_path=MODULE,
        expected_sha256=expected_file_digest)
    recorded=execute_mock_calibration(project_root=project_root,
        source_head=source_head,inputs=inputs,run_id=run_id,
        runner_id=runner_id,expected_file_digest=expected_file_digest)
    body={k:v for k,v in recorded.items() if k!="receipt_hash"}
    body["git_source_receipt_hash"]=attestation["receipt_hash"]
    body["source_identity_classification"]="LOCAL_GIT_CHECKOUT_ONLY"
    return {**body,"receipt_hash":digest(body)}
