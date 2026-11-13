from scripts import certify_ocr08_structure_live as cert


def test_ocr08_live_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "OCR08_STRUCTURE_LIVE_CERT_OK"
    assert cert.STATUS_FAILED == "OCR08_STRUCTURE_LIVE_CERT_FAILED"



def test_ocr08_live_cert_uses_governed_image_ocr_boundary() -> None:
    from backend.app.ocr_analysis import analyze_image_ocr

    # The live structure cert must traverse the same OCR entry point that
    # enforces the current subprocess permission boundary; it may not retain
    # a private/bypass OCR implementation after permission hardening.
    assert cert.analyze_image_ocr is analyze_image_ocr


def test_ocr08_pass_event_is_bounded_to_structure_scope() -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    ledger = json.loads((root / "_architecture" / "mucaro_v1_denominator_events.json").read_text(encoding="utf-8"))
    event = next(row for row in ledger["events"] if row["event_id"] == "evt-ocr08-bounded-structure-pass-20261102")
    assert event["requirement_id"] == "OCR-08"
    assert event["from_state"] == "OPEN"
    assert event["to_state"] == "PASS"
    joined = " ".join(event["evidence"]) + " " + event["reason"]
    assert "OCR-09" in joined
    assert "OCR-10" in joined
    assert "UNRESOLVED" in joined
