"""Attachment path-containment adversarial tests for inactive Claude integration."""
from __future__ import annotations

import json

import pytest

from backend.app.attachments import (
    AttachmentError,
    attachment_file,
    delete_conversation_attachments,
    get_attachment,
    list_attachments,
    save_attachment,
)
from backend.tests.test_api import make_settings


def test_reference_cannot_traverse_other_conversations(tmp_path):
    cfg = make_settings(tmp_path)
    entry = save_attachment("conv_a", "a.txt", b"private", cfg)
    with pytest.raises(AttachmentError):
        attachment_file("conv_b", entry["attachment_id"], cfg)
    with pytest.raises(AttachmentError):
        attachment_file("conv_a", "att_../../etc/passwd", cfg)


def test_payload_symlink_is_rejected(tmp_path):
    cfg = make_settings(tmp_path)
    entry = save_attachment("conv_a", "a.txt", b"original", cfg)
    file_path, _ = attachment_file("conv_a", entry["attachment_id"], cfg)
    target = tmp_path / "outside"
    target.write_text("secret")
    file_path.unlink()
    file_path.symlink_to(target)
    with pytest.raises(AttachmentError, match="not trusted"):
        attachment_file("conv_a", entry["attachment_id"], cfg)
    assert target.read_text() == "secret"


def test_metadata_cannot_escape_attachment_folder(tmp_path):
    cfg = make_settings(tmp_path)
    entry = save_attachment("conv_a", "a.txt", b"original", cfg)
    folder = cfg.data_dir / "attachments" / "conv_a" / entry["attachment_id"]
    meta = json.loads((folder / "meta.json").read_text())
    meta["stored_name"] = "../../outside"
    (folder / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(AttachmentError, match="invalid"):
        attachment_file("conv_a", entry["attachment_id"], cfg)


def test_root_symlink_rejects_read_write_and_deletion(tmp_path):
    cfg = make_settings(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    root = cfg.data_dir / "attachments"
    root.mkdir(parents=True)
    (root / "conv_a").symlink_to(outside, target_is_directory=True)
    for op in (
        lambda: save_attachment("conv_a", "a.txt", b"hello", cfg),
        lambda: list_attachments("conv_a", cfg),
        lambda: delete_conversation_attachments("conv_a", cfg),
    ):
        with pytest.raises(AttachmentError, match="not trusted"):
            op()
    assert list(outside.iterdir()) == []


def test_metadata_symlink_is_rejected(tmp_path):
    cfg = make_settings(tmp_path)
    entry = save_attachment("conv_a", "a.txt", b"original", cfg)
    folder = cfg.data_dir / "attachments" / "conv_a" / entry["attachment_id"]
    external = tmp_path / "external-metadata.json"
    external.write_text((folder / "meta.json").read_text())
    (folder / "meta.json").unlink()
    (folder / "meta.json").symlink_to(external)
    with pytest.raises(AttachmentError, match="not trusted"):
        get_attachment("conv_a", entry["attachment_id"], cfg)
