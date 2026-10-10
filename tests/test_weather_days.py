"""The weather skill's optional `days` input, used by the tool-first prefetch
to fetch only today and tomorrow (Claude's own calls still get 7 days)."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

REPO = Path(__file__).resolve().parent.parent


class WeatherDaysTests(unittest.TestCase):
    def test_days_limits_forecast_request(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("weather_app", REPO / "skills/weather/scripts/app.py")
        app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
        geo = MagicMock(); geo.json.return_value = {"results": [{"latitude": 1, "longitude": 2, "name": "X"}]}
        wx = MagicMock(); wx.json.return_value = {"current": {}, "daily": {}}
        with patch.object(app.requests, "get", side_effect=[geo, wx]) as get:
            app.get_weather("X", days=2)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["forecast_days"], 2)

    def test_prefetch_asks_for_two_days(self):
        data = yaml.safe_load((REPO / "config" / "filler_phrases.yaml").read_text())
        self.assertEqual(data["categories"]["weather"]["prefetch"]["input"]["days"], 2)


if __name__ == "__main__":
    unittest.main()
