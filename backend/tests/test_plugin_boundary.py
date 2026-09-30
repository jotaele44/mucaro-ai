import json
from pathlib import Path

import pytest

from backend.app.capability_policy import CapabilityPolicy
from backend.app.plugin_boundary import (
    MANIFEST_NAME, SCHEMA_VERSION, PluginBoundaryError, authorize_snapshot, inspect_plugin, inspect_store,
)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    return root


def make_plugin(store: Path, folder="one", plugin_id="org.example.sample", capabilities=None, network="none"):
    root = store / folder
    root.mkdir()
    (root / "plugin.py").write_text("print('not executed')\n", encoding="utf-8")
    data = {"schema_version": SCHEMA_VERSION, "id": plugin_id, "version": "1.0.0", "entrypoint": "plugin.py",
            "capabilities": [] if capabilities is None else capabilities, "network": network}
    (root / MANIFEST_NAME).write_text(json.dumps(data), encoding="utf-8")
    return root, data


def policy(*allowed):
    return CapabilityPolicy.build(policy_id="offline-test-v1", allowed=allowed)


def status(exc, target):
    assert exc.value.status == target


def test_local_inspection_only_and_exact_reapproval(store):
    root, _ = make_plugin(store)
    result = inspect_plugin(root, store, policy=policy())
    assert result["status"] == "INSPECTED_FOR_APPROVAL"
    assert result["execution_certified"] is False
    receipt = authorize_snapshot(root, store, policy=policy(), expected_sha256=result["snapshot_sha256"],
                                 explicit_operator_approval=True)
    assert receipt["status"] == "APPROVED_SNAPSHOT_ONLY"
    assert receipt["execution_certified"] is False
    with pytest.raises(PluginBoundaryError) as exc:
        authorize_snapshot(root, store, policy=policy(), expected_sha256=result["snapshot_sha256"],
                           explicit_operator_approval=False)
    status(exc, "APPROVAL_REQUIRED")


def test_mismatched_code_bytes_fails_stale_approval(store):
    root, _ = make_plugin(store)
    old = inspect_plugin(root, store, policy=policy())["snapshot_sha256"]
    (root / "plugin.py").write_text("print('tampered')\n")
    assert inspect_plugin(root, store, policy=policy())["snapshot_sha256"] != old
    with pytest.raises(PluginBoundaryError) as exc:
        authorize_snapshot(root, store, policy=policy(), expected_sha256=old, explicit_operator_approval=True)
    status(exc, "STALE_PLUGIN_SNAPSHOT")


def test_mismatched_policy_fails_stale_approval(store):
    root, _ = make_plugin(store)
    old = inspect_plugin(root, store, policy=policy())["snapshot_sha256"]
    with pytest.raises(PluginBoundaryError) as exc:
        authorize_snapshot(root, store, policy=CapabilityPolicy.build(policy_id="changed"),
                           expected_sha256=old, explicit_operator_approval=True)
    status(exc, "STALE_PLUGIN_SNAPSHOT")


def test_remote_plugin_blocked_offline_and_requires_policy(store):
    root, _ = make_plugin(store, capabilities=["network.public_http"], network="remote")
    blocked = inspect_plugin(root, store, policy=policy("network.public_http"), offline=True)
    assert blocked["status"] == "BLOCKED"
    assert blocked["offline_denied"]
    denied = inspect_plugin(root, store, policy=policy(), offline=False)
    assert denied["status"] == "BLOCKED"
    permitted = inspect_plugin(root, store, policy=policy("network.public_http"), offline=False)
    assert permitted["status"] == "INSPECTED_FOR_APPROVAL"


def test_duplicate_identity_not_silently_selected(store):
    make_plugin(store, "alpha")
    make_plugin(store, "beta")
    result = inspect_store(store, policy=policy())
    assert result["source_count"] == 2 and result["arithmetic_closes"]
    assert result["duplicate_ids"] == ["org.example.sample"]
    assert [p["status"] for p in result["candidates"]] == ["UNRESOLVED_DUPLICATE_PLUGIN_ID"] * 2


def test_unique_store_arithmetic_and_invalid_member_preservation(store):
    make_plugin(store)
    (store / "malicious").symlink_to(store / "one", target_is_directory=True)
    result = inspect_store(store, policy=policy())
    assert result["source_count"] == 2 and result["arithmetic_closes"]
    assert result["counts"] == {"INVALID": 1, "INSPECTED_FOR_APPROVAL": 1}


def test_manifest_duplicate_json_key_rejected(store):
    root, _ = make_plugin(store)
    (root / MANIFEST_NAME).write_text('{"id":"a.b","id":"a.c"}')
    with pytest.raises(PluginBoundaryError) as exc:
        inspect_plugin(root, store, policy=policy())
    status(exc, "INVALID_DUPLICATE_JSON_KEY")


