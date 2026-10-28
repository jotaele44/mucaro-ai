from scripts import certify_ocr08_structure_live as cert


def test_ocr08_live_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "OCR08_STRUCTURE_LIVE_CERT_OK"
    assert cert.STATUS_FAILED == "OCR08_STRUCTURE_LIVE_CERT_FAILED"


def test_ocr08_fixture_generator_resolves_a_supported_deterministic_font() -> None:
    font = cert._font(24)
    assert font.getname()[0]
