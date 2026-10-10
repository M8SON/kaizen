"""Skill guidance reaches Claude through the (cached) tool definitions, not
the system prompt: full SKILL.md bodies are no longer sent each request."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.prompt_builder import PromptBuilder
from core.skill_loader import SkillLoader
from core.skill_validator import SkillValidator

REPO_SKILLS = Path(__file__).resolve().parent.parent / "skills"


class SystemPromptTests(unittest.TestCase):
    def test_system_prompt_has_no_skill_bodies(self):
        skill = MagicMock()
        skill.name, skill.description = "weather", "Get the weather"
        skill.instructions = "UNIQUE-SKILL-BODY-TEXT"
        skill.frontmatter = {}
        memory = MagicMock()
        memory.load_for_prompt.return_value = ""
        stable, dynamic = PromptBuilder(memory_provider=memory).build_cacheable_parts(
            {"weather": skill}, {})
        self.assertNotIn("UNIQUE-SKILL-BODY-TEXT", stable + dynamic)
        self.assertNotIn("Available Skills", stable + dynamic)
        self.assertEqual(dynamic, "")


class ToolNotesTests(unittest.TestCase):
    def test_tool_notes_section_is_appended_to_description(self):
        body = (
            "# X\n\n## When to use\nLONG ROUTING PROSE\n\n"
            "## Tool notes\nConfirm twice before calling.\n\n"
            "## How to respond\nRESPONSE PROSE\n"
        )
        td = SkillValidator().build_tool_definition("x", "Does x.", body)
        self.assertEqual(td["description"], "Does x.\n\nConfirm twice before calling.")

    def test_no_notes_keeps_plain_description(self):
        td = SkillValidator().build_tool_definition("x", "Does x.", "# X\n\n## When to use\nprose\n")
        self.assertEqual(td["description"], "Does x.")


class RepoSkillToolDefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = SkillLoader(search_paths=[REPO_SKILLS])
        loader.load_all()
        cls.loader = loader
        cls.tools = {t["name"]: t for t in loader.get_tool_definitions()}

    def test_schedule_and_recall_have_real_schemas(self):
        sched = self.tools["schedule"]["input_schema"]["properties"]
        self.assertEqual(set(sched["action"]["enum"]), {"create", "list", "cancel", "modify"})
        self.assertIn("cron", sched)
        self.assertIn("since", self.tools["recall-session"]["input_schema"]["properties"])

    def test_safety_protocols_reach_claude(self):
        self.assertIn("confirm", self.tools["set-env-var"]["description"].lower())
        self.assertIn("confirm", self.tools["schedule"]["description"].lower())

    def test_inert_update_skill_hints_not_offered(self):
        # No bundled skill opts in to self-update, so the tool can do nothing.
        self.assertNotIn("update-skill-hints", self.tools)


if __name__ == "__main__":
    unittest.main()
