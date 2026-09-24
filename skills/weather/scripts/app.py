"""
Weather skill container - receives a location query, returns weather data.

Uses open-meteo (free, no API key required).

Contract:
  Input:  SKILL_INPUT env var (JSON with "query" field)
  Output: Weather summary printed to stdout
"""

import json
import os
import sys

import requests


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_ABBREVS = set(US_STATES.keys())
_STATE_NAMES = set(US_STATES.values())


def _resolve_state(suffix: str) -> str | None:
    """Return the canonical full state name for an abbrev or name; else None."""
    s = suffix.strip()
    if s.upper() in _STATE_ABBREVS:
        return US_STATES[s.upper()]
    if s.title() in _STATE_NAMES:
        return s.title()
    return None


def parse_location(query: str) -> tuple[str, str | None]:
    """Extract (city, full_state_name) from freeform input.

    Open-meteo's geocoder takes a city name only — no "City, State" or
    "City STATE" support — and returns multiple matches across countries.
    Pulling the state out lets us filter results to the right Burlington.
    """
    if "," in query:
        city, suffix = [p.strip() for p in query.split(",", 1)]
        return city, _resolve_state(suffix)

    tokens = query.strip().split()
    if len(tokens) >= 2 and tokens[-1].upper() in _STATE_ABBREVS:
        return " ".join(tokens[:-1]), US_STATES[tokens[-1].upper()]
    if len(tokens) >= 3:
        two = " ".join(tokens[-2:]).title()
        if two in _STATE_NAMES:
            return " ".join(tokens[:-2]), two
    if len(tokens) >= 2 and tokens[-1].title() in _STATE_NAMES:
        return " ".join(tokens[:-1]), tokens[-1].title()

    return query, None


def get_weather(location: str) -> str:
    try:
        city, state = parse_location(location)
        geo_resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 10 if state else 1},
            timeout=10,
        )
        geo_resp.raise_for_status()
        results = geo_resp.json().get("results", [])
        if not results:
            return f"Location not found: {location}"

        if state:
            matches = [r for r in results if r.get("admin1") == state]
            if not matches:
                return f"Location not found: {location}"
            place = matches[0]
        else:
            place = results[0]

        lat, lon = place["latitude"], place["longitude"]
        place_name = place.get("name", location)
        if state:
            place_name = f"{place_name}, {state}"

        w_resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weathercode,windspeed_10m,relativehumidity_2m",
                "temperature_unit": "fahrenheit",
                "windspeed_unit": "mph",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=10,
        )
        w_resp.raise_for_status()
        data = w_resp.json()

        current = data.get("current", {})
        condition = _weathercode_description(current.get("weathercode", 0))

        return json.dumps({
            "location": place_name,
            "temperature": f"{round(current.get('temperature_2m', 0))}°F",
            "feels_like": f"{round(current.get('apparent_temperature', 0))}°F",
            "conditions": condition,
            "humidity": f"{round(current.get('relativehumidity_2m', 0))}%",
            "wind_speed": f"{round(current.get('windspeed_10m', 0))} mph",
        })

    except Exception as exc:
        return f"Weather error: {exc}"


def _weathercode_description(code: int) -> str:
    """Map WMO weather interpretation code to a human-readable description."""
    table = {
        0: "clear sky",
        1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "icy fog",
        51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
        61: "light rain", 63: "rain", 65: "heavy rain",
        71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
        80: "light showers", 81: "showers", 82: "heavy showers",
        85: "snow showers", 86: "heavy snow showers",
        95: "thunderstorm", 96: "thunderstorm with hail", 99: "heavy thunderstorm with hail",
    }
    return table.get(code, "unknown conditions")


def main():
    raw_input = os.environ.get("SKILL_INPUT", "")
    if not raw_input:
        raw_input = sys.stdin.read()

    try:
        data = json.loads(raw_input)
        query = data.get("query", "")
    except json.JSONDecodeError:
        query = raw_input.strip()

    if not query:
        print("No location provided")
        sys.exit(1)

    print(get_weather(query))


if __name__ == "__main__":
    main()
