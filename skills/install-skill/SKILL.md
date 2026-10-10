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

Tell the user you'll walk them through spoken confirmations before anything is built.

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

For both flows: tell the user what will happen and that you will walk them through
confirmation steps before anything is built or installed. Use short spoken sentences.
Do not use markdown.
