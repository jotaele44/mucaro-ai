from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_strategy: Literal["hybrid", "lexical", "vector", "bm25", "persistent", "semantic"] = "hybrid"
    model_provider: str | None = None
    model_name: str | None = None
    allow_mock_fallback: bool = False
    allow_live: bool = False
    require_offline: bool = False


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ConversationUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class WorkspaceProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=500)


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=800)
    kind: Literal["preference", "profile", "instruction", "fact"] = "preference"


class MemoryUpdateRequest(BaseModel):
    enabled: bool


class PlanRequest(BaseModel):
    user_request: str = Field(min_length=1)
    inputs_available: list[str] = Field(default_factory=list)
    approval_state: str = "NOT_REQUESTED"


class WorkbenchPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_request: str = Field(min_length=1, max_length=20000)
    source_commit: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{40}$")
    features: list[str] = Field(default_factory=list, max_length=2000)
    inputs_available: list[str] = Field(default_factory=list, max_length=2000)
    profile: Literal["blockers", "extended", "maximum"] = "maximum"
    network: Literal["isolated", "offline"] = "isolated"
    hardware: Literal["available", "unknown", "unavailable"] = "unknown"
    platforms: list[Literal["linux", "macos", "windows"]] = Field(
        default_factory=lambda: ["macos"], min_length=1, max_length=3
    )


class ApprovalRequest(BaseModel):
    gate: dict
    approved_mode: str


class DirectoryPermissionRequest(BaseModel):
    path: str = Field(min_length=1)
    reason: str | None = None


class IngestPathRequest(BaseModel):
    path: str = Field(min_length=1)


class ToolCallRequest(BaseModel):
    tool_name: str = Field(min_length=1, max_length=120)
    arguments: dict = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=500)


class ToolDecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    # When supplied, the decision binds to the exact arguments the decider saw.
    arguments_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class InteractionOptionInput(BaseModel):
    option_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    consequence: str | None = Field(default=None, max_length=400)


class InteractionCreateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    options: list[InteractionOptionInput] = Field(min_length=1)
    allow_freeform: bool = True
    dismiss_pending: bool = False


class InteractionResolveRequest(BaseModel):
    expected_version: int = Field(ge=1)
    option_id: str | None = Field(default=None, max_length=80)
    freeform_text: str | None = Field(default=None, max_length=2000)


class InteractionDismissRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=400)


class RunOptions(BaseModel):
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_strategy: Literal["hybrid", "lexical", "vector", "bm25", "persistent", "semantic"] = "hybrid"
    model_provider: str | None = None
    model_name: str | None = None
    allow_mock_fallback: bool = False
    allow_live: bool = False
    require_offline: bool = False


class MessageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    mode: Literal["plan", "ask", "auto"] = "ask"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    interrupt: bool = False
    dismiss_pending_question: bool = False
    options: RunOptions = Field(default_factory=RunOptions)


class MessageEditRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    expected_revision: int = Field(default=0, ge=0)


class RunEditRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    expected_version: int = Field(ge=1)


class ExecutionEntryGateInput(BaseModel):
    gate_id: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=1, max_length=40)
    evidence_ref: str = Field(min_length=1, max_length=500)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExecutionEntryCreateRequest(BaseModel):
    mode: Literal["GOAL", "PLAN"]
    objective_id: str = Field(min_length=1, max_length=160)
    conversation_id: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=20000)
    runtime_mode: Literal["plan", "ask", "auto"] = "ask"
    options: dict = Field(default_factory=dict)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    required_gates: list[ExecutionEntryGateInput] = Field(min_length=1)
    policy_version: str = Field(default="execution-entry-v1", min_length=1, max_length=120)


class ExecutionEntryStateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    target_state: Literal["VECTOR_READY", "PLAN_APPROVED"]


class ExecutionEntryDecisionRequest(BaseModel):
    action: Literal["Proceed", "Adjust", "Execute", "Revise"]
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    changes: dict = Field(default_factory=dict)


class ExecutionEntryResumeRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
