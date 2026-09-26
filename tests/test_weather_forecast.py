"""Weather skill daily forecast shaping (no network)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "weather_app", Path(__file__).parent.parent / "skills" / "weather" / "scripts" / "app.py"
)
weather_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(weather_app)


def test_daily_forecast_names_days_and_formats_values():
    days = weather_app._daily_forecast({
        "time": ["2026-09-26", "2026-09-27"],
        "weathercode": [3, 61],
        "temperature_2m_max": [74.6, 58.2],
        "temperature_2m_min": [52.1, 45.0],
        "precipitation_probability_max": [4, 80],
    })
    assert days == [
        {"date": "2026-09-26", "day": "Saturday", "conditions": "overcast",
         "high": "75°F", "low": "52°F", "chance_of_rain": "4%"},
        {"date": "2026-09-27", "day": "Sunday", "conditions": "light rain",
         "high": "58°F", "low": "45°F", "chance_of_rain": "80%"},
    ]


def test_daily_forecast_tolerates_missing_series():
    days = weather_app._daily_forecast({"time": ["2026-09-28"]})
    assert days[0]["day"] == "Monday" and days[0]["high"] is None


def test_daily_forecast_empty():
    assert weather_app._daily_forecast({}) == []
