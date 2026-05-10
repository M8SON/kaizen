"""Tests for Orchestrator._execute_direct ack-success short-circuit."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_orchestrator():
    """Build a minimal Orchestrator with stubbed deps so we can test
    _execute_direct in isolation."""
    from core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.skills = {"music-control": MagicMock(name="music-control-skill")}
    orch.container_manager = MagicMock()
    return orch


def _make_route(skill="music-control", action=None, args=None):
    """Build a minimal route-like object with the attributes _execute_direct reads."""
    route = MagicMock()
    route.skill = skill
    route.action = action
    route.args = args or {"action": "pause"}
    return route


class ExecuteDirectAckSuccess(unittest.TestCase):
    def test_ack_string_fires_callback_and_returns_empty(self):
        orch = _make_orchestrator()
        orch.container_manager.execute_skill.return_value = "Paused."
        cb = MagicMock()
        result = orch._execute_direct(
            _make_route(), "pause", on_ack_success=cb
        )
        cb.assert_called_once_with()
        self.assertEqual(result, "")

    def test_non_ack_string_falls_through_and_returns_result(self):
        orch = _make_orchestrator()
        orch.container_manager.execute_skill.return_value = "Nothing is playing."
        cb = MagicMock()
        result = orch._execute_direct(
            _make_route(), "pause", on_ack_success=cb
        )
        cb.assert_not_called()
        self.assertEqual(result, "Nothing is playing.")

    def test_no_callback_provided_returns_result_as_before(self):
        """When on_ack_success is None, the helper must behave identically
        to before this feature: return the result string unchanged even if
        it would have been an ack string."""
        orch = _make_orchestrator()
        orch.container_manager.execute_skill.return_value = "Resumed."
        result = orch._execute_direct(
            _make_route(), "resume", on_ack_success=None
        )
        self.assertEqual(result, "Resumed.")

    def test_all_six_ack_strings_trigger_callback(self):
        """Spot-check every member of MUSIC_CONTROL_ACK_SUCCESS routes
        through the short-circuit."""
        from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
        for ack_str in MUSIC_CONTROL_ACK_SUCCESS:
            with self.subTest(ack=ack_str):
                orch = _make_orchestrator()
                orch.container_manager.execute_skill.return_value = ack_str
                cb = MagicMock()
                result = orch._execute_direct(
                    _make_route(), "pause", on_ack_success=cb
                )
                cb.assert_called_once_with()
                self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
