#!/usr/bin/env python3
"""
cost_report.py - What Kaizen's API calls cost, from its own usage log.

Reads ~/.kaizen/usage.db (USAGE_LOG_PATH), written by core/usage_log.py for
every Claude, Jev, ElevenLabs and Meta STT call, and prices it with
config/pricing.yaml (core/cost_summary.py, shared with the control panel).

    .venv/bin/python scripts/cost_report.py            # this month
    .venv/bin/python scripts/cost_report.py 2026-11    # a given month
    .venv/bin/python scripts/cost_report.py --days 7   # last 7 days
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cost_summary import days_window, month_window, summarize  # noqa: E402
from core.usage_log import db_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("month", nargs="?", help="YYYY-MM (default: this month)")
    ap.add_argument("--days", type=int, help="last N days instead of a month")
    args = ap.parse_args()

    start, end, label = days_window(args.days) if args.days else month_window(args.month)
    s = summarize(start, end, label, include_plan=not args.days)
    if s is None:
        print(f"No usage logged yet ({db_path()} does not exist).")
        return 0
    if not s["rows"]:
        print(f"No usage logged for {label}.")
        return 0

    print(f"Kaizen API usage, {label}\n")
    for row in s["rows"]:
        cost_s = "   no price" if row["cost"] is None else f"${row['cost']:>10.4f}"
        print(f"  {row['name']:<36} {cost_s}  {row['calls']:>5} calls  {row['usage']}")
    print(f"  {'elevenlabs plan (flat)':<36} ${s['plan_monthly']:>10.4f}  "
          f"{s['credits']:,.0f} of {s['plan_credits']:,} credits used "
          f"({s['credits'] / s['plan_credits']:.0%})")
    print(f"\n  {'Total':<36} ${s['total']:>10.4f}"
          + ("" if s["include_plan"] else "  (plan fee not included)"))
    if s["turns"]:
        print(f"  {'Usage cost per spoken request':<36} ${s['per_request']:>10.4f}"
              f"  ({s['turns']} transcribed)")
    p = s["projection"]
    if p:
        print(f"  {'30 days at this rate':<36} ${p['total']:>10.2f}"
              f"  (from {p['days_logged']:.1f} days logged; "
              f"~{p['credits']:,.0f} ElevenLabs credits)")
    if s["unpriced"]:
        print(f"\n  Not priced (add to config/pricing.yaml): {', '.join(s['unpriced'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
