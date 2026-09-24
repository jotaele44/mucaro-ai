from __future__ import annotations

import importlib.machinery
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from backend.app.agent_checkpoints import CheckpointError, create_checkpoint, get_checkpoint, transition_checkpoint
from backend.app.config import Settings
from backend.app.model_router import (
    MODEL_PROVIDER_SPECS,
    PROVIDER_INTERRUPTION_STATUSES,
    ModelProviderError,
    classify_provider_exception,
    generate_answer,
)
from backend.app.provider_recovery import (
    ProviderRecoveryError,
    execute_with_provider_checkpoint,
    plan_provider_failover,
    resume_with_failover,
    resume_with_provider,
)

SHA = "c" * 40
REPO = Path(__file__).resolve().parents[2]
CHUNKS = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}]
SENSITIVE = "sensitive-request-content"
COMPLETED = [{"id": "done-1", "status": "PASS"}]
PENDING = [{"id": "next-1", "status": "OPEN"}]
CONTRADICTIONS = [{"id": "contra-1", "type": "IDENTITY", "state": "UNRESOLVED"}]


# Fakes mirroring the Anthropic SDK hierarchy: APITimeoutError subclasses APIConnectionError,
# and HTTP failures carry status_code on APIStatusError subclasses.
class APIError(Exception):
    pass


class APIConnectionError(APIError):
    pass


class APITimeoutError(APIConnectionError):
    pass


class APIStatusError(APIError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(APIStatusError):
    pass


class InternalServerError(APIStatusError):
    pass


class OverloadedError(APIStatusError):
    pass


class AuthenticationError(APIStatusError):
    pass


class PermissionDeniedError(APIStatusError):
    pass


class BadRequestError(APIStatusError):
    pass


class NotFoundError(APIStatusError):
    pass


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def install_fake_anthropic(monkeypatch: pytest.MonkeyPatch, *, error: BaseException | None = None, text: str = "") -> None:
    module = types.ModuleType("anthropic")
    module.__spec__ = importlib.machinery.ModuleSpec("anthropic", None)

    class _Messages:
        def create(self, **kwargs):
            if error is not None:
                raise error
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    class Anthropic:
        def __init__(self) -> None:
            self.messages = _Messages()

    module.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv(MODEL_PROVIDER_SPECS["anthropic"].api_key_env, "test-key")


def executing_checkpoint(
    cfg: Settings, *, provider: str = "anthropic", model: str | None = "claude-test", run_id: str = "run-v04"
) -> dict:
    return create_checkpoint(
        session_id="session-v04",
        run_id=run_id,
        objective="Continue provider-bound research",
        source_sha=SHA,
        state="EXECUTING",
        completed_steps=COMPLETED,
        pending_steps=PENDING,
        contradictions=CONTRADICTIONS,
        provider=provider,
        model=model,
        cfg=cfg,
    )


def outage(status: str = "ERROR_MODEL_PROVIDER_UNAVAILABLE"):
    def operation():
        raise ModelProviderError(status, "outage")

    return operation


def assert_canonical_state_preserved(checkpoint: dict, original: dict) -> None:
    for field in ("checkpoint_id", "session_id", "run_id", "objective", "source_sha", "created_at"):
        assert checkpoint[field] == original[field]
    assert checkpoint["completed_steps"] == COMPLETED
    assert checkpoint["pending_steps"] == PENDING
    assert checkpoint["contradictions"] == CONTRADICTIONS


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (RateLimitError(SENSITIVE, 429), "ERROR_MODEL_PROVIDER_RATE_LIMIT", "anthropic:RateLimitError:http_429"),
        (InternalServerError(SENSITIVE, 500), "ERROR_MODEL_PROVIDER_UNAVAILABLE", "anthropic:InternalServerError:http_500"),
        (APIStatusError(SENSITIVE, 503), "ERROR_MODEL_PROVIDER_UNAVAILABLE", "anthropic:APIStatusError:http_503"),
        (OverloadedError(SENSITIVE, 529), "ERROR_MODEL_PROVIDER_UNAVAILABLE", "anthropic:OverloadedError:http_529"),
        (APITimeoutError(SENSITIVE), "ERROR_MODEL_PROVIDER_TIMEOUT", "anthropic:APITimeoutError"),
        (APIConnectionError(SENSITIVE), "ERROR_MODEL_PROVIDER_UNAVAILABLE", "anthropic:APIConnectionError"),
        (TimeoutError(SENSITIVE), "ERROR_MODEL_PROVIDER_TIMEOUT", "anthropic:TimeoutError"),
        (ConnectionResetError(SENSITIVE), "ERROR_MODEL_PROVIDER_UNAVAILABLE", "anthropic:ConnectionResetError"),
        (AuthenticationError(SENSITIVE, 401), "ERROR_MODEL_PROVIDER_AUTH", "anthropic:AuthenticationError:http_401"),
        (PermissionDeniedError(SENSITIVE, 403), "ERROR_MODEL_PROVIDER_AUTH", "anthropic:PermissionDeniedError:http_403"),
        (BadRequestError(SENSITIVE, 400), "ERROR_INVALID_MODEL_REQUEST", "anthropic:BadRequestError:http_400"),
        (NotFoundError(SENSITIVE, 404), "ERROR_INVALID_MODEL_REQUEST", "anthropic:NotFoundError:http_404"),
        (ValueError(SENSITIVE), "ERROR_MODEL_PROVIDER_FAILURE", "anthropic:ValueError"),
    ],
)
def test_provider_exceptions_normalize_without_leaking_messages(error: BaseException, status: str, detail: str) -> None:
    normalized = classify_provider_exception("anthropic", error)

    assert normalized.status == status
    assert normalized.detail == detail
    assert SENSITIVE not in normalized.detail


