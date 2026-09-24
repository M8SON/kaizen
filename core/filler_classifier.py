"""
FillerClassifier - Jev (TypeSafe AI) intent classification for filler phrases.

Classifies an STT transcript into one of the categories declared in
config/filler_phrases.yaml so the voice loop can speak a topic-relevant
filler line ("Let me check the weather...") while the real Claude/skill
turn runs, instead of a generic beep.

Failure-tolerant by design: a missing API key, network error, timeout, or
low-confidence answer all resolve to None. Callers MUST treat None as
"no specific filler this turn" and fall back to existing behavior (the
on_speech_done thinking-sound cue already covers that gap). This module
never raises to the caller and never blocks longer than `timeout_s`.

See docs/superpowers/specs/2026-09-23-jev-filler-response-design.md.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_PATTERNS_PATH = Path(__file__).parent.parent / "config" / "filler_phrases.yaml"


def load_categories(path: Path = DEFAULT_PATTERNS_PATH) -> dict[str, str]:
    """Load {category_name: criteria} from config/filler_phrases.yaml.

    Returns an empty dict (never raises) if the file is missing or malformed
    — callers treat an empty category set as "classifier unusable".
    """
    if not path.exists():
        logger.warning("FillerClassifier: patterns file not found at %s", path)
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        logger.error("FillerClassifier: malformed YAML at %s: %s", path, exc)
        return {}

    categories = {}
    for name, entry in (data.get("categories") or {}).items():
        criteria = (entry or {}).get("criteria", "").strip()
        if criteria:
            categories[name] = criteria
        else:
            logger.warning("FillerClassifier: category %r has no criteria — skipping", name)
    return categories


class FillerClassifier:
    """Classifies a transcript into a filler-phrase category via Jev.

    Network call with a hard timeout enforced via a daemon thread (the
    typesafe-sdk's own timeout support is not assumed). Any failure —
    missing key, timeout, SDK error, or low confidence — returns None.
    """

    def __init__(
        self,
        api_key: str,
        categories: dict[str, str],
        timeout_s: float = 0.5,
        confidence_threshold: float = 0.6,
        client=None,
    ):
        self._categories = dict(categories)
        self._timeout_s = timeout_s
        self._confidence_threshold = confidence_threshold
        self._client = client
        self._api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self._api_key) and bool(self._categories)

    def _get_client(self):
        if self._client is not None:
            return self._client
        from typesafe_sdk import TypeSafeClient
        self._client = TypeSafeClient(api_key=self._api_key)
        return self._client

    def classify(self, transcript: str) -> str | None:
        """Return a category name, or None on timeout/error/low confidence."""
        if not self.available or not transcript.strip():
            return None

        result: dict = {}

        def _call():
            try:
                client = self._get_client()
                try:
                    from typesafe_sdk import Choice
                    category_question = Choice(
                        instructions="Which category does this request belong to?",
                        criteria=self._categories,
                    )
                except ImportError:
                    # typesafe-sdk not installed in this environment (e.g. a
                    # test injecting a mock client). Fall back to a plain
                    # dict shape — real production use always has the SDK
                    # installed (see requirements.txt), so this only matters
                    # for tests exercising a mocked client.
                    category_question = {
                        "type": "choice",
                        "instructions": "Which category does this request belong to?",
                        "criteria": self._categories,
                    }
                response = client.system_one(
                    state={"transcript": transcript},
                    questions={"category": category_question},
                )
                result["response"] = response
            except Exception as exc:  # noqa: BLE001 - never raise to caller
                result["error"] = exc

        thread = threading.Thread(target=_call, daemon=True, name="jev-classify")
        thread.start()
        thread.join(timeout=self._timeout_s)

        if thread.is_alive():
            logger.warning(
                "FillerClassifier: Jev call exceeded %.2fs timeout — skipping filler",
                self._timeout_s,
            )
            return None

        if "error" in result:
            logger.warning("FillerClassifier: Jev call failed: %s", result["error"])
            return None

        response = result.get("response")
        if response is None:
            return None

        try:
            choice_answer = response.choices["category"]
            category = choice_answer.choice
            confidence = choice_answer.confidence
        except (KeyError, AttributeError) as exc:
            logger.warning("FillerClassifier: unexpected Jev response shape: %s", exc)
            return None

        if confidence is not None and confidence < self._confidence_threshold:
            logger.debug(
                "FillerClassifier: category=%s confidence=%.2f below threshold %.2f — skipping",
                category, confidence, self._confidence_threshold,
            )
            return None

        if category not in self._categories:
            logger.warning("FillerClassifier: Jev returned unknown category %r", category)
            return None

        return category


def build_filler_classifier() -> "FillerClassifier | None":
    """Construct a FillerClassifier from environment config.

    Returns None when disabled (FILLER_CLASSIFIER_ENABLED=false), no API
    key is set, or the phrase library has no categories — callers must
    treat None the same as a per-call classification miss (no filler).
    """
    import os

    if os.getenv("FILLER_CLASSIFIER_ENABLED", "true").strip().lower() != "true":
        return None

    api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        logger.info("FillerClassifier: TYPESAFE_API_KEY not set — filler classification disabled")
        return None

    categories = load_categories()
    if not categories:
        logger.warning("FillerClassifier: no categories loaded — filler classification disabled")
        return None

    timeout_s = float(os.getenv("FILLER_CLASSIFIER_TIMEOUT_MS", "800")) / 1000.0
    confidence_threshold = float(os.getenv("FILLER_CLASSIFIER_CONFIDENCE_THRESHOLD", "0.6"))

    return FillerClassifier(
        api_key=api_key,
        categories=categories,
        timeout_s=timeout_s,
        confidence_threshold=confidence_threshold,
    )
