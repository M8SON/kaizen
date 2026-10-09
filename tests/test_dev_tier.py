"""Symlinked (dev-tier) skills get the authored tier's security checks; a
symlink must not be a way around them."""

import tempfile
import unittest
from pathlib import Path

from core.skill_loader import SkillLoader


def _skill(root: Path, name: str, config: str):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Dev skill\n---\n\nBody.\n")
    (d / "config.yaml").write_text(config)
    (d / "scripts").mkdir()
    (d / "scripts" / "Dockerfile").write_text("FROM kaizen/base:latest\n")
    return d


class DevTierTests(unittest.TestCase):
    def _load(self, config):
        tmp = tempfile.mkdtemp()
        real = _skill(Path(tmp) / "src", "dev-demo", config)
        search = Path(tmp) / "authored"
        search.mkdir()
        (search / "dev-demo").symlink_to(real, target_is_directory=True)
        loader = SkillLoader(search_paths=[Path(tmp) / "bundled", search])
        loader.load_all()
        return loader

    def test_plain_docker_dev_skill_loads(self):
        loader = self._load("image: kaizen/dev-demo:latest\n")
        self.assertIn("dev-demo", loader.skills)
        self.assertEqual(loader.skills["dev-demo"].tier, "dev")

    def test_dev_skill_cannot_run_on_host(self):
        for config in ("type: native\n", "type: process\n"):
            with self.subTest(config=config):
                self.assertNotIn("dev-demo", self._load(config).skills)

    def test_dev_skill_volume_and_secret_checks_apply(self):
        for config in (
            "image: kaizen/dev-demo:latest\nvolumes:\n  - /:/host\n",
            "image: kaizen/dev-demo:latest\nenv_passthrough:\n  - ANTHROPIC_API_KEY\n",
            "image: kaizen/dev-demo:latest\ndevices:\n  - /dev/mem\n",
        ):
            with self.subTest(config=config):
                self.assertNotIn("dev-demo", self._load(config).skills)


if __name__ == "__main__":
    unittest.main()
