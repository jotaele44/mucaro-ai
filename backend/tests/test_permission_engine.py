from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.capability_policy import KNOWN_CAPABILITIES, CapabilityPolicy
from backend.app.config import Settings
from backend.app.permission_engine import (
    ALLOW,
    ASK,
    DENY,
    PermissionEngineError,
    PermissionRequest,
    evaluate,
    require,
)
from backend.app.permissions import grant_directory


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


@pytest.fixture()
def cfg(tmp_path: Path) -> Settings:
    root = tmp_path / "project"
    root.mkdir()
    return make_settings(root)


def _req(capability: str, **kw) -> PermissionRequest:
    return PermissionRequest(capability=capability, subject="test-subject", **kw)


def test_new_taxonomy_classes_are_known() -> None:
    assert {"git.read", "git.mutate", "mcp.invoke", "subagent.spawn"} <= KNOWN_CAPABILITIES


def test_unknown_capability_is_denied(cfg: Settings) -> None:
    assert evaluate(_req("teleport.anywhere"), cfg=cfg).decision == DENY


def test_unknown_origin_is_denied(cfg: Settings) -> None:
    assert evaluate(_req("model.invoke", origin="root"), cfg=cfg).decision == DENY


def test_read_only_capability_is_allowed(cfg: Settings) -> None:
    assert evaluate(_req("model.invoke"), cfg=cfg).decision == ALLOW


def test_side_effecting_agent_request_needs_approval(cfg: Settings) -> None:
    decision = evaluate(_req("subprocess.execute", origin="agent"), cfg=cfg)
    assert decision.decision == ASK


def test_explicit_approval_or_operator_origin_allows(cfg: Settings) -> None:
    assert evaluate(_req("subprocess.execute", origin="agent", approved=True), cfg=cfg).decision == ALLOW
    assert evaluate(_req("subprocess.execute", origin="operator"), cfg=cfg).decision == ALLOW


def test_planning_never_authorizes_execution_even_if_flagged_approved(cfg: Settings) -> None:
    for capability in ("subprocess.execute", "git.mutate", "network.public_http", "mcp.invoke", "subagent.spawn"):
        decision = evaluate(_req(capability, origin="planning", approved=True), cfg=cfg)
        assert decision.decision == ASK, capability


def test_explicit_policy_deny_beats_operator_origin(cfg: Settings) -> None:
    policy = CapabilityPolicy.build(policy_id="p", allowed=KNOWN_CAPABILITIES, denied=["git.mutate"])
    decision = evaluate(_req("git.mutate", origin="operator"), cfg=cfg, policy=policy)
    assert decision.decision == DENY


def test_capability_outside_allowlist_is_denied(cfg: Settings) -> None:
    policy = CapabilityPolicy.build(policy_id="p", allowed=["model.invoke"])
    assert evaluate(_req("subprocess.execute", origin="operator"), cfg=cfg, policy=policy).decision == DENY


def test_undeclared_escalation_is_denied(cfg: Settings) -> None:
    request = _req("subprocess.execute", origin="operator", declared=frozenset({"model.invoke"}))
    assert evaluate(request, cfg=cfg).decision == DENY


def test_mac_app_store_denies_subprocess_even_for_operator(cfg: Settings) -> None:
    store_cfg = replace(cfg, distribution_channel="mac_app_store")
    decision = evaluate(_req("subprocess.execute", origin="operator"), cfg=store_cfg)
    assert decision.decision == DENY
    assert "Mac App Store" in decision.reasons[0]


def test_unsupported_distribution_channel_is_denied(cfg: Settings) -> None:
    assert evaluate(_req("model.invoke"), cfg=replace(cfg, distribution_channel="sideload")).decision == DENY


def test_filesystem_requires_target(cfg: Settings) -> None:
    assert evaluate(_req("filesystem.read"), cfg=cfg).decision == DENY


