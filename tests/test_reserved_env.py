"""Authored/imported skills must never receive Kaizen's own secrets."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.skill_policy import TIER_AUTHORED, TIER_BUNDLED, TIER_IMPORTED
from core.skill_validator import SkillValidator


class ReservedEnvValidationTests(unittest.TestCase):
    def _validate(self, env, tier):
        return SkillValidator().validate_execution_config(
            {"image": "kaizen/demo:latest", "env_passthrough": env},
            tier=tier, skill_name="demo",
        )

    def test_reserved_names_rejected_for_untrusted_tiers(self):
        for tier in (TIER_AUTHORED, TIER_IMPORTED):
            for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                         "ELEVENLABS_API_KEY", "HOMEBRIDGE_PASSWORD", "SPOTIFY_CLIENT_SECRET"):
                with self.subTest(tier=tier, name=name), self.assertRaises(ValueError):
                    self._validate([name], tier)

    def test_ordinary_service_key_allowed(self):
        self._validate(["BRAVE_API_KEY"], TIER_IMPORTED)

    def test_bundled_unaffected(self):
        self._validate(["HOMEBRIDGE_PASSWORD"], TIER_BUNDLED)


class ReservedEnvRuntimeTests(unittest.TestCase):
    def test_reserved_vars_not_passed_to_untrusted_container(self):
        from core.container_manager import ContainerManager

        cm = ContainerManager()
        env = {"ANTHROPIC_API_KEY": "sk", "BRAVE_API_KEY": "b"}
        with patch.dict(os.environ, env):
            untrusted = cm._collect_env_vars(["ANTHROPIC_API_KEY", "BRAVE_API_KEY"], TIER_IMPORTED)
            bundled = cm._collect_env_vars(["ANTHROPIC_API_KEY", "BRAVE_API_KEY"], TIER_BUNDLED)
        self.assertEqual(untrusted, {"BRAVE_API_KEY": "b"})
        self.assertEqual(bundled, env)


if __name__ == "__main__":
    unittest.main()
