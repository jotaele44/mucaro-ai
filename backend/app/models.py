from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    model_provider: str | None = None
    model_name: str | None = None
    allow_mock_fallback: bool = False


class PlanRequest(BaseModel):
    user_request: str = Field(min_length=1)
    inputs_available: list[str] = Field(default_factory=list)
    approval_state: str = "NOT_REQUESTED"


class ApprovalRequest(BaseModel):
    gate: dict
    approved_mode: str
