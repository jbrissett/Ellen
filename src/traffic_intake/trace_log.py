"""Append-only timing / diagnostic trace log.

Counterpart to usage_tracker.py — that file records Anthropic API costs;
this file records EVERY timed event in the pipeline so we can see WHERE
time goes between API calls. Sits at `%LOCALAPPDATA%/TrafficIntake/trace.jsonl`.

Event shape: `{ts, event, duration_ms, ...metadata}` — one JSON object
per line. `event` is a dotted name like `"geocoder.tier"` or `"ui.send_clicked"`;
`duration_ms` is the operation's wall-clock duration (0 for instantaneous
events like a button click). Caller-supplied metadata flows through as
extra keys on the record.

Established 2026-05-25 to instrument the geocoder per-tier (which was
spending 71s on a 2-address email with no visibility into which tier
caused it) and to quantify the "ghost gap" between user send and the
next chat API call (Qt event loop + worker spin-up + Anthropic
first-token-in latency).

Usage:
  # Time a block — most common pattern
  with trace_log.timed("geocoder.tier", phase="overpass_raw", address=addr) as t:
      result = _overpass_intersection(...)
      t["hit"] = result is not None  # add fields during the block

  # Or record an instantaneous event
  trace_log.event("ui.send_clicked")

  # Inspect later
  python -m traffic_intake.trace_log --since 1h --filter geocoder

Best-effort + exception-swallowed (same discipline as usage_tracker) — a
tracing bug can never break the actual pipeline. Single threading.Lock
around append; multiple workers can record concurrently.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import logging
import threading
import time as _time
from pathlib import Path
from typing import Optional

from . import config

log = logging.getLogger(__name__)
_lock = threading.Lock()


def trace_path() -> Path:
    """Return the absolute path of the JSONL trace file."""
    return config.app_data_dir() / "trace.jsonl"


def event(name: str, *, duration_ms: int = 0, **meta) -> None:
    """Record an instantaneous (or already-measured) event. Best-effort;
    swallow exceptions so tracing never breaks the pipeline.
    """
    try:
        rec = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"),
            "event": name,
            "duration_ms": int(duration_ms),
        }
        # Coerce meta values to JSON-serializable forms; drop anything weird.
        for k, v in meta.items():
            if v is None or isinstance(v, (str, int, float, bool, list, dict)):
                rec[k] = v
            else:
                rec[k] = repr(v)[:200]
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _lock:
            with trace_path().open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception as exc:
        log.warning("trace_log.event failed (suppressed): %s", exc)


@contextlib.contextmanager
def timed(name: str, **meta):
    """Context manager — emits one event with `duration_ms` set to the
    wall-clock time spent inside the block.

    The yielded dict lets callers attach more fields during execution
    (e.g., whether a tier hit or missed). Those land in the same record.

        with trace_log.timed("geocoder.tier", phase="overpass_raw") as t:
            result = _overpass_intersection(...)
            t["hit"] = result is not None
            t["candidates_tried"] = 3
    """
    extra: dict = {}
    start = _time.monotonic()
    try:
        yield extra
    finally:
        try:
            duration_ms = int((_time.monotonic() - start) * 1000)
            full = {**meta, **extra}
            event(name, duration_ms=duration_ms, **full)
        except Exception as exc:
            log.warning("trace_log.timed teardown failed (suppressed): %s", exc)


def read_events(
    since: Optional[_dt.datetime] = None,
    until: Optional[_dt.datetime] = None,
    event_filter: Optional[str] = None,
) -> list[dict]:
    """Read events from the JSONL log, optionally filtered by time window
    or event-name substring."""
    path = trace_path()
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
                log.warning("trace.jsonl line %d malformed: %s", ln_no, exc)
                continue
            ts_str = rec.get("ts") or ""
            if ts_str and (since or until):
                try:
                    when = _dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    when = None
                if when is not None:
                    if since and when < since:
                        continue
                    if until and when >= until:
                        continue
            if event_filter and event_filter not in rec.get("event", ""):
                continue
            out.append(rec)
    return out


def _parse_since(text: str) -> Optional[_dt.datetime]:
    """Accept '1h' / '30m' / '24h' / 'YYYY-MM-DD' / ISO 8601."""
    text = text.strip().lower()
    now = _dt.datetime.now(_dt.timezone.utc)
    if text.endswith("h") and text[:-1].isdigit():
        return now - _dt.timedelta(hours=int(text[:-1]))
    if text.endswith("m") and text[:-1].isdigit():
        return now - _dt.timedelta(minutes=int(text[:-1]))
    if text.endswith("d") and text[:-1].isdigit():
        return now - _dt.timedelta(days=int(text[:-1]))
    try:
        dt = _dt.datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except ValueError:
        return None


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse
    from collections import Counter
    parser = argparse.ArgumentParser(
        prog="python -m traffic_intake.trace_log",
        description="Inspect timing events from trace.jsonl.",
    )
    parser.add_argument(
        "--since",
        help="Earliest time to include. Examples: '1h', '30m', '2d', "
             "or ISO date '2026-05-25'. Default: 24h ago.",
        default="24h",
    )
    parser.add_argument(
        "--filter",
        help="Substring match on event name. e.g. 'geocoder' or 'chat'.",
        default=None,
    )
    parser.add_argument(
        "--tail", type=int, default=None,
        help="Show only the last N matching events (after time filter).",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Group by event name and show count + avg/median/p95 duration.",
    )
    args = parser.parse_args(argv)

    since = _parse_since(args.since)
    if since is None:
        print(f"warning: couldn't parse --since {args.since!r}; defaulting to 24h", flush=True)
        since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)

    events = read_events(since=since, event_filter=args.filter)
    if args.tail:
        events = events[-args.tail:]

    print(f"Trace file: {trace_path()}")
    print(f"Since: {since.isoformat()}")
    if args.filter:
        print(f"Filter: {args.filter!r}")
    print(f"Events: {len(events)}")
    print()

    if not events:
        return 0

    if args.summary:
        # Group by event name; show count + duration stats
        from statistics import median
        by_event: dict[str, list[int]] = {}
        for ev in events:
            by_event.setdefault(ev["event"], []).append(int(ev.get("duration_ms", 0)))
        print(f"{'event':<40} {'count':>6} {'avg_ms':>9} {'p50_ms':>9} {'p95_ms':>9} {'max_ms':>9}")
        print("-" * 90)
        for name, durs in sorted(by_event.items(), key=lambda x: -sum(x[1])):
            durs_sorted = sorted(durs)
            avg = sum(durs) / len(durs)
            p50 = median(durs_sorted)
            p95 = durs_sorted[int(len(durs_sorted) * 0.95)] if len(durs_sorted) > 1 else durs_sorted[0]
            mx = max(durs_sorted)
            print(f"{name[:40]:<40} {len(durs):>6} {avg:>9.0f} {p50:>9.0f} {p95:>9.0f} {mx:>9}")
    else:
        # Per-event detail (last N)
        for ev in events:
            ts = ev.get("ts", "")[:19]  # trim ms + tz for column width
            dur = ev.get("duration_ms", 0)
            name = ev.get("event", "")
            extras = {k: v for k, v in ev.items()
                      if k not in {"ts", "event", "duration_ms"}}
            extras_str = " ".join(f"{k}={v!r}" for k, v in extras.items())
            print(f"{ts:<20} {dur:>6}ms  {name:<35}  {extras_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
