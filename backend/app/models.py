from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_strategy: Literal["hybrid", "lexical", "vector", "bm25", "persistent", "semantic"] = "hybrid"
    model_provider: str | None = None
    model_name: str | None = None
    allow_mock_fallback: bool = False
    allow_live: bool = False
    require_offline: bool = False
    choice_id: str | None = None
    interaction_id: str | None = None
    dismiss_pending: bool = False
    response_interaction: dict | None = None


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
