"""Isolated mock contract tests; no live model and no production integration."""
import unittest
from backend.app.model_contract_v1 import ContractError, ModelCapabilities, ModelRequest, validate_request

CAP = ModelCapabilities(tool_calls=True, streaming=True, structured_output=True)
def req(**kwargs):
    values = dict(provider="mock", model="mock-v1", messages=(("user", "hello"),), locality="local")
    values.update(kwargs)
    return ModelRequest(**values)

class TestContract(unittest.TestCase):
    def test_local_pass(self): self.assertEqual(validate_request(req(), CAP).model, "mock-v1")
    def test_remote_denied(self):
        with self.assertRaisesRegex(ContractError, "REMOTE_REQUIRES_OPT_IN"): validate_request(req(locality="remote"), CAP)
    def test_remote_opt_in(self): self.assertEqual(validate_request(req(locality="remote", allow_remote=True), CAP).locality, "remote")
    def test_missing_identity(self):
        with self.assertRaisesRegex(ContractError, "MODEL_IDENTITY_REQUIRED"): validate_request(req(model=" "), CAP)
    def test_tools_denied(self):
        with self.assertRaisesRegex(ContractError, "TOOL_CALLS_UNSUPPORTED"): validate_request(req(tools_requested=True), ModelCapabilities())
    def test_stream_denied(self):
        with self.assertRaisesRegex(ContractError, "STREAMING_UNSUPPORTED"): validate_request(req(stream_requested=True), ModelCapabilities())
    def test_structured_denied(self):
        with self.assertRaisesRegex(ContractError, "STRUCTURED_OUTPUT_UNSUPPORTED"): validate_request(req(structured_requested=True), ModelCapabilities())
    def test_budget_denied(self):
        with self.assertRaisesRegex(ContractError, "INVALID_TOKEN_BUDGET"): validate_request(req(max_output_tokens=8192), CAP)
    def test_empty_messages(self):
        with self.assertRaisesRegex(ContractError, "INVALID_MESSAGES"): validate_request(req(messages=()), CAP)
    def test_invalid_locality(self):
        with self.assertRaisesRegex(ContractError, "INVALID_LOCALITY"): validate_request(req(locality="lan"), CAP)
    def test_capability_pass(self): self.assertTrue(validate_request(req(tools_requested=True,stream_requested=True,structured_requested=True), CAP).tools_requested)

if __name__ == "__main__": unittest.main()
