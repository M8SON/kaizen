#!/usr/bin/env python3
"""
MiniClaw - Main entry point

A modular, voice-controlled AI assistant designed for Raspberry Pi.
Uses a skill-based architecture with Docker container execution.

Usage:
  python main.py              # Run voice assistant (default)
  python main.py --list       # List loaded skills
  python main.py --text       # Text-only mode (no microphone)
"""

import os
import sys
import argparse
import logging
import signal
from pathlib import Path
from dotenv import load_dotenv

# Early dispatch for `miniclaw skill <subcommand>`. Handled before loading
# the orchestrator stack so the CLI stays responsive and doesn't require
# Anthropic credentials / audio / etc.
if len(sys.argv) >= 2 and sys.argv[1] == "skill":
    from core.skill_cli import main as skill_main
    sys.exit(skill_main(sys.argv[2:]))

from core import profiling
from core.scheduler import SchedulesStore, SchedulerThread
from core.location_preference import resolve_location
from core.session_archive import SessionArchive
from core.voice_backends import build_stt_backend, build_wake_backend, build_vad_backend

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("miniclaw")


def _print_loaded_skills(orchestrator):
    skills = orchestrator.list_skills()
    if skills:
        print(f"\n  Loaded {len(skills)} skill(s):")
        for s in skills:
            print(f"    - {s['name']}")


def _fetch_weather_for_context(location: str, api_key: str) -> str:
    """Fetch a one-line weather summary for startup context. Returns empty string on any failure."""
    try:
        import json as _json
        import urllib.parse
        import urllib.request
        params = urllib.parse.urlencode({"q": location, "appid": api_key, "units": "imperial"})
        url = f"http://api.openweathermap.org/data/2.5/weather?{params}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.loads(resp.read())
        temp = round(data["main"]["temp"])
        desc = data["weather"][0]["description"]
        return f"Weather in {data['name']}: {temp}°F, {desc}."
    except Exception:
        return ""


def _build_startup_context() -> str:
    """Return a brief context string with date, time, and optional weather."""
    from datetime import datetime
    now = datetime.now()
    context = now.strftime("Today is %A, %B %-d. The time is %-I:%M %p.")

    location = resolve_location()
    api_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    if location and api_key:
        weather = _fetch_weather_for_context(location, api_key)
        if weather:
            context += f" {weather}"

    return context


def build_voice_interface():
    """Construct the default production voice interface from environment config."""
    from core.voice import VoiceInterface

    # WHISPER_MODEL is the legacy single-model knob; CPU and Hailo paths now
    # take separate variants because Hailo only has tiny/base HEFs while CPU
    # via faster-whisper happily runs Whisper-small.
    legacy_whisper = os.getenv("WHISPER_MODEL", "base")
    transcription_model_cpu = os.getenv(
        "WHISPER_MODEL_CPU",
        legacy_whisper if legacy_whisper != "base" else "small",
    )
    transcription_model_hailo = os.getenv("WHISPER_MODEL_HAILO", legacy_whisper)

    stt_backend, stt_status = build_stt_backend(
        transcription_model_cpu=transcription_model_cpu,
        transcription_model_hailo=transcription_model_hailo,
    )
    print(stt_status)

    wake_word_model = os.getenv("WAKE_WORD_MODEL", "hey_jarvis")
    wake_word_threshold = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))

    wake_backend, wake_msg = build_wake_backend(
        model_name=wake_word_model,
        threshold=wake_word_threshold,
    )
    logger.info(wake_msg)

    vad_backend_name = os.getenv("VAD_BACKEND", "silero")
    vad_threshold = float(os.getenv("VAD_THRESHOLD", "0.5"))
    vad_min_silence_ms = int(os.getenv("VAD_MIN_SILENCE_MS", "700"))
    rms_threshold = int(os.getenv("SILENCE_THRESHOLD", "1000"))

    vad_backend, vad_msg = build_vad_backend(
        backend_name=vad_backend_name,
        threshold=vad_threshold,
        rms_threshold=rms_threshold,
    )
    logger.info(vad_msg)

    return VoiceInterface(
        transcription_model=transcription_model_cpu,
        display_wake_word=_display_wake_word(),
        enable_tts=os.getenv("ENABLE_TTS", "true").lower() == "true",
        tts_voice=os.getenv("TTS_VOICE", "af_heart"),
        tts_speed=float(os.getenv("TTS_SPEED", "1.2")),
        silence_threshold=int(os.getenv("SILENCE_THRESHOLD", "1000")),
        silence_duration=float(os.getenv("SILENCE_DURATION", "2.0")),
        stt_backend=stt_backend,
        wake_backend=wake_backend,
        vad_backend=vad_backend,
        vad_min_silence_ms=vad_min_silence_ms,
    )


def _display_wake_word() -> str:
    """Human-readable form of the active openWakeWord model (e.g. "hey jarvis")."""
    return os.getenv("WAKE_WORD_MODEL", "hey_jarvis").replace("_", " ")