def test_filesystem_inside_project_is_allowed(cfg: Settings) -> None:
    target = cfg.project_root / "notes.txt"
    target.write_text("x")
    assert evaluate(_req("filesystem.read", target=str(target)), cfg=cfg).decision == ALLOW


def test_filesystem_outside_project_without_grant_is_denied(cfg: Settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    assert evaluate(_req("filesystem.read", target=str(outside)), cfg=cfg).decision == DENY


def test_filesystem_grant_allows_and_traversal_out_of_grant_is_denied(cfg: Settings, tmp_path: Path) -> None:
    granted = tmp_path / "granted"
    sibling = tmp_path / "sibling"
    granted.mkdir()
    sibling.mkdir()
    cfg.ensure_dirs()
    grant_directory(granted, cfg, granted_by="test")
    assert evaluate(_req("filesystem.read", target=str(granted)), cfg=cfg).decision == ALLOW
    escape = granted / ".." / "sibling"
    assert evaluate(_req("filesystem.read", target=str(escape)), cfg=cfg).decision == DENY


def test_symlink_escape_out_of_grant_is_denied(cfg: Settings, tmp_path: Path) -> None:
    granted = tmp_path / "granted"
    secret = tmp_path / "secret"
    granted.mkdir()
    secret.mkdir()
    (secret / "token.txt").write_text("s")
    link = granted / "link"
    link.symlink_to(secret)
    cfg.ensure_dirs()
    grant_directory(granted, cfg, granted_by="test")
    assert evaluate(_req("filesystem.read", target=str(link / "token.txt")), cfg=cfg).decision == DENY


def test_write_to_new_file_checks_nearest_existing_ancestor(cfg: Settings, tmp_path: Path) -> None:
    inside = cfg.project_root / "new" / "deeper" / "file.txt"
    assert evaluate(_req("filesystem.write", target=str(inside), origin="operator"), cfg=cfg).decision == ALLOW
    outside = tmp_path / "elsewhere" / "file.txt"
    assert evaluate(_req("filesystem.write", target=str(outside), origin="operator"), cfg=cfg).decision == DENY


def test_tampered_grant_ledger_fails_closed(cfg: Settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    cfg.ensure_dirs()
    from backend.app.permissions import permission_ledger_path

    permission_ledger_path(cfg).write_text('{"not": "a list"}')
    assert evaluate(_req("filesystem.read", target=str(outside)), cfg=cfg).decision == DENY


def test_require_raises_typed_error_with_decision(cfg: Settings) -> None:
    with pytest.raises(PermissionEngineError) as denied:
        require(_req("teleport.anywhere"), cfg=cfg)
    assert denied.value.status == "ERROR_PERMISSION_DENIED"
    with pytest.raises(PermissionEngineError) as asked:
        require(_req("git.mutate", origin="agent"), cfg=cfg)
    assert asked.value.status == "ERROR_PERMISSION_APPROVAL_REQUIRED"
    assert asked.value.decision.decision == ASK


def test_decision_serializes_with_schema_version(cfg: Settings) -> None:
    payload = evaluate(_req("model.invoke"), cfg=cfg).as_dict()
    assert payload["schema_version"] == "mucaro-permission-decision/1.0"
    assert payload["decision"] == ALLOW


def test_decisions_validate_against_published_schema(cfg: Settings) -> None:
    import json

    import jsonschema

    schema = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "permission_decision.schema.json").read_text())
    for request in (_req("model.invoke"), _req("git.mutate", origin="planning"), _req("teleport.anywhere")):
        jsonschema.validate(evaluate(request, cfg=cfg).as_dict(), schema)


@pytest.mark.parametrize("capability", ["git.read", "git.mutate"])
def test_git_capabilities_inherit_subprocess_denial_on_app_store(cfg: Settings, capability: str) -> None:
    decision = evaluate(_req(capability, origin="operator"), cfg=replace(cfg, distribution_channel="mac_app_store"))
    assert decision.decision == DENY
    assert "Mac App Store" in decision.reasons[0]
