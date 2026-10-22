"""Operator-authenticated learning API. Evidence payloads cannot impersonate reviewers."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import agent_consumers, learning
from .config import Settings


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Recognize(StrictRequest):
    text: str = Field(min_length=1, max_length=800)
    source_id: str = Field(min_length=1, max_length=200)
    source_bytes: str = Field(min_length=1, max_length=100000)
    permitted_use: bool = False
    purpose: str = Field(min_length=1, max_length=500)
    kind: str | None = None
    scope: str = Field(default="general", min_length=1, max_length=100)
    outcome: str = "UNKNOWN"


class Version(StrictRequest):
    version: int = Field(ge=1)


class Review(Version):
    accept: bool
    rationale: str = Field(min_length=1, max_length=5000)
    positive_case: str = Field(default="", max_length=5000)
    negative_case: str = Field(default="", max_length=5000)
    conflict_ids: list[str] = Field(default_factory=list, max_length=100)
    explicit_approval: bool = False
    verified_evidence: bool = False
    authority_safe: bool = False


class Verify(Version):
    positive_pass: bool
    negative_pass: bool
    evaluation_receipt: str = Field(min_length=1, max_length=10000)


class Rollback(Version):
    reason: str = Field(min_length=1, max_length=5000)


class Resolve(Version):
    kind: str
    scope: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=5000)


class Feedback(StrictRequest):
    episode_id: str = Field(min_length=1, max_length=200)
    candidate_id: str
    context: str = Field(min_length=1, max_length=100)
    reward: float = Field(ge=-1, le=1, allow_inf_nan=False)
    evidence: str = Field(min_length=1, max_length=10000)
    permitted_use: bool = False


class Capture(StrictRequest):
    enabled: bool
    purpose: str = Field(min_length=1, max_length=500)


class Scan(StrictRequest):
    permitted_use: bool = False
    purpose: str = Field(min_length=1, max_length=500)


class ConsumerBinding(StrictRequest):
    agent_id: str
    candidate_id: str
    candidate_version: int = Field(ge=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipe_id: str
    approved: bool = False


class ConsumerPrepare(StrictRequest):
    binding_id: str
    repo_root: str = Field(min_length=1, max_length=2000)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    file_path: str = Field(min_length=1, max_length=2000)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)


class ConsumerExecute(Version):
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool = False


class ConsumerStop(Version):
    reason: str = Field(min_length=1, max_length=5000)


class ConsumerReward(StrictRequest):
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    criteria: str = Field(min_length=1, max_length=5000)
    permitted_use: bool = False


def router(cfg: Settings, require_auth) -> APIRouter:
    routes = APIRouter(prefix="/learning", dependencies=[Depends(require_auth)])

    def invoke(fn, *args, **kwargs):
        try:
            return fn(cfg, *args, **kwargs)
        except learning.LearningError as exc:
            raise HTTPException(status_code=409, detail={"status": "ERROR_LEARNING_GATE", "detail": str(exc)}) from exc

    @routes.get("")
    def listing():
        return invoke(learning.snapshot)

    @routes.post("/capture")
    def capture(req: Capture):
        return invoke(learning.configure_capture, **req.model_dump())

    @routes.post("/recognize")
    def recognize(req: Recognize):
        return invoke(learning.recognize, **req.model_dump())

    @routes.post("/scan-run/{run_id}")
    def scan(run_id: str, req: Scan):
        return invoke(learning.scan_run, run_id, **req.model_dump())

    @routes.post("/{candidate_id}/resolve")
    def resolve(candidate_id: str, req: Resolve):
        return invoke(learning.resolve, candidate_id, **req.model_dump())

    @routes.post("/{candidate_id}/review")
    def review(candidate_id: str, req: Review):
        return invoke(learning.review, candidate_id, **req.model_dump())

    @routes.post("/{candidate_id}/apply")
    def apply(candidate_id: str, req: Version):
        return invoke(learning.apply, candidate_id, **req.model_dump())

    @routes.post("/{candidate_id}/verify")
    def verify(candidate_id: str, req: Verify):
        return invoke(learning.verify, candidate_id, **req.model_dump())

    @routes.post("/{candidate_id}/rollback")
    def rollback(candidate_id: str, req: Rollback):
        return invoke(learning.rollback, candidate_id, **req.model_dump())

    @routes.post("/feedback")
    def feedback(req: Feedback):
        return invoke(learning.feedback, **req.model_dump())

    @routes.get("/recommend")
    def recommend(context: str):
        return invoke(learning.recommend, context)

    @routes.get("/consumers")
    def consumer_listing():
        return invoke(agent_consumers.listing)

    @routes.post("/consumers/bind")
    def consumer_binding(req: ConsumerBinding):
        return invoke(agent_consumers.bind, **req.model_dump())

    @routes.post("/consumers/{binding_id}/revoke")
    def consumer_revoke(binding_id: str, req: ConsumerStop):
        return invoke(agent_consumers.unbind, binding_id, **req.model_dump())

    @routes.post("/consumer-runs/prepare")
    def consumer_prepare(req: ConsumerPrepare):
        return invoke(agent_consumers.prepare, **req.model_dump())

    @routes.post("/consumer-runs/{run_id}/execute")
    def consumer_execute(run_id: str, req: ConsumerExecute):
        return invoke(agent_consumers.execute, run_id, **req.model_dump())

    @routes.post("/consumer-runs/{run_id}/stop")
    def consumer_stop(run_id: str, req: ConsumerStop):
        return invoke(agent_consumers.stop, run_id, **req.model_dump())

    @routes.post("/consumer-runs/{run_id}/feedback")
    def consumer_reward(run_id: str, req: ConsumerReward):
        return invoke(agent_consumers.reward, run_id, **req.model_dump())

    return routes