def run_voice_mode(orchestrator, voice=None):
    """Run the assistant in voice mode with microphone input."""
    voice = voice or build_voice_interface()
    wake_word = _display_wake_word()

    from core.meta_skill import MetaSkillExecutor
    orchestrator.container_manager._meta_skill_executor = MetaSkillExecutor(
        voice=voice,
        orchestrator=orchestrator,
    )
    orchestrator.speak_callback = voice.speak

    orchestrator.inject_startup_context(_build_startup_context())
    voice.play_startup_sound()

    print("\n" + "=" * 60)
    print("  MiniClaw")
    print("=" * 60)
    print(f"\n  Wake word: '{wake_word}'")
    print("  Say 'goodbye' or 'stop' to exit.")
    print("  Press Ctrl+C to quit.\n")
    print("=" * 60)

    _print_loaded_skills(orchestrator)
    print()

    greeting = orchestrator.greet()
    print(f"Assistant: {greeting}\n")
    voice.speak(greeting)

    # How long to wait for follow-up speech before returning to wake word detection
    conversation_idle_timeout = float(os.getenv("CONVERSATION_IDLE_TIMEOUT", "8"))

    active_flag = getattr(orchestrator, "_conversation_active_flag", [False])

    # Close PyAudio cleanly on Ctrl+C / SIGTERM. Without this, an interrupt
    # delivered while a stream.read is in PortAudio's C-extension can leave
    # /dev/snd/pcmC*D0c claimed by the orphaned process, and the next
    # ./run.sh --voice fails with Errno -9996 on the XVF3800.
    def _shutdown_voice(signum, _frame):
        try:
            voice.shutdown()
        except Exception:
            logger.exception("voice.shutdown failed in signal handler")
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        sys.exit(0)

    prev_sigint = signal.signal(signal.SIGINT, _shutdown_voice)
    prev_sigterm = signal.signal(signal.SIGTERM, _shutdown_voice)

    try:
        while True:
            # Drain any scheduled fires that arrived while we were idle.
            while not orchestrator.scheduled_fire_queue.empty():
                try:
                    fire = orchestrator.scheduled_fire_queue.get_nowait()
                except Exception:
                    break
                delivered = orchestrator.process_scheduled_fire(fire)
                if delivered:
                    print(f"[scheduled] {delivered}\n")

            # Speak any pending next_wake announcements before the wake cycle.
            pending = orchestrator.drain_pending_announcements()
            if pending:
                voice.speak("Before we chat — " + " ".join(pending))

            # Wait for wake word
            print(f"\nWaiting for wake word: '{wake_word}'...")
            detected = voice.wait_for_wake_word()
            if not detected:
                break  # Ctrl+C

            print("Listening...")
            active_flag[0] = True
            orchestrator.start_session("voice")

            # Conversation session — keep listening until idle
            while True:
                with profiling.turn():
                    with profiling.stage("listen_record"):
                        # start_thinking_music fires the moment silence is
                        # detected (before STT), so the user hears music
                        # during STT instead of the silent gap.
                        transcription = voice.listen(
                            max_wait_seconds=conversation_idle_timeout,
                            on_speech_done=voice.start_thinking_music,
                        )

                    if not transcription:
                        voice.stop_thinking_music()
                        print("Session ended.")
                        active_flag[0] = False
                        orchestrator.end_session()
                        break

                    print(f"You: {transcription}")

                    # Check for exit
                    exit_words = ["goodbye", "exit", "quit", "stop"]
                    if any(word in transcription.lower() for word in exit_words):
                        voice.stop_thinking_music()
                        response = orchestrator.close_session()
                        print(f"\nAssistant: {response}")
                        voice.speak(response)
                        active_flag[0] = False
                        return

                    if os.getenv("LLM_STREAM_TO_TTS", "true").lower() == "true":
                        # Music shares the output device with Kokoro — release
                        # it before the streaming feeder spawns, otherwise the
                        # Kokoro consumer thread fights elevator music for the
                        # USB DAC.
                        voice.stop_thinking_music()
                        push, finalize = voice.speak_stream_feeder()
                        try:
                            with profiling.stage("tts"):
                                response = orchestrator.process_message(
                                    transcription, on_chunk=push
                                )
                                finalize()
                        except Exception:
                            finalize()
                            raise
                        print(f"Assistant: {response}\n")
                    else:
                        try:
                            response = orchestrator.process_message(transcription)
                        finally:
                            voice.stop_thinking_music()
                        print(f"Assistant: {response}\n")
                        with profiling.stage("tts"):
                            voice.speak(response)

                print("Listening...")

    except KeyboardInterrupt:
        print("\n\nShutting down...")
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)
        try:
            voice.shutdown()
        except Exception:
            logger.exception("voice.shutdown failed during cleanup")
        orchestrator.end_session()


