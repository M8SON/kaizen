"""
Meta Skill Executor — allows users to install new Kaizen skills by voice.

Authoring ("add a skill that does X"):
  1. "confirm create" — before Claude Code runs (it costs API calls).
  2. Claude Code writes the skill into a throwaway staging directory outside
     the repo. Its file tools are scoped to that directory, Bash is denied,
     user/project Claude settings are ignored, and it gets a minimal
     environment (no .env secrets beyond its own API key).
  3. The staged tree is checked: expected entries only, no symlinks.
  4. The shared InstallPipeline validates it at the *authored* tier (no
     native execution, resource clamps, device allowlist, scoped volumes,
     Dockerfile allowlist), speaks a permission summary, and walks the
     "confirm install" / "confirm build" / "confirm restart" gates before
     copying it into ~/.kaizen/authored/<name>, building and reloading.

Installing an existing skill from a URL or path uses the same pipeline at the
imported tier.
"""

import os
import re
import shutil
import logging
import subprocess
import tempfile
import textwrap
from pathlib import Path

from core.skill_policy import TIER_AUTHORED, TIER_IMPORTED

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTHORED_ROOT = Path.home() / ".kaizen" / TIER_AUTHORED
IMPORTED_ROOT = Path.home() / ".kaizen" / TIER_IMPORTED
CONFIRM_TIMEOUT = 25  # seconds to wait for each spoken confirmation

# Claude Code tool rules: file tools only inside its cwd (the staging dir).
CLAUDE_CODE_ALLOWED_TOOLS = [
    "Read(./**)", "Write(./**)", "Edit(./**)", "Glob", "Grep", "WebSearch", "WebFetch",
]
# Entries a staged skill may contain at its top level.
ALLOWED_STAGED_ENTRIES = {"SKILL.md", "config.yaml", "scripts", "references", "assets"}
REFERENCE_SKILL = "skill-tells-random"


class MetaSkillExecutor:

    def __init__(
        self,
        voice,
        orchestrator,
        *,
        run_claude_code=None,
        builder=None,
    ):
        self.voice = voice
        self.orchestrator = orchestrator
        self._run_claude_code = run_claude_code or _run_claude_code
        self._builder = builder

    def _install(self, staging: Path, *, tier: str, install_root: Path, from_url: str = ""):
        """Run the shared InstallPipeline with voice gates.
        Returns (decision, reloaded)."""
        from core.install_pipeline import DockerBuilder, InstallPipeline

        outer = self
        reloaded = []

        class VoiceConfirmer:
            def confirm_gate(self, gate: str, summary: str) -> bool:
                outer._speak(
                    f"Ready to {gate}. {summary}. "
                    f"Say 'confirm {gate}' to continue, or 'cancel' to stop."
                )
                return outer._confirm(f"confirm {gate}")

        class OrchestratorReloader:
            def reload(self):
                outer.orchestrator.reload_skills()
                reloaded.append(True)

        install_root.mkdir(parents=True, exist_ok=True)
        pipeline = InstallPipeline(
            confirmer=VoiceConfirmer(),
            builder=self._builder or DockerBuilder(),
            reloader=OrchestratorReloader(),
            install_root=install_root,
        )
        if from_url:
            decision = pipeline.install_from_url(from_url, tier=tier)
        else:
            decision = pipeline.install_from_path(staging, tier=tier)
        return decision, bool(reloaded)

    # ── URL install branch ────────────────────────────────────────────────

    def _install_from_source(self, source: str) -> str:
        """
        Voice-driven install of an existing agentskills.io-format skill from
        a URL or path, at the imported tier.
        """
        from core.install_pipeline import InstallDecision

        if source.startswith(("http://", "https://")):
            decision, _ = self._install(
                Path(), tier=TIER_IMPORTED, install_root=IMPORTED_ROOT, from_url=source,
            )
        else:
            decision, _ = self._install(
                Path(source), tier=TIER_IMPORTED, install_root=IMPORTED_ROOT,
            )

        if decision == InstallDecision.INSTALLED:
            return "Skill installed."
        if decision == InstallDecision.CANCELLED:
            return "Skill install cancelled."
        return "Skill install failed."

    # ── Public entry point ────────────────────────────────────────────────

    def run(self, tool_input: dict) -> str:
        from core.install_pipeline import InstallDecision

        source = tool_input.get("source", "").strip()
        if source:
            return self._install_from_source(source)

        description = tool_input.get("description", "").strip()
        if not description:
            return "Please describe what the skill should do or provide a source URL."

        skill_name = _derive_skill_name(description)
        spoken_name = skill_name.replace("-", " ")

        self._speak(
            f"I will write a new skill called {spoken_name}. "
            f"Say 'confirm create' to continue, or 'cancel' to stop."
        )
        if not self._confirm("confirm create"):
            return "Skill creation cancelled."

        staging_root = Path(tempfile.mkdtemp(prefix="kaizen-author-"))
        try:
            skill_dir = staging_root / skill_name
            skill_dir.mkdir()
            self._speak("Writing skill files now. This may take a minute.")
            success, output = self._run_claude_code(skill_name, description, skill_dir)
            if not success:
                return f"Skill file generation failed. {output[:150]}"

            problems = _check_staged_skill(skill_dir)
            if problems:
                logger.warning("Rejected authored skill %s: %s", skill_name, problems)
                return "Security check failed: the generated skill contains unexpected files. Installation aborted."

            decision, reloaded = self._install(
                skill_dir, tier=TIER_AUTHORED, install_root=AUTHORED_ROOT,
            )
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

        if decision == InstallDecision.INSTALLED:
            if reloaded:
                return f"Skill {spoken_name} is now active."
            return f"Skill {spoken_name} is installed. Restart Kaizen to load it."
        if decision == InstallDecision.CANCELLED:
            return "Skill install cancelled."
        return "Skill install failed."

    # ── Helpers ───────────────────────────────────────────────────────────

    def _speak(self, text: str):
        if self.voice is not None:
            self.voice.speak(text)
        else:
            logger.info("[meta_skill] %s", text)

    def _confirm(self, expected_phrase: str) -> bool:
        """
        Listen for up to CONFIRM_TIMEOUT seconds.
        Returns True only if all words of expected_phrase appear in the transcript.
        Returns False on timeout, silence, 'cancel', or a non-matching response.
        """
        if self.voice is None:
            logger.info("[meta_skill] voice not available — auto-cancelling confirmation")
            return False

        transcript = self.voice.listen(max_wait_seconds=CONFIRM_TIMEOUT)

        if not transcript:
            self._speak("No response received. Cancelling.")
            return False

        t = transcript.lower()
        if "cancel" in t:
            return False

        required = expected_phrase.lower().split()
        pos = 0
        for word in required:
            idx = t.find(word, pos)
            if idx == -1:
                return False
            pos = idx + len(word)
        return True


