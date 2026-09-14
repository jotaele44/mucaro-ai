from __future__ import annotations

import pytest

from backend.app.container_runtime import (
    CONTAINER_IMAGE_BINDINGS,
    ContainerBinding,
    ContainerRuntimeError,
    build_container_command,
    get_container_binding,
    validate_pinned_image,
)


PINNED = "ghcr.io/jotaele44/mucaro-worker@sha256:" + "a" * 64


def test_pinned_image_is_required() -> None:
    assert validate_pinned_image(PINNED) == PINNED
    for invalid in (
        "ghcr.io/jotaele44/mucaro-worker:latest",
        "ghcr.io/jotaele44/mucaro-worker@sha256:abc",
        "ghcr.io/jotaele44/mucaro-worker@sha256:" + "A" * 64,
    ):
        with pytest.raises(ContainerRuntimeError) as exc:
            validate_pinned_image(invalid)
        assert exc.value.status == "ERROR_CONTAINER_IMAGE_NOT_PINNED"


def test_container_binding_has_deterministic_digest(monkeypatch) -> None:
    binding = ContainerBinding(skill_id="test/container", image=PINNED)
    assert binding.digest == binding.digest
    assert len(binding.digest) == 64
    monkeypatch.setitem(CONTAINER_IMAGE_BINDINGS, "test/container", binding)
    resolved = get_container_binding("test/container")
    assert resolved.as_dict()["image"] == PINNED
    assert resolved.as_dict()["binding_sha256"] == binding.digest


def test_missing_container_binding_fails_closed() -> None:
    with pytest.raises(ContainerRuntimeError) as exc:
        get_container_binding("missing/container")
    assert exc.value.status == "ERROR_CONTAINER_BINDING_MISSING"


def test_secure_container_command_defaults_to_no_network_and_read_only() -> None:
    command = build_container_command(
        engine="/usr/bin/docker",
        image=PINNED,
        network_allowed=False,
        memory_mb=512,
        cpus=1.0,
        pids_limit=64,
    )
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--network=none" in command
    assert "--memory=512m" in command
    assert "--pids-limit=64" in command
    assert PINNED in command


def test_network_mode_requires_explicit_allowance() -> None:
    command = build_container_command(
        engine="podman",
        image=PINNED,
        network_allowed=True,
        memory_mb=512,
        cpus=1.0,
        pids_limit=64,
    )
    assert "--network=bridge" in command
