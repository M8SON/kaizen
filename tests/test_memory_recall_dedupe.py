"""Per-turn memory recall must not repeat memories already in the cached prompt."""

from unittest.mock import MagicMock

from core.conversation_state import ConversationState
from core.tool_loop import ToolLoop

STABLE = (
    "Persona.\n\n--- Remembered from past conversations ---\n"
    "location\n\nBurlington, Vermont\n\nfavorite color\n\nMason's favorite color is blue.\n"
)


def _loop(recalled):
    memory = MagicMock()
    memory.recall_for_message.return_value = recalled
    return ToolLoop(client=MagicMock(), model="m", skill_loader=MagicMock(),
                    container_manager=MagicMock(), conversation_state=ConversationState(),
                    memory_provider=memory)


def test_recall_fully_in_prompt_adds_nothing():
    loop = _loop("favorite color\n\nMason's favorite color is blue.\n\nlocation\n\nBurlington, Vermont")
    assert loop._augment_dynamic_block("CLOCK", "what's my color", STABLE) == "CLOCK"


def test_only_new_memories_are_added():
    loop = _loop("location\n\nBurlington, Vermont\n\ndog name\n\nThe dog is called Pip.")
    out = loop._augment_dynamic_block("CLOCK", "my dog", STABLE)
    assert "Relevant Memory Recall" in out
    assert "The dog is called Pip." in out and "dog name" in out
    assert "Burlington, Vermont" not in out


def test_uncached_path_dedupes_against_its_own_prompt():
    loop = _loop("location\n\nBurlington, Vermont")
    assert loop._augment_system_prompt(STABLE, "where am I") == STABLE
