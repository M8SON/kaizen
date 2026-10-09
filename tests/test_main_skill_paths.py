"""main.build_skill_paths must scan the tier directories installers write to."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class BuildSkillPaths(unittest.TestCase):
    def test_default_scans_bundled_authored_imported(self):
        from main import build_skill_paths
        from core.skill_loader import SkillLoader

        self.assertEqual(build_skill_paths(None), SkillLoader.DEFAULT_SEARCH_PATHS)

    def test_installed_skill_is_loaded_with_its_tier(self):
        import tempfile
        from unittest.mock import patch
        from core.skill_loader import SkillLoader
        from main import build_skill_paths

        with tempfile.TemporaryDirectory() as tmp:
            imported = Path(tmp) / "imported"
            skill = imported / "hello-skill"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: hello-skill\ndescription: Says hello\n---\n\nSay hello.\n"
            )
            (skill / "config.yaml").write_text("type: docker\nimage: kaizen/hello-skill:latest\n")
            paths = [SkillLoader.DEFAULT_SEARCH_PATHS[0], Path(tmp) / "authored", imported]
            with patch.object(SkillLoader, "DEFAULT_SEARCH_PATHS", paths):
                loader = SkillLoader(search_paths=build_skill_paths(None))
                loader.load_all()
        self.assertIn("hello-skill", loader.skills)
        self.assertEqual(loader.skills["hello-skill"].tier, "imported")

    def test_extra_dir_is_untrusted_and_keeps_bundled_first(self):
        from main import build_skill_paths
        from core.skill_loader import SkillLoader

        paths = build_skill_paths("/tmp/extra")
        self.assertEqual(paths[:3], SkillLoader.DEFAULT_SEARCH_PATHS)
        self.assertEqual(paths[3], Path("/tmp/extra"))
