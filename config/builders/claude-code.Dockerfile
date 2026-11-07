# Skill builder sandbox for Claude Code (core/skill_builder.py).
# Same Python as every skill (kaizen/base), plus Node and the Claude Code CLI,
# so the agent can run and test the skill it writes. Runs as the host user,
# all capabilities dropped, with only the skill's staging dir mounted.
FROM kaizen/base:latest
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm ca-certificates git \
 && npm install -g @anthropic-ai/claude-code@2.1.295 \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
