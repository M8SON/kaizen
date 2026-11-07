"""
Meta Skill Executor: the install-skill tool. Nothing is approved by voice.

- "Add a skill that does X" starts a background build (core.skill_builder):
  a coding agent writes and tests the skill in a Docker sandbox.
- "Install the skill at <url or path>" starts a background import: fetched,
  validated at the imported tier, built, and its own tests run.

Either way the skill waits on the control panel's Skills page until the user
approves it there.
"""

import re
import logging
from pathlib import Path

from core.skill_policy import TIER_AUTHORED, TIER_IMPORTED

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTHORED_ROOT = Path.home() / ".kaizen" / TIER_AUTHORED
IMPORTED_ROOT = Path.home() / ".kaizen" / TIER_IMPORTED

_REVIEW = ("When it's ready, review and approve it on the control panel's Skills "
           "page; nothing is installed until then.")


class MetaSkillExecutor:

    def __init__(self, voice, orchestrator, *, start_build=None, start_import=None):
        self.voice = voice
        self.orchestrator = orchestrator
        self._start_build = start_build
        self._start_import = start_import

    def run(self, tool_input: dict) -> str:
        from core import skill_builder

        source = tool_input.get("source", "").strip()
        if source:
            try:
                (self._start_import or skill_builder.start_import)(source)
            except (skill_builder.BuildError, OSError) as e:
                return f"Couldn't start installing the skill: {e}"
            return f"Fetching and checking that skill in the background. {_REVIEW}"

        description = tool_input.get("description", "").strip()
        if not description:
            return "Please describe what the skill should do or provide a source URL."

        skill_name = _derive_skill_name(description)
        try:
            (self._start_build or skill_builder.start)(description, skill_name)
        except (skill_builder.BuildError, OSError) as e:
            return f"Couldn't start building the skill: {e}"
        return (f"Building a skill called {skill_name.replace('-', ' ')} in the background. "
                f"It takes a few minutes. {_REVIEW}")


def _skill_roots() -> list[Path]:
    return [REPO_ROOT / "skills", AUTHORED_ROOT, IMPORTED_ROOT]


def _derive_skill_name(description: str) -> str:
    """Derive a kebab-case name from the description (first 3 meaningful words)."""
    words = re.sub(r"[^a-z0-9 ]", "", description.lower()).split()
    # Drop common filler words
    stop = {"a", "an", "the", "that", "can", "to", "for", "and", "or", "i", "me"}
    words = [w for w in words if w not in stop][:3]
    name = "-".join(words)[:32].strip("-") or "new-skill"

    # Avoid collisions with installed skills and builds not yet approved.
    from core import skill_builder
    existing = {
        p.name for root in _skill_roots() if root.is_dir() for p in root.iterdir()
    } | skill_builder.pending_names()
    base, i = name, 2
    while name in existing:
        name = f"{base}-{i}"
        i += 1
    return name
