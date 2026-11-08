"""Skill builder (core/skill_builder.py): sandboxed agent, test/repair loop,
approval. Docker and the agent are faked; nothing here calls a real service."""

import json
import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import skill_builder as sb

DOCKERFILE = 'FROM kaizen/base:latest\nCOPY app.py /app/app.py\nWORKDIR /app\nCMD ["python", "app.py"]\n'


def write_skill(d: Path, name: str, *, config=None, tests=None, app="print('ok')\n"):
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Demo skill\n---\n\n# Demo\n")
    (d / "config.yaml").write_text(config or f"image: kaizen/{name}:latest\ntimeout_seconds: 10\n")
    (d / "scripts").mkdir(exist_ok=True)
    (d / "scripts" / "Dockerfile").write_text(DOCKERFILE)
    (d / "scripts" / "app.py").write_text(app)
    (d / "references").mkdir(exist_ok=True)
    (d / "references" / "tests.json").write_text(json.dumps(
        tests if tests is not None else [{"input": {"n": 1}, "expect": ["rolled"]}]))


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.build_rc = 0

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc = self.build_rc if cmd[:2] == ["docker", "build"] else 0
        return SimpleNamespace(returncode=rc, stdout="", stderr="boom" if rc else "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIZEN_PANEL_DIR", str(tmp_path / "kaizen"))
    monkeypatch.setattr(sb, "AUTHORED_ROOT", tmp_path / "authored")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("SKILL_BUILDER", raising=False)
    docker = FakeDocker()
    monkeypatch.setattr(sb, "_docker", docker)
    monkeypatch.setattr(sb.shutil, "which", lambda b: f"/usr/bin/{b}" if b == "docker" else None)
    outputs = {"value": "I rolled a 4."}
    # By path: another test reloads core.container_manager.
    monkeypatch.setattr("core.container_manager.ContainerManager._run_container",
                        lambda self, cmd, tool_input, timeout, **kw: outputs["value"])
    return SimpleNamespace(tmp=tmp_path, docker=docker, outputs=outputs)


def agent_script(*rounds):
    """A fake _run_agent: each call runs the next function on the skill dir."""
    prompts = []

    def run_agent(job, agent, prompt):
        prompts.append(prompt)
        rounds[len(prompts) - 1](sb.skill_dir(job), job["name"])
        return True, "done"
    run_agent.prompts = prompts
    return run_agent


def new_job(name="dice-roller"):
    return sb.start("roll dice", name, launch=lambda job_id: None)


def test_ready_after_one_round(env, monkeypatch):
    agent = agent_script(lambda d, n: write_skill(d, n))
    monkeypatch.setattr(sb, "_run_agent", agent)
    job = sb.run(new_job()["id"])
    assert job["status"] == sb.READY, job["error"]
    assert job["round"] == 1 and job["tests"][0]["ok"] and job["sha256"]
    assert "Install skill 'dice-roller' at tier 'authored'" in job["summary"]
    assert ["docker", "build", "-q", "-t", "kaizen/dice-roller:candidate"] == env.docker.calls[-1][:5]
    assert "roll dice" in agent.prompts[0] and "references/tests.json" in agent.prompts[0]


def test_failed_test_goes_back_to_the_agent(env, monkeypatch):
    def broken(d, n):
        write_skill(d, n)
        env.outputs["value"] = "Skill execution error: Traceback ..."

    def fixed(d, n):
        env.outputs["value"] = "I rolled a 2."
    agent = agent_script(broken, fixed)
    monkeypatch.setattr(sb, "_run_agent", agent)
    job = sb.run(new_job()["id"])
    assert job["status"] == sb.READY and job["round"] == 2
    assert "Traceback" in agent.prompts[1] and "tests failed" in agent.prompts[1]


def test_gives_up_after_three_rounds(env, monkeypatch):
    env.outputs["value"] = "nothing useful"
    monkeypatch.setattr(sb, "_run_agent", agent_script(*[lambda d, n: write_skill(d, n)] * 3))
    job = sb.run(new_job()["id"])
    assert job["status"] == sb.FAILED and job["round"] == 3
    assert "after 3 rounds" in job["error"]


def rewrite(d: Path, n: str):
    """A repair round that starts the tree over."""
    for p in d.iterdir():
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    write_skill(d, n)


