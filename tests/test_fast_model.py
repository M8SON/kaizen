"""Jev-confident skill requests (weather, music, web search, memory) go to the
fast model (Haiku); anything it fails or declines is retried on Sonnet."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.conversation_state import ConversationState
from core.filler_classifier import FillerClassifier, load_fast_categories
from core.tool_loop import ToolLoop, ModelRefusal
from tests.test_orchestrator_routing import _make_orchestrator_with_mocks
from tests.test_tool_loop_archive import _FakeResponse, _make_text_response

REPO = Path(__file__).resolve().parent.parent


def _clf(conf, fast=("weather",)):
    clf = FillerClassifier(api_key="k", categories={"weather": "W", "smalltalk": "S"},
                           fast_categories=set(fast), fast_confidence=0.8)
    clf.last_confidence = conf
    return clf


class FastModelChoiceTests(unittest.TestCase):
    def test_confident_flagged_category_uses_fast_model(self):
        self.assertTrue(_clf(0.9).fast_model("weather"))

    def test_low_confidence_or_unflagged_does_not(self):
        self.assertFalse(_clf(0.79).fast_model("weather"))
        self.assertFalse(_clf(1.0).fast_model("smalltalk"))
        self.assertFalse(_clf(1.0).fast_model(None))

    def test_real_config_flags_only_simple_skill_categories(self):
        self.assertEqual(load_fast_categories(REPO / "config" / "filler_phrases.yaml"),
                         {"weather", "music", "web_search", "memory"})


def _loop(response, output_config=None):
    client = MagicMock()
    client.messages.create.return_value = response
    sl = MagicMock(); sl.get_tool_definitions.return_value = []
    loop = ToolLoop(client=client, model="claude-haiku-5-5", skill_loader=sl, container_manager=MagicMock(),
                    conversation_state=ConversationState(), output_config=output_config)
    return loop, client


class ToolLoopTests(unittest.TestCase):
    def test_output_config_is_sent(self):
        loop, client = _loop(_make_text_response("ok"), output_config={"effort": "low"})
        loop.run(user_message="hi", system_prompt="S")
        self.assertEqual(client.messages.create.call_args.kwargs["output_config"], {"effort": "low"})

    def test_no_output_config_by_default(self):
        loop, client = _loop(_make_text_response("ok"))
        loop.run(user_message="hi", system_prompt="S")
        self.assertNotIn("output_config", client.messages.create.call_args.kwargs)

    def test_refusal_raises(self):
        loop, _ = _loop(_FakeResponse([], stop_reason="refusal"))
        with self.assertRaises(ModelRefusal):
            loop.run(user_message="hi", system_prompt="S")


class OrchestratorFastPathTests(unittest.TestCase):
    def _orch(self, fast_run):
        orch = _make_orchestrator_with_mocks()
        orch.conversation_state = ConversationState()
        orch._fast_loop = MagicMock()
        orch._fast_loop.run.side_effect = fast_run
        return orch

    def test_fast_true_uses_fast_loop(self):
        orch = self._orch(lambda **kw: "Haiku answer")
        self.assertEqual(orch.process_message("weather?", fast=True), "Haiku answer")
        orch.tool_loop.run.assert_not_called()

    def test_fast_false_uses_main_model(self):
        orch = self._orch(lambda **kw: "Haiku answer")
        self.assertEqual(orch.process_message("tell me about rome", fast=False), "Sonnet response")
        orch._fast_loop.run.assert_not_called()

    def test_refusal_retries_on_main_model_with_clean_history(self):
        def fast_run(user_message, **kw):
            orch.conversation_state.append_user_text(user_message)
            raise ModelRefusal("declined")
        orch = self._orch(fast_run)
        self.assertEqual(orch.process_message("weather?", fast=True), "Sonnet response")
        self.assertEqual(orch.conversation_state.messages, [])

    def test_prefetched_tool_does_not_block_retry(self):
        def fast_run(user_message, **kw):
            orch.conversation_state.append_user_text(user_message)
            orch.conversation_state.append_assistant_content(
                [{"type": "tool_use", "id": "toolu_kaizen_abc", "name": "weather", "input": {}}])
            raise RuntimeError("haiku down")
        orch = self._orch(fast_run)
        self.assertEqual(orch.process_message("weather?", fast=True), "Sonnet response")

    def test_failure_after_real_tool_use_is_not_retried(self):
        def fast_run(user_message, **kw):
            orch.conversation_state.append_user_text(user_message)
            orch.conversation_state.append_assistant_content(
                [{"type": "tool_use", "id": "toolu_01", "name": "spotify", "input": {}}])
            raise RuntimeError("haiku down")
        orch = self._orch(fast_run)
        self.assertIn("went wrong", orch.process_message("play jazz", fast=True))
        orch.tool_loop.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
