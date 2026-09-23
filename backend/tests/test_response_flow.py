import unittest

from backend.app.response_flow import assemble, resolve, web_render


class ResponseFlowTests(unittest.TestCase):
    def test_code_is_content_and_render_is_repeatable(self):
        envelope = assemble("session", "turn", "Intro\n```python\nprint(1)\n```\nEnd")
        before = repr(envelope)
        first = web_render(envelope)
        self.assertEqual(first, web_render(envelope))
        self.assertEqual(before, repr(envelope))
        self.assertEqual([part["type"] for part in first], ["text", "code_block", "text"])
        self.assertFalse(first[1]["run_button"])

    def test_choice_resolution_and_negative_inputs(self):
        envelope = assemble("session", "turn", "", {"type": "MULTIPLE_CHOICE", "prompt": "Which?", "options": [{"id": "a", "label": "Alpha"}, {"id": "b", "label": "Beta"}]})
        pending = envelope["parts"][-1]
        self.assertEqual(envelope["pending_interaction_id"], pending["interaction_id"])
        self.assertEqual(resolve(pending, "", choice_id="b", interaction_id=pending["interaction_id"]), ("RESOLVED", "Beta"))
        self.assertEqual(resolve(pending, "custom"), ("RESOLVED", "custom"))
        self.assertEqual(resolve(pending, "new", dismiss=True), ("DISMISSED", "new"))
        for kwargs in ({"choice_id": "missing"}, {"interaction_id": "stale"}, {"choice_id": "a", "dismiss": True}):
            with self.assertRaises(ValueError):
                resolve(pending, "", **kwargs)

    def test_duplicate_ids_and_ambiguous_labels_fail_closed(self):
        with self.assertRaises(ValueError):
            assemble("s", "t", "", {"type": "MULTIPLE_CHOICE", "prompt": "Pick", "options": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]})
        pending = assemble("s", "t", "", {"type": "MULTIPLE_CHOICE", "prompt": "Pick", "options": [{"id": "a", "label": "Same"}, {"id": "b", "label": "same"}]})["parts"][-1]
        with self.assertRaises(ValueError):
            resolve(pending, "same")


if __name__ == "__main__":
    unittest.main()
