# tests/test_orchestrator_routing.py
"""Tests for Orchestrator tiered routing when OLLAMA_ENABLED=true."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_orchestrator_with_mocks():
    """
    Build an Orchestrator with all external dependencies mocked so no
    real API calls, containers, or file I/O occur.
    """
    from core.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.model = "test-model"
    orch.skill_loader = MagicMock()
    orch.skill_loader.load_all.return_value = {}
    orch.skill_loader.skipped_skills = {}
    orch.skill_loader.invalid_skills = {}
    orch.skill_loader.get_tool_definitions.return_value = []
    orch.skills = {}
    orch.skill_selector = MagicMock()
    orch.skill_selector.available = False
    orch.container_manager = MagicMock()
    orch.conversation_state = MagicMock()
    orch.memory_provider = MagicMock()
    orch.prompt_builder = MagicMock()
    orch.prompt_builder.build.return_value = "system prompt"
    orch.tool_loop = MagicMock()
    orch.tool_loop.run.return_value = "Claude response"
    orch._startup_context = ""
    orch.system_prompt = "system prompt"
    orch._tier_router = None
    orch._ollama_tool_loop = None
    return orch


class TestOrchestratorRoutingDisabled(unittest.TestCase):

    def test_process_message_goes_to_claude_when_tier_router_none(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = None
        result = orch.process_message("play some jazz")
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Claude response")


class TestOrchestratorRoutingEnabled(unittest.TestCase):

    def _make_router(self, tier):
        from core.tier_router import RouteResult
        router = MagicMock()
        router.route.return_value = RouteResult(tier=tier)
        return router

    def test_claude_route_goes_to_tool_loop(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("claude")
        orch._ollama_tool_loop = MagicMock()
        result = orch.process_message("install a new skill")
        orch.tool_loop.run.assert_called_once()
        orch._ollama_tool_loop.run.assert_not_called()
        self.assertEqual(result, "Claude response")

    def test_ollama_route_goes_to_ollama_loop(self):
        from core.ollama_tool_loop import EscalateSignal

        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = "Ollama response"
        result = orch.process_message("play some jazz")
        orch._ollama_tool_loop.run.assert_called_once()
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Ollama response")

    def test_ollama_escalation_falls_back_to_claude(self):
        from core.ollama_tool_loop import EscalateSignal

        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = EscalateSignal
        result = orch.process_message("something complex")
        orch._ollama_tool_loop.run.assert_called_once()
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Claude response")

    def test_direct_skill_route_calls_container_manager(self):
        from core.tier_router import RouteResult

        fake_skill = MagicMock()
        orch = _make_orchestrator_with_mocks()
        orch.skills = {"soundcloud_play": fake_skill}
        orch.container_manager.execute_skill.return_value = "Stopped."
        router = MagicMock()
        router.route.return_value = RouteResult(
            tier="direct", skill="soundcloud_play", args={"action": "stop"}
        )
        orch._tier_router = router
        orch._ollama_tool_loop = MagicMock()
        result = orch.process_message("stop")
        orch.container_manager.execute_skill.assert_called_once_with(
            fake_skill, {"action": "stop"}
        )
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Stopped.")

    def test_direct_close_session_calls_close_session(self):
        from core.tier_router import RouteResult

        orch = _make_orchestrator_with_mocks()
        orch.close_session = MagicMock(return_value="Goodbye!")
        router = MagicMock()
        router.route.return_value = RouteResult(tier="direct", action="close_session")
        orch._tier_router = router
        orch._ollama_tool_loop = MagicMock()
        result = orch.process_message("goodbye")
        orch.close_session.assert_called_once()
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Goodbye!")

    def test_ollama_tier_does_not_build_claude_system_prompt(self):
        """Ollama-tier success path must NOT build the heavy Claude prompt."""
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = "Ollama response"
        orch.prompt_builder.build_for_ollama.return_value = "slim ollama prompt"

        with patch.object(orch, "_build_system_prompt") as mock_build:
            result = orch.process_message("tell me a joke")
        mock_build.assert_not_called()
        orch.prompt_builder.build_for_ollama.assert_called_once()
        # Slim prompt should be what OllamaToolLoop received.
        call_args = orch._ollama_tool_loop.run.call_args
        self.assertEqual(call_args.kwargs["system_prompt"], "slim ollama prompt")
        self.assertEqual(result, "Ollama response")

    def test_ollama_escalation_builds_claude_prompt_lazily(self):
        """EscalateSignal must trigger exactly one Claude-prompt build."""
        from core.ollama_tool_loop import EscalateSignal

        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = EscalateSignal
        orch.prompt_builder.build_for_ollama.return_value = "slim ollama prompt"

        with patch.object(
            orch, "_build_system_prompt", return_value="full claude prompt"
        ) as mock_build:
            result = orch.process_message("complex question")
        mock_build.assert_called_once()
        orch.tool_loop.run.assert_called_once()
        # The Claude tool loop should receive the full prompt.
        self.assertEqual(
            orch.tool_loop.run.call_args.kwargs["system_prompt"],
            "full claude prompt",
        )
        self.assertEqual(result, "Claude response")

    def test_ollama_escalate_with_context_builds_claude_prompt_lazily(self):
        """EscalateWithContext (tools ran but Ollama couldn't finalize) must
        also lazy-build the Claude prompt before _claude_finalize_ollama_turn."""
        from core.ollama_tool_loop import EscalateWithContext

        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = EscalateWithContext(
            tool_activity=[{"name": "weather", "args": {}, "result": "sunny"}],
        )
        orch.prompt_builder.build_for_ollama.return_value = "slim ollama prompt"

        with patch.object(
            orch, "_build_system_prompt", return_value="full claude prompt"
        ) as mock_build, patch.object(
            orch, "_claude_finalize_ollama_turn", return_value="finalized"
        ) as mock_finalize:
            result = orch.process_message("what's the weather")

        mock_build.assert_called_once()
        mock_finalize.assert_called_once()
        # The full prompt must be forwarded to the finalizer.
        finalize_args = mock_finalize.call_args
        # _claude_finalize_ollama_turn(user_message, tool_activity, system_prompt)
        self.assertEqual(finalize_args.args[2], "full claude prompt")
        self.assertEqual(result, "finalized")

    def test_claude_tier_still_builds_full_system_prompt(self):
        """Claude-tier path is unchanged: build the full prompt before tool_loop.run."""
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("claude")
        orch._ollama_tool_loop = MagicMock()

        with patch.object(
            orch, "_build_system_prompt", return_value="full claude prompt"
        ) as mock_build:
            result = orch.process_message("install a new skill")
        mock_build.assert_called_once()
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Claude response")


if __name__ == "__main__":
    unittest.main()
