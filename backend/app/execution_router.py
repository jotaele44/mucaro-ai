from __future__ import annotations

from typing import Any, Callable

from .capability_policy import RUNTIME_CAPABILITY
from .config import Settings
from .container_runtime import ContainerRuntimeError, get_container_binding, run_container_worker
from .local_worker import run_local_worker

ROUTER_SCHEMA_VERSION = "mucaro-execution-router/1.0"
SUPPORTED_ROUTES = {"IN_PROCESS", "LOCAL_WORKER", "CONTAINER"}


class ExecutionRouterError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def select_execution_route(runtime: str, policy_decision: dict[str, Any]) -> str:
    if runtime not in RUNTIME_CAPABILITY:
        raise ExecutionRouterError("ERROR_EXECUTION_ROUTE_UNKNOWN", runtime)
    if policy_decision.get("status") != "AUTHORIZED":
        raise ExecutionRouterError("ERROR_EXECUTION_ROUTE_POLICY", "policy decision is not authorized")
    granted = policy_decision.get("granted")
    if not isinstance(granted, list):
        raise ExecutionRouterError("ERROR_EXECUTION_ROUTE_POLICY", "policy decision granted set is missing")
    required_capability = RUNTIME_CAPABILITY[runtime]
    if required_capability not in granted:
        raise ExecutionRouterError(
            "ERROR_EXECUTION_ROUTE_POLICY",
            f"runtime capability not granted: {required_capability}",
        )
    return runtime


def execute_routed(
    *,
    runtime: str,
    skill_id: str,
    handler: Callable[[dict[str, Any], Settings], dict[str, Any]],
    arguments: dict[str, Any],
    cfg: Settings,
    policy_decision: dict[str, Any],
) -> dict[str, Any]:
    route = select_execution_route(runtime, policy_decision)
    if route == "IN_PROCESS":
        result = handler(arguments, cfg)
        return {
            "route": route,
            "result": result,
            "adapter_receipt": {
                "schema_version": ROUTER_SCHEMA_VERSION,
                "route": route,
                "isolation": "APP_PROCESS",
            },
        }
    if route == "LOCAL_WORKER":
        granted = set(policy_decision.get("granted", []))
        worker = run_local_worker(
            project_root=cfg.project_root,
            skill_id=skill_id,
            arguments=arguments,
            network_allowed="network.public_http" in granted,
        )
        return {
            "route": route,
            "result": worker.get("result"),
            "adapter_receipt": worker,
        }
    if route == "CONTAINER":
        try:
            binding = get_container_binding(skill_id)
        except ContainerRuntimeError as exc:
            raise ExecutionRouterError(exc.status, exc.detail) from exc
        granted = set(policy_decision.get("granted", []))
        container = run_container_worker(
            project_root=cfg.project_root,
            skill_id=skill_id,
            image=binding.image,
            arguments=arguments,
            network_allowed="network.public_http" in granted,
        )
        receipt = {**container, "binding": binding.as_dict()}
        return {
            "route": route,
            "result": container.get("result"),
            "adapter_receipt": receipt,
        }
    if route not in SUPPORTED_ROUTES:
        raise ExecutionRouterError("ERROR_EXECUTION_ROUTE_UNAVAILABLE", route)
    raise ExecutionRouterError("ERROR_EXECUTION_ROUTE_UNAVAILABLE", route)
