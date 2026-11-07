"""Control panel (panel/): .env store, state, TLS, and the Flask app."""

import ipaddress
import os
import re
import shutil
import sqlite3
import stat
import time
from pathlib import Path

import pytest

from core import run_state
from panel import env_store, state, status, tls
from panel.app import create_app, is_local, sensitivity_to_thresholds, threshold_to_sensitivity

KEY = "sk-ant-api03-SECRETSECRETSECRET-abcd"


# --- env_store ------------------------------------------------------------

class TestEnvStore:
    def test_write_keeps_other_lines_and_is_0600(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# comment\nANTHROPIC_API_KEY=old\nOTHER=1\n")
        env_store.write_env(env, {"ANTHROPIC_API_KEY": KEY, "TTS_BACKEND": "elevenlabs"})
        assert env.read_text() == f"# comment\nANTHROPIC_API_KEY={KEY}\nOTHER=1\nTTS_BACKEND=elevenlabs\n"
        assert stat.S_IMODE(env.stat().st_mode) == 0o600

    def test_backup_is_0600_and_pruned_to_five(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\n")
        (tmp_path / ".env.bak.handmade").write_text("x")  # not the panel's; kept
        for i in range(8):
            env_store.write_env(env, {"TTS_SPEED": f"1.{i}"})
        ours = sorted(p for p in tmp_path.glob(".env.bak.2*"))
        assert len(ours) == 5
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in ours)
        assert (tmp_path / ".env.bak.handmade").exists()

    @pytest.mark.parametrize("key,value", [
        ("ANTHROPIC_API_KEY", "abc\nEVIL=1"),
        ("PATH", "/tmp"),
        ("TTS_BACKEND", "festival"),
        ("WAKE_WORD_THRESHOLD", "0.1"),
        ("ANTHROPIC_API_KEY", "has space"),
    ])
    def test_rejects(self, tmp_path, key, value):
        env = tmp_path / ".env"
        env.write_text("A=1\n")
        with pytest.raises(env_store.EnvError):
            env_store.write_env(env, {key: value})
        assert env.read_text() == "A=1\n"

    def test_mask_shows_at_most_four(self):
        assert env_store.mask("") == "not set"
        assert env_store.mask("short") == "set"
        assert env_store.mask(KEY) == "set, ends in abcd"

    def test_read_env_quotes_and_comments(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('A="x y"\nB=z # note\n#C=no\nexport D=d\n')
        assert env_store.read_env(env) == {"A": "x y", "B": "z", "D": "d"}


# --- state ----------------------------------------------------------------

class TestState:
    def test_setup_code_until_password(self, tmp_path):
        path = tmp_path / "panel.json"
        assert state.pending_setup_code(path) is None  # panel never started
        code = state.load(path)["setup_code"]
        assert re.fullmatch(r"\d{6}", code)
        assert state.pending_setup_code(path) == code
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        state.set_password("correct horse", path)
        assert state.pending_setup_code(path) is None
        assert state.check_password("correct horse", path)
        assert not state.check_password("wrong", path)
        assert "correct horse" not in path.read_text()

    def test_spoken_code(self):
        assert state.spoken_code("412903") == "4 1 2, 9 0 3"


def test_sensitivity_mapping():
    assert threshold_to_sensitivity(0.8) == 0
    assert threshold_to_sensitivity(0.3) == 100
    assert threshold_to_sensitivity(0.5) == 60
    # The Pi's hand-tuned soft stage (0.15 at 0.7) keeps its ratio.
    assert sensitivity_to_thresholds(60, 0.7, 0.15) == ("0.5", "0.11")
    assert sensitivity_to_thresholds(100, 0.5, 0) == ("0.3", "0")


def test_run_state_roundtrip():
    run_state.write("idle")
    assert run_state.read() == ("idle", os.getpid())


# --- tls ------------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")
class TestTls:
    def test_made_once_and_remade_when_address_changes(self, tmp_path):
        cert, key, made = tls.ensure_cert(tmp_path, "pi", ["192.168.1.5"])
        assert made and stat.S_IMODE(key.stat().st_mode) == 0o600
        assert not tls.ensure_cert(tmp_path, "pi", ["192.168.1.5"])[2]
        assert tls.ensure_cert(tmp_path, "pi", ["192.168.1.9"])[2]
        import subprocess
        text = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-text"],
                              capture_output=True, text=True).stdout
        assert "DNS:pi.local" in text and "IP Address:192.168.1.9" in text
        assert re.fullmatch(r"([0-9A-F]{2}:){31}[0-9A-F]{2}", tls.fingerprint(cert))

    def test_rejects_non_ip(self, tmp_path):
        with pytest.raises(ValueError):
            tls.ensure_cert(tmp_path, "pi", ["1.2.3.4,DNS:evil"])


# --- app ------------------------------------------------------------------

class FakeChecks:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def _r(self, name, key):
        self.calls.append((name, key))
        return (self.ok, "fine" if self.ok else f"Invalid key: bad key {key}")

    def check_claude(self, key, model): return self._r("claude", key)
    def check_elevenlabs(self, key, voice, model): return self._r("elevenlabs", key)
    def check_jev(self, key): return self._r("jev", key)
    def check_meta(self, key): return self._r("meta", key)


class FakeRestarter:
    pending = False
    last_restart = None

    def __init__(self):
        self.requests = []

    def request(self, now=False):
        self.requests.append(now)
        return "restarted"


@pytest.fixture
def panel(tmp_path, monkeypatch):
    from panel import app as app_mod
    monkeypatch.setattr(app_mod, "local_networks", lambda: HOME)
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={KEY}\nTTS_BACKEND=elevenlabs\n")
    checks, restarter = FakeChecks(), FakeRestarter()
    app = create_app(env_path=env, data_dir=tmp_path, restarter=restarter, checks=checks)
    app.config["TESTING"] = True
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "192.168.1.20"
    return {"app": app, "client": client, "env": env, "checks": checks,
            "restarter": restarter, "state": tmp_path / "panel.json"}


def _csrf(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


def _setup(p, password="a long password"):
    c = p["client"]
    code = state.load(p["state"])["setup_code"]
    return c.post("/setup", data={"csrf": _csrf(c, "/setup"), "code": code,
                                  "password": password, "confirm": password})


HOME = [ipaddress.ip_network("192.168.1.0/24"), ipaddress.ip_network("fe80::/64")]


@pytest.mark.parametrize("addr,ok", [
    ("192.168.1.2", True), ("127.0.0.1", True), ("::1", True),
    ("::ffff:192.168.1.2", True), ("fe80::1%eth0", True),
    ("8.8.8.8", False), ("2001:4860::8888", False),
    ("10.0.0.3", False),        # private, but not this network (e.g. a VPN)
    ("192.168.2.7", False),     # another subnet
    ("172.17.0.2", False),      # a skill container
])
def test_is_local(addr, ok):
    assert is_local(addr, HOME) is ok


def test_local_networks_skip_containers_and_vpns(monkeypatch):
    import subprocess
    from panel import app as app_mod
    out = "\n".join([
        "1: lo    inet 127.0.0.1/8 scope host lo",
        "2: eth0    inet 192.168.1.135/24 brd 192.168.1.255 scope global eth0",
        "2: eth0    inet6 fe80::8aa2:9eff:fec3:5cf4/64 scope link",
        "4: docker0    inet 172.17.0.1/16 scope global docker0",
        "5: wg0    inet 10.8.0.2/24 scope global wg0",
    ])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    monkeypatch.setitem(app_mod._nets_cache, "at", 0.0)
    assert [str(n) for n in app_mod.local_networks()] == ["192.168.1.0/24", "fe80::/64"]


def test_public_client_refused(panel):
    c = panel["client"]
    assert c.get("/login", environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code == 403
    assert c.get("/login", environ_base={"REMOTE_ADDR": "10.8.0.5"}).status_code == 403  # VPN


def test_first_visit_goes_to_setup_and_needs_the_code(panel):
    c = panel["client"]
    assert c.get("/").headers["Location"].endswith("/setup")
    r = c.post("/setup", data={"csrf": _csrf(c, "/setup"), "code": "000000x",
                               "password": "a long password", "confirm": "a long password"})
    assert "doesn&#39;t match" in r.get_data(as_text=True)
    r = _setup(panel)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert c.get("/").status_code == 200
    assert c.get("/setup").status_code == 302  # can't be claimed twice


def test_csrf_required(panel):
    _setup(panel)
    c = panel["client"]
    assert c.post("/restart", data={}).status_code == 400
    assert c.post("/setup/say-again").status_code in (400, 404)


def test_login_rate_limited(panel):
    _setup(panel)
    c = panel["app"].test_client()
    c.environ_base["REMOTE_ADDR"] = "192.168.1.30"
    token = _csrf(c)
    for _ in range(5):
        assert "Wrong password" in c.post("/login", data={"csrf": token, "password": "x"}).get_data(as_text=True)
    r = c.post("/login", data={"csrf": token, "password": "a long password"})
    assert "Too many attempts" in r.get_data(as_text=True)


def test_services_never_shows_a_key(panel):
    _setup(panel)
    html = panel["client"].get("/").get_data(as_text=True)
    assert KEY not in html and KEY[:-4] not in html
    assert "set, ends in abcd" in html


def test_test_button_scrubs_key_from_errors(panel):
    _setup(panel)
    panel["checks"].ok = False
    c = panel["client"]
    new = "sk-ant-api03-NEWNEWNEWNEWNEWNEW-wxyz"
    r = c.post("/api/test/claude", data={"ANTHROPIC_API_KEY": new},
               headers={"X-CSRF-Token": _csrf(c, "/")})
    body = r.get_json()
    assert body["ok"] is False and new not in body["message"] and "[key]" in body["message"]


def test_save_key_requires_a_passing_test(panel):
    _setup(panel)
    c = panel["client"]
    new = "sk-ant-api03-NEWNEWNEWNEWNEWNEW-wxyz"
    token = _csrf(c, "/")
    c.post("/save/brain", data={"csrf": token, "ANTHROPIC_API_KEY": new})
    assert KEY in panel["env"].read_text()  # untested: not saved
    assert c.post("/api/test/claude", data={"ANTHROPIC_API_KEY": new},
                  headers={"X-CSRF-Token": token}).get_json()["ok"]
    c.post("/save/brain", data={"csrf": token, "ANTHROPIC_API_KEY": new})
    assert f"ANTHROPIC_API_KEY={new}" in panel["env"].read_text()
    assert panel["restarter"].requests == [False]


def test_save_anyway(panel):
    _setup(panel)
    c = panel["client"]
    new = "sk-ant-api03-NEWNEWNEWNEWNEWNEW-wxyz"
    c.post("/save/brain", data={"csrf": _csrf(c, "/"), "ANTHROPIC_API_KEY": new, "force": "1"})
    assert f"ANTHROPIC_API_KEY={new}" in panel["env"].read_text()


def test_save_settings_and_sensitivity(panel):
    _setup(panel)
    c = panel["client"]
    token = _csrf(c, "/")
    c.post("/save/listener", data={"csrf": token, "STT_BACKEND": "meta", "META_MODEL_API_KEY": "",
                                   "sensitivity": "100"})
    values = env_store.read_env(panel["env"])
    assert values["STT_BACKEND"] == "meta" and values["WAKE_WORD_THRESHOLD"] == "0.3"
    assert "META_MODEL_API_KEY" not in values  # blank keeps (here: no) key
    r = c.post("/save/voice", data={"csrf": token, "TTS_SPEED": "9"}, follow_redirects=True)
    assert "isn&#39;t a valid value for TTS_SPEED" in r.get_data(as_text=True)
    assert "TTS_SPEED" not in env_store.read_env(panel["env"])


def test_status_and_costs_pages(panel, monkeypatch, tmp_path):
    _setup(panel)
    monkeypatch.setattr(status, "_run", lambda args, timeout=5: "")
    c = panel["client"]
    assert c.get("/status").status_code == 200
    assert set(c.get("/api/status").get_json()) >= {"jarvis", "speaker", "microphone", "network"}

    from core import usage_log
    db = tmp_path / "usage.db"
    monkeypatch.setenv("USAGE_LOG_PATH", str(db))
    monkeypatch.setenv("USAGE_LOG_ENABLED", "true")
    usage_log._ready_path = None
    usage_log.record("claude", "claude-sonnet-4-6", "turn", input_tokens=1_000_000)
    html = c.get("/costs").get_data(as_text=True)
    assert "claude claude-sonnet-4-6" in html and "$3.0000" in html and "$9.00" in html


class TestRestarter:
    def test_waits_while_busy_then_restarts_once(self, monkeypatch):
        info = {"active": "active", "state": "busy"}
        monkeypatch.setattr(status, "jarvis", lambda: dict(info))
        ran = []
        r = status.Restarter(poll_s=0.01, run=lambda: ran.append(1))
        assert r.request() == "waiting"
        assert r.request() == "waiting"  # coalesced
        time.sleep(0.05)
        assert ran == [] and r.pending
        info["state"] = "idle"
        deadline = time.time() + 2
        while not ran and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        assert ran == [1] and not r.pending

    def test_now_skips_the_wait(self, monkeypatch):
        monkeypatch.setattr(status, "jarvis", lambda: {"active": "active", "state": "busy"})
        ran = []
        assert status.Restarter(run=lambda: ran.append(1)).request(now=True) == "restarted"
        assert ran == [1]


def test_wake_score_parsing(monkeypatch):
    log = "\n".join([
        "21:21:50 [core.voice] INFO: Wake near-miss: peak score ch0=0.05 ch1=0.38 (threshold 0.70)",
        "21:30:10 [core.voice] INFO: Wake detected (soft, score 0.16) — confirming via transcript",
        "07:18:39 [core.voice] INFO: Wake detected (score 0.91)",
        "07:18:39 [core.voice] INFO: Wake detected",
    ])
    monkeypatch.setattr(status, "_run", lambda args, timeout=5: log)
    assert status.wake_scores() == [
        {"time": "07:18:39", "score": 0.91, "outcome": "woke"},
        {"time": "21:30:10", "score": 0.16, "outcome": "soft wake"},
        {"time": "21:21:50", "score": 0.38, "outcome": "missed"},
    ]


def test_jarvis_speaks_setup_code_only_while_unclaimed(tmp_path, monkeypatch):
    import main
    from unittest.mock import MagicMock
    monkeypatch.setenv("KAIZEN_PANEL_DIR", str(tmp_path))
    voice = MagicMock()
    main._announce_panel_setup(voice)  # no panel.json: panel not installed
    voice.speak.assert_not_called()
    code = state.load(tmp_path / "panel.json")["setup_code"]
    main._announce_panel_setup(voice)
    said = voice.speak.call_args.args[0]
    assert state.spoken_code(code) in said and "port 8080" in said
    state.set_password("a long password", tmp_path / "panel.json")
    voice.reset_mock()
    main._announce_panel_setup(voice)
    voice.speak.assert_not_called()


def test_friendly_errors():
    from panel.checks import _friendly

    class ApiError(Exception):
        status_code = 401
        body = {"detail": {"message": "Invalid API key"}}

    assert _friendly(ApiError("headers: {...}"), "k" * 20) == "Invalid key: Invalid API key"
    msg = _friendly(RuntimeError("401 Cannot authenticate with key " + "k" * 20), "k" * 20)
    assert msg.startswith("Invalid key") and "k" * 20 not in msg


def _ready_build(monkeypatch, tmp_path):
    """A finished build in the test KAIZEN_PANEL_DIR, Docker faked."""
    from types import SimpleNamespace
    from core import skill_builder as sb
    from tests.test_skill_builder import FakeDocker, agent_script, write_skill
    monkeypatch.setenv("KAIZEN_PANEL_DIR", str(tmp_path / "kz"))
    monkeypatch.setattr(sb, "AUTHORED_ROOT", tmp_path / "authored")
    docker = FakeDocker()
    monkeypatch.setattr(sb, "_docker", docker)
    monkeypatch.setattr(sb.shutil, "which", lambda b: "/usr/bin/docker" if b == "docker" else None)
    monkeypatch.setattr("core.container_manager.ContainerManager._run_container",
                        lambda *a, **k: "I rolled a 4.")
    monkeypatch.setattr(sb, "_run_agent", agent_script(lambda d, n: write_skill(
        d, n, app="print('<script>alert(1)</script>')\n")))
    job = sb.run(sb.start("roll dice", "dice-roller", launch=lambda i: None)["id"])
    return SimpleNamespace(sb=sb, job=job, docker=docker)


def test_skills_page_shows_a_ready_build_for_review(panel, monkeypatch, tmp_path):
    b = _ready_build(monkeypatch, tmp_path)
    _setup(panel)
    html = panel["client"].get("/skills").get_data(as_text=True)
    assert "dice-roller" in html and "Approve and install" in html
    assert "scripts/app.py" in html and "I rolled a 4." in html
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html  # escaped
    assert panel["client"].get("/api/skills").get_json()["jobs"][0]["status"] == "ready"
    assert b.job["status"] == "ready"


def test_approve_needs_csrf_then_installs_and_restarts(panel, monkeypatch, tmp_path):
    b = _ready_build(monkeypatch, tmp_path)
    _setup(panel)
    c = panel["client"]
    url = f"/skills/{b.job['id']}/approve"
    assert c.post(url, data={}).status_code == 400
    assert b.sb.load(b.job["id"])["status"] == "ready"
    r = c.post(url, data={"csrf": _csrf(c, "/skills")})
    assert r.status_code == 302 and panel["restarter"].requests == [False]
    assert (tmp_path / "authored" / "dice-roller" / "SKILL.md").exists()
    assert "Installed dice roller" in c.get("/skills").get_data(as_text=True)


def test_reject_and_bad_actions(panel, monkeypatch, tmp_path):
    b = _ready_build(monkeypatch, tmp_path)
    _setup(panel)
    c = panel["client"]
    token = _csrf(c, "/skills")
    assert c.post(f"/skills/{b.job['id']}/delete", data={"csrf": token}).status_code == 404
    c.post("/skills/../../x/approve", data={"csrf": token})
    c.post(f"/skills/{b.job['id']}/reject", data={"csrf": token})
    assert b.sb.load(b.job["id"])["status"] == "rejected"
    assert panel["restarter"].requests == []
    assert not (tmp_path / "authored" / "dice-roller").exists()


def test_skills_page_needs_login(panel):
    assert panel["client"].get("/skills").headers["Location"].endswith("/setup")
