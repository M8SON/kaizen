"""Tool-first: Kaizen runs the Jev-classified tool before Claude, so Claude's
first round only phrases the answer."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.conversation_state import ConversationState
from core.tool_loop import ToolLoop


def _loop(tool_names=("weather",)):
    skill_loader = MagicMock()
    skill_loader.get_tool_definitions.return_value = [{"name": n} for n in tool_names]
    skill_loader.get_skill.side_effect = lambda name: MagicMock(name=name)
    container_manager = MagicMock()
    container_manager.execute_skill.return_value = '{"temperature": "60F"}'
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="It's 60 and overcast.")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=5, output_tokens=5),
    )
    state = ConversationState()
    loop = ToolLoop(
        client=client, model="m", skill_loader=skill_loader,
        container_manager=container_manager, conversation_state=state,
    )
    return loop, client, container_manager, state


PREFETCH = {"tool": "weather", "input": {"query": "Burlington, Vermont"}}


def test_prefetch_runs_tool_then_one_claude_round():
    loop, client, cm, state = _loop()
    archived = []
    result = loop.run(
        user_message="to the weather today", system_prompt="sys", prefetch=PREFETCH,
        archive_callback=lambda u, acts, r: archived.append(acts),
    )

    assert result == "It's 60 and overcast."
    cm.execute_skill.assert_called_once()
    assert cm.execute_skill.call_args.args[1] == {"query": "Burlington, Vermont"}
    client.messages.create.assert_called_once()  # one Claude round, not two

    # Claude saw: user text, its "own" tool_use, the tool_result.
    sent = client.messages.create.call_args.kwargs["messages"]
    assert sent[0] == {"role": "user", "content": "to the weather today"}
    tool_use = sent[1]["content"][0]
    assert sent[1]["role"] == "assistant" and tool_use["type"] == "tool_use"
    assert tool_use["name"] == "weather" and tool_use["id"].startswith("toolu_kaizen_")
    result_block = sent[2]["content"][0]
    assert result_block["type"] == "tool_result" and result_block["tool_use_id"] == tool_use["id"]
    assert archived[0][0]["name"] == "weather"


def test_prefetch_skipped_when_tool_not_offered():
    loop, client, cm, state = _loop(tool_names=("web-search",))
    loop.run(user_message="weather", system_prompt="sys", prefetch=PREFETCH)
    cm.execute_skill.assert_not_called()
    assert client.messages.create.call_args.kwargs["messages"] == [
        {"role": "user", "content": "weather"}
    ]


def test_no_prefetch_is_unchanged():
    loop, client, cm, state = _loop()
    loop.run(user_message="hi", system_prompt="sys")
    cm.execute_skill.assert_not_called()
    assert len(client.messages.create.call_args.kwargs["messages"]) == 1
