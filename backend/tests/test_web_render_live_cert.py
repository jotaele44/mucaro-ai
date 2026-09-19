from scripts import certify_web_render_live as cert


def test_web_render_live_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "WEB_RENDER_LIVE_CERT_OK"
    assert cert.STATUS_FAILED == "WEB_RENDER_LIVE_CERT_FAILED"
