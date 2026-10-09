"""`type: process` — trusted bundled skills run scripts/app.py as a host
subprocess instead of a Docker container."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.skill_policy import TIER_AUTHORED, TIER_BUNDLED, TIER_IMPORTED
from core.skill_validator import SkillValidator


class _Skill:
    def __init__(self, skill_dir, config, tier=TIER_BUNDLED, name="demo"):
        self.name = name
        self.skill_dir = str(skill_dir)
        self.execution_config = config
        self.tier = tier


def _make_skill(tmp, body, config=None):
    scripts = Path(tmp) / "demo" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "app.py").write_text(body)
    return _Skill(Path(tmp) / "demo", config or {"type": "process", "timeout_seconds": 5})


class ProcessTypeValidationTests(unittest.TestCase):
    def test_allowed_for_bundled(self):
        SkillValidator().validate_execution_config(
            {"type": "process", "env_passthrough": ["BRAVE_API_KEY"]},
            tier=TIER_BUNDLED, skill_name="demo",
        )

    def test_rejected_for_untrusted_tiers(self):
        for tier in (TIER_AUTHORED, TIER_IMPORTED):
            with self.subTest(tier=tier), self.assertRaises(ValueError):
                SkillValidator().validate_execution_config(
                    {"type": "process"}, tier=tier, skill_name="demo",
                )


class ProcessExecutionTests(unittest.TestCase):
    def _manager(self):
        from core.container_manager import ContainerManager
        return ContainerManager()

    def test_runs_app_with_skill_input_and_only_passthrough_env(self):
        body = (
            "import json, os\n"
            "inp = json.loads(os.environ['SKILL_INPUT'])\n"
            "print(inp['q'], os.environ.get('BRAVE_API_KEY'), os.environ.get('ANTHROPIC_API_KEY'))\n"
        )
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"BRAVE_API_KEY": "b", "ANTHROPIC_API_KEY": "sk"}):
            skill = _make_skill(tmp, body, {"type": "process", "env_passthrough": ["BRAVE_API_KEY"]})
            out = self._manager().execute_skill(skill, {"q": "hello"})
        self.assertEqual(out, "hello b None")

    def test_nonzero_exit_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = _make_skill(tmp, "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
            out = self._manager().execute_skill(skill, {})
        self.assertIn("Skill execution error: boom", out)

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = _make_skill(tmp, "import time\ntime.sleep(5)\n", {"type": "process", "timeout_seconds": 1})
            out = self._manager().execute_skill(skill, {})
        self.assertEqual(out, "Skill timed out after 1 seconds")

    def test_does_not_need_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = _make_skill(tmp, "print('ok')\n")
            m = self._manager()
            m.docker_available = False
            self.assertEqual(m.execute_skill(skill, {}), "ok")


class BundledSkillConfigTests(unittest.TestCase):
    def test_trusted_bundled_skills_skip_docker_but_scraper_and_example_do_not(self):
        import yaml
        root = Path(__file__).parent.parent / "skills"
        def kind(name):
            return yaml.safe_load((root / name / "config.yaml").read_text()).get("type", "docker")
        for name in ("weather", "web-search", "homebridge"):
            self.assertEqual(kind(name), "process", name)
            self.assertFalse((root / name / "scripts" / "Dockerfile").exists(), name)
        self.assertEqual(kind("playwright-scraper"), "docker")
        self.assertEqual(kind("skill-tells-random"), "docker")


if __name__ == "__main__":
    unittest.main()
