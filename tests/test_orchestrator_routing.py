# tests/test_orchestrator_routing.py
"""Tests for Orchestrator tiered routing when MICRO_TIER_ENABLED=true."""

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
    orch.tool_loop.run.return_value = "Sonnet response"
    orch._startup_context = ""
    orch.system_prompt = "system prompt"
    orch._tier_router = None
    orch._micro_loop = None
    orch.archive = None
    orch._current_session_id = None
    return orch


class TestOrchestratorRoutingDisabled(unittest.TestCase):

    def test_process_message_goes_to_claude_when_tier_router_none(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = None
        result = orch.process_message("play some jazz")
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Sonnet response")


class TestOrchestratorRoutingEnabled(unittest.TestCase):

    def _make_router(self, tier):
        from core.tier_router import RouteResult
        router = MagicMock()
        router.route.return_value = RouteResult(tier=tier)
        return router

    def test_claude_route_goes_to_full_tool_loop(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("claude")
        orch._micro_loop = MagicMock()
        result = orch.process_message("install a new skill")
        orch.tool_loop.run.assert_called_once()
        orch._micro_loop.run.assert_not_called()
        self.assertEqual(result, "Sonnet response")

    def test_micro_route_goes_to_micro_loop(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("micro")
        orch._micro_loop = MagicMock()
        orch._micro_loop.run.return_value = "Haiku response"
        result = orch.process_message("play some jazz")
        orch._micro_loop.run.assert_called_once()
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Haiku response")

    def test_micro_failure_falls_back_to_sonnet(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("micro")
        orch._micro_loop = MagicMock()
        orch._micro_loop.run.side_effect = RuntimeError("haiku unavailable")
        result = orch.process_message("complex question")
        orch._micro_loop.run.assert_called_once()
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Sonnet response")

    def test_direct_skill_route_calls_container_manager(self):
        from core.tier_router import RouteResult

        fake_skill = MagicMock()
        orch = _make_orchestrator_with_mocks()
        orch.skills = {"soundcloud": fake_skill}
        orch.container_manager.execute_skill.return_value = "Stopped."
        router = MagicMock()
        router.route.return_value = RouteResult(
            tier="direct", skill="soundcloud", args={"action": "stop"}
        )
        orch._tier_router = router
        orch._micro_loop = MagicMock()
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
        orch._micro_loop = MagicMock()
        result = orch.process_message("goodbye")
        orch.close_session.assert_called_once()
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Goodbye!")

    def test_micro_tier_uses_slim_prompt_not_full_claude_prompt(self):
        """Micro-tier path must NOT build the heavy Sonnet prompt."""
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("micro")
        orch._micro_loop = MagicMock()
        orch._micro_loop.run.return_value = "Haiku response"
        orch.prompt_builder.build_for_micro_tier.return_value = "slim prompt"

        with patch.object(orch, "_build_system_prompt") as mock_build:
            result = orch.process_message("tell me a joke")
        mock_build.assert_not_called()
        orch.prompt_builder.build_for_micro_tier.assert_called_once()
        call_args = orch._micro_loop.run.call_args
        self.assertEqual(call_args.kwargs["system_prompt"], "slim prompt")
        self.assertEqual(result, "Haiku response")

    def test_claude_tier_still_builds_full_system_prompt(self):
        """Claude tier is unchanged: full prompt before tool_loop.run."""
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("claude")
        orch._micro_loop = MagicMock()

        with patch.object(
            orch, "_build_system_prompt", return_value="full claude prompt"
        ) as mock_build:
            result = orch.process_message("install a new skill")
        mock_build.assert_called_once()
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Sonnet response")


if __name__ == "__main__":
    unittest.main()
