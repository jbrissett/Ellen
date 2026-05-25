"""Per-call Anthropic API usage tracking.

Every Sonnet/Opus/Haiku call (extraction, chat turn, company-address
web search) appends one JSON line to `%LOCALAPPDATA%/TrafficIntake/usage.jsonl`
with timestamp, model, token counts, and an estimated cost. The file is
append-only and machine-readable, so historical cost can be reconstructed
at any granularity (per run, per day, per week) without a database.

Reading the data:
  python -m traffic_intake.usage_tracker            # last 30 days summary
  python -m traffic_intake.usage_tracker --since 2026-05-01
  python -m traffic_intake.usage_tracker --by-day   # daily breakdown

Why a flat JSONL and not SQLite / a service: the file is < 1KB per run,
single-writer (one Ellen instance per machine), and trivially shippable
to a spreadsheet if the user ever wants charts. SQLite would add a
schema migration burden the user shouldn't have to think about.

PRICING is current as of the date in PRICING_VERIFIED. Update both
when refreshing — the version string ships into each JSONL record so
older entries remain interpretable even after pricing changes.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from . import config

log = logging.getLogger(__name__)

# Pricing in USD per million tokens. Verified 2026-01 against the
# Anthropic public pricing page; update PRICING_VERIFIED if you refresh.
# Web search server-tool price is per 1,000 queries (not per token).
PRICING_VERIFIED = "2026-01"
PRICING: dict[str, dict[str, float]] = {
    # Sonnet 4.6 — default for everything except Opus fallback paths.
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,   # 1.25x input
        "cache_read":  0.30,   # 0.10x input
    },
    # Opus 4.7 — used only when Sonnet exhausts retries / 529s.
    "claude-opus-4-7": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read":  1.50,
    },
    # Haiku 4.5 — last-resort fallback in MODEL_CHAIN. Cheap.
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read":  0.10,
    },
}
# Default rates used when a model id isn't in PRICING (e.g., Anthropic
# ships a new model before we update this file). Lets us still record
# tokens — cost will be approximated using Sonnet rates and the record
# is marked with `cost_pricing_fallback: true` so the warning shows.
_FALLBACK_PRICING = PRICING["claude-sonnet-4-6"]

# Web search server tool charge — $10 per 1,000 queries as of 2026-01.
WEB_SEARCH_USD_PER_QUERY = 0.01


# Single-writer file lock so concurrent threads (chat worker + qchub
# worker can call this simultaneously, each on their own thread pool)
# don't interleave writes mid-line. The file is append-only with one
# JSON object per line; interleaving would produce un-parseable rows.
_write_lock = threading.Lock()


def usage_log_path() -> Path:
    """Return the absolute path of the JSONL usage log. Parent dir is
    guaranteed to exist (app_data_dir mkdirs)."""
    return config.app_data_dir() / "usage.jsonl"


def _estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int,
    cache_read_tokens: int,
    web_search_queries: int = 0,
) -> tuple[float, bool]:
    """Compute estimated $ cost for one API call. Returns (cost, fallback)
    where `fallback` is True if the model wasn't in PRICING and we used
    Sonnet rates as a stand-in.
    """
    rates = PRICING.get(model)
    fallback = rates is None
    if rates is None:
        rates = _FALLBACK_PRICING
    cost = (
        (input_tokens       / 1_000_000) * rates["input"]
        + (output_tokens    / 1_000_000) * rates["output"]
        + (cache_write_tokens / 1_000_000) * rates["cache_write"]
        + (cache_read_tokens  / 1_000_000) * rates["cache_read"]
        + web_search_queries * WEB_SEARCH_USD_PER_QUERY
    )
    return cost, fallback


def _usage_field(usage_obj: Any, name: str, default: int = 0) -> int:
    """Pull `name` from an Anthropic usage object regardless of shape.
    The SDK has shipped both attribute-style (Pydantic model) and
    dict-style payloads across versions; this handles either.
    """
    if usage_obj is None:
        return default
    if isinstance(usage_obj, dict):
        v = usage_obj.get(name)
    else:
        v = getattr(usage_obj, name, None)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def record(
    phase: str,
    model: str,
    usage_obj: Any,
    *,
    duration_ms: Optional[int] = None,
    web_search_queries: int = 0,
    meta: Optional[dict] = None,
) -> dict:
    """Record one Anthropic API call's usage to the JSONL log.

    `phase` is a short string identifying the call site for grouping:
      "extraction"             — extractor.py (per email)
      "chat"                   — chat.py (per Ellen turn)
      "company_address_search" — qchub.py web_search lookup

    `usage_obj` is `response.usage` from the Anthropic SDK (Pydantic
    model or dict; both supported). For server tools that don't carry
    standard usage (e.g., the web_search tool result), pass `usage_obj=None`
    and set `web_search_queries` to the query count — the line still
    records cost.

    `meta` is an optional dict of phase-specific context (run_dir,
    n_locations, retry_attempt, etc.) — surfaced verbatim in the record
    for later analysis. Keep it small (JSON-serializable, ≤ ~500 chars).

    Returns the record that was written, so callers can also log a
    one-line summary into their own diagnostic log if they want.

    Failures are swallowed (warning-logged) — usage tracking must never
    break the actual API flow.
    """
    try:
        input_tokens       = _usage_field(usage_obj, "input_tokens")
        output_tokens      = _usage_field(usage_obj, "output_tokens")
        cache_write_tokens = _usage_field(usage_obj, "cache_creation_input_tokens")
        cache_read_tokens  = _usage_field(usage_obj, "cache_read_input_tokens")

        cost_usd, fallback = _estimate_cost_usd(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
            web_search_queries=web_search_queries,
        )

        record_dict: dict[str, Any] = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"),
            "phase": phase,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_read_tokens":  cache_read_tokens,
            "web_search_queries": web_search_queries,
            "cost_usd": round(cost_usd, 6),
            "pricing_version": PRICING_VERIFIED,
        }
        if fallback:
            record_dict["cost_pricing_fallback"] = True
        if duration_ms is not None:
            record_dict["duration_ms"] = int(duration_ms)
        if meta:
            # Truncate any single field to 500 chars so a giant subject
            # line / URL can't bloat the JSONL beyond practical use.
            record_dict["meta"] = {
                k: (v[:500] if isinstance(v, str) and len(v) > 500 else v)
                for k, v in meta.items()
            }

        path = usage_log_path()
        line = json.dumps(record_dict, ensure_ascii=False) + "\n"
        with _write_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        return record_dict
    except Exception as exc:
        log.warning("usage_tracker.record failed (suppressed): %s", exc)
        return {}


def read_records(
    since: Optional[_dt.datetime] = None,
    until: Optional[_dt.datetime] = None,
) -> list[dict]:
    """Read records from the JSONL log, optionally filtered by time
    window. Returns a list of dicts in file order (oldest first).
    Malformed lines are skipped with a warning.
    """
    path = usage_log_path()
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for ln_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                log.warning("usage.jsonl line %d malformed: %s", ln_no, exc)
                continue
            ts = rec.get("ts")
            if ts and (since or until):
                try:
                    when = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    when = None
                if when is not None:
                    if since and when < since:
                        continue
                    if until and when >= until:
                        continue
            out.append(rec)
    return out


def summarize(
    records: list[dict],
    *,
    group_by: str = "total",
) -> dict:
    """Aggregate cost + token totals across records.

    `group_by`:
      "total" — single aggregate over the whole list
      "phase" — keyed by phase (extraction/chat/...)
      "model" — keyed by model id
      "day"   — keyed by YYYY-MM-DD (UTC)

    Returns a dict mapping group key → totals dict with cost_usd, calls,
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens.
    For group_by="total" the dict has one key "all".
    """
    def empty_bucket() -> dict:
        return {
            "calls": 0,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "web_search_queries": 0,
        }

    buckets: dict[str, dict] = {}
    for rec in records:
        if group_by == "phase":
            key = rec.get("phase", "(unknown)")
        elif group_by == "model":
            key = rec.get("model", "(unknown)")
        elif group_by == "day":
            ts = rec.get("ts") or ""
            key = ts[:10] if len(ts) >= 10 else "(undated)"
        else:
            key = "all"
        b = buckets.setdefault(key, empty_bucket())
        b["calls"] += 1
        b["cost_usd"]            += float(rec.get("cost_usd") or 0.0)
        b["input_tokens"]        += int(rec.get("input_tokens") or 0)
        b["output_tokens"]       += int(rec.get("output_tokens") or 0)
        b["cache_read_tokens"]   += int(rec.get("cache_read_tokens") or 0)
        b["cache_write_tokens"]  += int(rec.get("cache_write_tokens") or 0)
        b["web_search_queries"]  += int(rec.get("web_search_queries") or 0)
    return buckets


def _format_summary_table(buckets: dict, *, sort_by_cost: bool = True) -> str:
    """Render a `summarize()` result as a fixed-width text table for the
    CLI. Columns: key | calls | input | output | cache R/W | cost.
    """
    if not buckets:
        return "(no records in this window)"
    items = list(buckets.items())
    if sort_by_cost:
        items.sort(key=lambda kv: kv[1]["cost_usd"], reverse=True)
    rows = []
    header = f"{'key':<30} {'calls':>6} {'input':>10} {'output':>9} {'cache_r':>9} {'cache_w':>9}  {'cost':>9}"
    rows.append(header)
    rows.append("-" * len(header))
    for key, b in items:
        rows.append(
            f"{str(key)[:30]:<30} "
            f"{b['calls']:>6} "
            f"{b['input_tokens']:>10,} "
            f"{b['output_tokens']:>9,} "
            f"{b['cache_read_tokens']:>9,} "
            f"{b['cache_write_tokens']:>9,}  "
            f"${b['cost_usd']:>8.4f}"
        )
    return "\n".join(rows)


def _main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint: print a usage summary for the requested window.

    Defaults to the last 30 days, total breakdown by phase + day.
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m traffic_intake.usage_tracker",
        description="Summarize Anthropic API usage + cost from the Ellen JSONL log.",
    )
    parser.add_argument(
        "--since",
        help="Earliest date to include (YYYY-MM-DD or ISO datetime). "
             "Default: 30 days ago.",
    )
    parser.add_argument(
        "--until",
        help="Latest date to exclude (YYYY-MM-DD or ISO datetime). "
             "Default: now.",
    )
    parser.add_argument(
        "--by-day",
        action="store_true",
        help="Show day-by-day breakdown instead of phase totals.",
    )
    args = parser.parse_args(argv)

    since: Optional[_dt.datetime] = None
    if args.since:
        since = _dt.datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=_dt.timezone.utc)
    else:
        since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
    until: Optional[_dt.datetime] = None
    if args.until:
        until = _dt.datetime.fromisoformat(args.until)
        if until.tzinfo is None:
            until = until.replace(tzinfo=_dt.timezone.utc)

    records = read_records(since=since, until=until)
    path = usage_log_path()
    print(f"Usage log: {path}")
    print(f"Window: since={since.isoformat() if since else 'beginning'}, "
          f"until={until.isoformat() if until else 'now'}")
    print(f"Records in window: {len(records)}")
    print(f"Pricing version: {PRICING_VERIFIED} (Sonnet 4.6 ${PRICING['claude-sonnet-4-6']['input']:.2f}/MTok input)")
    print()
    if not records:
        return 0
    total = summarize(records, group_by="total")["all"]
    print(f"TOTAL: {total['calls']} calls, ${total['cost_usd']:.4f}")
    print()
    if args.by_day:
        print("By day:")
        print(_format_summary_table(summarize(records, group_by="day"), sort_by_cost=False))
    else:
        print("By phase:")
        print(_format_summary_table(summarize(records, group_by="phase")))
        print()
        print("By model:")
        print(_format_summary_table(summarize(records, group_by="model")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
