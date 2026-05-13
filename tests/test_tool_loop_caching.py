"""Tests for Anthropic prompt caching wired through the ToolLoop.

When the caller passes both `system_prompt` and `system_prompt_dynamic`, the
ToolLoop must assemble the `system=` kwarg as a 2-block list and mark the
stable block with `cache_control: {"type": "ephemeral"}`. The legacy single-
string behavior must remain unchanged when `system_prompt_dynamic` is omitted.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_tool_loop():
    from core.tool_loop import ToolLoop

    skill_loader = MagicMock()
    skill_loader.skills = {}
    skill_loader.get_tool_definitions.return_value = []

    conversation_state = MagicMock()
    conversation_state.select_messages_for_prompt.return_value = []

    container_manager = MagicMock()

    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )

    loop = ToolLoop(
        client=client,
        model="test-model",
        skill_loader=skill_loader,
        container_manager=container_manager,
        conversation_state=conversation_state,
        memory_provider=None,
    )
    return loop, client


class TestToolLoopCaching(unittest.TestCase):
    def test_dynamic_path_sends_two_block_system_with_cache_control(self):
        loop, client = _make_tool_loop()
        loop.run(
            user_message="hi",
            system_prompt="STABLE PREFIX",
            system_prompt_dynamic="DYNAMIC SUFFIX",
        )

        kwargs = client.messages.create.call_args.kwargs
        system = kwargs["system"]
        self.assertIsInstance(system, list)
        self.assertEqual(len(system), 2)

        stable_block, dynamic_block = system
        self.assertEqual(stable_block["type"], "text")
        self.assertEqual(stable_block["text"], "STABLE PREFIX")
        self.assertEqual(stable_block["cache_control"], {"type": "ephemeral"})

        self.assertEqual(dynamic_block["type"], "text")
        self.assertEqual(dynamic_block["text"], "DYNAMIC SUFFIX")
        self.assertNotIn("cache_control", dynamic_block)

    def test_dynamic_empty_string_falls_back_to_legacy_single_string(self):
        """Empty dynamic suffix means caller did NOT opt into caching — keep
        the single-string system field for byte-exact compatibility with
        callers that don't want cache writes."""
        loop, client = _make_tool_loop()
        loop.run(
            user_message="hi",
            system_prompt="just a string",
            system_prompt_dynamic="",
        )

        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["system"], "just a string")

    def test_legacy_single_arg_path_unchanged(self):
        loop, client = _make_tool_loop()
        loop.run(user_message="hi", system_prompt="legacy")

        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["system"], "legacy")


if __name__ == "__main__":
    unittest.main()
