"""Persistent geocode cache — same address asked twice = the second time is free.

Stored as append-only JSONL at `%LOCALAPPDATA%/TrafficIntake/geocode_cache.jsonl`,
loaded once into an in-memory dict on first access. Single-writer
(threading.Lock around the file append). No TTL — intersections don't move;
re-locate the file manually if a street rename surfaces a stale entry.

Phase 1 of the geocoder speed optimization (2026-05-26 baseline showed
geocoder phase averaging 5.4 min, range up to 13 min, with the chain
running the full 5 tiers for any address Overpass misses on first try).
Caching turns the second + third + Nth run of the same intersection
into ~0ms. Highest single-shot win on real-client emails because QC's
client list re-visits the same cities repeatedly (Lakeland, Winchester,
Pittsboro, etc.).

The cache key is order-insensitive for intersections: "A and B" hits the
same entry as "B and A" / "A & B" / "B & A". Non-intersection addresses
are cached by their normalized full text.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from . import config

log = logging.getLogger(__name__)


CACHE_VERSION = 1  # bump if the record schema changes incompatibly


def cache_path() -> Path:
    """Return the absolute path of the JSONL cache file. Parent dir is
    guaranteed to exist (app_data_dir mkdirs)."""
    return config.app_data_dir() / "geocode_cache.jsonl"


# ---- key normalization ----

_STATE_ABBREVS_FOR_NORM = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "rhode island": "ri",
    "south carolina": "sc", "south dakota": "sd", "tennessee": "tn",
    "texas": "tx", "utah": "ut", "vermont": "vt", "virginia": "va",
    "washington": "wa", "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
    "district of columbia": "dc",
}


def _normalize_state(state_raw: str) -> str:
    """Normalize state to lowercase 2-letter abbrev where possible."""
    s = state_raw.strip().lower()
    return _STATE_ABBREVS_FOR_NORM.get(s, s)


def _normalize_street(street: str) -> str:
    """Aggressive normalize for cache-key purposes: lowercase, collapse
    whitespace, drop punctuation that doesn't matter for identity.

    Does NOT strip suffixes (Rd vs Road) — those CAN matter for distinguishing
    similarly-named streets in big cities. If two emails spell the same
    intersection differently ("Senseny Rd" vs "Senseny Road"), they'll get
    two cache entries — fine; both eventually resolve to the same coords.
    """
    s = street.strip().lower()
    s = re.sub(r"[,\.]", "", s)         # drop commas + periods
    s = re.sub(r"\s+", " ", s)          # collapse runs of whitespace
    return s


def normalize_address(address: str) -> str:
    """Build the cache key for any address. Handles two shapes:

      - Intersection (`<X> and <Y>, <city>, <state>`): produces an
        order-insensitive key `intersection|state|city|street_a|street_b`
        where street_a < street_b alphabetically. So "Senseny Rd and
        Greenwood Rd, Winchester VA" and "Greenwood Rd & Senseny Rd,
        Winchester, VA" both produce `intersection|va|winchester|greenwood rd|senseny rd`.

      - Single address (anything that doesn't match the intersection
        regex): produces `addr|<normalized full text>` — case + whitespace
        only, no part-reordering.
    """
    if not address:
        return ""
    # Try the same intersection regex used elsewhere in the geocoder.
    m = re.match(
        r"^\s*(?P<a>.+?)\s+(?:and|&|at)\s+(?P<b>.+?)\s*,\s*"
        r"(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Za-z]{2}|[A-Za-z][A-Za-z\s]+?)"
        r"(?:\s+\d{5}(?:-\d{4})?)?\s*$",
        address,
        re.IGNORECASE,
    )
    if m:
        a = _normalize_street(m.group("a"))
        b = _normalize_street(m.group("b"))
        if a > b:
            a, b = b, a  # sort so order-insensitive
        city = _normalize_street(m.group("city"))
        state = _normalize_state(m.group("state"))
        return f"intersection|{state}|{city}|{a}|{b}"
    # Fall through: single address.
    return f"addr|{_normalize_street(address)}"


# ---- record dataclass ----

@dataclass
class CachedGeocode:
    key: str
    lat: float
    lon: float
    formatted_address: str
    location_type: str
    place_id: Optional[str]
    cached_at: str  # ISO 8601 UTC
    cache_version: int = CACHE_VERSION

    def to_geocode_result(self):
        """Reconstruct a geocoder.GeocodeResult. Imported lazily to avoid
        circular import (geocoder imports geocode_cache, not the other way)."""
        from .geocoder import GeocodeResult
        return GeocodeResult(
            latitude=self.lat,
            longitude=self.lon,
            formatted_address=self.formatted_address,
            location_type=self.location_type,
            place_id=self.place_id,
        )


# ---- in-memory cache + locks ----

_cache: dict[str, CachedGeocode] = {}
_loaded = False
_lock = threading.Lock()


def _ensure_loaded() -> None:
    """Read the JSONL file once into the in-memory dict. Idempotent —
    subsequent calls are no-ops. Malformed lines are skipped (logged at
    INFO). Most recent entry wins on duplicate keys (file order)."""
    global _loaded
    with _lock:
        if _loaded:
            return
        path = cache_path()
        if path.exists():
            try:
                for ln_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        log.info("geocode_cache.jsonl line %d malformed (%s); skipping", ln_no, exc)
                        continue
                    if d.get("cache_version", 1) != CACHE_VERSION:
                        # Future-proofing: skip entries from incompatible schema versions.
                        continue
                    try:
                        entry = CachedGeocode(**d)
                    except TypeError:
                        # Missing fields or unexpected schema — skip.
                        continue
                    _cache[entry.key] = entry  # later entries overwrite earlier (file-order most-recent-wins)
            except OSError as exc:
                log.warning("Couldn't read geocode cache (%s); starting empty", exc)
        _loaded = True
        if _cache:
            log.info("geocode_cache: loaded %d entries from %s", len(_cache), path)


def lookup(address: str):
    """Return the cached GeocodeResult for `address`, or None on miss.
    Loads the file lazily on first call."""
    _ensure_loaded()
    key = normalize_address(address)
    if not key:
        return None
    with _lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    return entry.to_geocode_result()


def store(address: str, result) -> None:
    """Append `result` to the cache for `address`. In-memory + on-disk.
    Best-effort — disk failures don't propagate.

    `result` is a geocoder.GeocodeResult (or anything with .latitude, .longitude,
    .formatted_address, .location_type, .place_id attributes).
    """
    _ensure_loaded()
    key = normalize_address(address)
    if not key:
        return
    entry = CachedGeocode(
        key=key,
        lat=float(result.latitude),
        lon=float(result.longitude),
        formatted_address=result.formatted_address or "",
        location_type=result.location_type or "",
        place_id=result.place_id,
        cached_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    )
    with _lock:
        _cache[key] = entry
    line = json.dumps(asdict(entry), ensure_ascii=False) + "\n"
    try:
        with _lock:
            with cache_path().open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError as exc:
        log.warning("geocode_cache: couldn't persist entry (%s); kept in-memory only", exc)


def clear() -> int:
    """Wipe the cache (in-memory + on-disk). Returns count of entries
    that were dropped. Defensive — used by the CLI helper."""
    _ensure_loaded()
    with _lock:
        n = len(_cache)
        _cache.clear()
        try:
            cache_path().unlink(missing_ok=True)
        except OSError as exc:
            log.warning("geocode_cache: couldn't delete cache file (%s)", exc)
    return n


def size() -> int:
    """Number of entries currently cached. Loads the file lazily."""
    _ensure_loaded()
    with _lock:
        return len(_cache)


# ---- CLI for inspection / clearing ----

def _main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m traffic_intake.geocode_cache",
        description="Inspect or clear the geocode cache.",
    )
    parser.add_argument("--clear", action="store_true", help="Wipe the cache and exit.")
    parser.add_argument("--list", action="store_true", help="List all cached entries.")
    args = parser.parse_args(argv)

    path = cache_path()
    print(f"Cache file: {path}")
    print(f"Cache version: {CACHE_VERSION}")

    if args.clear:
        n = clear()
        print(f"Cleared {n} entries.")
        return 0

    _ensure_loaded()
    print(f"Entries: {size()}")
    if args.list:
        for key, entry in sorted(_cache.items()):
            print(f"  {key:80s} -> {entry.lat:.5f}, {entry.lon:.5f}  ({entry.location_type})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
