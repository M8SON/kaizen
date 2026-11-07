"""Tests for PromptBuilder persona-name derivation from wake-word env."""

import os
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.prompt_builder import PromptBuilder, persona_name_from_env


_WAKE_ENV_VARS = ("WAKE_WORD_MODEL",)


class _WakeEnv:
    """Context manager that scrubs and restores wake-related env vars."""

    def __init__(self, **overrides):
        self.overrides = overrides
        self._saved = {}

    def __enter__(self):
        for var in _WAKE_ENV_VARS:
            self._saved[var] = os.environ.get(var)
            os.environ.pop(var, None)
        for k, v in self.overrides.items():
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for var in _WAKE_ENV_VARS:
            prior = self._saved.get(var)
            if prior is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prior


class TestPersonaFromEnv(unittest.TestCase):
    def test_hey_jarvis_yields_jarvis(self):
        with _WakeEnv(WAKE_WORD_MODEL="hey_jarvis"):
            self.assertEqual(persona_name_from_env(), "Jarvis")

    def test_alexa_yields_alexa(self):
        with _WakeEnv(WAKE_WORD_MODEL="alexa"):
            self.assertEqual(persona_name_from_env(), "Alexa")

    def test_hey_mycroft_yields_mycroft(self):
        with _WakeEnv(WAKE_WORD_MODEL="hey_mycroft"):
            self.assertEqual(persona_name_from_env(), "Mycroft")

    def test_default_is_jarvis_when_env_empty(self):
        # Default: WAKE_WORD_MODEL=hey_jarvis.
        with _WakeEnv():
            self.assertEqual(persona_name_from_env(), "Jarvis")


class TestPromptBuilderPersona(unittest.TestCase):
    def test_base_prompt_uses_persona_name_from_env(self):
        with _WakeEnv(WAKE_WORD_MODEL="hey_jarvis"):
            pb = PromptBuilder()
        self.assertEqual(pb.persona_name, "Jarvis")
        self.assertIn("Your name is Jarvis.", pb.BASE_PROMPT)
        self.assertNotIn("Your name is Computer.", pb.BASE_PROMPT)

    def test_explicit_persona_name_overrides_env(self):
        with _WakeEnv(WAKE_WORD_MODEL="hey_jarvis"):
            pb = PromptBuilder(persona_name="Custom")
        self.assertEqual(pb.persona_name, "Custom")
        self.assertIn("Your name is Custom.", pb.BASE_PROMPT)

    def test_build_emits_persona_in_full_prompt(self):
        with _WakeEnv(WAKE_WORD_MODEL="alexa"):
            pb = PromptBuilder()
        prompt = pb.build(skills={}, skipped_skills={})
        self.assertIn("Your name is Alexa.", prompt)


class TestOwnerName(unittest.TestCase):
    """No owner is built in: OWNER_NAME sets it, unset is neutral."""

    def test_owner_from_env_in_full_and_micro_prompts(self):
        with _WakeEnv(), mock.patch.dict(os.environ, {"OWNER_NAME": "the Lee family"}):
            pb = PromptBuilder()
        self.assertIn("You are the Lee family's personal voice assistant", pb.BASE_PROMPT)
        self.assertIn("the Lee family's personal voice assistant", pb.build_for_micro_tier())

    def test_unset_owner_is_neutral(self):
        with _WakeEnv(), mock.patch.dict(os.environ, {"OWNER_NAME": ""}):
            pb = PromptBuilder()
        self.assertIn("You are a personal voice assistant", pb.BASE_PROMPT)
        self.assertNotIn("Mason", pb.BASE_PROMPT + pb.build_for_micro_tier())

    def test_identity_answers_render_owner(self):
        from core.filler_classifier import DEFAULT_PATTERNS_PATH, load_answer_phrases
        with mock.patch.dict(os.environ, {"OWNER_NAME": "Sam"}):
            named = load_answer_phrases(DEFAULT_PATTERNS_PATH, "Jarvis")["identity"]
        with mock.patch.dict(os.environ, {"OWNER_NAME": ""}):
            neutral = load_answer_phrases(DEFAULT_PATTERNS_PATH, "Jarvis")["identity"]
        self.assertIn("I'm Jarvis, Sam's voice assistant. I run right here on a Raspberry Pi.", named)
        self.assertIn("I'm Jarvis, your voice assistant. I run right here on a Raspberry Pi.", neutral)
        self.assertFalse(any("{owners}" in p or "Mason" in p for p in named + neutral))


class TestBuildForGreeting(unittest.TestCase):
    """The greeting path skips memories, skills, and self-update guidance."""

    def test_includes_persona(self):
        with _WakeEnv(WAKE_WORD_MODEL="hey_jarvis"):
            pb = PromptBuilder()
        prompt = pb.build_for_greeting()
        self.assertIn("Your name is Jarvis.", prompt)

    def test_appends_startup_context(self):
        with _WakeEnv():
            pb = PromptBuilder()
        prompt = pb.build_for_greeting("It is Friday afternoon. 70F sunny.")
        self.assertIn("Friday afternoon", prompt)
        self.assertIn("--- Current Context ---", prompt)

    def test_omits_startup_context_when_blank(self):
        with _WakeEnv():
            pb = PromptBuilder()
        prompt = pb.build_for_greeting("")
        self.assertNotIn("--- Current Context ---", prompt)

    def test_omits_skill_section(self):
        from unittest.mock import MagicMock
        skill = MagicMock()
        skill.name = "weather"
        skill.description = "Get the weather"
        skill.instructions = "DETAILED SKILL BODY ABOUT WEATHER LOOKUPS"
        skill.frontmatter = {}
        with _WakeEnv():
            pb = PromptBuilder()
        # Even after building a full prompt with skills, the greeting path is
        # untouched — it never references the loaded skill set.
        pb.build(skills={"weather": skill}, skipped_skills={})
        prompt = pb.build_for_greeting()
        self.assertNotIn("DETAILED SKILL BODY", prompt)
        self.assertNotIn("Available Skills", prompt)

    def test_omits_memory_section(self):
        from unittest.mock import MagicMock
        memory = MagicMock()
        memory.load_for_prompt.return_value = "MASON LIKES TURBO TEA AT 3PM"
        with _WakeEnv():
            pb = PromptBuilder(memory_provider=memory)
        prompt = pb.build_for_greeting()
        self.assertNotIn("MASON LIKES TURBO TEA", prompt)
        self.assertNotIn("Remembered from past conversations", prompt)

    def test_meaningfully_smaller_than_full_prompt(self):
        # The whole point of the greeting path is to drop input tokens.
        from unittest.mock import MagicMock
        skill = MagicMock()
        skill.name = "weather"
        skill.description = "Get the weather"
        skill.instructions = "X" * 4000
        skill.frontmatter = {}
        memory = MagicMock()
        memory.load_for_prompt.return_value = "Y" * 1000
        with _WakeEnv():
            pb = PromptBuilder(memory_provider=memory)
        full = pb.build(skills={"weather": skill}, skipped_skills={})
        lean = pb.build_for_greeting("It is Friday.")
        self.assertLess(len(lean), len(full))
        self.assertNotIn("Y" * 100, lean)


if __name__ == "__main__":
    unittest.main()
