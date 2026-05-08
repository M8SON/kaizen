"""Tests for PromptBuilder persona-name derivation from wake-word env."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.prompt_builder import PromptBuilder, persona_name_from_env


_WAKE_ENV_VARS = ("WAKE_BACKEND", "WAKE_WORD_MODEL", "WAKE_PHRASE")


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
    def test_openwakeword_hey_jarvis_yields_jarvis(self):
        with _WakeEnv(WAKE_BACKEND="openwakeword", WAKE_WORD_MODEL="hey_jarvis"):
            self.assertEqual(persona_name_from_env(), "Jarvis")

    def test_openwakeword_alexa_yields_alexa(self):
        with _WakeEnv(WAKE_BACKEND="openwakeword", WAKE_WORD_MODEL="alexa"):
            self.assertEqual(persona_name_from_env(), "Alexa")

    def test_openwakeword_hey_mycroft_yields_mycroft(self):
        with _WakeEnv(WAKE_BACKEND="openwakeword", WAKE_WORD_MODEL="hey_mycroft"):
            self.assertEqual(persona_name_from_env(), "Mycroft")

    def test_whisper_backend_uses_wake_phrase(self):
        with _WakeEnv(WAKE_BACKEND="whisper", WAKE_PHRASE="computer"):
            self.assertEqual(persona_name_from_env(), "Computer")

    def test_whisper_backend_takes_last_token_of_multiword_phrase(self):
        with _WakeEnv(WAKE_BACKEND="whisper", WAKE_PHRASE="hey computer"):
            self.assertEqual(persona_name_from_env(), "Computer")

    def test_default_is_jarvis_when_env_empty(self):
        # Defaults: WAKE_BACKEND=openwakeword, WAKE_WORD_MODEL=hey_jarvis.
        with _WakeEnv():
            self.assertEqual(persona_name_from_env(), "Jarvis")


class TestPromptBuilderPersona(unittest.TestCase):
    def test_base_prompt_uses_persona_name_from_env(self):
        with _WakeEnv(WAKE_BACKEND="openwakeword", WAKE_WORD_MODEL="hey_jarvis"):
            pb = PromptBuilder()
        self.assertEqual(pb.persona_name, "Jarvis")
        self.assertIn("Your name is Jarvis.", pb.BASE_PROMPT)
        self.assertNotIn("Your name is Computer.", pb.BASE_PROMPT)

    def test_explicit_persona_name_overrides_env(self):
        with _WakeEnv(WAKE_BACKEND="openwakeword", WAKE_WORD_MODEL="hey_jarvis"):
            pb = PromptBuilder(persona_name="Custom")
        self.assertEqual(pb.persona_name, "Custom")
        self.assertIn("Your name is Custom.", pb.BASE_PROMPT)

    def test_build_emits_persona_in_full_prompt(self):
        with _WakeEnv(WAKE_BACKEND="whisper", WAKE_PHRASE="computer"):
            pb = PromptBuilder(max_skill_tokens=None)
        prompt = pb.build(skills={}, skipped_skills={})
        self.assertIn("Your name is Computer.", prompt)


if __name__ == "__main__":
    unittest.main()
