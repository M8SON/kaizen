---
name: save-memory
description: Save a durable fact about Mason to long-term memory.
---
# Save Memory

## What is worth saving
Save things that would be useful to recall in a future session:
- Stated preferences ("I prefer temperatures in Celsius")
- Ongoing projects or goals Mason has described
- Facts about Mason's life or work he has mentioned
- Anything Mason explicitly asks you to remember

You do not need to wait for an explicit "remember this" command if the information is clearly durable and useful across future conversations.

Do not save passing remarks, one-off requests, or things that will not matter next session.

## What to save
Extract the core fact or preference worth remembering. Keep it concise. For example:
- "My wife's name is Sarah"
- "User prefers temperatures in Celsius"
- "The garage door code is 1234"
- "User is working on Kaizen routing reliability"

## Tool notes

Only preferences, projects, facts about him, or things he asks you to remember. Acknowledge briefly.

## Inputs

```yaml
type: object
properties:
  topic:
    type: string
    description: 3-5 word label
  content:
    type: string
    description: One factual statement
required:
  - topic
  - content
```

## How to respond
After saving, confirm naturally. For example: "Got it, I'll remember that." Keep it short.
Do not read the content back unless the user asks.
If you saved something proactively, keep the acknowledgement minimal and natural.
