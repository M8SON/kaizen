import unittest
from pathlib import Path


UNIT_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "systemd"
    / "kaizen.service"
)


class SystemdUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = UNIT_PATH.read_text(encoding="utf-8")

    def test_waits_for_network_online(self):
        self.assertIn("After=network-online.target", self.text)
        self.assertIn("Wants=network-online.target", self.text)

    def test_execstart_runs_run_sh_voice(self):
        self.assertIn("ExecStart=%h/kaizen/run.sh --voice", self.text)

    def test_working_directory_is_repo_root(self):
        self.assertIn("WorkingDirectory=%h/kaizen", self.text)

    def test_restart_policy_is_on_failure_with_5s_delay(self):
        self.assertIn("Restart=on-failure", self.text)
        self.assertIn("RestartSec=5", self.text)

    def test_install_target_is_default(self):
        self.assertIn("WantedBy=default.target", self.text)


if __name__ == "__main__":
    unittest.main()
