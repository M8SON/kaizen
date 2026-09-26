#!/usr/bin/env python3
"""
build_filler_audio.py - Pre-synthesize filler phrases via ElevenLabs.

Reads config/filler_phrases.yaml and synthesizes each phrase once via the
ElevenLabs API (same ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID /
ELEVENLABS_MODEL_ID the live TTS backend already uses), caching the raw
PCM audio as float32 .npy arrays under ~/.kaizen/filler_audio/<category>/.

At runtime, core.voice.VoiceInterface.play_filler() plays these cached
files directly — no network call, no live synthesis, no ElevenLabs
latency on the filler hot path.

Usage:
    .venv/bin/python scripts/build_filler_audio.py
    .venv/bin/python scripts/build_filler_audio.py --force   # regenerate all

Requires ELEVENLABS_API_KEY in the environment (or .env).

See docs/superpowers/specs/2026-09-23-jev-filler-response-design.md.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import numpy as np
import yaml

from core.filler_classifier import FILLER_AUDIO_ROOT as CACHE_ROOT, phrase_slug, render_phrase
from core.prompt_builder import persona_name_from_env

CONFIG_PATH = Path(__file__).parent.parent / "config" / "filler_phrases.yaml"


def _load_config(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: config not found at {path}")
        sys.exit(1)
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    categories = data.get("categories") or {}
    if not categories:
        print(f"ERROR: no categories found in {path}")
        sys.exit(1)
    return categories


def _synth_pcm(client, text: str, voice_id: str, model_id: str) -> np.ndarray:
    """One-shot (non-streaming) synthesis to a float32 PCM array at 24kHz."""
    audio_bytes = b"".join(
        client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=model_id,
            output_format="pcm_24000",
        )
    )
    n = len(audio_bytes) - (len(audio_bytes) % 2)
    return np.frombuffer(audio_bytes[:n], dtype="<i2").astype(np.float32) / 32768.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Regenerate all phrases, ignoring cache")
    args = parser.parse_args()

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set — cannot synthesize filler audio.")
        return 1

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip() or "onwK4e9ZLuTAKqWW03F9"
    model_id = os.getenv("ELEVENLABS_MODEL_ID", "").strip() or "eleven_flash_v2_5"

    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        print("ERROR: elevenlabs package not installed. pip install elevenlabs")
        return 1

    client = ElevenLabs(api_key=api_key)
    categories = _load_config(CONFIG_PATH)
    persona = persona_name_from_env()

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    synthesized = 0
    skipped = 0
    failed = 0
    coverage: dict[str, int] = {}

    for category, entry in categories.items():
        phrases = (entry or {}).get("phrases") or []
        if not phrases:
            print(f"  WARNING: category {category!r} has no phrases — skipping")
            coverage[category] = 0
            continue

        cat_dir = CACHE_ROOT / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        cached_count = 0

        for phrase in phrases:
            phrase = render_phrase(phrase, persona)
            out_path = cat_dir / f"{phrase_slug(phrase)}.npy"
            if out_path.exists() and not args.force:
                skipped += 1
                cached_count += 1
                continue
            try:
                pcm = _synth_pcm(client, phrase, voice_id, model_id)
                np.save(out_path, pcm)
                synthesized += 1
                cached_count += 1
                print(f"  [{category}] synthesized: {phrase!r} -> {out_path.name}")
            except Exception as exc:
                failed += 1
                print(f"  [{category}] FAILED: {phrase!r}: {exc}")

        coverage[category] = cached_count

    print()
    print(f"Done: {synthesized} synthesized, {skipped} already cached, {failed} failed.")
    print("Category coverage:")
    for category, count in coverage.items():
        marker = "✓" if count > 0 else "✗"
        print(f"  {marker} {category}: {count} phrase(s) cached")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
