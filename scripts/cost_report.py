#!/usr/bin/env python3
"""
cost_report.py - What Kaizen's API calls cost, from its own usage log.

Reads ~/.kaizen/usage.db (USAGE_LOG_PATH), written by core/usage_log.py for
every Claude, Jev, ElevenLabs and Meta STT call, and prices it with
config/pricing.yaml.

    .venv/bin/python scripts/cost_report.py            # this month
    .venv/bin/python scripts/cost_report.py 2026-11    # a given month
    .venv/bin/python scripts/cost_report.py --days 7   # last 7 days
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.usage_log import db_path  # noqa: E402

PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing.yaml"


def window(args) -> tuple[float, float, str]:
    """(start_ts, end_ts, label)."""
    now = time.time()
    if args.days:
        return now - args.days * 86400, now, f"last {args.days} days"
    month = args.month or datetime.now().strftime("%Y-%m")
    y, m = (int(x) for x in month.split("-"))
    start = datetime(y, m, 1).timestamp()
    end = datetime(y + (m == 12), m % 12 + 1, 1).timestamp()
    return start, end, month


def cost_of(row: dict, prices: dict) -> float | None:
    service, model = row["service"], row["model"]
    if service == "claude":
        p = prices.get("claude", {}).get(model)
        if not p:
            return None
        return (row["input_tokens"] * p["input"] + row["output_tokens"] * p["output"]
                + row["cache_read_tokens"] * p["cache_read"]
                + row["cache_write_tokens"] * p["cache_write"]) / 1e6
    if service == "jev":
        return row["input_tokens"] * prices["jev"]["input_per_million"] / 1e6
    if service == "elevenlabs":
        return 0.0  # flat plan fee, added once in main()
    if service == "meta_stt":
        return row["audio_ms"] / 60000 * prices["meta_stt"]["per_minute"]
    return None


def usage_text(row: dict) -> str:
    s = row["service"]
    if s == "claude":
        return (f"{row['input_tokens']:,} in / {row['output_tokens']:,} out / "
                f"{row['cache_read_tokens']:,} cache read / {row['cache_write_tokens']:,} cache write tokens")
    if s == "jev":
        return f"{row['input_tokens']:,} in / {row['output_tokens']:,} out tokens"
    if s == "elevenlabs":
        return f"{row['credits']:,.0f} credits"
    if s == "meta_stt":
        ms = row["audio_ms"]
        return f"{ms / 60000:,.1f} min audio" if ms >= 60000 else f"{ms / 1000:.0f} s audio"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("month", nargs="?", help="YYYY-MM (default: this month)")
    ap.add_argument("--days", type=int, help="last N days instead of a month")
    args = ap.parse_args()

    path = db_path()
    if not path.exists():
        print(f"No usage logged yet ({path} does not exist).")
        return 0
    prices = yaml.safe_load(PRICING_PATH.read_text())
    start, end, label = window(args)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT service, model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
        " SUM(output_tokens) AS output_tokens, SUM(cache_read_tokens) AS cache_read_tokens,"
        " SUM(cache_write_tokens) AS cache_write_tokens, SUM(credits) AS credits,"
        " SUM(audio_ms) AS audio_ms, MIN(ts) AS first_ts"
        " FROM usage WHERE ts >= ? AND ts < ? GROUP BY service, model"
        " ORDER BY service, model", (start, end))]
    turns = conn.execute(
        "SELECT COUNT(*) FROM usage WHERE ts >= ? AND ts < ? AND service = 'meta_stt'",
        (start, end)).fetchone()[0]
    conn.close()

    if not rows:
        print(f"No usage logged for {label}.")
        return 0

    print(f"Kaizen API usage, {label}\n")
    total = 0.0
    unpriced = []
    for row in rows:
        cost = cost_of(row, prices)
        name = f"{row['service']} {row['model'] or ''}".strip()
        if cost is None:
            unpriced.append(name)
            cost_s = "   no price"
        else:
            total += cost
            cost_s = f"${cost:>10.4f}"
        print(f"  {name:<36} {cost_s}  {row['calls']:>5} calls  {usage_text(row)}")
    el = prices["elevenlabs"]
    credits = sum(r["credits"] for r in rows if r["service"] == "elevenlabs")
    usage_cost = total
    print(f"  {'elevenlabs plan (flat)':<36} ${el['plan_monthly']:>10.4f}  "
          f"{credits:,.0f} of {el['plan_credits']:,} credits used ({credits / el['plan_credits']:.0%})")
    if not args.days:
        total += el["plan_monthly"]
    print(f"\n  {'Total':<36} ${total:>10.4f}" + ("" if not args.days else "  (plan fee not included)"))
    if turns:
        print(f"  {'Usage cost per spoken request':<36} ${usage_cost / turns:>10.4f}  ({turns} transcribed)")
    # Project from when logging actually started, not the 1st of the month.
    logged_for = time.time() - min(r["first_ts"] for r in rows)
    if not args.days and time.time() < end and logged_for >= 86400:
        scale = 30 * 86400 / logged_for
        print(f"  {'30 days at this rate':<36} ${usage_cost * scale + el['plan_monthly']:>10.2f}"
              f"  (from {logged_for / 86400:.1f} days logged; "
              f"~{credits * scale:,.0f} ElevenLabs credits)")
    if unpriced:
        print(f"\n  Not priced (add to config/pricing.yaml): {', '.join(unpriced)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
