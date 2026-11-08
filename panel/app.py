"""The control panel's Flask app: login, services, status, costs, skill builds.

Security (spec §7): only devices on the Pi's own network segment; one admin password (scrypt)
claimed with the setup code Jarvis speaks; signed session cookies
(Secure, HttpOnly, SameSite=Strict); a CSRF token on every POST; keys are
write-only and scrubbed from every message.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import subprocess
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from core import cost_summary, skill_builder
from panel import checks as default_checks
from panel import env_store, state, status

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent

MAX_SKILL_REQUEST = 2000  # characters of a typed skill description

DEFAULTS = {
    "CLAUDE_MODEL": "claude-sonnet-4-6",
    "FAST_MODEL": "claude-haiku-5-5",
    "TTS_BACKEND": "kokoro-onnx",
    "ELEVENLABS_VOICE_ID": "onwK4e9ZLuTAKqWW03F9",
    "ELEVENLABS_MODEL_ID": "eleven_flash_v2_5",
    "TTS_SPEED": "1.2",
    "FILLER_CLASSIFIER_ENABLED": "true",
    "FILLER_CLASSIFIER_CONFIDENCE_THRESHOLD": "0.6",
    "STT_BACKEND": "whisper",
    "WAKE_WORD_THRESHOLD": "0.5",
    "WAKE_WORD_SOFT_THRESHOLD": "0",
}

# Which form fields each card may save. Keys are tested before saving.
CARDS = {
    "brain": ["ANTHROPIC_API_KEY", "CLAUDE_MODEL", "FAST_MODEL"],
    "voice": ["TTS_BACKEND", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "TTS_SPEED"],
    "decision": ["FILLER_CLASSIFIER_ENABLED", "TYPESAFE_API_KEY",
                 "FILLER_CLASSIFIER_CONFIDENCE_THRESHOLD"],
    "listener": ["STT_BACKEND", "META_MODEL_API_KEY"],
    "skills": ["BRAVE_API_KEY", "OPENWEATHER_API_KEY"],
    "household": ["OWNER_NAME", "HOME_LOCATION"],
}
TESTED_KEYS = {"ANTHROPIC_API_KEY": "claude", "ELEVENLABS_API_KEY": "elevenlabs",
               "TYPESAFE_API_KEY": "jev", "META_MODEL_API_KEY": "meta"}
KEY_FOR_TEST = {v: k for k, v in TESTED_KEYS.items()}

# Wake-word sensitivity slider: 0 = fewest false wakes, 100 = easiest to wake.
WAKE_MAX, WAKE_MIN = 0.8, 0.3

LOGIN_LIMIT = 5        # failed attempts...
LOGIN_WINDOW_S = 60    # ...per minute, per client address

_LOOPBACK = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]
# Interfaces that aren't the home network: containers, VPNs, tunnels.
_NOT_HOME = ("lo", "docker", "br-", "veth", "virbr", "wg", "tun", "tap",
             "tailscale", "zt", "ppp")
_nets_cache: dict = {"at": 0.0, "nets": []}


def local_networks() -> list:
    """The subnets of the Pi's own home-network interfaces (Ethernet, Wi-Fi),
    e.g. 192.168.1.0/24 and fe80::/64. Re-read every 30 s so a new DHCP lease
    or a switch to Wi-Fi is picked up."""
    if time.time() - _nets_cache["at"] < 30:
        return _nets_cache["nets"]
    nets = []
    try:
        out = subprocess.run(["ip", "-o", "addr", "show"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] not in ("inet", "inet6"):
            continue
        if parts[1].startswith(_NOT_HOME):
            continue
        try:
            nets.append(ipaddress.ip_interface(parts[3]).network)
        except ValueError:
            continue
    _nets_cache.update(at=time.time(), nets=nets)
    return nets


def is_local(addr: str, networks: list | None = None) -> bool:
    """True only for the Pi itself or a device on the same home network
    segment. No remote access: not the internet, a VPN, or a container."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in net for net in _LOOPBACK):
        return True
    nets = local_networks() if networks is None else networks
    return any(ip.version == net.version and ip in net for net in nets)


