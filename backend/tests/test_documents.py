from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.db import init_db, schema_version
from backend.app.documents import (
    DocumentError,
    create_comment,
    create_document,
    get_document,
    list_comments,
    list_documents,
    list_revisions,
    set_comment_state,
    update_document,
    store_asset,
    get_asset,
    list_assets,
)


def cfg(tmp_path: Path) -> Settings:
    return Settings(project_root=tmp_path, data_dir=tmp_path / "data", sqlite_path=tmp_path / "data" / "mucaro.sqlite")


def test_schema_upgrade_and_document_round_trip(tmp_path):
    settings = cfg(tmp_path)
    settings.ensure_dirs()
    init_db(settings.sqlite_path)
    assert schema_version(settings.sqlite_path) == 4

    created = create_document("Research plan", initial_text="First paragraph", cfg=settings)
    assert created["revision"] == 1
    assert created["document"]["tabs"][0]["blocks"][0]["text"] == "First paragraph"
    assert len(list_documents(cfg=settings)) == 1

    doc = created["document"]
    tab = doc["tabs"][0]
    tab["blocks"].append({"block_id": "blk_check", "type": "checklist", "items": [{"item_id": "item_1", "text": "Verify source", "checked": False}]})
    saved = update_document(created["document_id"], doc, 1, cfg=settings)
    assert saved["revision"] == 2
    assert list_revisions(created["document_id"], cfg=settings)[0]["revision"] == 2


def test_revision_conflict_fails_closed(tmp_path):
    settings = cfg(tmp_path)
    settings.ensure_dirs()
    init_db(settings.sqlite_path)
    created = create_document("Conflict", cfg=settings)
    doc = created["document"]
    doc["tabs"][0]["blocks"].append({"block_id": "blk_a", "type": "paragraph", "text": "A"})
    update_document(created["document_id"], doc, 1, cfg=settings)
    with pytest.raises(DocumentError, match="Expected revision 1, found 2"):
        update_document(created["document_id"], doc, 1, cfg=settings)


def test_duplicate_ids_remote_images_and_missing_section_children_rejected(tmp_path):
    settings = cfg(tmp_path)
    settings.ensure_dirs()
    init_db(settings.sqlite_path)
    created = create_document("Validation", cfg=settings)

    dup = created["document"]
    dup["tabs"][0]["blocks"] = [
        {"block_id": "same", "type": "paragraph", "text": "A"},
        {"block_id": "same", "type": "paragraph", "text": "B"},
    ]
    with pytest.raises(DocumentError) as exc:
        update_document(created["document_id"], dup, 1, cfg=settings)
    assert exc.value.status == "ERROR_DOCUMENT_DUPLICATE_BLOCK_ID"

    bad_image = get_document(created["document_id"], cfg=settings)["document"]
    bad_image["tabs"][0]["blocks"] = [{"block_id": "img1", "type": "image", "src": "https://remote.example/image.png", "caption": ""}]
    with pytest.raises(DocumentError) as exc:
        update_document(created["document_id"], bad_image, 1, cfg=settings)
    assert exc.value.status == "ERROR_DOCUMENT_REMOTE_IMAGE"

    missing = get_document(created["document_id"], cfg=settings)["document"]
    missing["tabs"][0]["blocks"] = [{"block_id": "sec1", "type": "section", "title": "Section", "children": ["does_not_exist"]}]
    with pytest.raises(DocumentError) as exc:
        update_document(created["document_id"], missing, 1, cfg=settings)
    assert exc.value.status == "ERROR_DOCUMENT_SECTION_CHILD_MISSING"


