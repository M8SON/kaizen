"""Tests for OllamaToolLoop and EscalateSignal."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_loop(
    host="http://localhost:11434",
    model="phi4-mini",
    skill_loader=None,
    container_manager=None,
    conversation_state=None,
    timeout_seconds=8.0,
):
    from core.ollama_tool_loop import OllamaToolLoop

    if skill_loader is None:
        skill_loader = MagicMock()
        skill_loader.get_tool_definitions.return_value = []
        skill_loader.get_skill.return_value = None

    if container_manager is None:
        container_manager = MagicMock()

    if conversation_state is None:
        from core.conversation_state import ConversationState
        conversation_state = ConversationState()

    return OllamaToolLoop(
        host=host,
        model=model,
        skill_loader=skill_loader,
        container_manager=container_manager,
        conversation_state=conversation_state,
        timeout_seconds=timeout_seconds,
    )


def _make_response(content=None, finish_reason="stop", tool_calls=None):
    """Build a minimal Ollama /v1/chat/completions JSON response."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "message": message,
                "finish_reason": finish_reason if not tool_calls else "tool_calls",
            }
        ]
    }


class TestEscalateSignal(unittest.TestCase):

    def test_escalate_signal_is_singleton(self):
        from core.ollama_tool_loop import EscalateSignal as E1
        from core.ollama_tool_loop import EscalateSignal as E2
        self.assertIs(E1, E2)

    def test_escalate_signal_identity_comparison(self):
        from core.ollama_tool_loop import _EscalateSignalType, EscalateSignal
        second_instance = _EscalateSignalType()
        self.assertIs(second_instance, EscalateSignal)
        self.assertIsNot(EscalateSignal, None)
        self.assertNotEqual(EscalateSignal, "ESCALATE")


class TestTimeoutEscalation(unittest.TestCase):

    def test_timeout_returns_escalate_signal(self):
        import requests
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop()
        with patch("requests.post", side_effect=requests.Timeout):
            result = loop.run("play some jazz", "you are a voice assistant")
        self.assertIs(result, EscalateSignal)

    def test_connection_error_returns_escalate_signal(self):
        import requests
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop()
        with patch("requests.post", side_effect=requests.ConnectionError):
            result = loop.run("play some jazz", "you are a voice assistant")
        self.assertIs(result, EscalateSignal)

    def test_conversation_state_unchanged_on_timeout(self):
        import requests

        from core.conversation_state import ConversationState
        state = ConversationState()
        loop = _make_loop(conversation_state=state)
        with patch("requests.post", side_effect=requests.Timeout):
            loop.run("play some jazz", "you are a voice assistant")
        # ConversationState must be untouched — Claude will append the message itself
        self.assertEqual(state.messages, [])