@pytest.mark.parametrize("damage,expect", [
    (lambda d: (d / "scripts" / "env").symlink_to("/etc/passwd"), "symlink scripts/env"),
    (lambda d: (d / "run.sh").write_text("echo hi"), "unexpected entry run.sh"),
    (lambda d: (d / "config.yaml").write_text("type: native\n"), "Validation failed"),
    (lambda d: (d / "scripts" / "Dockerfile").write_text("FROM ubuntu\n"), "Dockerfile rejected"),
    (lambda d: (d / "references" / "tests.json").write_text("[]"), "non-empty list"),
])
def test_bad_trees_are_sent_back_not_built(env, monkeypatch, damage, expect):
    def bad(d, n):
        write_skill(d, n)
        damage(d)
    agent = agent_script(bad, rewrite)
    monkeypatch.setattr(sb, "_run_agent", agent)
    job = sb.run(new_job()["id"])
    assert expect in agent.prompts[1]
    assert job["status"] == sb.READY and job["round"] == 2


def test_junk_from_running_the_code_is_removed(env, monkeypatch):
    def write(d, n):
        write_skill(d, n)
        (d / "scripts" / "__pycache__").mkdir()
        (d / "scripts" / "__pycache__" / "app.cpython-311.pyc").write_bytes(b"x")
    monkeypatch.setattr(sb, "_run_agent", agent_script(write))
    job = sb.run(new_job()["id"])
    assert job["status"] == sb.READY
    assert not (sb.skill_dir(job) / "scripts" / "__pycache__").exists()


def test_tests_skipped_when_a_key_is_missing(env, monkeypatch):
    monkeypatch.delenv("DEMO_API_KEY", raising=False)
    monkeypatch.setattr(sb, "_run_agent", agent_script(lambda d, n: write_skill(
        d, n, config=f"image: kaizen/{n}:latest\nenv_passthrough: [DEMO_API_KEY]\n")))
    job = sb.run(new_job()["id"])
    assert job["status"] == sb.READY and job["tests"] == []
    assert "DEMO_API_KEY" in job["tests_note"]


def test_one_build_at_a_time_and_unknown_agent(env, monkeypatch):
    new_job()
    with pytest.raises(sb.BuildError, match="still being built"):
        new_job("other")
    monkeypatch.setenv("SKILL_BUILDER", "nope")
    with pytest.raises(sb.BuildError, match="unknown coding agent"):
        new_job("third")


def test_dead_worker_is_reported_failed(env):
    job = new_job()
    job["pid"] = 2 ** 22 + 12345  # not a running process
    sb._save(job)
    assert sb.list_jobs()[0]["status"] == sb.FAILED


def test_approve_installs_exactly_what_was_reviewed(env, monkeypatch):
    monkeypatch.setattr(sb, "_run_agent", agent_script(lambda d, n: write_skill(d, n)))
    job = sb.run(new_job()["id"])
    sb.approve(job["id"])
    installed = env.tmp / "authored" / "dice-roller"
    assert (installed / "SKILL.md").exists() and (installed / ".install.json").exists()
    assert ["docker", "tag", "kaizen/dice-roller:candidate", "kaizen/dice-roller:latest"] in env.docker.calls
    assert sb.load(job["id"])["status"] == sb.INSTALLED
    with pytest.raises(sb.BuildError, match="not ready"):
        sb.approve(job["id"])


def test_approve_refuses_files_changed_after_review(env, monkeypatch):
    monkeypatch.setattr(sb, "_run_agent", agent_script(lambda d, n: write_skill(d, n)))
    job = sb.run(new_job()["id"])
    (sb.skill_dir(job) / "scripts" / "app.py").write_text("import os; os.system('evil')\n")
    with pytest.raises(sb.BuildError, match="changed after the build"):
        sb.approve(job["id"])
    assert not (env.tmp / "authored" / "dice-roller").exists()


def test_reject_removes_files_and_image(env, monkeypatch):
    monkeypatch.setattr(sb, "_run_agent", agent_script(lambda d, n: write_skill(d, n)))
    job = sb.run(new_job()["id"])
    sb.reject(job["id"])
    assert ["docker", "rmi", "kaizen/dice-roller:candidate"] in env.docker.calls
    assert not sb.skill_dir(job).exists()
    assert sb.load(job["id"])["status"] == sb.REJECTED


