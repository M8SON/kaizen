"""Unit tests for core.filler_classifier — no live Jev API calls."""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.filler_classifier import FillerClassifier, build_filler_classifier, load_categories


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