def test_interruption_set_pauses_outages_but_not_invalid_or_unknown_failures() -> None:
    for status in (
        "ERROR_MODEL_PROVIDER_RATE_LIMIT",
        "ERROR_MODEL_PROVIDER_TIMEOUT",
        "ERROR_MODEL_PROVIDER_UNAVAILABLE",
        "ERROR_MODEL_PROVIDER_AUTH",
    ):
        assert status in PROVIDER_INTERRUPTION_STATUSES
    assert "ERROR_INVALID_MODEL_REQUEST" not in PROVIDER_INTERRUPTION_STATUSES
    assert "ERROR_MODEL_PROVIDER_FAILURE" not in PROVIDER_INTERRUPTION_STATUSES
    existing = ModelProviderError("ERROR_MODEL_PROVIDER_TIMEOUT", "already normalized")
    assert classify_provider_exception("anthropic", existing) is existing


def test_live_adapter_success_path_still_cites_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_anthropic(monkeypatch, text="Lineage needs citations (chunk_a).")

    answer = generate_answer("What is required?", CHUNKS, model_provider="anthropic", model_name="claude-test")

    assert answer["model_provider"] == "anthropic"
    assert answer["model_name"] == "claude-test"
    assert answer["cited_chunk_ids"] == ["chunk_a"]


