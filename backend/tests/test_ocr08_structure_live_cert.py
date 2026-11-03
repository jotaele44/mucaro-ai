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