def threshold_to_sensitivity(threshold: float) -> int:
    t = min(max(threshold, WAKE_MIN), WAKE_MAX)
    return round((WAKE_MAX - t) / (WAKE_MAX - WAKE_MIN) * 100)


def sensitivity_to_thresholds(sensitivity: int, old_threshold: float,
                              old_soft: float) -> tuple[str, str]:
    """New (threshold, soft threshold). The soft stage keeps its ratio to the
    threshold, so a hand-tuned soft stage moves with the slider and an off
    one stays off."""
    s = min(max(sensitivity, 0), 100)
    t = round(WAKE_MAX - (WAKE_MAX - WAKE_MIN) * s / 100, 2)
    soft = round(t * old_soft / old_threshold, 2) if old_soft > 0 and old_threshold > 0 else 0
    return f"{t:g}", f"{soft:g}"


def is_skill_link(source: str) -> bool:
    """An https link with a host and no embedded login (it would be stored and
    shown on the page). The panel never installs from a path on the Pi."""
    if not source or any(c.isspace() or ord(c) < 32 for c in source):
        return False
    try:
        parts = urlsplit(source)
        return (parts.scheme == "https" and bool(parts.hostname)
                and parts.username is None and parts.password is None)
    except ValueError:
        return False


def _fingerprint_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_app(*, env_path: Path | None = None, data_dir: Path | None = None,
               restarter: status.Restarter | None = None, checks=default_checks,
               cert_fingerprint: str = "") -> Flask:
    env_path = env_path or REPO / ".env"
    data_dir = data_dir or state.data_dir()
    state_file = data_dir / "panel.json"
    restarter = restarter or status.Restarter()
    failures: dict[str, deque] = defaultdict(deque)
    say_again = {"last": 0.0}

    app = Flask(__name__)
    panel_state = state.load(state_file)
    app.secret_key = panel_state["session_secret"]
    app.config.update(
        SESSION_COOKIE_NAME="kaizen_panel",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=64 * 1024,
    )

    def has_password() -> bool:
        return bool(state.load(state_file).get("password_hash"))

    def env() -> dict[str, str]:
        return env_store.read_env(env_path)

    def setting(values: dict, key: str) -> str:
        return values.get(key) or DEFAULTS.get(key, "")

    def rate_limited(addr: str) -> bool:
        q = failures[addr]
        now = time.time()
        while q and now - q[0] > LOGIN_WINDOW_S:
            q.popleft()
        return len(q) >= LOGIN_LIMIT

    def log_in() -> None:
        session.clear()
        session.permanent = True
        session["user"] = "admin"
        session["csrf"] = secrets.token_hex(16)
        # Rotating the secret on set_password logs out old sessions; pick
        # up the current one.
        app.secret_key = state.load(state_file)["session_secret"]

    @app.before_request
    def guard():
        if not is_local(request.remote_addr or ""):
            abort(403)
        open_paths = {"setup", "login", "static", "say_code_again"}
        if request.endpoint in open_paths:
            if request.method == "POST":
                _check_csrf()
            return None
        if not has_password():
            return redirect(url_for("setup"))
        if session.get("user") != "admin":
            if request.path.startswith("/api/"):
                return jsonify(ok=False, message="Logged out, reload the page"), 401
            return redirect(url_for("login"))
        if request.method == "POST":
            _check_csrf()
        return None

    def _check_csrf():
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Form expired, reload the page and try again")

    @app.after_request
    def headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
        return resp

    @app.context_processor
    def inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf": session["csrf"], "fingerprint": cert_fingerprint,
                "logged_in": session.get("user") == "admin",
                "restart_pending": restarter.pending}

    # --- setup and login -------------------------------------------------

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if has_password():
            return redirect(url_for("login"))
        error = None
        if request.method == "POST":
            addr = request.remote_addr or ""
            code = state.load(state_file).get("setup_code", "")
            password = request.form.get("password", "")
            if rate_limited(addr):
                error = "Too many attempts. Wait a minute and try again."
            elif not hmac.compare_digest(request.form.get("code", "").strip(), code):
                failures[addr].append(time.time())
                error = "That code doesn't match the one Jarvis said."
            elif len(password) < 10:
                error = "Use at least 10 characters."
            elif password != request.form.get("confirm", ""):
                error = "The two passwords don't match."
            else:
                state.set_password(password, state_file)
                log_in()
                flash("Password set. You're logged in.")
                return redirect(url_for("services"))
        return render_template("setup.html", error=error)

    @app.post("/setup/say-again")
    def say_code_again():
        """Before a password exists: restart Jarvis (when idle) so he says the
        code again. At most once a minute."""
        if has_password():
            abort(404)
        if time.time() - say_again["last"] < 60:
            flash("Jarvis was asked less than a minute ago. Give him a moment.")
        else:
            say_again["last"] = time.time()
            result = restarter.request()
            flash("Jarvis is restarting and will say the code in about 30 seconds."
                  if result == "restarted" else
                  "Jarvis will say the code once he finishes what he's doing.")
        return redirect(url_for("setup"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not has_password():
            return redirect(url_for("setup"))
        error = None
        if request.method == "POST":
            addr = request.remote_addr or ""
            if rate_limited(addr):
                error = "Too many attempts. Wait a minute and try again."
            elif state.check_password(request.form.get("password", ""), state_file):
                failures.pop(addr, None)
                log_in()
                return redirect(url_for("services"))
            else:
                failures[addr].append(time.time())
                error = "Wrong password."
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # --- services --------------------------------------------------------

    def models() -> list[str]:
        prices = yaml.safe_load(cost_summary.PRICING_PATH.read_text())
        return list(prices.get("claude", {}))

    def remembered_location(values: dict) -> str:
        try:
            from core.memory_provider import MemoryProvider
            vault = values.get("MEMORY_VAULT_PATH") or str(Path.home() / ".kaizen" / "memory")
            return MemoryProvider(vault_path=Path(vault).expanduser(),
                                  backend="vault").load_topic("location") or ""
        except Exception:  # noqa: BLE001
            return ""

    @app.get("/")
    def services():
        values = env()
        keys = {k: env_store.mask(values.get(k)) for k in env_store.SECRET_KEYS}
        threshold = float(setting(values, "WAKE_WORD_THRESHOLD"))
        return render_template(
            "services.html", keys=keys, v={k: setting(values, k) for k in env_store.SETTINGS},
            models=models(), sensitivity=threshold_to_sensitivity(threshold),
            soft_on=float(setting(values, "WAKE_WORD_SOFT_THRESHOLD")) > 0,
            remembered_location=remembered_location(values),
        )

    def run_check(service: str, key: str, values: dict) -> tuple[bool, str]:
        if service == "claude":
            return checks.check_claude(key, request.form.get("CLAUDE_MODEL")
                                       or setting(values, "CLAUDE_MODEL"))
        if service == "elevenlabs":
            return checks.check_elevenlabs(
                key, request.form.get("ELEVENLABS_VOICE_ID") or setting(values, "ELEVENLABS_VOICE_ID"),
                setting(values, "ELEVENLABS_MODEL_ID"))
        if service == "jev":
            return checks.check_jev(key)
        return checks.check_meta(key)

    @app.post("/api/test/<service>")
    def test_service(service):
        if service not in KEY_FOR_TEST:
            abort(404)
        values = env()
        env_key = KEY_FOR_TEST[service]
        pasted = request.form.get(env_key, "").strip()
        key = pasted or values.get(env_key, "")
        if not key:
            return jsonify(ok=False, message="No key saved yet. Paste one first.")
        ok, message = run_check(service, key, values)
        message = env_store.scrub(message, [key])
        if ok and pasted:
            session.setdefault("tested", {})[env_key] = _fingerprint_key(pasted)
            session.modified = True
        logger.info("Panel: %s test %s", service, "passed" if ok else "failed")
        return jsonify(ok=ok, message=message)

    @app.post("/save/<card>")
    def save(card):
        if card not in CARDS:
            abort(404)
        values = env()
        updates: dict[str, str] = {}
        force = request.form.get("force") == "1"
        for field in CARDS[card]:
            if field not in request.form:
                continue
            value = request.form[field].strip()
            if field in env_store.SECRET_KEYS:
                if not value:
                    continue  # blank = keep the saved key
                tested = session.get("tested", {}).get(field)
                if field in TESTED_KEYS and not force and tested != _fingerprint_key(value):
                    flash("Test the new key before saving it (or use \"save anyway\").", "error")
                    return redirect(url_for("services") + f"#{card}")
            if values.get(field, DEFAULTS.get(field, "")) != value or field in env_store.SECRET_KEYS:
                updates[field] = value
        if card == "listener" and "sensitivity" in request.form:
            try:
                s = int(request.form["sensitivity"])
            except ValueError:
                abort(400)
            t, soft = sensitivity_to_thresholds(
                s, float(setting(values, "WAKE_WORD_THRESHOLD")),
                float(setting(values, "WAKE_WORD_SOFT_THRESHOLD")))
            if t != setting(values, "WAKE_WORD_THRESHOLD"):
                updates["WAKE_WORD_THRESHOLD"] = t
                if soft != setting(values, "WAKE_WORD_SOFT_THRESHOLD"):
                    updates["WAKE_WORD_SOFT_THRESHOLD"] = soft
        if not updates:
            flash("Nothing changed.")
            return redirect(url_for("services") + f"#{card}")
        try:
            env_store.write_env(env_path, updates)
        except env_store.EnvError as exc:
            flash(str(exc), "error")
            return redirect(url_for("services") + f"#{card}")
        for field in updates:
            session.get("tested", {}).pop(field, None)
        session.modified = True
        logger.info("Panel: saved %s", ", ".join(sorted(updates)))
        result = restarter.request()
        flash("Saved. Jarvis is restarting to apply it." if result == "restarted"
              else "Saved. Jarvis will restart when he finishes the current conversation.")
        return redirect(url_for("services") + f"#{card}")

    @app.get("/api/wake-scores")
    def wake_scores():
        return jsonify(events=status.wake_scores())

    # --- status ----------------------------------------------------------

    def status_data() -> dict:
        return {"jarvis": status.jarvis(), "speaker": status.speaker(),
                "microphone": status.microphone(), "network": status.network(),
                "restart_pending": restarter.pending,
                "last_restart": (datetime.fromtimestamp(restarter.last_restart).strftime("%H:%M:%S")
                                 if restarter.last_restart else "")}

    @app.get("/status")
    def status_page():
        return render_template("status.html", s=status_data())

    @app.get("/api/status")
    def status_api():
        return jsonify(status_data())

    @app.post("/restart")
    def restart():
        now = request.form.get("now") == "1"
        result = restarter.request(now=now)
        flash("Jarvis is restarting." if result == "restarted"
              else "Jarvis will restart when he finishes the current conversation.")
        back = "services" if request.form.get("back") == "services" else "status_page"
        return redirect(url_for(back))

    # --- skills (builds waiting for approval) -----------------------------

    @app.get("/skills")
    def skills():
        jobs = skill_builder.list_jobs()
        files = {j["id"]: skill_builder.files(j) for j in jobs if j["status"] == skill_builder.READY}
        return render_template("skills.html", jobs=jobs, files=files,
                               when=lambda ts: datetime.fromtimestamp(ts).strftime("%b %d %H:%M"))

    @app.get("/api/skills")
    def skills_api():
        return jsonify(jobs=[{"id": j["id"], "status": j["status"], "steps": len(j["log"])}
                             for j in skill_builder.list_jobs()])

    @app.post("/skills/build")
    def skill_build():
        from core.meta_skill import _derive_skill_name
        description = " ".join(request.form.get("description", "").split())[:MAX_SKILL_REQUEST]
        if len(description) < 10:
            flash("Describe what the skill should do in a sentence or two.", "error")
            return redirect(url_for("skills"))
        name = _derive_skill_name(description)
        try:
            skill_builder.start(description, name)
        except (skill_builder.BuildError, OSError) as exc:
            flash(f"Couldn't start the build: {exc}", "error")
            return redirect(url_for("skills"))
        logger.info("Panel: started skill build %s", name)
        flash(f"Building {name.replace('-', ' ')}. It takes a minute or two; this page updates on its own.")
        return redirect(url_for("skills"))

    @app.post("/skills/import")
    def skill_import():
        source = request.form.get("source", "").strip()[:500]
        if not is_skill_link(source):
            flash("Paste an https:// link to a git repository or .tar.gz, without a password in it.", "error")
            return redirect(url_for("skills"))
        try:
            skill_builder.start_import(source)
        except (skill_builder.BuildError, OSError) as exc:
            flash(f"Couldn't start the install: {exc}", "error")
            return redirect(url_for("skills"))
        logger.info("Panel: started skill import from %s", source)
        flash("Fetching and checking that skill. This page updates on its own.")
        return redirect(url_for("skills"))

    @app.post("/skills/<job_id>/<action>")
    def skill_action(job_id, action):
        if action not in ("approve", "reject"):
            abort(404)
        try:
            job = getattr(skill_builder, action)(job_id)
        except (skill_builder.BuildError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("skills"))
        spoken = job["name"].replace("-", " ")
        if action == "reject":
            flash(f"Rejected the {spoken} skill.")
            return redirect(url_for("skills"))
        logger.info("Panel: approved skill %s", job["name"])
        result = restarter.request()
        flash(f"Installed {spoken}. " + ("Jarvis is restarting to load it." if result == "restarted"
              else "Jarvis will load it when he finishes the current conversation."))
        return redirect(url_for("skills"))

    # --- costs -----------------------------------------------------------

    @app.get("/costs")
    def costs():
        now = datetime.now()
        months = []
        y, m = now.year, now.month
        for _ in range(4):
            months.append(f"{y}-{m:02d}")
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        month = request.args.get("month", months[0])
        if month not in months:
            month = months[0]
        start, end, label = cost_summary.month_window(month)
        summary = cost_summary.summarize(start, end, label, include_plan=True)
        return render_template("costs.html", s=summary, months=months, month=month)

    return app


def serve(host: str = "0.0.0.0") -> None:
    """Run the panel: HTTPS on 8443, plus a plain-HTTP redirect on 8080."""
    import threading

    from cheroot import wsgi
    from cheroot.ssl.builtin import BuiltinSSLAdapter

    from panel import tls

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")
    data_dir = state.data_dir()
    cert, key, made = tls.ensure_cert(data_dir / "panel-tls")
    fp = tls.fingerprint(cert)
    logger.info("Panel certificate %s (fingerprint %s)", "created" if made else "reused", fp)
    if not state.load().get("password_hash"):  # also creates the setup code
        logger.info("Panel has no password yet; Jarvis speaks the setup code at startup")

    app = create_app(cert_fingerprint=fp)
    https = wsgi.Server((host, state.PANEL_HTTPS_PORT), app, numthreads=8,
                        server_name="kaizen-panel")
    https.ssl_adapter = BuiltinSSLAdapter(str(cert), str(key))

    def redirect_app(environ, start_response):
        addr = environ.get("REMOTE_ADDR", "")
        if not is_local(addr):
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Forbidden\n"]
        host_hdr = environ.get("HTTP_HOST", "").rsplit(":", 1)[0] or "localhost"
        if not all(c.isalnum() or c in ".-[]:" for c in host_hdr):
            host_hdr = "localhost"
        start_response("308 Permanent Redirect",
                       [("Location", f"https://{host_hdr}:{state.PANEL_HTTPS_PORT}/"),
                        ("Content-Type", "text/plain")])
        return [b"Moved to HTTPS\n"]

    http = wsgi.Server((host, state.PANEL_HTTP_PORT), redirect_app, numthreads=2,
                       server_name="kaizen-panel")
    threading.Thread(target=http.start, daemon=True, name="panel-http").start()
    logger.info("Panel on https://%s.local:%d", os.uname().nodename.split(".")[0],
                state.PANEL_HTTPS_PORT)
    try:
        https.start()
    finally:
        http.stop()
        https.stop()
