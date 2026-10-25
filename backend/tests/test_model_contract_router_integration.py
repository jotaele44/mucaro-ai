"""Router/contract integration tests; no external model, tool execution or network."""
import os
import unittest
from unittest.mock import patch
from backend.app.model_router import ModelProviderError, generate_answer

class TestRouterContractIntegration(unittest.TestCase):
    def test_mock_dispatch_after_contract(self):
        with patch("backend.app.model_router._mock_answer", return_value={"answer":"ok"}) as mock:
            answer = generate_answer("hello", [], model_provider="mock")
        self.assertEqual(answer["answer"], "ok")
        mock.assert_called_once()

    def test_empty_query_fails_before_dispatch(self):
        with patch("backend.app.model_router._mock_answer") as mock:
            with self.assertRaises(ModelProviderError) as raised:
                generate_answer(" ", [], model_provider="mock")
        self.assertEqual(raised.exception.status, "ERROR_MODEL_CONTRACT")
        mock.assert_not_called()

    def test_remote_without_opt_in_never_dispatches(self):
        with patch.dict(os.environ, {"MUCARO_AI_LIVE_MODE":"0"}):
            with patch("backend.app.model_router._anthropic_answer") as remote:
                with self.assertRaises(ModelProviderError) as raised:
                    generate_answer("hello", [], model_provider="anthropic")
        self.assertEqual(raised.exception.status, "ERROR_MODEL_CONTRACT")
        remote.assert_not_called()

    def test_mock_streaming_is_rejected_not_silently_ignored(self):
        with patch("backend.app.model_router._mock_answer") as mock:
            with self.assertRaises(ModelProviderError) as raised:
                generate_answer("hello", [], model_provider="mock", on_text_delta=lambda _: None)
        self.assertEqual(raised.exception.status, "ERROR_MODEL_CONTRACT")
        mock.assert_not_called()

    def test_local_permission_gate_is_retained(self):
        with patch("backend.app.model_router._require_model_invoke", side_effect=ModelProviderError("ERROR_PERMISSION_DENIED","denied")) as gate:
            with patch("backend.app.model_router._local_answer") as local:
                with self.assertRaises(ModelProviderError) as raised:
                    generate_answer("hello", [], model_provider="openai_compatible_local")
        self.assertEqual(raised.exception.status, "ERROR_PERMISSION_DENIED")
        gate.assert_called_once()
        local.assert_not_called()

if __name__ == "__main__":
    unittest.main()
