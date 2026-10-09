"""Untrusted skills run only images Kaizen builds locally from their own,
validated Dockerfile — never an arbitrary or pulled image — and installs
reject symlinks."""

import shutil
import tempfile
import unittest
from pathlib import Path

from core.container_manager import ContainerManager
from core.install_pipeline import InstallDecision, InstallPipeline
from core.skill_loader import SkillLoader
from core.skill_policy import TIER_AUTHORED, TIER_BUNDLED, TIER_IMPORTED
from core.skill_validator import SkillValidator
from tests.test_install_pipeline import FIXTURES, AlwaysApprove, NoopBuilder, NoopReloader


class ImageNameTests(unittest.TestCase):
    def _validate(self, image, tier):
        SkillValidator().validate_execution_config({"image": image}, tier=tier, skill_name="demo")

    def test_untrusted_must_use_own_local_image_name(self):
        for tier in (TIER_AUTHORED, TIER_IMPORTED):
            for image in ("docker.io/someone/demo:latest", "someone/demo", "kaizen/other:latest",
                          "kaizen/demo@sha256:abc", "ghcr.io/kaizen/demo:latest"):
                with self.subTest(tier=tier, image=image), self.assertRaises(ValueError):
                    self._validate(image, tier)
            self._validate("kaizen/demo:latest", tier)
            self._validate("kaizen/demo:v2", tier)

    def test_bundled_unrestricted(self):
        self._validate("kaizen/anything:latest", TIER_BUNDLED)


class DockerfileRequiredTests(unittest.TestCase):
    def test_untrusted_docker_skill_without_dockerfile_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            imported = Path(tmp) / "imported"
            shutil.copytree(FIXTURES / "good-skill", imported / "good-skill")
            (imported / "good-skill" / "scripts" / "Dockerfile").unlink()
            loader = SkillLoader(search_paths=[Path(tmp) / "bundled", Path(tmp) / "authored", imported])
            loader.load_all()
            self.assertNotIn("good-skill", loader.skills)


class NoPullTests(unittest.TestCase):
    def test_containers_never_pull(self):
        cmd = ContainerManager()._build_docker_cmd(image="kaizen/demo:latest")
        self.assertIn("--pull=never", cmd)


class SymlinkRejectionTests(unittest.TestCase):
    def test_install_rejects_symlinks_in_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "good-skill"
            shutil.copytree(FIXTURES / "good-skill", staging)
            (staging / "scripts" / "secrets").symlink_to(Path.home() / ".ssh")
            install_root = Path(tmp) / "imported"
            pipeline = InstallPipeline(confirmer=AlwaysApprove(), builder=NoopBuilder(),
                                       reloader=NoopReloader(), install_root=install_root)
            self.assertEqual(pipeline.install_from_path(staging, tier=TIER_IMPORTED),
                             InstallDecision.FAILED)
            self.assertFalse((install_root / "good-skill").exists())


if __name__ == "__main__":
    unittest.main()
