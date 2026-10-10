"""Each Claude request is logged: what the user said (as sent), the tools, and
the per-turn system text; optionally the full request to a JSONL file."""

import json
import logging
from unittest.mock import MagicMock, patch

from core.conversation_state import ConversationState
from core.tool_loop import ToolLoop
from tests.test_tool_loop_archive import _make_text_response


def _run(tmp_path, env):
    client = MagicMock()
    client.messages.create.return_value = _make_text_response("Sunny.")
    sl = MagicMock()
    sl.get_tool_definitions.return_value = [{"name": "weather"}, {"name": "spotify"}]
    loop = ToolLoop(client=client, model="claude-test", skill_loader=sl, container_manager=MagicMock(),
                    conversation_state=ConversationState(), memory_provider=None)
    with patch.dict("os.environ", env, clear=False), \
         patch("core.tool_loop.REQUEST_LOG_PATH", tmp_path / "claude_requests.jsonl"):
        loop.run(user_message="what the weather today", system_prompt="STABLE",
                 system_prompt_dynamic="Voice intent: weather (1.00).")


def test_summary_line_shows_user_text_tools_and_dynamic_system(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="core.tool_loop"):
        _run(tmp_path, {"KAIZEN_LOG_REQUESTS": "false"})
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Claude request"))
    assert "what the weather today" in line
    assert "weather, spotify" in line
    assert "Voice intent: weather (1.00)." in line
    assert not (tmp_path / "claude_requests.jsonl").exists()


def test_full_request_dumped_when_enabled(tmp_path):
    _run(tmp_path, {"KAIZEN_LOG_REQUESTS": "true"})
    rows = [json.loads(l) for l in (tmp_path / "claude_requests.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    req = rows[0]
    assert req["model"] == "claude-test"
    assert req["tools"] == ["weather", "spotify"]
    assert req["system"][0]["text"] == "STABLE"
    assert req["messages"][-1]["content"] == "what the weather today"