def test_announced_once(env, monkeypatch):
    monkeypatch.setattr(sb, "_run_agent", agent_script(lambda d, n: write_skill(d, n)))
    sb.run(new_job()["id"])
    assert sb.take_announcements() == [
        "The dice roller skill is ready. Review and approve it on the control panel."]
    assert sb.take_announcements() == []


def test_job_ids_cant_escape_the_builds_dir(env):
    for bad in ("../../etc", "20261107-120000-abcdef/../x", ""):
        with pytest.raises(sb.BuildError):
            sb.load(bad)


def test_sandbox_sees_only_work_dir_and_its_own_key(tmp_path):
    agent = sb.load_agents()["claude-code"]
    cmd = sb._sandbox_cmd("20261107-120000-abcdef", "kaizen/builder-claude-code:x", agent,
                          "write it", tmp_path)
    mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
    assert mounts == [f"{tmp_path}:/work"]
    assert "--cap-drop=ALL" in cmd and "--read-only" in cmd and "--pull=never" in cmd
    assert not any("docker.sock" in a for a in cmd)
    i = cmd.index("ANTHROPIC_API_KEY")
    assert cmd[i - 1] == "-e"  # passed by name; the value comes from the environment
    assert not any(a.startswith("ANTHROPIC_API_KEY=") for a in cmd)
    assert cmd[cmd.index("kaizen/builder-claude-code:x") + 1:] == [
        "claude", "-p", "write it", "--output-format", "json", "--dangerously-skip-permissions"]