class TestEscalateTriggers(unittest.TestCase):

    def _run_with_response(self, response_json, skill_loader=None, container_manager=None):
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop(
            skill_loader=skill_loader or MagicMock(
                get_tool_definitions=MagicMock(return_value=[]),
                get_skill=MagicMock(return_value=None),
            ),
            container_manager=container_manager or MagicMock(),
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_json
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp):
            return loop.run("play some jazz", "you are a voice assistant")

    def test_explicit_escalate_word_escalates(self):
        from core.ollama_tool_loop import EscalateSignal
        result = self._run_with_response(_make_response(content="ESCALATE"))
        self.assertIs(result, EscalateSignal)

    def test_empty_response_escalates(self):
        from core.ollama_tool_loop import EscalateSignal
        result = self._run_with_response(_make_response(content=""))
        self.assertIs(result, EscalateSignal)

    def test_unknown_tool_name_escalates(self):
        from core.ollama_tool_loop import EscalateSignal

        tool_call = {
            "id": "call_1",
            "function": {"name": "nonexistent_skill", "arguments": "{}"},
        }
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = None  # skill not found

        result = self._run_with_response(
            _make_response(tool_calls=[tool_call]),
            skill_loader=sl,
        )
        self.assertIs(result, EscalateSignal)

    def test_malformed_tool_args_escalates(self):
        from core.ollama_tool_loop import EscalateSignal

        fake_skill = MagicMock()
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = fake_skill  # skill exists

        tool_call = {
            "id": "call_1",
            "function": {"name": "weather", "arguments": "NOT_VALID_JSON"},
        }
        result = self._run_with_response(
            _make_response(tool_calls=[tool_call]),
            skill_loader=sl,
        )
        self.assertIs(result, EscalateSignal)

    def test_plain_text_response_returned_as_string(self):
        result = self._run_with_response(_make_response(content="The weather is sunny."))
        self.assertEqual(result, "The weather is sunny.")

    def test_successful_tool_call_returns_string(self):
        fake_skill = MagicMock()
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = fake_skill

        cm = MagicMock()
        cm.execute_skill.return_value = "Currently 18°C and cloudy in London."

        # First response: tool call. Second response: final text.
        tool_call_response = _make_response(
            tool_calls=[{
                "id": "call_1",
                "function": {"name": "weather", "arguments": '{"city": "London"}'},
            }]
        )
        final_response = _make_response(content="It is 18 degrees and cloudy in London.")

        mock_resp_1 = MagicMock()
        mock_resp_1.json.return_value = tool_call_response
        mock_resp_1.raise_for_status.return_value = None

        mock_resp_2 = MagicMock()
        mock_resp_2.json.return_value = final_response
        mock_resp_2.raise_for_status.return_value = None

        with patch("requests.post", side_effect=[mock_resp_1, mock_resp_2]):
            result = _make_loop(skill_loader=sl, container_manager=cm).run(
                "what's the weather in London", "you are a voice assistant"
            )

        self.assertEqual(result, "It is 18 degrees and cloudy in London.")
        cm.execute_skill.assert_called_once_with(fake_skill, {"city": "London"})

    def test_successful_turn_commits_to_conversation_state(self):
        from core.conversation_state import ConversationState
        state = ConversationState()
        loop = _make_loop(conversation_state=state)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_response(content="Sure, playing jazz.")
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp):
            loop.run("play jazz", "system prompt")
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.messages[0]["role"], "user")
        self.assertEqual(state.messages[1]["role"], "assistant")

    def test_execute_skill_returning_none_escalates(self):
        from core.ollama_tool_loop import EscalateSignal

        fake_skill = MagicMock()
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = fake_skill

        cm = MagicMock()
        cm.execute_skill.return_value = None

        tool_call = {
            "id": "call_1",
            "function": {"name": "weather", "arguments": '{"city": "London"}'},
        }
        result = self._run_with_response(
            _make_response(tool_calls=[tool_call]),
            skill_loader=sl,
            container_manager=cm,
        )
        self.assertIs(result, EscalateSignal)

    def test_execute_skill_raising_escalates(self):
        from core.ollama_tool_loop import EscalateSignal

        fake_skill = MagicMock()
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = fake_skill

        cm = MagicMock()
        cm.execute_skill.side_effect = RuntimeError("docker timeout")

        tool_call = {
            "id": "call_1",
            "function": {"name": "weather", "arguments": '{"city": "London"}'},
        }
        result = self._run_with_response(
            _make_response(tool_calls=[tool_call]),
            skill_loader=sl,
            container_manager=cm,
        )
        self.assertIs(result, EscalateSignal)


