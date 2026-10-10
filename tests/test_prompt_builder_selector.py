"""Tests for the slim micro-tier prompt. (Per-skill expansion in the full
prompt was removed: skill guidance now lives in the tool definitions —
see test_slim_prompt.py.)"""

import sys
import types
import unittest

sys.modules.setdefault(
    "anthropic", types.SimpleNamespace(NOT_GIVEN=object(), Anthropic=object)
)


def test_build_for_micro_tier_returns_slim_prompt():
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder()
    prompt = pb.build_for_micro_tier()
    assert "voice assistant" in prompt.lower()
    # Persona name is included so the model identifies correctly when asked
    assert pb.persona_name in prompt
    # Heavy persona scaffolding is intentionally absent
    assert "warm and direct" not in prompt


def test_build_for_micro_tier_token_estimate_under_budget():
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder()
    prompt = pb.build_for_micro_tier()
    # Use the same heuristic as the rest of the codebase (~4 chars/token).
    estimated = max(1, len(prompt) // 4)
    assert estimated < 100, f"slim prompt was {estimated} estimated tokens"


def test_build_for_micro_tier_does_not_include_skill_bodies():
    """The micro-tier prompt must not contain skill markdown — tools are
    delivered via the API's tools parameter (top-K filtered), not bodies."""
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder()
    prompt = pb.build_for_micro_tier()
    assert "--- Skill Instructions ---" not in prompt
    assert "--- Remembered from past conversations ---" not in prompt
    assert "Unavailable Skills" not in prompt


if __name__ == "__main__":
    unittest.main()
