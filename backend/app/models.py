from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_strategy: Literal["hybrid", "lexical", "vector", "persistent"] = "hybrid"
    model_provider: str | None = None
    model_name: str | None = None
    allow_mock_fallback: bool = False


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class WorkspaceProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=500)


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
