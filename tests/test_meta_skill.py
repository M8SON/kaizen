import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import meta_skill
from core.meta_skill import MetaSkillExecutor, _derive_skill_name, _run_claude_code


class FakeVoice:
    def __init__(self, transcripts):
        self.transcripts = list(transcripts)
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def listen(self, max_wait_seconds):
        if self.transcripts:
            return self.transcripts.pop(0)
        return ""


class FakeOrchestrator:
    def __init__(self):
        self.reload_count = 0

    def reload_skills(self):
        self.reload_count += 1


class FakeBuilder:
    def __init__(self):
        self.built = []

    def build(self, skill_dir, image):
        self.built.append((Path(skill_dir).name, image))


def _write_demo_skill(config="image: kaizen/{name}:latest\nenv_passthrough:\n  - DEMO_KEY\n"):
    seen = {}

    def write(skill_name, description, skill_dir):
        seen["skill_dir"] = skill_dir
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: Demo skill\n---\n\n# Demo\n",
            encoding="utf-8",
        )
        (skill_dir / "config.yaml").write_text(config.format(name=skill_name), encoding="utf-8")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "Dockerfile").write_text(
            'FROM kaizen/base:latest\nCOPY app.py /app/app.py\nWORKDIR /app\nCMD ["python", "app.py"]\n',
            encoding="utf-8",
        )
        (skill_dir / "scripts" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        return True, "ok"

    return write, seen


class AuthoredFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.authored_root = Path(self._tmp.name) / "authored"
        patcher = patch.object(meta_skill, "AUTHORED_ROOT", self.authored_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _executor(self, transcripts, write):
        self.orchestrator = FakeOrchestrator()
        self.builder = FakeBuilder()
        self.voice = FakeVoice(transcripts)
        return MetaSkillExecutor(
            voice=self.voice,
            orchestrator=self.orchestrator,
            run_claude_code=write,
            builder=self.builder,
        )

    def test_authored_skill_installs_to_authored_root(self):
        write, seen = _write_demo_skill()
        executor = self._executor(
            ["confirm create", "confirm install", "confirm build", "confirm restart"], write,
        )
        result = executor.run({"description": "Create demo skill"})

        self.assertEqual(result, "Skill create demo skill is now active.")
        installed = self.authored_root / "create-demo-skill"
        self.assertTrue((installed / "SKILL.md").exists())
        self.assertTrue((installed / ".install.json").exists())
        self.assertEqual(self.builder.built, [("create-demo-skill", "kaizen/create-demo-skill:latest")])
        self.assertEqual(self.orchestrator.reload_count, 1)
        self.assertTrue(any("DEMO_KEY" in s for s in self.voice.spoken))
        # Claude Code wrote into a throwaway staging dir, removed afterwards.
        self.assertFalse(meta_skill.REPO_ROOT in seen["skill_dir"].parents)
        self.assertFalse(seen["skill_dir"].exists())

    def test_cancel_at_create_gate_never_runs_claude_code(self):
        write = MagicMock()
        executor = self._executor(["cancel"], write)
        result = executor.run({"description": "Create demo skill"})
        self.assertEqual(result, "Skill creation cancelled.")
        write.assert_not_called()

    def test_symlink_in_staged_skill_aborts(self):
        write, _ = _write_demo_skill()

        def write_with_link(skill_name, description, skill_dir):
            write(skill_name, description, skill_dir)
            (skill_dir / "scripts" / "env").symlink_to("/etc/passwd")
            return True, "ok"

        executor = self._executor(["confirm create", "confirm install"], write_with_link)
        result = executor.run({"description": "Create demo skill"})
        self.assertIn("Security check failed", result)
        self.assertFalse((self.authored_root / "create-demo-skill").exists())

    def test_unexpected_top_level_file_aborts(self):
        write, _ = _write_demo_skill()

        def write_extra(skill_name, description, skill_dir):
            write(skill_name, description, skill_dir)
            (skill_dir / "run.sh").write_text("echo hi\n")
            return True, "ok"

        executor = self._executor(["confirm create"], write_extra)
        result = executor.run({"description": "Create demo skill"})
        self.assertIn("Security check failed", result)

    def test_native_config_rejected_at_authored_tier(self):
        write, _ = _write_demo_skill(config="type: native\n")
        executor = self._executor(["confirm create", "confirm install"], write)
        result = executor.run({"description": "Create demo skill"})
        self.assertEqual(result, "Skill install failed.")
        self.assertFalse((self.authored_root / "create-demo-skill").exists())


class DeriveSkillNameTests(unittest.TestCase):
    def test_kebab_case_and_collision_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / "a", Path(tmp) / "b"]
            (roots[1] / "random-joke-teller").mkdir(parents=True)
            with patch.object(meta_skill, "_skill_roots", return_value=roots):
                self.assertEqual(
                    _derive_skill_name("a random joke teller"), "random-joke-teller-2"
                )


class RunClaudeCodeSandboxTests(unittest.TestCase):
    def test_claude_code_is_scoped_to_staging_dir_with_minimal_env(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test", "BRAVE_API_KEY": "secret"}), \
             patch("core.meta_skill.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
            ok, _ = _run_claude_code("demo", "does demo things", Path(tmp))

        self.assertTrue(ok)
        args, kwargs = run.call_args
        cmd = args[0]
        self.assertEqual(kwargs["cwd"], str(Path(tmp)))
        for rule in ("Read(./**)", "Write(./**)", "Edit(./**)"):
            self.assertIn(rule, cmd)
        self.assertNotIn("Read", cmd)  # no unscoped Read rule
        self.assertIn("Bash", cmd[cmd.index("--disallowedTools") + 1])
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertEqual(kwargs["env"].get("ANTHROPIC_API_KEY"), "sk-test")
        self.assertNotIn("BRAVE_API_KEY", kwargs["env"])


if __name__ == "__main__":
    unittest.main()
