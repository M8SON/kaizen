import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import meta_skill
from core import skill_builder
from core.meta_skill import MetaSkillExecutor, _derive_skill_name


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


class AuthoredFlowTests(unittest.TestCase):
    """"Add a skill that does X" starts a background build; no spoken gates."""

    def _executor(self, start_build):
        self.voice = FakeVoice([])
        return MetaSkillExecutor(voice=self.voice, orchestrator=None, start_build=start_build)

    def test_description_starts_a_build_without_asking(self):
        started = []
        executor = self._executor(lambda description, name: started.append((description, name)))
        with patch.object(meta_skill, "_skill_roots", return_value=[]):
            result = executor.run({"description": "Roll some dice"})
        self.assertEqual(started, [("Roll some dice", "roll-some-dice")])
        self.assertIn("control panel", result)
        self.assertEqual(self.voice.spoken, [])  # nothing asked aloud

    def test_build_that_cant_start_is_reported(self):
        def busy(description, name):
            raise skill_builder.BuildError("another skill is still being built")
        result = self._executor(busy).run({"description": "Roll some dice"})
        self.assertIn("another skill is still being built", result)

    def test_source_starts_an_import_without_asking(self):
        imported = []
        executor = MetaSkillExecutor(voice=FakeVoice([]), orchestrator=None,
                                     start_import=imported.append)
        result = executor.run({"source": "https://github.com/foo/bar"})
        self.assertEqual(imported, ["https://github.com/foo/bar"])
        self.assertIn("control panel", result)
        self.assertEqual(executor.voice.spoken, [])

    def test_nothing_to_build(self):
        result = self._executor(lambda *a: None).run({"description": "  "})
        self.assertIn("describe", result)


class DeriveSkillNameTests(unittest.TestCase):
    def test_kebab_case_and_collision_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / "a", Path(tmp) / "b"]
            (roots[1] / "random-joke-teller").mkdir(parents=True)
            with patch.object(meta_skill, "_skill_roots", return_value=roots):
                self.assertEqual(
                    _derive_skill_name("a random joke teller"), "random-joke-teller-2"
                )


if __name__ == "__main__":
    unittest.main()
