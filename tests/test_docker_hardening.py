"""Containers for untrusted skills: own network, no capabilities, non-root,
process limit, and a forced stop when the call times out."""

import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from core.container_manager import ContainerManager


class DockerCmdHardeningTests(unittest.TestCase):
    def setUp(self):
        self.cmd = ContainerManager()._build_docker_cmd(image="kaizen/demo:latest", name="kaizen-demo-x")

    def test_isolation_flags(self):
        self.assertIn("--network=bridge", self.cmd)
        self.assertNotIn("--network=host", self.cmd)
        self.assertIn("--cap-drop=ALL", self.cmd)
        self.assertIn("--security-opt=no-new-privileges", self.cmd)
        self.assertTrue(any(a.startswith("--pids-limit=") for a in self.cmd))
        self.assertEqual(self.cmd[self.cmd.index("--name") + 1], "kaizen-demo-x")
        self.assertIn("HOME=/tmp", self.cmd)

    def test_runs_as_non_root(self):
        user = self.cmd[self.cmd.index("--user") + 1]
        uid = user.split(":")[0]
        self.assertNotEqual(uid, "0")
        if os.getuid() != 0:
            self.assertEqual(user, f"{os.getuid()}:{os.getgid()}")

    def test_image_is_last(self):
        self.assertEqual(self.cmd[-1], "kaizen/demo:latest")


class DockerTimeoutKillTests(unittest.TestCase):
    def test_timeout_kills_container_by_name(self):
        cm = ContainerManager()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] == ["docker", "run"]:
                raise subprocess.TimeoutExpired(cmd, 1)
            return MagicMock(returncode=0)

        with patch("core.container_manager.subprocess.run", side_effect=fake_run):
            out = cm._run_container(["docker", "run", "--name", "kaizen-demo-x", "img"], {}, 1)

        self.assertEqual(out, "Skill timed out after 1 seconds")
        self.assertIn(["docker", "kill", "kaizen-demo-x"], [c[:3] for c in calls])


if __name__ == "__main__":
    unittest.main()
