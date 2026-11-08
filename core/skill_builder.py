"""Build a skill with a coding agent in a Docker sandbox, test it, and leave
it for approval on the control panel.

"Add a skill that does X" starts a build (`start`), which runs in its own
process (`python -m core.skill_builder <id>`, a transient systemd user unit
so a Kaizen restart doesn't kill it):

  1. The agent (config/coding_agents.yaml) runs in a container that sees only
     the skill's staging dir at /work and its own credential. It may run
     anything there, so it tests its own work.
  2. The files are checked (expected entries only, no symlinks) and validated
     at the authored tier (core.install_pipeline.inspect_staged).
  3. The image is built as kaizen/<name>:candidate and the skill's
     references/tests.json run through the real skill container flags.
  4. A failure goes back to the agent, up to BUILD_ROUNDS rounds in all.

"Install the skill at <url or path>" (`start_import`) is the same job
without an agent: fetch, validate at the imported tier, build the candidate,
run the skill's own tests if it ships any.

A ready job waits in ~/.kaizen/builds/<id>/ until the panel approves it
(`approve`: installs to ~/.kaizen/<tier>/<name>, tags the image) or rejects
it. There is no spoken approval. State is job.json, written atomically; the
panel and Kaizen only read it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import yaml

from core import usage_log
from core.dockerfile_validator import with_wheels_only
from core.install_metadata import compute_skill_sha256
from core.skill_policy import TIER_AUTHORED, TIER_IMPORTED

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_FILE = REPO_ROOT / "config" / "coding_agents.yaml"
AUTHORED_ROOT = Path.home() / ".kaizen" / TIER_AUTHORED  # where skill_loader looks
IMPORTED_ROOT = Path.home() / ".kaizen" / TIER_IMPORTED
DEFAULT_AGENT = "claude-code"
REFERENCE_SKILL = "skill-tells-random"
BUILD_ROUNDS = 3
AGENT_TIMEOUT_S = 600
MAX_TESTS = 10
ALLOWED_STAGED_ENTRIES = {"SKILL.md", "config.yaml", "scripts", "references", "assets"}
# Left behind by agents running the code; removed before the tree check.
_JUNK = {"__pycache__", ".pytest_cache", ".claude", ".mypy_cache"}

BUILDING, READY, FAILED, INSTALLED, REJECTED = "building", "ready", "failed", "installed", "rejected"
FINISHED = (READY, FAILED)

_docker = subprocess.run  # patched in tests


class BuildError(Exception):
    """A build can't start, or a job can't be approved or rejected."""


# ── Paths and job state ─────────────────────────────────────────────────────

def builds_root() -> Path:
    return Path(os.environ.get("KAIZEN_PANEL_DIR", Path.home() / ".kaizen")) / "builds"


def _job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", job_id or ""):
        raise BuildError("no such build")
    return builds_root() / job_id


def load(job_id: str) -> dict:
    try:
        return json.loads((_job_dir(job_id) / "job.json").read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise BuildError("no such build") from e


def _save(job: dict) -> None:
    job["updated"] = time.time()
    path = _job_dir(job["id"]) / "job.json"
    tmp = path.with_name("job.json.tmp")
    tmp.write_text(json.dumps(job, indent=1))
    os.replace(tmp, path)


def _log(job: dict, text: str) -> None:
    logger.info("Skill build %s: %s", job["id"], text)
    job["log"].append([time.time(), text])
    _save(job)


def skill_dir(job: dict) -> Path:
    if not job.get("name"):
        raise BuildError("this job has no skill yet")
    return _job_dir(job["id"]) / job["name"]


def _root(job: dict) -> Path:
    return IMPORTED_ROOT if job.get("tier") == TIER_IMPORTED else AUTHORED_ROOT


def pending_names() -> set[str]:
    return {j["name"] for j in list_jobs() if j["status"] in (BUILDING, READY) and j.get("name")}


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False


def list_jobs(limit: int = 20) -> list[dict]:
    """Newest first. A building job whose worker has died is reported failed."""
    root = builds_root()
    jobs = []
    for d in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        try:
            job = load(d.name)
        except BuildError:
            continue
        if job["status"] == BUILDING and job.get("pid") and not _alive(job["pid"]):
            job["status"], job["error"] = FAILED, "The build stopped unexpectedly."
            _save(job)
        jobs.append(job)
        if len(jobs) >= limit:
            break
    return jobs


def take_announcements() -> list[str]:
    """Spoken notices for builds that finished since the last call."""
    notes = []
    for job in list_jobs():
        if job["status"] in FINISHED and not job.get("announced"):
            spoken = f"the {job['name'].replace('-', ' ')} skill" if job.get("name") else "that skill"
            verb = "install" if job.get("kind") == "import" else "build"
            notes.append(
                f"{spoken[0].upper() + spoken[1:]} is ready. Review and approve it on the control panel."
                if job["status"] == READY else
                f"I couldn't {verb} {spoken}. The control panel has the details."
            )
            job["announced"] = True
            _save(job)
    return notes


# ── Agents ──────────────────────────────────────────────────────────────────

def load_agents(path: Path = AGENTS_FILE) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def agent_name() -> str:
    return os.environ.get("SKILL_BUILDER", "").strip() or DEFAULT_AGENT


def _sandbox_image(name: str, agent: dict) -> str:
    """kaizen/builder-<agent>:<hash of its Dockerfile>, built on first use."""
    dockerfile = REPO_ROOT / agent["dockerfile"]
    tag = hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]
    image = f"kaizen/builder-{name}:{tag}"
    if _docker(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        result = _docker(["docker", "build", "-q", "-t", image, "-f", str(dockerfile),
                          str(dockerfile.parent)], capture_output=True, text=True, timeout=1200)
        if result.returncode != 0:
            raise BuildError(f"couldn't build the {name} sandbox: {result.stderr.strip()[-300:]}")
    return image


def _sandbox_cmd(job_id: str, image: str, agent: dict, prompt: str, work: Path) -> list[str]:
    uid = os.getuid()
    user = f"{uid}:{os.getgid()}" if uid != 0 else "65534:65534"
    cmd = [
        "docker", "run", "--rm", "--pull=never", "--name", f"kaizen-build-{job_id}",
        "--network=bridge", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user", user, "--memory=1g", "--cpus=2", "--pids-limit=512",
        "--read-only", "--tmpfs", "/tmp:rw,exec,size=512m", "-e", "HOME=/tmp/home",
        "-v", f"{work}:/work", "-w", "/work",
    ]
    if agent.get("credential_env"):
        cmd += ["-e", agent["credential_env"]]  # value from our env, not argv
    cmd.append(image)
    return cmd + [prompt if a == "{prompt}" else a for a in agent["command"]]


def _run_agent(job: dict, agent: dict, prompt: str) -> tuple[bool, str]:
    work = skill_dir(job)
    image = _sandbox_image(job["agent"], agent)
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    key = agent.get("credential_env")
    if key:
        if not os.environ.get(key):
            raise BuildError(f"{key} isn't set; add it on the control panel's Services page")
        env[key] = os.environ[key]
    try:
        result = _docker(_sandbox_cmd(job["id"], image, agent, prompt, work), env=env,
                         capture_output=True, text=True, timeout=AGENT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _docker(["docker", "kill", f"kaizen-build-{job['id']}"], capture_output=True)
        return False, f"the agent didn't finish within {AGENT_TIMEOUT_S // 60} minutes"
    text = result.stdout.strip()
    if agent.get("output") == "claude-json":
        text = _record_claude_json(text)
    if result.returncode != 0:
        return False, (text or result.stderr.strip())[-500:]
    return True, text


def _record_claude_json(stdout: str) -> str:
    """Log Claude Code's per-model usage; return its final message."""
    try:
        data = json.loads(stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return stdout
    for model, u in (data.get("modelUsage") or {}).items():
        usage_log.record("claude", model, "skill-build",
                         input_tokens=u.get("inputTokens"), output_tokens=u.get("outputTokens"),
                         cache_read_tokens=u.get("cacheReadInputTokens"),
                         cache_write_tokens=u.get("cacheCreationInputTokens"))
    return str(data.get("result", ""))


def _reference_skill_text() -> str:
    ref = REPO_ROOT / "skills" / REFERENCE_SKILL
    return "\n".join(f"--- {rel} ---\n{(ref / rel).read_text(encoding='utf-8')}"
                     for rel in ("SKILL.md", "config.yaml", "scripts/Dockerfile", "scripts/app.py"))


def _prompt(job: dict) -> str:
    name = job["name"]
    return textwrap.dedent(f"""
        You are writing a new Kaizen voice-assistant skill named '{name}'.
        The user asked for: {job['description']}

        Write these files in the current directory (relative paths only):
          SKILL.md            YAML frontmatter with name: {name} and a description,
                              then when to use it, an "## Inputs" YAML schema, and
                              how to respond (spoken aloud: short, no markdown)
          config.yaml         image: kaizen/{name}:latest, env_passthrough (only the
                              API keys this skill needs), timeout_seconds
          scripts/Dockerfile  must start with: FROM kaizen/base:latest
                              (python:3.11-slim plus requests)
          scripts/app.py      reads JSON from the SKILL_INPUT env var, prints the
                              result to stdout as one or two spoken sentences
          references/tests.json
                              2 to 5 tests: [{{"input": {{...}}, "expect": ["text the
                              output must contain", ...]}}]. Expect stable text,
                              not live values that change (prices, weather).

        Rules:
          - Dockerfile instructions allowed: FROM, RUN (pip install or apt-get
            only), COPY (local files only), WORKDIR, CMD, ENV.
          - Every RUN comes before any COPY or ENV. Use at most one pip install,
            as the last RUN command, naming packages directly (no requirements
            file). pip can only install pre-built wheels; choose packages that
            have one for linux aarch64.
          - Do not set type, volumes or devices in config.yaml.
          - No other files.
          - You can run commands here and reach the internet. Run every test
            (SKILL_INPUT='<json>' python scripts/app.py) and fix failures before
            you finish. Docker isn't available; the image is built after you.
          - API keys named in env_passthrough aren't available here; they are
            when the tests run again in the real container.

        Reference skill (an existing bundled skill to follow):
        {{reference}}
    """).strip().replace("{reference}", _reference_skill_text())


def _repair_prompt(job: dict, problem: str) -> str:
    return textwrap.dedent(f"""
        The Kaizen skill '{job['name']}' in the current directory failed a check
        after you finished:

        {problem}

        Fix it, keep the same files and rules, rerun the tests in
        references/tests.json, and finish.
    """).strip()


# ── Checks ──────────────────────────────────────────────────────────────────

def _clean_and_check_tree(d: Path, *, strict: bool = True) -> list[str]:
    """Problems with the tree: symlinks, special files and, when strict
    (agent-written skills), any top-level entry outside the skill layout."""
    for p in sorted(d.rglob("*"), reverse=True):
        if p.name in _JUNK and p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
    problems = [f"unexpected entry {e.name}" for e in d.iterdir()
                if strict and e.name not in ALLOWED_STAGED_ENTRIES]
    for p in d.rglob("*"):
        if p.is_symlink():
            problems.append(f"symlink {p.relative_to(d)}")
        elif not (p.is_file() or p.is_dir()):
            problems.append(f"special file {p.relative_to(d)}")
    return problems


def _load_tests(d: Path) -> list[dict]:
    try:
        tests = json.loads((d / "references" / "tests.json").read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"references/tests.json is missing or not JSON: {e}") from e
    if not isinstance(tests, list) or not tests:
        raise ValueError("references/tests.json must be a non-empty list")
    for t in tests:
        if not (isinstance(t, dict) and isinstance(t.get("input"), dict)
                and isinstance(t.get("expect"), list)
                and all(isinstance(x, str) for x in t["expect"])):
            raise ValueError('each test must be {"input": {...}, "expect": ["...", ...]}')
    return tests[:MAX_TESTS]


def _run_tests(job: dict, config: dict, image: str) -> list[dict]:
    from core.container_manager import ContainerManager
    cm = ContainerManager.__new__(ContainerManager)  # no docker probe or native handlers
    cm.memory_limit = ContainerManager.DEFAULT_MEMORY_LIMIT
    env = cm._collect_env_vars(config.get("env_passthrough", []), job.get("tier", TIER_AUTHORED))
    results = []
    for t in _load_tests(skill_dir(job)):
        cmd = cm._build_docker_cmd(
            image=image, name=f"kaizen-test-{uuid.uuid4().hex[:8]}", env_vars=env,
            input_data=json.dumps(t["input"]), memory=config.get("memory"),
            read_only=config.get("read_only", True), extra_tmpfs=config.get("extra_tmpfs", []),
        )
        out = cm._run_container(cmd, t["input"], config.get("timeout_seconds", 15))
        failed = out.startswith(("Skill execution error", "Skill timed out", "Skill execution failed"))
        missing = [x for x in t["expect"] if x.lower() not in out.lower()]
        results.append({"input": t["input"], "expect": t["expect"], "output": out[:500],
                        "ok": not failed and not missing})
    return results


def _check_round(job: dict) -> str | None:
    """None when the skill is ready, else the problem (for the agent to fix)."""
    from core.install_pipeline import inspect_staged
    d = skill_dir(job)
    tier = job.get("tier", TIER_AUTHORED)
    problems = _clean_and_check_tree(d, strict=tier == TIER_AUTHORED)
    if problems:
        return "The skill folder has entries that aren't allowed: " + "; ".join(problems)
    try:
        _name, config, summary = inspect_staged(d, tier=tier)
    except ValueError as e:
        return f"Validation failed: {e}"

    wanted = config.get("env_passthrough", [])
    missing = [k for k in wanted if not os.environ.get(k)]
    image = f"kaizen/{job['name']}:candidate"
    _log(job, "Building the image")
    build_file = _job_dir(job["id"]) / "Dockerfile.build"  # outside the reviewed files
    build_file.write_text(with_wheels_only((d / "scripts" / "Dockerfile").read_text(encoding="utf-8")))
    built = _docker(["docker", "build", "-q", "-t", image, "-f", str(build_file), str(d / "scripts")],
                    capture_output=True, text=True, timeout=900)
    if built.returncode != 0:
        return f"docker build failed:\n{built.stderr.strip()[-1500:]}"
    job["summary"] = summary.to_text()
    if tier == TIER_IMPORTED and wanted:  # no keys for downloaded code before approval
        job["tests"] = []
        job["tests_note"] = (f"Tests not run: this skill asks for {', '.join(wanted)}, "
                             "and a downloaded skill gets no keys until you approve it.")
        _log(job, job["tests_note"])
        return None
    if missing:
        job["tests"] = []
        job["tests_note"] = f"Tests not run: {', '.join(missing)} isn't set yet."
        _log(job, job["tests_note"])
        return None
    if tier == TIER_IMPORTED and not (d / "references" / "tests.json").exists():
        job["tests"] = []
        job["tests_note"] = "This skill doesn't ship tests (references/tests.json)."
        _log(job, job["tests_note"])
        return None
    try:
        _log(job, "Running the tests")
        job["tests"] = _run_tests(job, config, image)
    except ValueError as e:
        return str(e)
    failed = [t for t in job["tests"] if not t["ok"]]
    _log(job, f"{len(job['tests']) - len(failed)} of {len(job['tests'])} tests passed")
    if failed:
        return "These tests failed in the real container:\n" + "\n".join(
            f"input {json.dumps(t['input'])}: expected {t['expect']}, got {t['output']!r}"
            for t in failed)
    return None


# ── Entry points ────────────────────────────────────────────────────────────

def start(description: str, name: str, *, launch=None) -> dict:
    """Create a build job and start its worker. One build at a time."""
    agents = load_agents()
    agent = agent_name()
    if agent not in agents:
        raise BuildError(f"unknown coding agent {agent!r}; choose one of {', '.join(agents)}")
    if any(j["status"] == BUILDING for j in list_jobs()):
        raise BuildError("another skill is still being built; try again when it's done")
    job_id = _new_id()
    (builds_root() / job_id / name).mkdir(parents=True)
    job = {"id": job_id, "kind": "author", "tier": TIER_AUTHORED, "name": name,
           "description": description, "agent": agent, "status": BUILDING,
           "created": time.time(), "log": [], "round": 0, "tests": [], "summary": "",
           "error": "", "announced": False}
    return _begin(job, launch)


def start_import(source: str, *, launch=None) -> dict:
    """Create a job that installs an existing skill from an https URL (git or
    tarball) or a local path, for approval on the panel."""
    source = source.strip()
    if not (source.startswith("https://") or Path(source).expanduser().is_dir()):
        raise BuildError("give an https link to a git repository or tarball, or a folder on the Pi")
    if any(j["status"] == BUILDING for j in list_jobs()):
        raise BuildError("another skill is still being built; try again when it's done")
    job = {"id": _new_id(), "kind": "import", "tier": TIER_IMPORTED, "name": "",
           "description": "", "source": source, "agent": "", "status": BUILDING,
           "created": time.time(), "log": [], "round": 0, "tests": [], "summary": "",
           "error": "", "announced": False}
    (builds_root() / job["id"]).mkdir(parents=True)
    return _begin(job, launch)


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _begin(job: dict, launch) -> dict:
    job_id = job["id"]
    _save(job)
    try:
        (launch or _launch)(job_id)
    except (OSError, subprocess.SubprocessError) as e:
        job["status"], job["error"] = FAILED, f"couldn't start the build: {e}"
        job["announced"] = True
        _save(job)
        raise BuildError(job["error"]) from e
    return job


def _launch(job_id: str) -> None:
    cmd = [sys.executable, "-m", "core.skill_builder", job_id]
    if shutil.which("systemd-run"):
        setenv = [f"--setenv=KAIZEN_PANEL_DIR={os.environ['KAIZEN_PANEL_DIR']}"] \
            if os.environ.get("KAIZEN_PANEL_DIR") else []
        subprocess.run(["systemd-run", "--user", "--collect", "--quiet",
                        f"--unit=kaizen-skill-build-{job_id}", f"--working-directory={REPO_ROOT}",
                        *setenv, "--", *cmd], check=True, timeout=30)
    else:
        subprocess.Popen(cmd, cwd=REPO_ROOT, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(job_id: str) -> dict:
    """The worker: agent rounds until the checks pass or BUILD_ROUNDS run out."""
    job = load(job_id)
    job["pid"] = os.getpid()
    if job.get("kind") == "import":
        return _run_import(job)
    try:
        agent = load_agents()[job["agent"]]
        prompt = _prompt(job)
        for rnd in range(1, BUILD_ROUNDS + 1):
            job["round"] = rnd
            if rnd == 1 and not shutil.which("docker"):
                raise BuildError("Docker isn't available")
            _log(job, f"{job['agent']} is writing the skill" if rnd == 1
                 else f"{job['agent']} is fixing it (round {rnd} of {BUILD_ROUNDS})")
            ok, text = _run_agent(job, agent, prompt)
            if not ok:
                problem = f"The agent failed: {text}"
                _log(job, problem)
                raise BuildError(problem)
            problem = _check_round(job)
            if problem is None:
                job["sha256"] = compute_skill_sha256(skill_dir(job))
                job["status"] = READY
                _log(job, "Ready for review")
                return job
            _log(job, problem.splitlines()[0])
            job["error"] = problem
            prompt = _repair_prompt(job, problem)
        raise BuildError(f"Still failing after {BUILD_ROUNDS} rounds: {job['error'][:300]}")
    except Exception as e:  # noqa: BLE001 — every failure ends up in job.json
        job["status"], job["error"] = FAILED, str(e)
        _log(job, f"Failed: {e}")
        return job


def _skill_folder(d: Path) -> Path:
    """d, or its one subfolder holding a SKILL.md."""
    if (d / "SKILL.md").is_file():
        return d
    subdirs = [p for p in d.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()]
    if len(subdirs) != 1:
        raise BuildError("no SKILL.md at the top of that skill")
    return subdirs[0]


def _run_import(job: dict) -> dict:
    """Fetch, validate at the imported tier, build, run the shipped tests."""
    from core.install_pipeline import fetch_skill
    from core.skill_validator import SkillValidator
    try:
        if not shutil.which("docker"):
            raise BuildError("Docker isn't available")
        work = _job_dir(job["id"]) / "fetch"
        work.mkdir()
        _log(job, f"Fetching {job['source']}")
        if job["source"].startswith("https://"):
            fetched = _skill_folder(fetch_skill(job["source"], work))
        else:
            fetched = work / "staging"
            shutil.copytree(_skill_folder(Path(job["source"]).expanduser()), fetched, symlinks=True)
        problems = _clean_and_check_tree(fetched, strict=False)  # before reading anything in it
        if problems:
            raise BuildError("The skill folder has entries that aren't allowed: " + "; ".join(problems))
        fm, _ = SkillValidator().parse_frontmatter((fetched / "SKILL.md").read_text(encoding="utf-8"))
        name = fm.get("name") if isinstance(fm, dict) else None
        if not name or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(name)):
            raise BuildError("SKILL.md has no valid kebab-case name")
        installed = {p.name for root in (REPO_ROOT / "skills", AUTHORED_ROOT, IMPORTED_ROOT)
                     if root.is_dir() for p in root.iterdir()}
        if name in installed or name in pending_names():
            raise BuildError(f"a skill called {name} already exists")
        shutil.move(str(fetched), str(_job_dir(job["id"]) / name))
        shutil.rmtree(work, ignore_errors=True)
        job["name"], job["description"] = name, str(fm.get("description", ""))[:300]
        problem = _check_round(job)
        if problem and not job.get("tests"):
            raise BuildError(problem)
        job["sha256"] = compute_skill_sha256(skill_dir(job))
        job["status"] = READY
        _log(job, "Ready for review" + (" (some tests failed)" if problem else ""))
    except Exception as e:  # noqa: BLE001 — every failure ends up in job.json
        job["status"], job["error"] = FAILED, str(e)
        _log(job, f"Failed: {e}")
    return job


def files(job: dict, limit: int = 64 * 1024) -> list[tuple[str, str]]:
    """The skill's files for review, as (relative path, text)."""
    d = skill_dir(job)
    out = []
    for p in sorted(d.rglob("*")) if d.is_dir() else []:
        if p.is_file() and not p.is_symlink():
            data = p.read_bytes()
            text = data[:limit].decode("utf-8", errors="replace")
            if len(data) > limit:
                text += f"\n\n[{len(data) - limit} more bytes not shown]"
            out.append((str(p.relative_to(d)), text))
    return out


def approve(job_id: str) -> dict:
    """Install a ready build exactly as reviewed."""
    from core.install_pipeline import inspect_staged, place_skill
    job = load(job_id)
    if job["status"] != READY:
        raise BuildError(f"this build is {job['status']}, not ready")
    d = skill_dir(job)
    if compute_skill_sha256(d) != job.get("sha256"):
        raise BuildError("the files changed after the build; build it again")
    name, config, _ = inspect_staged(d, tier=job.get("tier", TIER_AUTHORED))
    root = _root(job)
    if (root / name).exists():
        raise BuildError(f"a skill called {name} is already installed")
    candidate = f"kaizen/{name}:candidate"
    tagged = _docker(["docker", "tag", candidate, config["image"]], capture_output=True, text=True)
    if tagged.returncode != 0:
        raise BuildError(f"couldn't tag the image: {tagged.stderr.strip()[:200]}")
    _docker(["docker", "rmi", candidate], capture_output=True)
    root.mkdir(parents=True, exist_ok=True)
    place_skill(d, root, name, source=job.get("source") or f"skill-builder:{job['agent']}:{job_id}")
    shutil.rmtree(d, ignore_errors=True)
    job["status"] = INSTALLED
    _log(job, "Approved and installed")
    return job


def reject(job_id: str) -> dict:
    job = load(job_id)
    if job["status"] not in FINISHED:
        raise BuildError(f"this build is {job['status']}")
    if job.get("name"):
        _docker(["docker", "rmi", f"kaizen/{job['name']}:candidate"], capture_output=True)
        shutil.rmtree(skill_dir(job), ignore_errors=True)
    shutil.rmtree(_job_dir(job_id) / "fetch", ignore_errors=True)
    job["status"] = REJECTED
    _log(job, "Rejected")
    return job


if __name__ == "__main__":
    from dotenv import load_dotenv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    load_dotenv(REPO_ROOT / ".env")
    run(sys.argv[1])
