"""Unit tests for core.filler_classifier — no live Jev API calls."""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.filler_classifier import (
    DEFAULT_PATTERNS_PATH,
    load_actions,
    load_prefetch,
    FillerClassifier,
    build_filler_classifier,
    load_answer_phrases,
    load_categories,
    phrase_slug,
)


def _fake_response(choice: str, confidence: float):
    """Build a stand-in for the typesafe_sdk response shape:
    response.choices["category"].choice / .confidence
    """
    answer = MagicMock()
    answer.choice = choice
    answer.confidence = confidence
    response = MagicMock()
    response.choices = {"category": answer}
    return response


class LoadCategoriesTests(unittest.TestCase):
    def test_loads_real_config(self):
        categories = load_categories()
        self.assertIn("weather", categories)
        self.assertIn("music", categories)
        self.assertTrue(all(isinstance(v, str) and v for v in categories.values()))

    def test_missing_file_returns_empty(self):
        categories = load_categories(Path("/nonexistent/path.yaml"))
        self.assertEqual(categories, {})


class FillerClassifierTests(unittest.TestCase):
    def setUp(self):
        self.categories = {"weather": "Weather questions.", "music": "Music control."}

    def test_high_confidence_returns_category(self):
        client = MagicMock()
        client.system_one.return_value = _fake_response("weather", 0.9)
        clf = FillerClassifier(
            api_key="fake", categories=self.categories, client=client,
        )
        self.assertEqual(clf.classify("what's the weather"), "weather")

    def test_low_confidence_returns_none(self):
        client = MagicMock()
        client.system_one.return_value = _fake_response("weather", 0.2)
        clf = FillerClassifier(
            api_key="fake", categories=self.categories,
            confidence_threshold=0.6, client=client,
        )
        self.assertIsNone(clf.classify("what's the weather"))

    def test_client_exception_returns_none(self):
        client = MagicMock()
        client.system_one.side_effect = RuntimeError("network gone")
        clf = FillerClassifier(api_key="fake", categories=self.categories, client=client)
        self.assertIsNone(clf.classify("what's the weather"))

    def test_timeout_returns_none(self):
        client = MagicMock()

        def _slow_call(**kwargs):
            time.sleep(0.3)
            return _fake_response("weather", 0.9)

        client.system_one.side_effect = _slow_call
        clf = FillerClassifier(
            api_key="fake", categories=self.categories,
            timeout_s=0.05, client=client,
        )
        self.assertIsNone(clf.classify("what's the weather"))

    def test_unknown_category_returns_none(self):
        client = MagicMock()
        client.system_one.return_value = _fake_response("not_a_real_category", 0.9)
        clf = FillerClassifier(api_key="fake", categories=self.categories, client=client)
        self.assertIsNone(clf.classify("something"))

    def test_empty_transcript_returns_none_without_calling_client(self):
        client = MagicMock()
        clf = FillerClassifier(api_key="fake", categories=self.categories, client=client)
        self.assertIsNone(clf.classify("   "))
        client.system_one.assert_not_called()

    def test_unavailable_without_api_key(self):
        clf = FillerClassifier(api_key="", categories=self.categories)
        self.assertFalse(clf.available)
        self.assertIsNone(clf.classify("what's the weather"))

    def test_unavailable_without_categories(self):
        clf = FillerClassifier(api_key="fake", categories={})
        self.assertFalse(clf.available)


