"""
Price Kaizen's usage log. Shared by scripts/cost_report.py and the control
panel's Costs page, so the two can never show different numbers.

Usage comes from core/usage_log.py (~/.kaizen/usage.db); prices from
config/pricing.yaml, applied here at read time.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

import yaml

from core.usage_log import db_path

PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing.yaml"


def month_window(month: str | None = None) -> tuple[float, float, str]:
    """(start_ts, end_ts, "YYYY-MM") for a calendar month (default: this one)."""
    month = month or datetime.now().strftime("%Y-%m")
    y, m = (int(x) for x in month.split("-"))
    start = datetime(y, m, 1).timestamp()
    end = datetime(y + (m == 12), m % 12 + 1, 1).timestamp()
    return start, end, month


def days_window(days: int) -> tuple[float, float, str]:
    now = time.time()
    return now - days * 86400, now, f"last {days} days"


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
        return 0.0  # flat plan fee, added once per month
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


def summarize(start: float, end: float, label: str, *, include_plan: bool,
              path: Path | None = None, prices: dict | None = None) -> dict | None:
    """Priced usage between start and end. None when nothing is logged at all
    (no database); `rows` is empty when the window has no usage.

    include_plan: add the ElevenLabs flat fee to the total (month windows).
    """
    path = path or db_path()
    if not path.exists():
        return None
    prices = prices or yaml.safe_load(PRICING_PATH.read_text())

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
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
    finally:
        conn.close()

    el = prices["elevenlabs"]
    usage_cost = 0.0
    unpriced = []
    for row in rows:
        row["name"] = f"{row['service']} {row['model'] or ''}".strip()
        row["cost"] = cost_of(row, prices)
        row["usage"] = usage_text(row)
        if row["cost"] is None:
            unpriced.append(row["name"])
        else:
            usage_cost += row["cost"]
    credits = sum(r["credits"] for r in rows if r["service"] == "elevenlabs")

    projection = None
    if rows:
        # Project from when logging actually started, not the 1st of the month.
        logged_for = time.time() - min(r["first_ts"] for r in rows)
        if include_plan and time.time() < end and logged_for >= 86400:
            scale = 30 * 86400 / logged_for
            projection = {"total": usage_cost * scale + el["plan_monthly"],
                          "days_logged": logged_for / 86400,
                          "credits": credits * scale}

    return {
        "label": label,
        "rows": rows,
        "usage_cost": usage_cost,
        "plan_monthly": el["plan_monthly"],
        "plan_credits": el["plan_credits"],
        "credits": credits,
        "include_plan": include_plan,
        "total": usage_cost + (el["plan_monthly"] if include_plan else 0.0),
        "turns": turns,
        "per_request": usage_cost / turns if turns else None,
        "projection": projection,
        "unpriced": unpriced,
    }
