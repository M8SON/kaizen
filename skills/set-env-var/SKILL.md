---
name: set-env-var
description: Save an API key or credential that an unavailable skill needs to .env.
---
# Set Environment Variable

## When to use
Use this skill when:
- The user provides an API key or credential for a skill that was listed as unavailable
- The user says something like "I have the API key, it's XYZ" after being told a skill is missing one
- The user explicitly asks to set or update an environment variable

Do NOT use this for general configuration questions or anything unrelated to missing skill credentials.

## Confirmation required — do NOT call this tool immediately

Before invoking this tool you MUST complete both confirmation steps conversationally:

1. Repeat the key value back character by character or in short groups so the user can verify it was heard correctly. For example: "I heard the key as A-B-C-1-2-3-X-Y-Z, is that correct?"
2. After the user confirms the value is correct, ask a second time before writing: "Got it. Shall I save that to your config now?"
3. Only call the tool after the user confirms both times.

If the user says no at either step, ask them to repeat the key.

## Tool notes

Before calling, read the value back in short groups, then ask "Shall I save that now?"; call only after both confirmations. Never read the value aloud after saving.

## Inputs

```yaml
type: object
properties:
  key:
    type: string
    description: Variable name
  value:
    type: string
required:
  - key
  - value
```

## How to respond
After the tool runs, confirm which key was saved and whether the skill is now available.
Do not read the key value back aloud after saving.
Use short spoken sentences. Do not use markdown.
