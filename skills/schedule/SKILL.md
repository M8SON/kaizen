---
name: schedule
description: Create, list, cancel, or modify recurring scheduled tasks the assistant
  fires on a cron schedule.
---
# Schedule

Use this skill when the user wants something to happen on a repeating schedule — morning briefings, hourly checks, weekly summaries, and so on. Do not use it for one-shot reminders like "in 10 minutes" (no one-shot support yet).

## When to use

- "Every morning at 8 tell me the weather"
- "Every weekday at 7am open the dashboard"
- "What do I have scheduled?"
- "Cancel my morning briefing"
- "Change my morning briefing to 9am"

## Actions

### create

Convert the user's natural-language timing to a standard 5-field cron expression. Then speak the resolved time back in plain English and wait for the user to say "confirm" before calling the tool.

Choose delivery by phrasing:
- "remind me", "alert me", "tell me now", "right away" → `immediate`
- "silently", "in the background", "just log" → `silent`
- anything else → `next_wake`

Input schema:

```yaml
action: create
cron: "0 8 * * *"
prompt: "tell me the weather and top news"
delivery: next_wake
label: morning briefing   # optional; short, voice-friendly
```

### list

Input: `{"action": "list"}`. Speak the result as natural language: "You have three scheduled items: a morning briefing at 8am every day, ...".

### cancel

Input: `{"action": "cancel", "id_or_label": "morning briefing"}`. If multiple schedules could match, list them and ask which to cancel before calling the tool.

### modify

Input: `{"action": "modify", "id_or_label": "morning briefing", "cron": "0 9 * * *"}`. Only include the fields the user wants to change. Confirm the new schedule in plain English before calling.

## Confirmation rule

For create, cancel, and modify: always read the resolved action back to the user and wait for a "confirm" before calling the tool. This mirrors the `set_env_var` and `save_memory` patterns.

## Tool notes

For recurring tasks only (no one-shot reminders yet). Convert the timing to a 5-field cron. Delivery: immediate for "remind me" or "right away", silent for "silently" or "in the background", otherwise next_wake. For create, cancel and modify, read the resolved action back in plain English and call only after the user says confirm; if several schedules match, ask which one.

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [create, list, cancel, modify]
  cron:
    type: string
    description: 5-field cron expression, e.g. "0 8 * * *" (create; modify when changing the time)
  prompt:
    type: string
    description: The natural-language request to run when it fires (create)
  delivery:
    type: string
    enum: [immediate, next_wake, silent]
  label:
    type: string
    description: Short, voice-friendly name (optional)
  id_or_label:
    type: string
    description: Which schedule to cancel or modify
required:
  - action
```