def test_comment_anchor_and_state(tmp_path):
    settings = cfg(tmp_path)
    settings.ensure_dirs()
    init_db(settings.sqlite_path)
    created = create_document("Comments", initial_text="abcdef", cfg=settings)
    tab = created["document"]["tabs"][0]
    block = tab["blocks"][0]
    comment = create_comment(
        created["document_id"],
        tab_id=tab["tab_id"],
        block_id=block["block_id"],
        text="Check this",
        start_offset=1,
        end_offset=3,
        cfg=settings,
    )
    assert comment["state"] == "ACTIVE"
    assert len(list_comments(created["document_id"], cfg=settings)) == 1
    resolved = set_comment_state(comment["comment_id"], "RESOLVED", cfg=settings)
    assert resolved["state"] == "RESOLVED"


def test_noop_save_does_not_synthesize_revision(tmp_path):
    settings = cfg(tmp_path)
    settings.ensure_dirs()
    init_db(settings.sqlite_path)
    created = create_document("Noop", cfg=settings)
    again = update_document(created["document_id"], created["document"], 1, cfg=settings)
    assert again["revision"] == 1
    assert [row["revision"] for row in list_revisions(created["document_id"], cfg=settings)] == [1]


def test_inline_marks_validate_and_comment_anchor_orphans_after_edit(tmp_path):
    settings = cfg(tmp_path)
    settings.ensure_dirs()
    init_db(settings.sqlite_path)
    created = create_document("Rich", initial_text="abcdef", cfg=settings)
    tab = created["document"]["tabs"][0]
    block = tab["blocks"][0]
    comment = create_comment(
        created["document_id"],
        tab_id=tab["tab_id"],
        block_id=block["block_id"],
        text="range",
        start_offset=1,
        end_offset=6,
        cfg=settings,
    )
    doc = created["document"]
    doc["tabs"][0]["blocks"][0]["text"] = "abc"
    doc["tabs"][0]["blocks"][0]["marks"] = [{"type": "bold", "start": 0, "end": 3}]
    saved = update_document(created["document_id"], doc, 1, cfg=settings)
    assert saved["document"]["tabs"][0]["blocks"][0]["marks"] == [{"type": "bold", "start": 0, "end": 3}]
    comments = list_comments(created["document_id"], cfg=settings)
    assert comments[0]["comment_id"] == comment["comment_id"]
    assert comments[0]["state"] == "ORPHANED"


def test_invalid_inline_mark_range_fails_closed(tmp_path):
    settings = cfg(tmp_path)
    settings.ensure_dirs()
    init_db(settings.sqlite_path)
    created = create_document("Marks", initial_text="abc", cfg=settings)
    doc = created["document"]
    doc["tabs"][0]["blocks"][0]["marks"] = [{"type": "bold", "start": 0, "end": 99}]
    with pytest.raises(DocumentError) as exc:
        update_document(created["document_id"], doc, 1, cfg=settings)
    assert exc.value.status == "ERROR_DOCUMENT_INVALID_MARK_RANGE"


def test_document_assets_are_content_hashed_deduplicated_and_bounded(tmp_path):
    settings = cfg(tmp_path)
    settings.ensure_dirs()
    init_db(settings.sqlite_path)
    created = create_document("Assets", cfg=settings)
    first = store_asset(
        created["document_id"],
        filename="figure.png",
        media_type="image/png",
        content=b"\x89PNG\r\n\x1a\nfixture",
        cfg=settings,
    )
    second = store_asset(
        created["document_id"],
        filename="renamed.png",
        media_type="image/png",
        content=b"\x89PNG\r\n\x1a\nfixture",
        cfg=settings,
    )
    assert first["asset_id"] == second["asset_id"]
    assert first["sha256"] == second["sha256"]
    assert first["url"].startswith("/document-assets/")
    assert get_asset(first["asset_id"], cfg=settings)["content"].endswith(b"fixture")
    assert len(list_assets(created["document_id"], cfg=settings)) == 1

    with pytest.raises(DocumentError) as exc:
        store_asset(created["document_id"], filename="remote.svg", media_type="image/svg+xml", content=b"<svg/>", cfg=settings)
    assert exc.value.status == "ERROR_DOCUMENT_ASSET_TYPE"