def test_claude_json_usage_is_logged(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    monkeypatch.setenv("USAGE_LOG_ENABLED", "true")
    monkeypatch.setenv("USAGE_LOG_PATH", str(db))
    out = json.dumps({"result": "All tests pass.", "modelUsage": {"claude-opus-5-5": {
        "inputTokens": 8, "outputTokens": 3194, "cacheReadInputTokens": 61085,
        "cacheCreationInputTokens": 8594}}})
    assert sb._record_claude_json(out) == "All tests pass."
    rows = sqlite3.connect(db).execute(
        "SELECT service, model, purpose, input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_tokens FROM usage").fetchall()
    assert rows == [("claude", "claude-opus-5-5", "skill-build", 8, 3194, 61085, 8594)]


def test_missing_credential_fails_clearly(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    job = sb.run(new_job()["id"])
    assert job["status"] == sb.FAILED and "ANTHROPIC_API_KEY isn't set" in job["error"]


def test_builder_dockerfile_builds_on_the_skill_base():
    text = (sb.REPO_ROOT / sb.load_agents()["claude-code"]["dockerfile"]).read_text()
    assert text.splitlines()[[l.startswith("FROM") for l in text.splitlines()].index(True)] \
        == "FROM kaizen/base:latest"
    assert os.path.basename(sb.AGENTS_FILE) == "coding_agents.yaml"


# --- imports (install from a URL or path) ---------------------------------

@pytest.fixture
def imported(env, monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "IMPORTED_ROOT", tmp_path / "imported")
    src = tmp_path / "src" / "repo"
    (src / "joke-teller").mkdir(parents=True)
    write_skill(src / "joke-teller", "joke-teller")
    (src / "README.md").write_text("hi")
    return src


def test_import_from_a_folder_is_ready_for_review(env, imported):
    job = sb.run(sb.start_import(str(imported), launch=lambda i: None)["id"])
    assert job["status"] == sb.READY, job["error"]
    assert job["name"] == "joke-teller" and job["tier"] == "imported"
    assert "tier 'imported'" in job["summary"] and job["tests"][0]["ok"]
    assert not (sb._job_dir(job["id"]) / "fetch").exists()
    sb.approve(job["id"])
    assert (env.tmp / "imported" / "joke-teller" / "SKILL.md").exists()
    assert not (env.tmp / "authored" / "joke-teller").exists()


def test_import_extra_files_allowed_but_not_symlinks(env, imported):
    (imported / "joke-teller" / "LICENSE").write_text("MIT")
    ok = sb.run(sb.start_import(str(imported / "joke-teller"), launch=lambda i: None)["id"])
    assert ok["status"] == sb.READY
    sb.reject(ok["id"])
    (imported / "joke-teller" / "scripts" / "key").symlink_to("/etc/passwd")
    bad = sb.run(sb.start_import(str(imported / "joke-teller"), launch=lambda i: None)["id"])
    assert bad["status"] == sb.FAILED and "symlink" in bad["error"]


def test_import_failing_tests_are_shown_not_hidden(env, imported):
    env.outputs["value"] = "nope"
    job = sb.run(sb.start_import(str(imported), launch=lambda i: None)["id"])
    assert job["status"] == sb.READY and not job["tests"][0]["ok"]


def test_import_without_tests_or_with_a_taken_name(env, imported):
    (imported / "joke-teller" / "references" / "tests.json").unlink()
    job = sb.run(sb.start_import(str(imported), launch=lambda i: None)["id"])
    assert job["status"] == sb.READY and "doesn't ship tests" in job["tests_note"]
    again = sb.run(sb.start_import(str(imported), launch=lambda i: None)["id"])
    assert again["status"] == sb.FAILED and "already exists" in again["error"]


def test_import_refuses_plain_http_and_missing_folders(env):
    for bad in ("http://example.com/x.git", "/no/such/folder"):
        with pytest.raises(sb.BuildError, match="https link"):
            sb.start_import(bad, launch=lambda i: None)


def test_import_failure_is_announced_without_a_name(env, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    job = sb.run(sb.start_import(str(empty), launch=lambda i: None)["id"])
    assert job["status"] == sb.FAILED and "no SKILL.md" in job["error"]
    assert sb.take_announcements() == [
        "I couldn't install that skill. The control panel has the details."]
    sb.reject(job["id"])
    assert sb.load(job["id"])["status"] == sb.REJECTED


def test_import_gets_no_keys_before_approval(env, imported, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "brave-secret")
    (imported / "joke-teller" / "config.yaml").write_text(
        "image: kaizen/joke-teller:latest\nenv_passthrough: [BRAVE_API_KEY]\ntimeout_seconds: 10\n")
    ran = []
    monkeypatch.setattr("core.container_manager.ContainerManager._run_container",
                        lambda self, cmd, *a, **kw: ran.append(cmd) or "ok")
    job = sb.run(sb.start_import(str(imported), launch=lambda i: None)["id"])
    assert job["status"] == sb.READY and job["tests"] == [] and ran == []
    assert "gets no keys" in job["tests_note"]


def test_import_symlinked_skill_md_is_not_read(env, tmp_path):
    src = tmp_path / "linked"
    src.mkdir()
    secret = tmp_path / "secret.env"
    secret.write_text("---\nname: leaked-secret\ndescription: x\n---\n")
    (src / "SKILL.md").symlink_to(secret)
    job = sb.run(sb.start_import(str(src), launch=lambda i: None)["id"])
    assert job["status"] == sb.FAILED and "symlink" in job["error"] and not job["name"]


def test_import_does_not_copy_a_folder_without_a_skill(env, tmp_path, monkeypatch):
    big = tmp_path / "home"
    (big / "documents").mkdir(parents=True)
    monkeypatch.setattr(sb.shutil, "copytree", lambda *a, **kw: pytest.fail("copied"))
    job = sb.run(sb.start_import(str(big), launch=lambda i: None)["id"])
    assert job["status"] == sb.FAILED and "no SKILL.md" in job["error"]


def test_reserved_image_names_cant_be_imported(env, imported):
    for name in ("base", "builder-claude-code"):
        shutil.rmtree(imported)
        (imported / name).mkdir(parents=True)
        write_skill(imported / name, name)
        job = sb.run(sb.start_import(str(imported), launch=lambda i: None)["id"])
        assert job["status"] == sb.FAILED and "reserved" in job["error"]
        assert not any(c[:2] == ["docker", "build"] for c in env.docker.calls)


def test_review_says_when_a_file_is_cut_short(env, monkeypatch):
    agent = agent_script(lambda d, n: write_skill(d, n, app="x = 1\n" * 20000))
    monkeypatch.setattr(sb, "_run_agent", agent)
    job = sb.run(new_job()["id"])
    app = dict(sb.files(job))["scripts/app.py"]
    assert "more bytes not shown" in app
