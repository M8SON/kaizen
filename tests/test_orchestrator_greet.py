"""Tests for the lean greeting path on Orchestrator.greet."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_orchestrator():
    from core.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.model = "test-model"
    orch.prompt_builder = MagicMock()
    orch.prompt_builder.build_for_greeting.return_value = "LEAN_PROMPT"
    orch.tool_loop = MagicMock()
    orch._startup_context = "It is Friday evening."

    # Anthropic SDK shape: response.content is a list of blocks; each text
    # block has .type and .text.
    text_block = SimpleNamespace(type="text", text="Hey Mason, happy Friday.")
    orch.client.messages.create.return_value = SimpleNamespace(content=[text_block])
    return orch


class TestGreet(unittest.TestCase):
    def test_greet_uses_lean_prompt(self):
        orch = _make_orchestrator()
        orch.greet()
        orch.prompt_builder.build_for_greeting.assert_called_once_with("It is Friday evening.")
        kwargs = orch.client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["system"], "LEAN_PROMPT")

    def test_greet_does_not_send_tools(self):
        orch = _make_orchestrator()
        orch.greet()
        kwargs = orch.client.messages.create.call_args.kwargs
        self.assertNotIn("tools", kwargs)

    def test_greet_bypasses_tool_loop(self):
        orch = _make_orchestrator()
        orch.greet()
        orch.tool_loop.run.assert_not_called()

    def test_greet_uses_low_max_tokens(self):
        orch = _make_orchestrator()
        orch.greet()
        kwargs = orch.client.messages.create.call_args.kwargs
        self.assertLessEqual(kwargs["max_tokens"], 500)

    def test_greet_returns_extracted_text(self):
        orch = _make_orchestrator()
        result = orch.greet()
        self.assertEqual(result, "Hey Mason, happy Friday.")

    def test_greet_falls_back_when_response_empty(self):
        orch = _make_orchestrator()
        orch.client.messages.create.return_value = SimpleNamespace(content=[])
        result = orch.greet()
        self.assertTrue(result)  # non-empty fallback


if __name__ == "__main__":
    unittest.main()
