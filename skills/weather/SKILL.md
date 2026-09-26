---
name: weather
description: Get current weather and the 7-day forecast (today through the next 6 days) for a location
---
# Weather Skill

## When to use
Use this skill when the user asks about weather, temperature, or
conditions in a specific location — now or on any day in the next week
("tomorrow", "Monday", "this weekend"). One call returns both current
conditions and a daily `forecast` list; don't web-search for forecasts.

## Inputs

```yaml
type: object
properties:
  query:
    type: string
    description: City name or location (e.g., 'London', 'New York', 'Burlington VT')
required:
  - query
```

## How to respond
Answer only what was asked, conversationally and briefly for spoken delivery.
For now, give temperature and conditions (humidity/wind only if relevant).
For another day, use that day's `forecast` entry: conditions, high/low, and
chance of rain.

Examples: "It's currently 72 degrees and sunny in Burlington with light winds."
"Tomorrow looks rainy, a high of 58 and a low of 45, with an 80 percent chance of rain."
