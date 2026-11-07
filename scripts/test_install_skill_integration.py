#!/usr/bin/env python3
"""
Optional integration test for the real skill builder (core/skill_builder.py).

Asks install-skill for a disposable skill, then runs the build in-process:
the real coding agent in its Docker sandbox, the real validation, image build
and tests. Approves it the way the control panel does, into a throwaway
authored dir, and checks the skill loads.

Builds jobs under a temporary KAIZEN_PANEL_DIR, and by default removes the
skill and its images afterwards, so ~/.kaizen is left unchanged. Billed: one
agent run (about $0.10-0.15 with Claude Code).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ["KAIZEN_PANEL_DIR"] = tempfile.mkdtemp(prefix="kaizen-build-it-")

from core import meta_skill, skill_builder  # noqa: E402
from core.meta_skill import MetaSkillExecutor  # noqa: E402
from core.skill_loader import SkillLoader  # noqa: E402


def _check_prerequisites() -> list[str]:
    errors = []
    if not shutil.which("docker"):
        errors.append("docker not found in PATH")
    elif subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode != 0:
        errors.append("docker info failed (are you in the docker group?)")
    agent = skill_builder.load_agents().get(skill_builder.agent_name())
    if agent is None:
        errors.append(f"unknown SKILL_BUILDER {skill_builder.agent_name()!r}")
    elif agent.get("credential_env") and not os.environ.get(agent["credential_env"]):
        errors.append(f"{agent['credential_env']} not set (in the environment or .env)")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real skill builder integration test")
    parser.add_argument("--keep-artifacts", action="store_true",
                        help="Keep the built skill and its Docker image")
    parser.add_argument("--description", default=None,
                        help="Skill description (default: a disposable hello skill)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    errors = _check_prerequisites()
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    authored = Path(tempfile.mkdtemp(prefix="kaizen-authored-"))
    meta_skill.AUTHORED_ROOT = skill_builder.AUTHORED_ROOT = authored
    token = uuid.uuid4().hex[:8]
    description = args.description or (
        f"integration {token[:4]} {token[4:]} create a test skill that returns a short hello message")
    print(f"[info] description: {description}")

    started = []
    executor = MetaSkillExecutor(
        voice=None, orchestrator=None,
        start_build=lambda d, n: started.append(skill_builder.start(d, n, launch=lambda i: None)))
    print(f"[result] {executor.run({'description': description})}")
    if not started:
        print("error: no build started", file=sys.stderr)
        return 1
    job = started[0]
    name = job["name"]
    try:
        job = skill_builder.run(job["id"])
        for _ts, text in job["log"]:
            print(f"[build] {text}")
        if job["status"] != skill_builder.READY:
            print(f"error: build {job['status']}: {job['error']}", file=sys.stderr)
            return 1
        for t in job["tests"]:
            print(f"[test] {'ok ' if t['ok'] else 'FAIL'} {t['input']} -> {t['output'][:100]}")
        skill_builder.approve(job["id"])
        loader = SkillLoader(search_paths=[REPO_ROOT / "skills", authored])
        loader.load_all()
        if name not in loader.skills:
            print(f"error: approved skill '{name}' did not load", file=sys.stderr)
            return 1
        print(f"[ok] skill '{name}' built, tested, approved and loaded")
        return 0
    finally:
        if args.keep_artifacts:
            print(f"[info] kept {authored / name} and kaizen/{name}")
        else:
            shutil.rmtree(authored, ignore_errors=True)
            shutil.rmtree(os.environ["KAIZEN_PANEL_DIR"], ignore_errors=True)
            for tag in ("latest", "candidate"):
                subprocess.run(["docker", "image", "rm", "-f", f"kaizen/{name}:{tag}"],
                               capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
