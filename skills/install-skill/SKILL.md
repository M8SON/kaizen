---
name: install-skill
description: 'Add a new skill: write one from a description, or install one from a URL or path.'
---
# Install Skill

## When to use

**Author from scratch** — call with `description` when the user asks for a capability
that doesn't exist yet:
- "add a skill that does X"
- "install a skill to X"
- "teach yourself to X"
- "create a skill for X"

**Install existing** — call with `source` when the user references a URL, repo, or path
to an agentskills.io-format skill:
- "install the pdf-tools skill from github dot com slash foo slash bar"
- "install the skill at this URL: ..."
- "import the skill from ..."

Do NOT use this for general questions or tasks the assistant can already do.

## Tool notes

Both kinds run in the background and wait on the control panel's Skills page for the user to approve them there; there are no spoken confirmations, so call it as soon as the request is clear.

## Inputs

```yaml
type: object
properties:
  description:
    type: string
    description: What the new skill should do
  source:
    type: string
    description: URL or path of an existing skill to install
```

Exactly one of `description` or `source` should be provided. `source` takes precedence
when both are present.

## How to respond

Say in one or two short sentences that the skill is being built (or fetched
and checked) and will be ready to approve on the control panel in a few
minutes. Do not use markdown.
