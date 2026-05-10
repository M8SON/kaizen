import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install_systemd_service.sh"


class InstallSystemdServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INSTALL_SH.read_text(encoding="utf-8")

    def test_has_strict_bash_flags(self):
        self.assertIn("set -e", self.text)

    def test_copies_unit_to_user_config_dir(self):
        self.assertIn(".config/systemd/user", self.text)
        self.assertIn("config/systemd/miniclaw.service", self.text)

    def test_runs_daemon_reload(self):
        self.assertIn("systemctl --user daemon-reload", self.text)

    def test_enables_and_starts_unit(self):
        self.assertIn("systemctl --user enable --now miniclaw.service", self.text)

    def test_checks_and_enables_linger(self):
        self.assertIn("loginctl show-user", self.text)
        self.assertIn("loginctl enable-linger", self.text)

    def test_ensures_persistent_journal_dir(self):
        self.assertIn("/var/log/journal", self.text)

    def test_verifies_wait_online_service(self):
        self.assertIn("NetworkManager-wait-online", self.text)

    def test_refuses_when_run_sh_missing(self):
        self.assertIn("run.sh", self.text)

    def test_is_executable(self):
        self.assertTrue(INSTALL_SH.stat().st_mode & 0o111, "install script not executable")


if __name__ == "__main__":
    unittest.main()