class TestHistoryTrimming(unittest.TestCase):
    def _make_loop(self, max_history_tokens=1500):
        # Build a minimal OllamaToolLoop with a fake ConversationState.
        from core.ollama_tool_loop import OllamaToolLoop

        class _FakeConversationState:
            def __init__(self, msgs):
                self._msgs = msgs

            def select_messages_for_prompt(self):
                return list(self._msgs)

        class _FakeSkillLoader:
            def get_tool_definitions(self):
                return []

        # Long history: alternating user/assistant, each ~400 chars (~100 tokens).
        msgs = []
        for i in range(20):
            msgs.append({"role": "user", "content": "u" * 400 + f" turn {i}"})
            msgs.append(
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "a" * 400 + f" reply {i}"}],
                }
            )

        return OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=_FakeSkillLoader(),
            container_manager=None,
            conversation_state=_FakeConversationState(msgs),
            max_history_tokens=max_history_tokens,
        )

    def test_default_max_history_tokens_is_1500(self):
        loop = self._make_loop()
        # Default applies when max_history_tokens=None and env var unset.
        assert loop.max_history_tokens == 1500

    def test_env_var_overrides_default(self):
        import os
        from core.ollama_tool_loop import OllamaToolLoop

        os.environ["OLLAMA_CONVERSATION_MAX_TOKENS"] = "750"
        try:
            loop = OllamaToolLoop(
                host="http://localhost:11434",
                model="phi4-mini",
                skill_loader=type("_S", (), {"get_tool_definitions": lambda self: []})(),
                container_manager=None,
                conversation_state=type(
                    "_C", (), {"select_messages_for_prompt": lambda self: []}
                )(),
            )
            assert loop.max_history_tokens == 750
        finally:
            del os.environ["OLLAMA_CONVERSATION_MAX_TOKENS"]

    def test_explicit_param_overrides_env(self):
        import os
        from core.ollama_tool_loop import OllamaToolLoop

        os.environ["OLLAMA_CONVERSATION_MAX_TOKENS"] = "750"
        try:
            loop = OllamaToolLoop(
                host="http://localhost:11434",
                model="phi4-mini",
                skill_loader=type("_S", (), {"get_tool_definitions": lambda self: []})(),
                container_manager=None,
                conversation_state=type(
                    "_C", (), {"select_messages_for_prompt": lambda self: []}
                )(),
                max_history_tokens=200,
            )
            assert loop.max_history_tokens == 200
        finally:
            del os.environ["OLLAMA_CONVERSATION_MAX_TOKENS"]

    def test_trim_history_keeps_recent_messages_within_budget(self):
        loop = self._make_loop(max_history_tokens=400)  # ~ 4 messages of 100 toks
        messages = loop._build_local_messages(
            system_prompt="sys", user_message="hi"
        )
        # First message is system, last is user; everything between is trimmed history.
        history = messages[1:-1]
        # Sum of estimated tokens in history must be ≤ budget.
        total = sum(max(1, len(m["content"]) // 4) for m in history)
        assert total <= 400, f"history {total} toks exceeded budget 400"
        # Should retain at least one message (the most recent).
        assert len(history) >= 1

    def test_trim_history_drops_leading_assistant_after_trim(self):
        # Build a history where the last user-then-assistant pair would put an
        # assistant first if naive trimming kept N messages from the end.
        from core.ollama_tool_loop import OllamaToolLoop

        msgs = [
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": [{"type": "text", "text": "old asst"}]},
            {"role": "user", "content": "mid user"},
            {"role": "assistant", "content": [{"type": "text", "text": "mid asst"}]},
        ]

        class _FakeConversationState:
            def select_messages_for_prompt(self_inner):
                return list(msgs)

        class _FakeSkillLoader:
            def get_tool_definitions(self_inner):
                return []

        # Budget=6 fits 3 messages (~2 toks each). The 4th iteration breaks
        # because the previous `old user` would push us over. After reverse,
        # kept = [old asst, mid user, mid asst]; the leading-assistant prune
        # drops `old asst`, leaving the [user, asst] pair we want to verify.
        loop = OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=_FakeSkillLoader(),
            container_manager=None,
            conversation_state=_FakeConversationState(),
            max_history_tokens=6,
        )
        messages = loop._build_local_messages(system_prompt="sys", user_message="now")
        history = messages[1:-1]
        # Non-vacuous: history must be non-empty AND start with user.
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "mid user"
        assert history[1]["role"] == "assistant"


class TestWarmup(unittest.TestCase):
    def test_warmup_posts_to_generate_with_keep_alive(self):
        from core.ollama_tool_loop import OllamaToolLoop

        loop = _make_loop(host="http://example.invalid:11434", model="phi4-mini")
        with patch("core.ollama_tool_loop.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            loop.warmup()
        mock_post.assert_called_once()
        call = mock_post.call_args
        self.assertEqual(call.args[0], "http://example.invalid:11434/api/generate")
        self.assertEqual(call.kwargs["json"]["model"], "phi4-mini")
        self.assertEqual(call.kwargs["json"]["keep_alive"], "30m")

    def test_warmup_swallows_request_exception(self):
        from core.ollama_tool_loop import OllamaToolLoop
        import requests as _requests

        loop = _make_loop()
        with patch("core.ollama_tool_loop.requests.post") as mock_post:
            mock_post.side_effect = _requests.RequestException("boom")
            # Must not raise — warmup is fire-and-forget.
            loop.warmup()

    def test_warmup_async_returns_started_thread(self):
        loop = _make_loop()
        with patch("core.ollama_tool_loop.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            thread = loop.warmup_async()
            thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(thread.name, "ollama-warmup")
        mock_post.assert_called_once()


class TestSkillSelectorFiltering(unittest.TestCase):
    """OllamaToolLoop should filter tool defs to top-K when a SkillSelector is wired in."""

    def _defs(self, *names):
        return [
            {
                "name": n,
                "description": f"desc-{n}",
                "input_schema": {"type": "object"},
            }
            for n in names
        ]

    def test_filters_to_selected_when_selector_available(self):
        sl = MagicMock()
        sl.get_tool_definitions.return_value = self._defs("weather", "web-search", "schedule", "soundcloud")
        selector = MagicMock()
        selector.available = True
        selector.select.return_value = {"weather"}

        from core.ollama_tool_loop import OllamaToolLoop
        from core.conversation_state import ConversationState
        loop = OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=sl,
            container_manager=MagicMock(),
            conversation_state=ConversationState(),
            skill_selector=selector,
        )
        defs = loop._build_tool_definitions("what's the weather")
        names = [d["function"]["name"] for d in defs]
        self.assertEqual(names, ["weather"])
        selector.select.assert_called_once_with("what's the weather")

    def test_falls_back_to_all_when_selector_unavailable(self):
        sl = MagicMock()
        sl.get_tool_definitions.return_value = self._defs("weather", "web-search")
        selector = MagicMock()
        selector.available = False

        from core.ollama_tool_loop import OllamaToolLoop
        from core.conversation_state import ConversationState
        loop = OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=sl,
            container_manager=MagicMock(),
            conversation_state=ConversationState(),
            skill_selector=selector,
        )
        defs = loop._build_tool_definitions("hello")
        names = sorted(d["function"]["name"] for d in defs)
        self.assertEqual(names, ["weather", "web-search"])
        selector.select.assert_not_called()

    def test_falls_back_to_all_when_selector_returns_empty(self):
        sl = MagicMock()
        sl.get_tool_definitions.return_value = self._defs("weather", "web-search")
        selector = MagicMock()
        selector.available = True
        selector.select.return_value = set()

        from core.ollama_tool_loop import OllamaToolLoop
        from core.conversation_state import ConversationState
        loop = OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=sl,
            container_manager=MagicMock(),
            conversation_state=ConversationState(),
            skill_selector=selector,
        )
        defs = loop._build_tool_definitions("hello")
        names = sorted(d["function"]["name"] for d in defs)
        self.assertEqual(names, ["weather", "web-search"])

    def test_falls_back_to_all_when_no_user_message(self):
        sl = MagicMock()
        sl.get_tool_definitions.return_value = self._defs("weather", "web-search")
        selector = MagicMock()
        selector.available = True

        from core.ollama_tool_loop import OllamaToolLoop
        from core.conversation_state import ConversationState
        loop = OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=sl,
            container_manager=MagicMock(),
            conversation_state=ConversationState(),
            skill_selector=selector,
        )
        defs = loop._build_tool_definitions(None)
        names = sorted(d["function"]["name"] for d in defs)
        self.assertEqual(names, ["weather", "web-search"])
        selector.select.assert_not_called()

    def test_selector_exception_falls_back_to_all(self):
        sl = MagicMock()
        sl.get_tool_definitions.return_value = self._defs("weather", "web-search")
        selector = MagicMock()
        selector.available = True
        selector.select.side_effect = RuntimeError("boom")

        from core.ollama_tool_loop import OllamaToolLoop
        from core.conversation_state import ConversationState
        loop = OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=sl,
            container_manager=MagicMock(),
            conversation_state=ConversationState(),
            skill_selector=selector,
        )
        defs = loop._build_tool_definitions("hello")
        names = sorted(d["function"]["name"] for d in defs)
        self.assertEqual(names, ["weather", "web-search"])

    def test_filtered_zero_match_falls_back_to_all(self):
        # Selector returns names that don't exist in the loaded tool defs —
        # never strand the model with zero tools.
        sl = MagicMock()
        sl.get_tool_definitions.return_value = self._defs("weather", "web-search")
        selector = MagicMock()
        selector.available = True
        selector.select.return_value = {"nonexistent-skill"}

        from core.ollama_tool_loop import OllamaToolLoop
        from core.conversation_state import ConversationState
        loop = OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=sl,
            container_manager=MagicMock(),
            conversation_state=ConversationState(),
            skill_selector=selector,
        )
        defs = loop._build_tool_definitions("hello")
        names = sorted(d["function"]["name"] for d in defs)
        self.assertEqual(names, ["weather", "web-search"])


if __name__ == "__main__":
    unittest.main()
