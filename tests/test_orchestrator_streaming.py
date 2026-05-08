"""Tests for the on_chunk streaming path through Orchestrator/ToolLoop."""

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_streaming_response(text: str, deltas: list[str]):
    """Fake Anthropic stream context: yields deltas, returns a final message."""
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )
    stream_obj = MagicMock()
    stream_obj.text_stream = iter(deltas)
    stream_obj.get_final_message.return_value = final

    @contextmanager
    def stream_cm(**kwargs):
        yield stream_obj

    return stream_cm, stream_obj


def _make_tool_loop():
    """Build a ToolLoop wired against a mocked Anthropic client."""
    from core.tool_loop import ToolLoop

    skill_loader = MagicMock()
    skill_loader.skills = {}
    skill_loader.get_tool_definitions.return_value = []

    conversation_state = MagicMock()
    conversation_state.select_messages_for_prompt.return_value = []

    container_manager = MagicMock()

    client = MagicMock()
    loop = ToolLoop(
        client=client,
        model="test-model",
        skill_loader=skill_loader,
        container_manager=container_manager,
        conversation_state=conversation_state,
        memory_provider=None,
    )
    return loop, client


class TestToolLoopStreaming(unittest.TestCase):
    def test_streaming_forwards_text_deltas_to_on_chunk(self):
        loop, client = _make_tool_loop()
        stream_cm, _ = _make_streaming_response(
            "Hello there.", deltas=["Hello", " there", "."]
        )
        client.messages.stream = stream_cm

        chunks = []
        result = loop.run(
            user_message="hi",
            system_prompt="you are a bot",
            on_chunk=chunks.append,
        )

        self.assertEqual(chunks, ["Hello", " there", "."])
        self.assertEqual(result, "Hello there.")

    def test_no_on_chunk_uses_non_streaming_path(self):
        loop, client = _make_tool_loop()
        # client.messages.create is the non-streaming code path
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="OK.")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        )

        result = loop.run(user_message="hi", system_prompt="x")

        self.assertEqual(result, "OK.")
        client.messages.create.assert_called_once()
        # The streaming context manager should NOT have been touched.
        client.messages.stream.assert_not_called()

    def test_streaming_strips_sdk_internal_parsed_output_field(self):
        """Anthropic's streaming SDK adds `parsed_output` to text blocks.

        The API rejects that field with HTTP 400 on echo, so the tool loop
        must sanitise streamed content before committing to conversation
        state.
        """
        loop, client = _make_tool_loop()

        # Mock a streamed text block with an SDK-internal field present.
        class _Block:
            def model_dump(self, **kw):
                return {"type": "text", "text": "Hello.", "parsed_output": {"junk": True}}

        final = SimpleNamespace(
            content=[_Block()],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )
        stream_obj = MagicMock()
        stream_obj.text_stream = iter(["Hello."])
        stream_obj.get_final_message.return_value = final

        @contextmanager
        def stream_cm(**_kwargs):
            yield stream_obj

        client.messages.stream = stream_cm

        loop.run(user_message="hi", system_prompt="x", on_chunk=lambda _t: None)

        # Whatever was committed to conversation state must NOT contain
        # parsed_output on any block — that's the API-rejected field.
        committed_content = loop.conversation_state.append_assistant_content.call_args.args[0]
        for block in committed_content:
            self.assertNotIn("parsed_output", block)

    def test_on_chunk_exception_does_not_break_round(self):
        loop, client = _make_tool_loop()
        stream_cm, _ = _make_streaming_response(
            "Done.", deltas=["Done", "."]
        )
        client.messages.stream = stream_cm

        def bad_chunk(_text):
            raise RuntimeError("boom")

        result = loop.run(
            user_message="hi",
            system_prompt="x",
            on_chunk=bad_chunk,
        )

        # Exceptions in on_chunk are logged and swallowed; the round still
        # produces the full assembled text.
        self.assertEqual(result, "Done.")


class TestOrchestratorProcessMessageStream(unittest.TestCase):
    def _make_orchestrator(self):
        from core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.client = MagicMock()
        orch.model = "test-model"
        orch.prompt_builder = MagicMock()
        orch.prompt_builder.build.return_value = "system prompt"
        orch.tool_loop = MagicMock()
        orch.skills = {}
        orch.skill_loader = MagicMock()
        orch.skill_loader.skipped_skills = {}
        orch.skill_loader.invalid_skills = {}
        orch.skill_selector = MagicMock()
        orch.skill_selector.available = False
        orch.conversation_state = MagicMock()
        orch._startup_context = ""
        orch.system_prompt = "system prompt"
        orch._tier_router = None
        orch._micro_loop = None
        orch.archive = None
        orch._current_session_id = None
        return orch

    def test_no_tier_router_path_forwards_on_chunk_to_tool_loop(self):
        orch = self._make_orchestrator()
        orch.tool_loop.run.return_value = "Result"

        chunks = []
        result = orch.process_message("hi", on_chunk=chunks.append)

        self.assertEqual(result, "Result")
        # The tool_loop was called with on_chunk=...
        kwargs = orch.tool_loop.run.call_args.kwargs
        self.assertEqual(kwargs["on_chunk"], chunks.append)

    def test_direct_route_delivers_full_result_via_on_chunk(self):
        orch = self._make_orchestrator()
        orch._tier_router = MagicMock()
        route = SimpleNamespace(tier="direct", action=None, skill="weather", args={"query": "x"})
        orch._tier_router.route.return_value = route
        orch.skills = {"weather": MagicMock()}
        orch.container_manager = MagicMock()
        orch.container_manager.execute_skill.return_value = "Sunny."

        chunks = []
        result = orch.process_message("weather", on_chunk=chunks.append)

        self.assertEqual(result, "Sunny.")
        # Direct routes don't stream — caller gets the full result as one delta.
        self.assertEqual(chunks, ["Sunny."])

    def test_claude_tier_forwards_on_chunk_to_tool_loop(self):
        orch = self._make_orchestrator()
        orch._tier_router = MagicMock()
        orch._tier_router.route.return_value = SimpleNamespace(
            tier="claude", action=None, skill=None, args={}
        )
        orch.tool_loop.run.return_value = "Claude says hi."
        orch._micro_loop = MagicMock()  # exists but shouldn't be called

        chunks = []
        result = orch.process_message("complex", on_chunk=chunks.append)

        self.assertEqual(result, "Claude says hi.")
        kwargs = orch.tool_loop.run.call_args.kwargs
        self.assertEqual(kwargs["on_chunk"], chunks.append)
        orch._micro_loop.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