def run_text_mode(orchestrator):
    """Run the assistant in text-only mode (no microphone needed)."""
    print("\n" + "=" * 60)
    print("  MiniClaw (Text Mode)")
    print("=" * 60)

    _print_loaded_skills(orchestrator)
    print("\n  Type your message. Type 'quit' to exit.\n")

    orchestrator.start_session("text")
    try:
        while True:
            # Drain any scheduled fires that arrived while idle.
            while not orchestrator.scheduled_fire_queue.empty():
                try:
                    fire = orchestrator.scheduled_fire_queue.get_nowait()
                except Exception:
                    break
                delivered = orchestrator.process_scheduled_fire(fire)
                if delivered:
                    print(f"[scheduled] {delivered}\n")

            pending = orchestrator.drain_pending_announcements()
            for note in pending:
                print(f"[scheduled] {note}\n")

            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print(f"Assistant: {orchestrator.close_session()}")
                break

            if user_input.lower() == "/skills":
                for s in orchestrator.list_skills():
                    print(f"  {s['name']} ({s['format']}) - {s['description']}")
                continue

            if user_input.lower() == "/reset":
                orchestrator.reset_conversation()
                print("  Conversation reset.")
                continue

            response = orchestrator.process_message(user_input)
            print(f"Assistant: {response}\n")

    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
    finally:
        orchestrator.end_session()


def list_skills(orchestrator):
    """Print loaded skills and exit."""
    skills = orchestrator.list_skills()

    if not skills:
        print("No skills loaded.")
        return

    print(f"\n{'Name':<20} {'Directory':<40} Description")
    print("-" * 80)
    for s in skills:
        print(f"{s['name']:<20} {s['dir']:<40} {s['description'][:40]}")
    print()


def main():
    parser = argparse.ArgumentParser(description="MiniClaw")
    parser.add_argument(
        "--text", action="store_true", help="Run in text-only mode"
    )
    parser.add_argument(
        "--list", action="store_true", help="List loaded skills and exit"
    )
    parser.add_argument(
        "--skills-dir",
        type=str,
        default=None,
        help="Additional skills directory to scan",
    )
    parser.add_argument(
        "--skill-select",
        type=str,
        metavar="QUERY",
        help="Test semantic skill selection for a query without making an API call",
    )
    args = parser.parse_args()

    # Validate API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set. Add it to your .env file.")
        sys.exit(1)

    # Build skill search paths
    skill_paths = [
        Path("./skills"),
        Path.home() / ".miniclaw" / "skills",
    ]
    if args.skills_dir:
        skill_paths.insert(0, Path(args.skills_dir))

    # Initialize orchestrator
    from core.orchestrator import Orchestrator

    archive = SessionArchive()

    orchestrator = Orchestrator(
        anthropic_api_key=api_key,
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        skill_paths=skill_paths,
        container_memory=os.getenv("CONTAINER_MEMORY", "256m"),
        conversation_max_messages=int(os.getenv("CONVERSATION_MAX_MESSAGES", "24")),
        conversation_max_tokens=int(os.getenv("CONVERSATION_MAX_TOKENS", "6000")),
        memory_max_tokens=int(os.getenv("MEMORY_MAX_TOKENS", "2000")),
        memory_recall_max_tokens=int(os.getenv("MEMORY_RECALL_MAX_TOKENS", "600")),
        skill_prompt_max_tokens=int(os.getenv("SKILL_PROMPT_MAX_TOKENS", "4000")),
        skill_select_top_k=int(os.getenv("SKILL_SELECT_TOP_K", "2")),
        archive=archive,
    )

    # Inject orchestrator reference for native skills that need to reload
    orchestrator.container_manager._orchestrator = orchestrator
    orchestrator.container_manager._archive = archive
    orchestrator.container_manager._skill_loader_for_self_update = orchestrator.skill_loader

    # --- scheduler wiring ---
    schedules_path = Path.home() / ".miniclaw" / "schedules.yaml"
    schedules_store = SchedulesStore(schedules_path)
    orchestrator.container_manager._schedules_store = schedules_store
    orchestrator.scheduler_log_path = Path.home() / ".miniclaw" / "scheduler.log"

    # Mutable flag read by the orchestrator to downgrade immediate fires while a
    # conversation is active. Toggled in run_voice_mode around each session.
    conversation_active = [False]
    orchestrator.is_conversation_active = lambda: conversation_active[0]
    orchestrator._conversation_active_flag = conversation_active

    scheduler_thread = SchedulerThread(
        store=schedules_store,
        fire_queue=orchestrator.scheduled_fire_queue,
    )
    scheduler_thread.start()
    orchestrator._scheduler_thread = scheduler_thread

    if hasattr(args, "skill_select") and args.skill_select:
        query = args.skill_select
        selected = orchestrator.skill_selector.select(query)
        always_full = orchestrator.prompt_builder.ALWAYS_FULL_SKILLS
        all_skills = set(orchestrator.skills.keys())
        compact = all_skills - selected - always_full
        print(f"\nQuery: {query!r}")
        print(f"Selected for full instructions: {sorted(selected)}")
        print(f"Always-full skills: {sorted(always_full)}")
        print(f"Compact one-liners: {sorted(compact)}")
        sys.exit(0)

    # Run in requested mode
    try:
        if args.list:
            list_skills(orchestrator)
        elif args.text:
            run_text_mode(orchestrator)
        else:
            run_voice_mode(orchestrator)
    finally:
        try:
            scheduler_thread.stop()
            scheduler_thread.join(timeout=2.0)
        except Exception:
            pass


if __name__ == "__main__":
    main()
