from scripts import certify_ocr08_structure_live as cert


def test_ocr08_live_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "OCR08_STRUCTURE_LIVE_CERT_OK"
    assert cert.STATUS_FAILED == "OCR08_STRUCTURE_LIVE_CERT_FAILED"