# ── Module-level helpers ──────────────────────────────────────────────────────

def _skill_roots() -> list[Path]:
    return [REPO_ROOT / "skills", AUTHORED_ROOT, IMPORTED_ROOT]


def _derive_skill_name(description: str) -> str:
    """Derive a kebab-case name from the description (first 3 meaningful words)."""
    words = re.sub(r"[^a-z0-9 ]", "", description.lower()).split()
    # Drop common filler words
    stop = {"a", "an", "the", "that", "can", "to", "for", "and", "or", "i", "me"}
    words = [w for w in words if w not in stop][:3]
    name = "-".join(words)[:32].strip("-") or "new-skill"

    # Avoid collisions with skills in any tier
    existing = {
        p.name for root in _skill_roots() if root.is_dir() for p in root.iterdir()
    }
    base, i = name, 2
    while name in existing:
        name = f"{base}-{i}"
        i += 1
    return name


def _reference_skill_text() -> str:
    """The bundled reference skill's files, inlined because Claude Code
    cannot read outside its staging directory."""
    ref = REPO_ROOT / "skills" / REFERENCE_SKILL
    parts = []
    for rel in ("SKILL.md", "config.yaml", "scripts/Dockerfile", "scripts/app.py"):
        parts.append(f"--- {rel} ---\n{(ref / rel).read_text(encoding='utf-8')}")
    return "\n".join(parts)


def _claude_code_env() -> dict:
    """Minimal environment for Claude Code: no .env secrets except its own key."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER")
           if k in os.environ}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    return env


def _run_claude_code(skill_name: str, description: str, skill_dir: Path) -> tuple[bool, str]:
    """
    Run Claude Code in skill_dir (a staging directory outside the repo) to
    write the skill. File tools are scoped to skill_dir, Bash is denied, and
    user/project settings are ignored so no wider allow rules apply.
    """
    image_name = f"kaizen/{skill_name}:latest"

    prompt = textwrap.dedent(f"""
        You are writing a new Kaizen voice-assistant skill named '{skill_name}'.
        The user wants: {description}

        Write these files in the current directory (relative paths only):
          SKILL.md            — YAML frontmatter with name: {skill_name} and a
                                description, then when-to-use, an Inputs JSON
                                schema, and how to respond (spoken, no markdown)
          config.yaml         — image: {image_name}, env_passthrough (only the
                                API keys this skill needs), timeout_seconds
          scripts/Dockerfile  — MUST start with: FROM kaizen/base:latest
          scripts/app.py      — reads JSON from the SKILL_INPUT env var, prints
                                the result to stdout

        Rules:
          - Only these Dockerfile instructions: FROM, RUN (pip install or apt-get
            only), COPY (local files only), WORKDIR, CMD, ENV
          - Do not set type: native, volumes, or devices in config.yaml
          - Do not create any other files

        Reference skill (an existing bundled skill to follow):
        {{reference}}
    """).strip().replace("{reference}", _reference_skill_text())

    try:
        result = subprocess.run(
            [
                "claude",
                "--allowedTools", *CLAUDE_CODE_ALLOWED_TOOLS,
                "--disallowedTools", "Bash",
                "--setting-sources", "",
                "--output-format", "text",
                "-p", prompt,
            ],
            cwd=str(skill_dir),
            capture_output=True,
            text=True,
            timeout=300,
            env=_claude_code_env(),
        )
        success = result.returncode == 0
        output = result.stdout.strip() if success else (result.stderr or result.stdout).strip()
        return success, output
    except FileNotFoundError:
        return False, "Claude Code CLI not found. Install it with: npm install -g @anthropic-ai/claude-code"
    except subprocess.TimeoutExpired:
        return False, "Claude Code timed out after 5 minutes."


def _check_staged_skill(skill_dir: Path) -> list[str]:
    """Problems with a Claude-written skill tree: unexpected top-level
    entries, symlinks, or anything that isn't a regular file or directory."""
    problems = []
    for entry in skill_dir.iterdir():
        if entry.name not in ALLOWED_STAGED_ENTRIES:
            problems.append(f"unexpected entry {entry.name}")
    for p in skill_dir.rglob("*"):
        if p.is_symlink():
            problems.append(f"symlink {p.relative_to(skill_dir)}")
        elif not (p.is_file() or p.is_dir()):
            problems.append(f"special file {p.relative_to(skill_dir)}")
    return problems
