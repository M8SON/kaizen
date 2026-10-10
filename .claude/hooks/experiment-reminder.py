#!/usr/bin/env python3
"""Stop hook: remind Claude to write unsaved test results to docs/experiments/.

Blocks the stop once (per new measurement) when, since the last
docs/experiments/ write, the session either ran a timing script, or touched the
Pi and the final reply has a results table (two or more rows with numbers).
Any error lets the stop through.
"""
import json
import os
import re
import sys

# Commands that touch the real system: the Pi or its logs.
MEASURE = re.compile(r"\bssh\b[^\n]*\b(pi|archimedes@)|journalctl", re.IGNORECASE)
# Ad-hoc experiment scripts: they time something.
EXPERIMENT = re.compile(r"monotonic|perf_counter|timeit|bench", re.IGNORECASE)
TABLE_ROW_WITH_NUMBER = re.compile(r"^\|.*\d.*\|\s*$", re.MULTILINE)

REASON = (
    "This session ran measurements against real hardware or APIs and reported "
    "results that aren't in docs/experiments/ yet. If they are worth keeping, "
    "write a dated results file there (question, method, raw numbers, "
    "conclusion, not tested) and add it to docs/experiments/README.md, per "
    "CLAUDE.md. If there is nothing worth keeping, say so in one line and stop."
)


def main():
    hook = json.load(sys.stdin)
    if hook.get("stop_hook_active"):
        return

    last_measure = last_experiment = last_saved = -1
    final_text = ""
    with open(hook["transcript_path"], encoding="utf-8") as f:
        for i, line in enumerate(f):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            for block in entry.get("message", {}).get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    final_text = block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_input = block.get("input") or {}
                    command = tool_input.get("command", "") if block.get("name") == "Bash" else ""
                    if EXPERIMENT.search(command):
                        last_experiment = i
                    if MEASURE.search(command):
                        last_measure = i
                    elif "docs/experiments/" in str(tool_input.get("file_path", "")):
                        last_saved = i

    has_table = len(TABLE_ROW_WITH_NUMBER.findall(final_text)) >= 2
    if last_experiment > last_saved:
        trigger = last_experiment
    elif last_measure > last_saved and has_table:
        trigger = last_measure
    else:
        return

    state_dir = os.path.join(os.path.expanduser("~"), ".claude", "hook-state", "kaizen-experiment-reminder")
    os.makedirs(state_dir, exist_ok=True)
    state_file = os.path.join(state_dir, f"{hook['session_id']}.json")
    try:
        with open(state_file, encoding="utf-8") as f:
            reminded = json.load(f).get("reminded_measure", -1)
    except (OSError, ValueError):
        reminded = -1
    if trigger <= reminded:
        return
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump({"reminded_measure": max(last_measure, last_experiment)}, f)

    print(json.dumps({"decision": "block", "reason": REASON}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