@pytest.mark.parametrize("change,target", [
    ({"id": "Similar Unicode․ID"}, "INVALID_PLUGIN_ID"),
    ({"id": "acme"}, "INVALID_PLUGIN_ID"),
    ({"entrypoint": "../plugin.py"}, "INVALID_ENTRYPOINT"),
    ({"entrypoint": "other.py"}, "ENTRYPOINT_MISSING"),
    ({"capabilities": ["filesystem.read", "filesystem.read"]}, "INVALID_CAPABILITIES"),
    ({"capabilities": ["remote.exec"]}, "UNKNOWN_CAPABILITY"),
    ({"capabilities": ["network.public_http"], "network": "none"}, "NETWORK_CLASS_CONTRADICTION"),
    ({"network": "remote"}, "NETWORK_CLASS_CONTRADICTION"),
    ({"version": "01.0.0"}, "INVALID_PLUGIN_VERSION"),
    ({"schema_version": "mucaro-independent-plugin/2"}, "INVALID_SCHEMA_VERSION"),
])
def test_invalid_manifest_fail_closed(store, change, target):
    root, data = make_plugin(store)
    data.update(change)
    (root / MANIFEST_NAME).write_text(json.dumps(data))
    with pytest.raises(PluginBoundaryError) as exc:
        inspect_plugin(root, store, policy=policy())
    status(exc, target)


def test_extra_field_rejected(store):
    root, data = make_plugin(store)
    data["unknown"] = 1
    (root / MANIFEST_NAME).write_text(json.dumps(data))
    with pytest.raises(PluginBoundaryError) as exc:
        inspect_plugin(root, store, policy=policy())
    status(exc, "INVALID_MANIFEST_FIELDS")


def test_symlinked_entrypoint_fails_closed(store):
    root, _ = make_plugin(store)
    (root / "plugin.py").unlink()
    (root / "plugin.py").symlink_to("/etc/passwd")
    with pytest.raises(PluginBoundaryError) as exc:
        inspect_plugin(root, store, policy=policy())
    status(exc, "INVALID_PLUGIN_MEMBER")


def test_plugin_root_outside_store_rejected(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PluginBoundaryError) as exc:
        inspect_plugin(outside, store, policy=policy())
    status(exc, "INVALID_PLUGIN_ROOT")


def test_resource_limit_directories_and_member_path(store):
    root, _ = make_plugin(store)
    (root / ("x" * 241)).write_text("x")
    with pytest.raises(PluginBoundaryError) as exc:
        inspect_plugin(root, store, policy=policy())
    status(exc, "INVALID_PLUGIN_MEMBER")


def test_store_explicit_exclusion_conservation(store):
    make_plugin(store)
    (store / "README.txt").write_text("not a plugin")
    result = inspect_store(store, policy=policy())
    assert result["source_count"] == 2
    assert result["retained_count"] == 1 and result["excluded_count"] == 1
    assert result["arithmetic_closes"]


def test_offline_flag_rejects_ambiguous_string(store):
    root, _ = make_plugin(store)
    with pytest.raises(PluginBoundaryError) as exc:
        inspect_plugin(root, store, policy=policy(), offline="False")
    status(exc, "INVALID_OFFLINE_FLAG")


def test_duplicate_id_blocks_individual_approval(store):
    original, _ = make_plugin(store, folder="original")
    inspected = inspect_plugin(original, store, policy=policy())
    make_plugin(store, folder="different_folder")
    with pytest.raises(PluginBoundaryError) as exc:
        authorize_snapshot(original, store, policy=policy(), expected_sha256=inspected["snapshot_sha256"],
                           explicit_operator_approval=True)
    status(exc, "DUPLICATE_PLUGIN_ID")


def test_snapshot_binds_policy_entitlements_not_just_policy_name(store):
    original, _ = make_plugin(store, capabilities=["filesystem.read"])
    original_policy = policy("filesystem.read")
    old = inspect_plugin(original, store, policy=original_policy)["snapshot_sha256"]
    changed_policy = policy("filesystem.read", "filesystem.write")
    assert changed_policy.policy_id == original_policy.policy_id
    assert inspect_plugin(original, store, policy=changed_policy)["snapshot_sha256"] != old
    with pytest.raises(PluginBoundaryError) as exc:
        authorize_snapshot(original, store, policy=changed_policy, expected_sha256=old,
                           explicit_operator_approval=True)
    status(exc, "STALE_PLUGIN_SNAPSHOT")


def test_snapshot_binds_empty_directories_not_just_file_hashes(store):
    original, _ = make_plugin(store)
    old = inspect_plugin(original, store, policy=policy())["snapshot_sha256"]
    (original / "empty-subdirectory").mkdir()
    with pytest.raises(PluginBoundaryError) as exc:
        authorize_snapshot(original, store, policy=policy(), expected_sha256=old,
                           explicit_operator_approval=True)
    status(exc, "STALE_PLUGIN_SNAPSHOT")


def test_plugin_store_child_denominator_is_bounded_without_truncation(store):
    from backend.app.plugin_boundary import MAX_STORE_CHILDREN
    for count in range(MAX_STORE_CHILDREN + 1):
        (store / f"ignored-{count:04}.txt").touch()
    with pytest.raises(PluginBoundaryError) as exc:
        inspect_store(store, policy=policy())
    status(exc, "PLUGIN_STORE_RESOURCE_LIMIT")


def test_single_plugin_approval_rechecks_store_denominator(store):
    original, _ = make_plugin(store)
    old = inspect_plugin(original, store, policy=policy())["snapshot_sha256"]
    from backend.app.plugin_boundary import MAX_STORE_CHILDREN
    for count in range(MAX_STORE_CHILDREN):
        (store / f"ignored-{count:04}.txt").touch()
    with pytest.raises(PluginBoundaryError) as exc:
        authorize_snapshot(original, store, policy=policy(), expected_sha256=old,
                           explicit_operator_approval=True)
    status(exc, "PLUGIN_STORE_RESOURCE_LIMIT")