class AnswerCategoryTests(unittest.TestCase):
    def setUp(self):
        self.categories = {"identity": "Asks the assistant's name.", "weather": "Weather."}
        self.answers = {"identity": ["I'm Jarvis."]}

    def _clf(self, choice, confidence):
        client = MagicMock()
        client.system_one.return_value = _fake_response(choice, confidence)
        return FillerClassifier(
            api_key="fake", categories=self.categories, client=client,
            answer_phrases=self.answers, answer_confidence_threshold=0.85,
        )

    def test_real_config_answer_phrases_render_persona(self):
        answers = load_answer_phrases(DEFAULT_PATTERNS_PATH, "Jarvis")
        self.assertIn("identity", answers)
        self.assertIn("capabilities", answers)
        self.assertNotIn("weather", answers)
        self.assertTrue(any("Jarvis" in p for p in answers["identity"]))
        self.assertFalse(any("{persona}" in p for ps in answers.values() for p in ps))

    def test_answer_category_above_answer_threshold_returned(self):
        clf = self._clf("identity", 0.9)
        self.assertEqual(clf.classify("what's your name"), "identity")
        self.assertTrue(clf.is_answer("identity"))
        self.assertEqual(clf.pick_answer("identity"), "I'm Jarvis.")

    def test_answer_category_between_thresholds_defers_to_claude(self):
        # 0.7 clears the filler threshold (0.6) but not the answer one (0.85).
        self.assertIsNone(self._clf("identity", 0.7).classify("what's your name"))

    def test_answer_category_without_confidence_defers_to_claude(self):
        self.assertIsNone(self._clf("identity", None).classify("what's your name"))

    def test_filler_category_unaffected_by_answer_threshold(self):
        clf = self._clf("weather", 0.7)
        self.assertEqual(clf.classify("is it raining"), "weather")
        self.assertFalse(clf.is_answer("weather"))
        self.assertIsNone(clf.pick_answer("weather"))

    def test_logs_category_confidence_and_outcome(self):
        with self.assertLogs("core.filler_classifier", level="INFO") as logs:
            self._clf("identity", 0.9).classify("what's your name")
            self._clf("identity", 0.7).classify("what's your name")
            self._clf("weather", 0.3).classify("hmm")
        joined = "\n".join(logs.output)
        self.assertIn("category=identity confidence=0.90", joined)
        self.assertIn("-> answer", joined)
        self.assertIn("-> Claude (below answer threshold 0.85)", joined)
        self.assertIn("-> skip (below 0.60)", joined)

    def test_intent_hint_names_category_criteria_and_confidence(self):
        clf = self._clf("weather", 0.92)
        clf.classify("to the weather today")
        hint = clf.intent_hint("weather")
        self.assertIn("'weather'", hint)
        self.assertIn("Weather.", hint)
        self.assertIn("0.92", hint)
        self.assertIn("instead of asking", hint)

    def test_real_config_prefetches_weather_with_location_template(self):
        self.assertEqual(
            load_prefetch(DEFAULT_PATTERNS_PATH)["weather"],
            {"tool": "weather", "input": {"query": "{location}"}},
        )

    def test_prefetch_call_resolves_location(self):
        clf = FillerClassifier(
            api_key="k", categories=self.categories,
            prefetch={"weather": {"tool": "weather", "input": {"query": "{location}"}}},
        )
        with patch("core.location_preference.resolve_location", return_value="Burlington, Vermont"):
            self.assertEqual(
                clf.prefetch_call("weather"),
                {"tool": "weather", "input": {"query": "Burlington, Vermont"}},
            )
        self.assertIsNone(clf.prefetch_call("identity"))

    def test_prefetch_skipped_without_location(self):
        clf = FillerClassifier(
            api_key="k", categories=self.categories,
            prefetch={"weather": {"tool": "weather", "input": {"query": "{location}"}}},
        )
        with patch("core.location_preference.resolve_location", return_value=""):
            self.assertIsNone(clf.prefetch_call("weather"))

    def test_hint_mentions_prefetched_tool(self):
        hint = self._clf("weather", 0.9).intent_hint(
            "weather", {"tool": "weather", "input": {"query": "Burlington"}}
        )
        self.assertIn("already ran the weather tool", hint)

    def test_prefetch_disabled_unless_tool_first_enabled(self):
        env = {"FILLER_CLASSIFIER_ENABLED": "true", "TYPESAFE_API_KEY": "k", "TOOL_FIRST_ENABLED": "false"}
        with patch.dict("os.environ", env):
            self.assertEqual(build_filler_classifier()._prefetch, {})
        env["TOOL_FIRST_ENABLED"] = "true"
        with patch.dict("os.environ", env):
            self.assertIn("weather", build_filler_classifier()._prefetch)

    def test_real_config_has_stop_action(self):
        self.assertEqual(load_actions(DEFAULT_PATTERNS_PATH).get("stop"), "stop")

    def test_action_category_needs_strict_confidence(self):
        def clf(conf):
            client = MagicMock()
            client.system_one.return_value = _fake_response("stop", conf)
            return FillerClassifier(
                api_key="k", categories={"stop": "Stop.", "weather": "W."}, client=client,
                actions={"stop": "stop"}, answer_confidence_threshold=0.85,
            )
        self.assertEqual(clf(0.95).classify("stop"), "stop")
        self.assertIsNone(clf(0.7).classify("stop by the store later"))
        self.assertEqual(clf(0.95).action_for("stop"), "stop")
        self.assertIsNone(clf(0.95).action_for("weather"))

    def test_phrase_slug_is_stable(self):
        # Changing this breaks every previously built cache file.
        self.assertEqual(phrase_slug("One moment."), "one-moment-" + __import__("hashlib").sha1(b"One moment.").hexdigest()[:8])


class BuildFillerClassifierTests(unittest.TestCase):
    def test_disabled_by_env_flag(self):
        with patch.dict("os.environ", {"FILLER_CLASSIFIER_ENABLED": "false", "TYPESAFE_API_KEY": "x"}, clear=False):
            self.assertIsNone(build_filler_classifier())

    def test_disabled_without_api_key(self):
        with patch.dict("os.environ", {"FILLER_CLASSIFIER_ENABLED": "true"}, clear=False):
            import os
            os.environ.pop("TYPESAFE_API_KEY", None)
            self.assertIsNone(build_filler_classifier())

    def test_enabled_with_api_key_returns_classifier(self):
        with patch.dict(
            "os.environ",
            {"FILLER_CLASSIFIER_ENABLED": "true", "TYPESAFE_API_KEY": "fake-key"},
            clear=False,
        ):
            clf = build_filler_classifier()
            self.assertIsNotNone(clf)
            self.assertTrue(clf.available)


if __name__ == "__main__":
    unittest.main()