def test_live_adapter_outage_raises_normalized_error(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_anthropic(monkeypatch, error=RateLimitError(SENSITIVE, 429))

    with pytest.raises(ModelProviderError) as exc:
        generate_answer("What is required?", CHUNKS, model_provider="anthropic")

    assert exc.value.status == "ERROR_MODEL_PROVIDER_RATE_LIMIT"
    assert exc.value.detail == "anthropic:RateLimitError:http_429"


def test_live_adapter_outage_honors_explicit_mock_fallback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_anthropic(monkeypatch, error=OverloadedError(SENSITIVE, 529))

    answer = generate_answer("What is required?", CHUNKS, model_provider="anthropic", allow_mock_fallback=True)

    assert answer["model_provider"] == "mock"
    assert answer["provider_warning"] == "ERROR_MODEL_PROVIDER_UNAVAILABLE"


def test_real_adapter_outage_pauses_checkpoint_and_preserves_canonical_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_settings(tmp_path)
    install_fake_anthropic(monkeypatch, error=RateLimitError(SENSITIVE, 429))
    cp = executing_checkpoint(cfg)

    result = execute_with_provider_checkpoint(
        cp["checkpoint_id"],
        provider="anthropic",
        model="claude-test",
        operation=lambda: generate_answer("q", CHUNKS, model_provider="anthropic", model_name="claude-test"),
        cfg=cfg,
    )

    paused = result["checkpoint"]
    assert result["status"] == "PAUSED_PROVIDER"
    assert result["provider_error"] == "ERROR_MODEL_PROVIDER_RATE_LIMIT"
    assert paused["state"] == "PAUSED_PROVIDER"
    assert paused["pause_reason"] == "anthropic:ERROR_MODEL_PROVIDER_RATE_LIMIT"
    assert paused["sequence"] == 1
    assert_canonical_state_preserved(paused, cp)
    events = paused["metadata"]["provider_events"]
    assert [event["event"] for event in events] == ["PROVIDER_INTERRUPTED"]
    assert events[0]["provider"] == "anthropic"
    assert events[0]["model"] == "claude-test"
    assert events[0]["status"] == "ERROR_MODEL_PROVIDER_RATE_LIMIT"
    assert events[0]["checkpoint_sequence"] == paused["sequence"]

    stored = get_checkpoint(cp["checkpoint_id"], cfg)
    assert stored == paused
    assert SENSITIVE not in json.dumps(stored)
    schema = json.loads((REPO / "schemas" / "agent_checkpoint.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(stored)) == []


def test_failover_live_to_offline_skips_failed_provider_and_resumes_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_settings(tmp_path)
    install_fake_anthropic(monkeypatch, error=APITimeoutError(SENSITIVE))
    cp = executing_checkpoint(cfg)
    execute_with_provider_checkpoint(
        cp["checkpoint_id"],
        provider="anthropic",
        model="claude-test",
        operation=lambda: generate_answer("q", CHUNKS, model_provider="anthropic"),
        cfg=cfg,
    )
    paused = get_checkpoint(cp["checkpoint_id"], cfg)

    decision = plan_provider_failover(paused, failover_chain=["Anthropic", "mock", "anthropic"])

    assert get_checkpoint(cp["checkpoint_id"], cfg) == paused
    assert decision["status"] == "FAILOVER_READY"
    assert decision["failover_chain"] == ["anthropic", "mock"]
    assert decision["failed_provider"] == "anthropic"
    assert decision["candidates"][0]["reasons"] == ["FAILED_PROVIDER"]
    assert decision["selected_provider"] == "mock"
    assert all(decision["invariants"].values())
    assert len(decision["decision_sha256"]) == 64

    resumed = resume_with_failover(cp["checkpoint_id"], failover_chain=["anthropic", "mock"], cfg=cfg)

    assert resumed["state"] == "EXECUTING"
    assert resumed["pause_reason"] is None
    assert resumed["provider"] == "mock"
    assert resumed["model"] == MODEL_PROVIDER_SPECS["mock"].default_model
    assert resumed["sequence"] == paused["sequence"] + 1
    assert_canonical_state_preserved(resumed, cp)
    events = resumed["metadata"]["provider_events"]
    assert [event["event"] for event in events] == ["PROVIDER_INTERRUPTED", "PROVIDER_RESUMED"]
    assert events[1]["selection"] == "FAILOVER"
    assert (events[1]["from_provider"], events[1]["to_provider"]) == ("anthropic", "mock")
    assert len(events[1]["route_sha256"]) == 64
    assert events[1]["failover_decision_sha256"] == decision["decision_sha256"]
    assert events[1]["checkpoint_sequence"] == resumed["sequence"]

    continued = execute_with_provider_checkpoint(
        cp["checkpoint_id"],
        provider=resumed["provider"],
        model=resumed["model"],
        operation=lambda: generate_answer("q", CHUNKS, model_provider=resumed["provider"]),
        cfg=cfg,
    )
    assert continued["status"] == "PROVIDER_EXECUTION_OK"
    assert continued["result"]["cited_chunk_ids"] == ["chunk_a"]


def test_failover_never_escalates_offline_work_to_live_without_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_settings(tmp_path)
    install_fake_anthropic(monkeypatch, text="chunk_a")
    cp = executing_checkpoint(cfg, provider="mock", model="mock")
    execute_with_provider_checkpoint(cp["checkpoint_id"], provider="mock", model="mock", operation=outage(), cfg=cfg)
    paused = get_checkpoint(cp["checkpoint_id"], cfg)

    with pytest.raises(ProviderRecoveryError) as exc:
        resume_with_failover(cp["checkpoint_id"], failover_chain=["mock", "anthropic"], cfg=cfg)
    assert exc.value.status == "ERROR_PROVIDER_FAILOVER_EXHAUSTED"
    assert "anthropic:LIVE_PROVIDER_NOT_EXPLICITLY_ALLOWED" in exc.value.detail
    assert get_checkpoint(cp["checkpoint_id"], cfg) == paused

    with pytest.raises(ProviderRecoveryError) as explicit:
        resume_with_provider(cp["checkpoint_id"], provider="anthropic", cfg=cfg)
    assert explicit.value.status == "ERROR_PROVIDER_ROUTE_BLOCKED"
    assert get_checkpoint(cp["checkpoint_id"], cfg) == paused

    # Positive control: the same ready live provider is reachable once live use is explicit.
    resumed = resume_with_failover(cp["checkpoint_id"], failover_chain=["mock", "anthropic"], allow_live=True, cfg=cfg)
    assert resumed["provider"] == "anthropic"
    assert resumed["metadata"]["provider_events"][-1]["to_provider"] == "anthropic"


def test_resume_rejects_unknown_and_unready_providers_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.delenv(MODEL_PROVIDER_SPECS["anthropic"].api_key_env, raising=False)
    cp = executing_checkpoint(cfg)
    execute_with_provider_checkpoint(cp["checkpoint_id"], provider="anthropic", model="a", operation=outage(), cfg=cfg)
    paused = get_checkpoint(cp["checkpoint_id"], cfg)

    with pytest.raises(ProviderRecoveryError) as unknown:
        resume_with_provider(cp["checkpoint_id"], provider="other", cfg=cfg)
    assert unknown.value.status == "ERROR_UNSUPPORTED_MODEL_PROVIDER"

    with pytest.raises(ProviderRecoveryError) as unknown_chain:
        plan_provider_failover(paused, failover_chain=["mock", "other"])
    assert unknown_chain.value.status == "ERROR_UNSUPPORTED_MODEL_PROVIDER"

    with pytest.raises(ProviderRecoveryError) as empty_chain:
        resume_with_failover(cp["checkpoint_id"], failover_chain=[], cfg=cfg)
    assert empty_chain.value.status == "ERROR_PROVIDER_FAILOVER_CHAIN"

    with pytest.raises(ProviderRecoveryError) as unready:
        resume_with_provider(cp["checkpoint_id"], provider="anthropic", allow_live=True, cfg=cfg)
    assert unready.value.status == "ERROR_PROVIDER_ROUTE_BLOCKED"
    assert unready.value.detail == "anthropic:PREFERRED_PROVIDER_NOT_ELIGIBLE"

    with pytest.raises(ProviderRecoveryError) as disallowed:
        resume_with_provider(cp["checkpoint_id"], provider="mock", allowed_providers=["anthropic"], cfg=cfg)
    assert disallowed.value.status == "ERROR_MODEL_ROUTING_PROVIDER_NOT_ALLOWED"

    assert get_checkpoint(cp["checkpoint_id"], cfg) == paused


def test_provider_work_requires_executing_state_and_never_runs_otherwise(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    calls: list[str] = []

    def operation():
        calls.append("called")
        return {}

    cp = executing_checkpoint(cfg)
    execute_with_provider_checkpoint(cp["checkpoint_id"], provider="anthropic", model="a", operation=outage(), cfg=cfg)
    with pytest.raises(ProviderRecoveryError) as paused_exc:
        execute_with_provider_checkpoint(cp["checkpoint_id"], provider="mock", model=None, operation=operation, cfg=cfg)
    assert paused_exc.value.status == "ERROR_CHECKPOINT_NOT_EXECUTING"

    done = executing_checkpoint(cfg, run_id="run-v04-done")
    transition_checkpoint(done["checkpoint_id"], state="COMPLETED", cfg=cfg)
    with pytest.raises(ProviderRecoveryError) as terminal_exc:
        execute_with_provider_checkpoint(done["checkpoint_id"], provider="mock", model=None, operation=operation, cfg=cfg)
    assert terminal_exc.value.status == "ERROR_CHECKPOINT_NOT_EXECUTING"
    assert calls == []

    running = executing_checkpoint(cfg, run_id="run-v04-running")
    with pytest.raises(ProviderRecoveryError) as not_paused:
        resume_with_failover(running["checkpoint_id"], failover_chain=["mock"], cfg=cfg)
    assert not_paused.value.status == "ERROR_CHECKPOINT_NOT_PROVIDER_PAUSED"

    with pytest.raises(ProviderRecoveryError) as missing:
        resume_with_failover("checkpoint_missing", failover_chain=["mock"], cfg=cfg)
    assert missing.value.status == "ERROR_CHECKPOINT_NOT_FOUND"


def test_non_interruption_adapter_failures_propagate_without_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_settings(tmp_path)
    for index, (error, status) in enumerate(
        (
            (BadRequestError(SENSITIVE, 400), "ERROR_INVALID_MODEL_REQUEST"),
            (ValueError(SENSITIVE), "ERROR_MODEL_PROVIDER_FAILURE"),
        )
    ):
        install_fake_anthropic(monkeypatch, error=error)
        cp = executing_checkpoint(cfg, run_id=f"run-v04-{index}")
        with pytest.raises(ModelProviderError) as exc:
            execute_with_provider_checkpoint(
                cp["checkpoint_id"],
                provider="anthropic",
                model="a",
                operation=lambda: generate_answer("q", CHUNKS, model_provider="anthropic"),
                cfg=cfg,
            )
        assert exc.value.status == status
        assert get_checkpoint(cp["checkpoint_id"], cfg) == cp


def test_annotations_cannot_rewrite_checkpoint_identity(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = executing_checkpoint(cfg)

    with pytest.raises(CheckpointError) as identity:
        transition_checkpoint(
            cp["checkpoint_id"],
            state="PAUSED_PROVIDER",
            pause_reason="x",
            annotations={"session_id": "hijacked", "provider": "mock"},
            cfg=cfg,
        )
    assert identity.value.status == "ERROR_CHECKPOINT_FIELD"

    with pytest.raises(CheckpointError) as metadata:
        transition_checkpoint(
            cp["checkpoint_id"], state="PAUSED_PROVIDER", pause_reason="x", annotations={"metadata": []}, cfg=cfg
        )
    assert metadata.value.status == "ERROR_CHECKPOINT_FIELD"
    assert get_checkpoint(cp["checkpoint_id"], cfg) == cp


def test_repeated_outage_cycles_keep_append_only_sequence_bound_events(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = executing_checkpoint(cfg)

    execute_with_provider_checkpoint(
        cp["checkpoint_id"], provider="anthropic", model="a", operation=outage("ERROR_MODEL_PROVIDER_RATE_LIMIT"), cfg=cfg
    )
    resume_with_failover(cp["checkpoint_id"], failover_chain=["anthropic", "mock"], cfg=cfg)
    execute_with_provider_checkpoint(cp["checkpoint_id"], provider="mock", model="mock", operation=outage(), cfg=cfg)
    final = resume_with_provider(cp["checkpoint_id"], provider="mock", cfg=cfg)

    events = final["metadata"]["provider_events"]
    assert [(event["event"], event.get("selection")) for event in events] == [
        ("PROVIDER_INTERRUPTED", None),
        ("PROVIDER_RESUMED", "FAILOVER"),
        ("PROVIDER_INTERRUPTED", None),
        ("PROVIDER_RESUMED", "EXPLICIT"),
    ]
    assert [event["checkpoint_sequence"] for event in events] == [1, 2, 3, 4]
    assert final["sequence"] == 4
    assert final["state"] == "EXECUTING"
    assert (events[3]["from_provider"], events[3]["to_provider"]) == ("mock", "mock")
    assert_canonical_state_preserved(final, cp)


def _run(root: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MUCARO_AI_ROOT"] = str(root)
    env["PYTHONPATH"] = str(REPO)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_outage_pause_and_failover_survive_process_boundaries(tmp_path: Path) -> None:
    writer = _run(
        tmp_path,
        f"""
import json
from backend.app.agent_checkpoints import create_checkpoint
from backend.app.config import Settings
from backend.app.model_router import ModelProviderError
from backend.app.provider_recovery import execute_with_provider_checkpoint
cfg = Settings.load()
cp = create_checkpoint(
    session_id="session-v04-process",
    run_id="run-v04-process",
    objective="Survive provider outage",
    source_sha="{SHA}",
    state="EXECUTING",
    completed_steps={COMPLETED!r},
    pending_steps={PENDING!r},
    contradictions={CONTRADICTIONS!r},
    provider="anthropic",
    model="claude-test",
    cfg=cfg,
)
def operation():
    raise ModelProviderError("ERROR_MODEL_PROVIDER_UNAVAILABLE", "outage")
result = execute_with_provider_checkpoint(
    cp["checkpoint_id"], provider="anthropic", model="claude-test", operation=operation, cfg=cfg
)
print(json.dumps(result["checkpoint"], sort_keys=True))
""",
    )
    paused = json.loads(writer.stdout)
    assert paused["state"] == "PAUSED_PROVIDER"

    reader = _run(
        tmp_path,
        f"""
import json
from backend.app.config import Settings
from backend.app.provider_recovery import resume_with_failover
resumed = resume_with_failover("{paused['checkpoint_id']}", failover_chain=["anthropic", "mock"], cfg=Settings.load())
print(json.dumps(resumed, sort_keys=True))
""",
    )
    resumed = json.loads(reader.stdout)

    for field in ("checkpoint_id", "session_id", "run_id", "objective", "source_sha", "created_at"):
        assert resumed[field] == paused[field]
    assert resumed["completed_steps"] == COMPLETED
    assert resumed["pending_steps"] == PENDING
    assert resumed["contradictions"] == CONTRADICTIONS
    assert resumed["state"] == "EXECUTING"
    assert resumed["provider"] == "mock"
    assert resumed["sequence"] == paused["sequence"] + 1
    assert resumed["metadata"]["provider_events"][0] == paused["metadata"]["provider_events"][0]
    assert resumed["metadata"]["provider_events"][1]["selection"] == "FAILOVER"

    verify = _run(
        tmp_path,
        f"""
import json
from backend.app.agent_checkpoints import get_checkpoint
from backend.app.config import Settings
print(json.dumps(get_checkpoint("{paused['checkpoint_id']}", Settings.load()), sort_keys=True))
""",
    )
    assert json.loads(verify.stdout) == resumed
