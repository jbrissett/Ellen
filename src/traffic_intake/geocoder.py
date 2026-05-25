"""Intersection geocoder — Overpass (OSM) → Places API (New) → Autocomplete-canonicalize → legacy Geocoding API.

Used to turn each StudyLocation's `address_or_intersection` (the LLM-friendly
human form, e.g. 'N Socrum Loop Road and Old Combee Road, Lakeland, FL') into
an accurate lat/lon. Replaces Claude's vision/text_only estimates which are
fine for identifying *which* intersection a client meant, but too rough for
client-facing map pins.

Chain (in accuracy-first order — established 2026-05-25 after the Winchester VA
email went 1/8 on the legacy Geocoding-only path):

  1. **Overpass / OpenStreetMap** (free, no key) — for any input parseable as
     "<street A> and <street B>, <city>, <state>". OSM models intersections
     as the literal shared node where two named highway ways meet, so when
     Overpass returns a hit, the coords are pixel-perfect (the actual node
     position, not a geometric center). Failure mode = miss, not wrong-pin.

  2. **Google Places API (New) `places:searchText`** with `includedType="intersection"`
     and `locationRestriction.rectangle` set to the city's bbox. Broader
     coverage than Overpass for newer / private / commercial roads. Tagged
     `PLACES_NEW_INTERSECTION` for confidence reporting.

  3. **Places Autocomplete-canonicalize + retry searchText** — when step 2
     misses, hit Autocomplete (New) on each street individually with
     `includedPrimaryTypes=["route"]` to fix fuzzy spelling / missing
     suffix, then re-issue searchText with the canonical pair. This is
     the standard fix for human/LLM-typed street names.

  4. **Legacy Geocoding API + Places Find Place** — last resort for
     non-intersection inputs (single addresses, mile posts, etc.) and as
     a safety net if every intersection-aware tier missed.

City bbox is resolved once per (city, state) via one Geocoding API call and
cached for the process lifetime, so multi-intersection emails in the same
city don't waste calls.

Costs (verified 2026-01):
  - Overpass: free (rate-limited at the public endpoint)
  - Places API (New) searchText: $32/1k = $0.032/call
  - Places API (New) autocomplete: $2.83/1k = $0.0028/call
  - Legacy Geocoding: $5/1k = $0.005/call
  - Legacy Find Place from Text: $17/1k = $0.017/call

NOTE for users: Places API (New) is a separate Google Cloud product. Enable
it once at https://console.cloud.google.com/apis/library/places.googleapis.com
on the same project that has Geocoding API enabled. Same API key.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Optional

import requests

from .config import get_google_geocoding_key, get_here_api_key

log = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
PLACES_NEW_SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_NEW_AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
HERE_GEOCODE_URL = "https://geocode.search.hereapi.com/v1/geocode"
# Public Overpass endpoint. Rate-limited (~10K queries/day per IP); fine for
# our volume but worth swapping to a self-hosted instance if QC's email
# throughput climbs. Free alternates: overpass.kumi.systems, overpass.private.coffee.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Lowered 15s → 10s 2026-05-25. The big accuracy win is in BETTER APIs, not
# in giving each call longer. With parallelization (one thread per address)
# the per-call ceiling matters more than the per-address total.
REQUEST_TIMEOUT_SEC = 10
# Overpass executes against the planet OSM database, so it's slower
# than a Google point-lookup. 10s is enough for the bbox-scoped
# intersection queries we issue (verified — successful hits return
# in <2s). Lowered from 20s 2026-05-25 to keep the per-address
# worst-case time bounded under the geocoder phase budget.
OVERPASS_TIMEOUT_SEC = 10

# Tuple form is required because requests' single-value timeout applies to
# BOTH connect and the "between bytes" read window — not to total wall time.
# Observed Zephyrhills trace 2026-05-25: a Places autocomplete call with
# `timeout=10` hung 80+ seconds when the server slow-trickled bytes. Tuple
# form caps both phases independently and prevents that runaway.
_REQ_TIMEOUT = (REQUEST_TIMEOUT_SEC, REQUEST_TIMEOUT_SEC)
_OVERPASS_TIMEOUT = (OVERPASS_TIMEOUT_SEC, OVERPASS_TIMEOUT_SEC)

# Module-level connection pool. Without this, every call paid a fresh TLS
# handshake (~3-5 round trips) — the 40s cold bbox_lookup was largely
# handshake overhead, not Google compute. Session reuses keep-alive
# connections across calls (and across worker threads — requests.Session
# is thread-safe for its core HTTP methods).
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Ellen (Quality Counts) traffic-study intake"})


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str
    # Source tier (richer than the original Google-only location_type):
    #   OSM_NODE                — Overpass shared-node intersection (best for standard intersections)
    #   OSM_MOTORWAY_JUNCTION   — Overpass highway=motorway_junction node (best for tagged ramps)
    #   HERE_INTERSECTION       — HERE Geocoder, resultType=intersection (high queryScore)
    #   HERE_STREET             — HERE Geocoder, resultType=street (on the named street, ~50m from intersection)
    #   HERE_HOUSE_NUMBER       — HERE Geocoder, resultType=houseNumber (nearby address, ~100m from intersection)
    #   HERE_LOCALITY           — HERE Geocoder, broader area match (>500m, treat as low)
    #   ROOFTOP / RANGE_INTERPOLATED / GEOMETRIC_CENTER — legacy Geocoding API (single addresses)
    #   APPROXIMATE             — legacy Geocoding city-level pin (last resort)
    # Deprecated / retained for back-compat (no longer produced by current chain):
    #   PLACES_NEW_INTERSECTION / PLACES_NEW_CANONICAL / PLACES_NEW_POI / PLACES_FIND
    #   — the Places-based tiers were dropped 2026-05-25 after they produced
    #   "pins in fields" results (POI-near-streets, not actual intersections).
    location_type: str
    place_id: Optional[str] = None

    @property
    def confidence(self) -> str:
        """Map source tier to our high/medium/low scale."""
        if self.location_type in (
            "OSM_NODE", "OSM_MOTORWAY_JUNCTION",
            "HERE_INTERSECTION",
            "ROOFTOP", "RANGE_INTERPOLATED",
        ):
            # Exact intersection node OR HERE explicitly-typed intersection
            # OR Google's full-address resolution. Tightly-bound.
            return "high"
        if self.location_type in (
            "GEOMETRIC_CENTER", "HERE_STREET", "HERE_HOUSE_NUMBER",
            # Legacy Places tiers kept here for back-compat reads of stored data.
            "PLACES_NEW_INTERSECTION", "PLACES_NEW_CANONICAL", "PLACES_FIND",
        ):
            # On the right street or near an address on the road; usually
            # within ~50-100m of the actual cross-street. Pin lands in
            # the right neighborhood; user should glance to verify exact spot.
            return "medium"
        # PLACES_NEW_POI, HERE_LOCALITY, APPROXIMATE, anything else
        return "low"


# Module-level cache for (city, state) → bbox lookups. One Geocoding call
# per unique city per process lifetime. Thread-safe via the lock.
#
# Per-key in-flight locks deduplicate concurrent FIRST-misses: without
# them, two parallel workers can both check the cache (empty), both make
# the same ~40s Google call, both wait, both write the same result.
# Observed trace 2026-05-25: Zephyrhills FL had 2× 40.7s bbox_lookup
# events fired simultaneously by two workers. Now the second worker
# blocks on the first worker's lock until the bbox is cached.
_bbox_cache: dict[tuple[str, str], Optional[dict]] = {}
_bbox_cache_lock = threading.Lock()  # guards cache reads/writes
_bbox_inflight_locks: dict[tuple[str, str], threading.Lock] = {}  # per-key fetch locks


class GeocoderUnavailable(Exception):
    """No API key configured — geocoder cannot run."""


class GeocodingError(Exception):
    """Google returned an error status for a specific query."""


_SUFFIX_ABBREV = {
    "Road": "Rd", "Boulevard": "Blvd", "Drive": "Dr",
    "Avenue": "Ave", "Street": "St", "Lane": "Ln",
    "Court": "Ct", "Circle": "Cir", "Place": "Pl",
    "Highway": "Hwy", "Parkway": "Pkwy", "Trail": "Trl",
}


def _query_variants(address: str) -> list[str]:
    """Generate alternate phrasings to maximize Geocoder hit rate.

    Expanded 2026-05-25 after Winchester VA email went 1-for-8: LLM-vision
    extractions from email screenshots often produce intersection names
    with non-canonical suffix spelling, "and" vs "&", or extra modifiers
    ("Mall Drive" vs the canonical "Mall Dr"). The variant list is now
    aggressive — we try the most-canonical forms first (Google's caching
    favors them) but keep going through stripped/reversed/connector
    variants before giving up and handing off to the Places fallback.
    """
    variants: list[str] = [address]

    # 1. Suffix abbreviations on full text ("Drive" → "Dr").
    abbreviated = address
    for full, abbr in _SUFFIX_ABBREV.items():
        abbreviated = re.sub(rf"\b{full}\b", abbr, abbreviated)
    if abbreviated != address:
        variants.append(abbreviated)

    # 2. "and" → "&" (some Google cache shards key on this).
    if " and " in address.lower():
        variants.append(re.sub(r"\s+and\s+", " & ", address, flags=re.IGNORECASE))

    # 3. "at" connector — Google geocoder also accepts "X at Y".
    if " and " in address.lower():
        variants.append(re.sub(r"\s+and\s+", " at ", address, flags=re.IGNORECASE))

    # 4. For "<A> and <B>, <city>" — try reversed order (some intersections
    #    only match when the larger-classified road comes first).
    m = re.match(r"^(.+?)\s+and\s+(.+?),\s*(.+)$", address, re.IGNORECASE)
    if m:
        a, b, rest = m.groups()
        variants.append(f"{b.strip()} and {a.strip()}, {rest.strip()}")
        variants.append(f"{b.strip()} & {a.strip()}, {rest.strip()}")

    # 5. Drop street-type suffixes entirely. Useful when the LLM picked the
    #    wrong suffix (Drive vs Dr vs Driveway) — bare road names are
    #    surprisingly tolerated by Google for intersection queries.
    bare = address
    for full in _SUFFIX_ABBREV:
        bare = re.sub(rf"\b{full}\b", "", bare, flags=re.IGNORECASE)
    for abbr in _SUFFIX_ABBREV.values():
        bare = re.sub(rf"\b{abbr}\b", "", bare, flags=re.IGNORECASE)
    bare = re.sub(r"\s{2,}", " ", bare).strip()
    if bare and bare != address:
        variants.append(bare)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


# =====================================================================
# Address parsing — pull intersection components out of free text
# =====================================================================

# State abbreviations / names mapping for normalizing whatever the LLM produced.
_STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_STATE_NAMES = {abbr: name.title() for name, abbr in _STATE_ABBREV.items()}


def _parse_intersection(address: str) -> Optional[dict]:
    """Try to split a free-text address into intersection components.

    Returns a dict with keys `street_a`, `street_b`, `city`, `state`
    (state as 2-letter abbrev), or None if the input doesn't look like
    an "<X> and <Y>, <city>, <state>" intersection.

    Accepted shapes (case-insensitive):
      "Apple Blossom Mall Dr and Pleasant Valley Rd, Winchester, VA"
      "Route 7 & Berryville Pike, Winchester, Virginia"
      "Senseny Rd at Greenwood Rd, Winchester, VA 22602"
    """
    if not address or not address.strip():
        return None
    # Try the full "A <conn> B, City, State [ZIP]" form first.
    m = re.match(
        r"^\s*(?P<a>.+?)\s+(?:and|&|at)\s+(?P<b>.+?)\s*,\s*"
        r"(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Za-z]{2}|[A-Za-z][A-Za-z\s]+?)"
        r"(?:\s+\d{5}(?:-\d{4})?)?\s*$",
        address,
        re.IGNORECASE,
    )
    if not m:
        return None
    state_raw = m.group("state").strip().lower()
    state = _STATE_ABBREV.get(state_raw, state_raw.upper() if len(state_raw) == 2 else None)
    if not state:
        return None
    return {
        "street_a": m.group("a").strip(),
        "street_b": m.group("b").strip(),
        "city": m.group("city").strip(),
        "state": state,
    }


# =====================================================================
# Locality bounding box — cached
# =====================================================================

def _locality_bbox(city: str, state: str, api_key: str) -> Optional[dict]:
    """Return the city's bounding box for use as Places `locationRestriction`.

    Issues exactly ONE Geocoding API call per unique (city, state) per
    process lifetime; subsequent lookups (including concurrent ones from
    parallel geocoder workers) block on a per-key in-flight lock until
    the first thread populates the cache. Returns a dict like
    {"low": {"latitude": .., "longitude": ..},
     "high": {"latitude": .., "longitude": ..}} or None if the city
    couldn't be resolved.
    """
    key = (city.strip().lower(), state.strip().upper())
    # Fast path: already cached
    with _bbox_cache_lock:
        if key in _bbox_cache:
            return _bbox_cache[key]
        # Get or create the per-key in-flight lock atomically under the
        # main cache lock to avoid two threads creating different locks.
        fetch_lock = _bbox_inflight_locks.setdefault(key, threading.Lock())

    # Per-key lock dedupes concurrent first-misses. The second arriving
    # thread blocks here until the first one finishes its fetch + write.
    with fetch_lock:
        with _bbox_cache_lock:
            if key in _bbox_cache:
                # First thread already populated while we were waiting.
                return _bbox_cache[key]
        # We're the first (or sole) thread to fetch. Do the API call,
        # write the result, return.
        return _locality_bbox_fetch(key, city, state, api_key)


def prewarm_bboxes(addresses: list[str], api_key: str) -> int:
    """Resolve city bboxes for all unique (city, state) pairs in `addresses`
    BEFORE the threadpool launches. Returns the number of unique cities
    resolved (for logging).

    Why: workers in the geocoder threadpool each call `_locality_bbox` as
    their first step. If that's a cold miss, the worker blocks ~40s on a
    Google Geocoding call. The dedupe lock saves the duplicate API call
    but NOT the wall-clock wait — every parallel worker waits for the
    first one. Resolving sequentially up-front (1 cold call per unique
    city) means every worker sees a cache hit and starts geocoding
    immediately. Net win on a 4-address, 1-city email: ~40s → ~0s
    blocked time per worker.
    """
    seen: set[tuple[str, str]] = set()
    for addr in addresses:
        parsed = _parse_intersection(addr)
        if parsed is None:
            continue
        key = (parsed["city"].strip().lower(), parsed["state"].strip().upper())
        if key in seen:
            continue
        seen.add(key)
        # Fires the cold call + caches the result. Subsequent worker
        # calls to `_locality_bbox(city, state, ...)` will hit the cache.
        _locality_bbox(parsed["city"], parsed["state"], api_key)
    return len(seen)


def _locality_bbox_fetch(
    key: tuple[str, str], city: str, state: str, api_key: str,
) -> Optional[dict]:
    """Inner fetch — caller holds the per-key in-flight lock."""
    params = {
        "address": f"{city}, {state}",
        "components": f"country:US|administrative_area:{state}",
        "key": api_key,
    }
    try:
        r = _SESSION.get(GEOCODE_URL, params=params, timeout=_REQ_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        log.info("Locality bbox lookup failed for %r, %r: %s", city, state, exc)
        with _bbox_cache_lock:
            _bbox_cache[key] = None
        return None
    data = r.json()
    if data.get("status") != "OK" or not data.get("results"):
        log.info("Locality bbox: Google returned %s for %r, %r", data.get("status"), city, state)
        with _bbox_cache_lock:
            _bbox_cache[key] = None
        return None
    viewport = data["results"][0].get("geometry", {}).get("viewport")
    if not viewport:
        with _bbox_cache_lock:
            _bbox_cache[key] = None
        return None
    bbox = {
        "low":  {"latitude": viewport["southwest"]["lat"], "longitude": viewport["southwest"]["lng"]},
        "high": {"latitude": viewport["northeast"]["lat"], "longitude": viewport["northeast"]["lng"]},
    }
    with _bbox_cache_lock:
        _bbox_cache[key] = bbox
    return bbox


# =====================================================================
# Tier 1: Overpass / OpenStreetMap intersection node lookup
# =====================================================================

def _strip_street_suffix(name: str) -> str:
    """Strip directional prefixes and suffix words for OSM regex matching.

    OSM canonicalizes street suffixes ("Pleasant Valley Rd" stored as
    "Pleasant Valley Road"). We match with a regex on the BASE name only
    — drop suffix words from the query side so OSM's full-spelled names
    don't get filtered out by our abbreviated input.
    """
    s = name.strip()
    # Drop trailing suffix word (e.g., "Pleasant Valley Rd" -> "Pleasant Valley").
    for suffix in (
        "Boulevard", "Blvd", "Highway", "Hwy", "Parkway", "Pkwy",
        "Avenue", "Ave", "Drive", "Dr", "Street", "St", "Road", "Rd",
        "Lane", "Ln", "Court", "Ct", "Circle", "Cir", "Place", "Pl",
        "Trail", "Trl", "Way", "Pike",
    ):
        s = re.sub(rf"\b{suffix}\b\.?$", "", s, flags=re.IGNORECASE).strip()
    # Drop leading directional ("N Main St" -> "Main").
    s = re.sub(r"^(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\s+", "", s, flags=re.IGNORECASE)
    return s.strip().strip(",").strip()


def _street_name_candidates(name: str) -> list[str]:
    """Generate progressively-broader OSM-matchable name fragments.

    Why: LLM-extracted names often have an extra descriptor that OSM
    omits ("Apple Blossom Mall Drive" → OSM has "Apple Blossom Drive";
    "Mall" was a marketing label, not in OSM). Generating multiple
    candidates lets us combine them into a single Overpass regex
    `(full|two-word)` so one query catches several spellings.

    Returns up to 2 candidates ordered most-specific first:
      "Apple Blossom Mall Drive"  →  ["Apple Blossom Mall", "Apple Blossom"]
      "Pleasant Valley Rd"        →  ["Pleasant Valley"]
      "Main St"                   →  ["Main"]

    Single-word fallbacks like "Pleasant" or "Apple" are deliberately
    omitted — they match too many unrelated streets ("Mt Pleasant Rd",
    "Pineapple St"), bloating Overpass to 504-timeout territory when
    combined with a second street's broad regex. Two-word fragments are
    nearly always specific enough to be unique within a city bbox.
    """
    base = _strip_street_suffix(name)
    if not base:
        return []
    words = base.split()
    out = [base]
    if len(words) >= 3:
        out.append(" ".join(words[:2]))
    # De-duplicate while preserving order; drop very short fragments.
    seen: set[str] = set()
    result: list[str] = []
    for c in out:
        c_lower = c.lower()
        if c_lower not in seen and len(c) >= 3:
            seen.add(c_lower)
            result.append(c)
    return result


def _overpass_escape(s: str) -> str:
    """Escape characters that would break an Overpass QL string literal."""
    return s.replace('\\', '\\\\').replace('"', '\\"')


def _overpass_intersection(
    street_a: str, street_b: str, city: str, state: str,
    bbox: Optional[dict] = None,
) -> Optional[GeocodeResult]:
    """Query Overpass for the OSM node where two named ways intersect.

    Returns the exact lat/lon of the shared node if both ways exist in
    OSM within the given area. Returns None on miss or any error — never
    raises (we want gracious fallthrough to Tier 2).

    Scoping: if `bbox` is provided (preferred), search inside that
    rectangle. Otherwise fall back to OSM admin areas. The bbox path is
    MUCH more reliable because many "X, City" addresses are actually
    just outside the city's admin boundary (Winchester VA is an
    independent city — its surrounding county shares the same mailing
    address). Tested 2026-05-25: Senseny × Greenwood in "Winchester VA"
    only resolves via bbox; admin-area scoping misses it because the
    intersection node is in Frederick County.

    bbox shape (matches Places-New `locationRestriction.rectangle`):
      {"low":  {"latitude": ..., "longitude": ...},
       "high": {"latitude": ..., "longitude": ...}}
    """
    candidates_a = _street_name_candidates(street_a)
    candidates_b = _street_name_candidates(street_b)
    if not candidates_a or not candidates_b:
        return None
    # One regex per street, alternation across the candidate fragments.
    # `(Apple Blossom Mall|Apple Blossom|Apple)` matches any way whose name
    # contains any of those substrings — broad enough to tolerate the
    # "Mall" / "Boulevard" / "South" descriptor noise typical of LLM
    # extractions, tight enough that the combination with street B's
    # regex prunes false positives.
    regex_a = "|".join(_overpass_escape(c) for c in candidates_a)
    regex_b = "|".join(_overpass_escape(c) for c in candidates_b)

    if bbox is not None:
        # Pad the city bbox slightly (≈3km) so intersections sitting just
        # past the city limit aren't excluded. Google viewports are already
        # generous but a small overshoot is free insurance.
        pad = 0.03  # ~3km in degrees at US latitudes
        south = bbox["low"]["latitude"]   - pad
        west  = bbox["low"]["longitude"]  - pad
        north = bbox["high"]["latitude"]  + pad
        east  = bbox["high"]["longitude"] + pad
        scope_pre = ""
        scope_filter = f"({south},{west},{north},{east})"
    else:
        # Fallback: admin-area scoping. Less reliable but better than no
        # spatial filter (an unscoped name~"Main" query returns thousands
        # of streets and times out).
        state_name = _STATE_NAMES.get(state.upper(), state)
        scope_pre = (
            f'area["name"="{_overpass_escape(state_name)}"]["admin_level"="4"]->.state;\n'
            f'area(area.state)["name"="{_overpass_escape(city)}"]["admin_level"~"6|7|8"]->.city;\n'
        )
        scope_filter = "(area.city)"

    ql = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_SEC}];\n"
        f"{scope_pre}"
        f'way{scope_filter}[highway][name~"({regex_a})",i]->.w1;\n'
        f'way{scope_filter}[highway][name~"({regex_b})",i]->.w2;\n'
        "node(w.w1)(w.w2);\n"
        "out 5;\n"
    )
    try:
        r = _SESSION.post(OVERPASS_URL, data={"data": ql}, timeout=_OVERPASS_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        log.info("Overpass call failed for %r × %r: %s — falling through to Places.",
                 street_a, street_b, exc)
        return None
    try:
        data = r.json()
    except json.JSONDecodeError as exc:
        log.info("Overpass returned non-JSON for %r × %r: %s", street_a, street_b, exc)
        return None
    elements = data.get("elements") or []
    nodes = [e for e in elements if e.get("type") == "node"]
    if not nodes:
        log.info("Overpass: no shared node for %r × %r in %s, %s.",
                 street_a, street_b, city, state)
        return None
    # Multiple shared nodes can happen when street A crosses street B
    # more than once (boulevards with service roads, divided highways).
    # Take the first — caller can correct on the map. Log when this fires.
    if len(nodes) > 1:
        log.info(
            "Overpass: %d shared nodes for %r × %r in %s — taking the first.",
            len(nodes), street_a, street_b, city,
        )
    node = nodes[0]
    return GeocodeResult(
        latitude=float(node["lat"]),
        longitude=float(node["lon"]),
        formatted_address=f"{street_a} & {street_b}, {city}, {state}",
        location_type="OSM_NODE",
        place_id=f"osm-node-{node.get('id')}",
    )


# =====================================================================
# Tier 2 + 3: Places API (New) — searchText + Autocomplete canonicalize
# =====================================================================

def _places_new_search_text(
    query: str, bbox: Optional[dict], api_key: str,
    *, include_intersection_only: bool = True,
) -> Optional[GeocodeResult]:
    """POST to Places API (New) `places:searchText` with bbox bias.
    Returns the top result's coords on hit.

    Two important Google-API quirks established 2026-05-25 by live tests:

      1. `includedType="intersection"` is INVALID — the API returns
         HTTP 400 "Invalid included_type". The published docs imply it
         works, but the supported-types list rejects it. So we filter
         intersection-vs-not in Python from the returned `types` array.

      2. `locationRestriction.rectangle` HARD-FILTERS — and Google's
         intersection-typed places appear to be stored at coordinates
         that often sit just outside a city's tight viewport rectangle.
         A restriction box that's right-sized for the city CUTS OUT
         the intersection results we want. Use `locationBias` instead
         (soft preference). Empirically: with `locationBias`,
         "Senseny Road & Greenwood Road" returns a `types=['intersection']`
         result; with `locationRestriction` on the same bbox, 0 results.

    When `include_intersection_only=True` (the default for the
    intersection chain), we keep only results whose `types` array
    contains "intersection" — drops POIs (restaurants, malls) that
    happen to sit near the named streets. Caller can pass False to
    accept POI matches as approximate fallbacks.
    """
    body: dict = {
        "textQuery": query,
        "languageCode": "en",
        "regionCode": "US",
        "maxResultCount": 5,
    }
    if bbox is not None:
        # locationBias (soft) rather than locationRestriction (hard).
        # Same shape — the rectangle structure is identical.
        body["locationBias"] = {"rectangle": bbox}
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.location,places.formattedAddress,places.types,places.id",
    }
    try:
        r = _SESSION.post(
            PLACES_NEW_SEARCH_TEXT_URL,
            headers=headers,
            data=json.dumps(body),
            timeout=_REQ_TIMEOUT,
        )
    except requests.RequestException as exc:
        log.info("Places (New) searchText network error: %s", exc)
        return None
    if r.status_code == 403:
        log.warning(
            "Places API (New) returned 403 — likely not enabled on this project. "
            "Enable at https://console.cloud.google.com/apis/library/places.googleapis.com "
            "(same project as Geocoding API; reuses the same API key). Falling through."
        )
        return None
    if not r.ok:
        log.info("Places (New) searchText HTTP %d for %r: %s",
                 r.status_code, query, (r.text or "")[:200])
        return None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None
    places = data.get("places") or []
    if not places:
        return None
    # When include_intersection_only=True, prefer results with the
    # `intersection` type (Google's place graph DOES carry that type;
    # just rejects it as an includedType filter). Filtering after the
    # fact avoids both the 400 and the empty-result-on-restriction
    # problem documented above.
    if include_intersection_only:
        intersection_places = [
            p for p in places if "intersection" in (p.get("types") or [])
        ]
        if intersection_places:
            top = intersection_places[0]
            location_type = "PLACES_NEW_INTERSECTION"
        else:
            return None
    else:
        top = places[0]
        location_type = "PLACES_NEW_POI"  # POI near the named streets — approximate
    loc = top.get("location") or {}
    if "latitude" not in loc or "longitude" not in loc:
        return None
    return GeocodeResult(
        latitude=float(loc["latitude"]),
        longitude=float(loc["longitude"]),
        formatted_address=top.get("formattedAddress", ""),
        location_type=location_type,
        place_id=top.get("id"),
    )


def _places_new_autocomplete_route(
    street_name: str, bbox: Optional[dict], api_key: str,
) -> Optional[str]:
    """POST to Places API (New) `places:autocomplete` with `includedPrimaryTypes=["route"]`
    to canonicalize a single street name within a city's bbox.

    Returns the top suggestion's main_text (e.g. "Pleasant Valley Rd"
    becomes Google's canonical "Pleasant Valley Road") or None on miss.
    Used by Tier 3 to canonicalize each street individually before
    re-issuing searchText with the corrected pair.
    """
    body: dict = {
        "input": street_name,
        "includedPrimaryTypes": ["route"],
        "languageCode": "en",
        "regionCode": "US",
    }
    if bbox is not None:
        body["locationRestriction"] = {"rectangle": bbox}
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        # Default field mask returns the structured suggestion text.
    }
    try:
        r = _SESSION.post(
            PLACES_NEW_AUTOCOMPLETE_URL,
            headers=headers,
            data=json.dumps(body),
            timeout=_REQ_TIMEOUT,
        )
    except requests.RequestException as exc:
        log.info("Places (New) autocomplete network error for %r: %s", street_name, exc)
        return None
    if r.status_code == 403:
        # Already logged by searchText if it hit first; stay quiet here.
        return None
    if not r.ok:
        log.info("Places (New) autocomplete HTTP %d for %r: %s",
                 r.status_code, street_name, (r.text or "")[:200])
        return None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None
    suggestions = data.get("suggestions") or []
    for s in suggestions:
        pp = s.get("placePrediction") or {}
        # Prefer structuredFormat.mainText (the street name without the city
        # tail), fall back to text.text (the full suggestion text).
        sf = pp.get("structuredFormat") or {}
        main = (sf.get("mainText") or {}).get("text")
        if main:
            return main.strip()
        full = (pp.get("text") or {}).get("text")
        if full:
            return full.strip()
    return None


# =====================================================================
# HERE Geocoder — Tier 4 fallback (2026-05-25 eval: 16/16 on Pittsboro)
# =====================================================================

def _here_geocode(
    address: str, *, city: Optional[str] = None, state: Optional[str] = None,
) -> Optional[GeocodeResult]:
    """Query HERE Geocoding & Search v7 for `address`. Returns the top result
    if HERE finds anything in the right region, None otherwise.

    Eval established 2026-05-25 on 16 Pittsboro intersections that the
    Overpass + Places chain failed on: HERE hit 16/16, with 8 explicitly
    `resultType=intersection`. Significantly better intersection +
    interchange coverage than OSM-based sources because HERE's data
    foundation is automotive-grade (NAVTEQ heritage + BMW/Audi/Daimler
    fleet telemetry), not crowd-sourced.

    Auth: API key via `?apiKey=` query parameter. Read from keyring via
    config.get_here_api_key(). Returns None (with a one-time warning
    log) if no key is configured — caller continues to the next tier.

    Result tier mapping:
      - resultType=intersection → HERE_INTERSECTION (high confidence)
      - resultType=street       → HERE_STREET       (medium)
      - resultType=houseNumber  → HERE_HOUSE_NUMBER (medium — near the address)
      - anything else           → HERE_LOCALITY     (low)

    Proximity hint: if city + state provided, biases ranking toward
    that area's center via the `at` param. We use bias rather than
    `in:bbox` restriction (which causes weird 400s on HERE's parser).
    Country scoping via `in=countryCode:USA` to avoid same-named
    streets in other countries.
    """
    key = get_here_api_key()
    if not key:
        # Don't spam — one warning per process (caller logs the absence
        # itself if it cares to surface to the user).
        return None

    # Proximity bias via city's approximate center (resolved through the
    # bbox helper which we already cache per city). Fall back to no bias
    # if city/state unknown.
    at_param = None
    if city and state:
        bbox = _locality_bbox(city, state, get_google_geocoding_key() or "")
        if bbox is not None:
            cx = (bbox["low"]["latitude"]  + bbox["high"]["latitude"])  / 2
            cy = (bbox["low"]["longitude"] + bbox["high"]["longitude"]) / 2
            at_param = f"{cx},{cy}"

    params = {
        "q": address,
        "in": "countryCode:USA",
        "apiKey": key,
        "limit": 3,
        "lang": "en-US",
    }
    if at_param:
        params["at"] = at_param

    try:
        r = _SESSION.get(HERE_GEOCODE_URL, params=params, timeout=_REQ_TIMEOUT)
    except requests.RequestException as exc:
        log.info("HERE network error: %s", exc)
        return None
    if r.status_code == 401:
        log.warning(
            "HERE returned 401 — API key invalid or revoked. "
            "Re-create at https://platform.here.com → Access Manager → Apps → your app → Credentials."
        )
        return None
    if r.status_code == 429:
        log.warning("HERE returned 429 — daily quota exceeded (free tier is 1k/day).")
        return None
    if not r.ok:
        log.info("HERE HTTP %d for %r: %s", r.status_code, address, (r.text or "")[:200])
        return None
    try:
        data = r.json()
    except Exception:
        return None
    items = data.get("items") or []
    if not items:
        return None
    top = items[0]
    pos = top.get("position") or {}
    lat, lng = pos.get("lat"), pos.get("lng")
    if lat is None or lng is None:
        return None

    result_type = top.get("resultType") or "unknown"
    tier_map = {
        "intersection": "HERE_INTERSECTION",
        "street":       "HERE_STREET",
        "houseNumber":  "HERE_HOUSE_NUMBER",
    }
    location_type = tier_map.get(result_type, "HERE_LOCALITY")

    return GeocodeResult(
        latitude=float(lat),
        longitude=float(lng),
        formatted_address=top.get("title", "") or top.get("address", {}).get("label", ""),
        location_type=location_type,
        place_id=top.get("id"),
    )


# =====================================================================
# Tier 1b: Overpass motorway_junction — for tagged ramps/interchanges
# =====================================================================

def _overpass_motorway_junction(
    street_a: str, street_b: str, city: str, state: str,
    bbox: Optional[dict] = None,
) -> Optional[GeocodeResult]:
    """For inputs that look like highway interchanges, look up OSM
    `highway=motorway_junction` nodes. These are tagged at the actual
    gore points of ramps with `ref` (exit number) and sometimes `name`
    matching the cross street.

    Coverage in OSM for interchanges is patchy — varies state by state —
    but when it hits the coords are exact. Fast fail when it misses,
    so cheap to try as Tier 1b alongside the standard intersection-node
    query.

    Strategy: search the city bbox for motorway_junction nodes whose
    `name` regex-matches the non-highway street (the cross street), OR
    whose `ref` matches an exit-number pattern in the input. First node
    hit wins.

    NOTE: this catches the cases where OSM HAS the data; the long-tail
    "OSM doesn't know this ramp" cases fall through to HERE (Tier 4).
    """
    if bbox is None:
        return None

    # The "cross street" is whichever side ISN'T the highway designation.
    # Highway-like patterns: starts with "US", "I-", "SR-", "Hwy", "Route",
    # or contains "Ramp" / "Bypass".
    HIGHWAY_RE = re.compile(r"^(US|I-|SR-?\s?\d|Highway|Hwy|Route)\b", re.IGNORECASE)
    RAMPISH = re.compile(r"\b(Ramp|Bypass|Off-Ramp|On-Ramp|Loop)\b", re.IGNORECASE)
    a_is_hwy = bool(HIGHWAY_RE.match(street_a.strip())) or bool(RAMPISH.search(street_a))
    b_is_hwy = bool(HIGHWAY_RE.match(street_b.strip())) or bool(RAMPISH.search(street_b))
    if not (a_is_hwy or b_is_hwy):
        return None  # not an interchange query; skip this tier

    cross_street = street_b if a_is_hwy else street_a
    cross_candidates = _street_name_candidates(cross_street)
    if not cross_candidates:
        return None
    cross_regex = "|".join(_overpass_escape(c) for c in cross_candidates)

    pad = 0.03
    south = bbox["low"]["latitude"]   - pad
    west  = bbox["low"]["longitude"]  - pad
    north = bbox["high"]["latitude"]  + pad
    east  = bbox["high"]["longitude"] + pad

    ql = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_SEC}];\n"
        f"node({south},{west},{north},{east})"
        f'[highway=motorway_junction][name~"({cross_regex})",i];\n'
        "out 5;\n"
    )
    try:
        r = _SESSION.post(OVERPASS_URL, data={"data": ql}, timeout=_OVERPASS_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.info("Overpass motorway_junction call failed for %r × %r: %s",
                 street_a, street_b, exc)
        return None
    nodes = [e for e in (data.get("elements") or []) if e.get("type") == "node"]
    if not nodes:
        return None
    node = nodes[0]
    return GeocodeResult(
        latitude=float(node["lat"]),
        longitude=float(node["lon"]),
        formatted_address=f"{street_a} & {street_b}, {city}, {state}",
        location_type="OSM_MOTORWAY_JUNCTION",
        place_id=f"osm-junction-{node.get('id')}",
    )


# =====================================================================
# Shape detection — routes addresses to the right tier sequence
# =====================================================================

# Tokens that mark an address as interchange-shaped — skip Tier 1 (regular
# intersection node) since those queries are guaranteed to miss for ramps.
_INTERCHANGE_PATTERN = re.compile(
    r"\b(Ramp|Exit|Bypass|Off-Ramp|On-Ramp|Loop|"
    r"I-\d+|SR-?\s?\d+|US ?\d+(?:/\d+)?|Highway \d+|Hwy \d+|Route \d+)\b",
    re.IGNORECASE,
)


def _is_interchange_shaped(parsed: dict) -> bool:
    """True if either street looks like a highway/ramp/exit, suggesting
    Tier 1b (motorway_junction) and Tier 4 (HERE) are more likely to
    succeed than Tier 1 (named-node intersection) or Tier 3 (Autocomplete
    canonicalize). Saves 30-60s on per-address chain time."""
    a = parsed.get("street_a", "")
    b = parsed.get("street_b", "")
    return bool(_INTERCHANGE_PATTERN.search(a) or _INTERCHANGE_PATTERN.search(b))


def _intersection_chain(
    parsed: dict, api_key: str,
) -> Optional[GeocodeResult]:
    """Walk the intersection-aware chain with SHAPE ROUTING.

    Trimmed 2026-05-25 PM after Zephyrhills trace showed Tier 2 (Places
    Autocomplete) burning 81s on a single Overpass-miss address. The
    autocomplete tier was a "bridge" — canonicalize street names, then
    retry Overpass — but HERE (Tier 4) handles fuzzy LLM-typed names
    natively, so the bridge isn't earning its 80s cost. Dropped Tier 2 +
    Tier 3 entirely; chain is now strictly Overpass-then-HERE.

    Order (accuracy-first):
      INTERSECTION:  Tier 1 (Overpass intersection-node) → Tier 4 (HERE)
      INTERCHANGE:   Tier 1b (Overpass motorway_junction) → Tier 4 (HERE)

      1.  Overpass intersection-node          — FREE, exact for standard intersections
      1b. Overpass motorway_junction          — FREE, exact for tagged ramps
      4.  HERE Geocoder                       — ~$0.001/call (free 1k/day); best coverage

    Returns first hit. None if every tier missed → caller falls to LLM
    text estimate.

    The autocomplete functions (`_places_new_autocomplete_route`) are
    retained in the module for now in case HERE coverage regresses and
    the bridge is needed back. Re-introducing them just means wiring two
    timed() blocks between the Overpass and HERE tiers.
    """
    from . import trace_log  # local import — avoid module-load circularity

    street_a, street_b = parsed["street_a"], parsed["street_b"]
    city, state = parsed["city"], parsed["state"]
    addr_tag = f"{street_a} & {street_b}, {city}, {state}"

    # Resolve the city bbox once up-front. Cached after the first lookup
    # per process, so emails with multiple intersections in the same city
    # only pay this Geocoding call once. Note: in the extractor, the bboxes
    # for all unique (city, state) pairs are pre-warmed sequentially BEFORE
    # the threadpool launches, so worker threads see a cache hit here
    # rather than blocking ~40s on a cold first call.
    with trace_log.timed("geocoder.bbox_lookup", city=city, state=state) as t:
        bbox = _locality_bbox(city, state, api_key)
        t["bbox_resolved"] = bbox is not None
    is_interchange = _is_interchange_shaped(parsed)

    # ---- Tier 1: Overpass intersection-node (skipped for interchanges) ----
    if not is_interchange:
        with trace_log.timed("geocoder.tier", phase="overpass_raw", address=addr_tag) as t:
            result = _overpass_intersection(street_a, street_b, city, state, bbox=bbox)
            t["hit"] = result is not None
        if result is not None:
            log.info("Tier 1 (Overpass raw) HIT %r × %r in %s — exact node.",
                     street_a, street_b, city)
            return result

    # ---- Tier 1b: Overpass motorway_junction (interchanges only) ----
    if is_interchange:
        with trace_log.timed("geocoder.tier", phase="overpass_motorway_junction", address=addr_tag) as t:
            result = _overpass_motorway_junction(street_a, street_b, city, state, bbox=bbox)
            t["hit"] = result is not None
        if result is not None:
            log.info("Tier 1b (Overpass motorway_junction) HIT %r × %r in %s — exact ramp node.",
                     street_a, street_b, city)
            return result

    # ---- Tier 4: HERE Geocoder ----
    # Best coverage for interchanges + LLM-mis-named streets. Returns
    # tagged-tier coords (intersection / street / houseNumber); confidence
    # mapping in GeocodeResult.confidence handles the gradient.
    here_query = f"{street_a} and {street_b}, {city}, {state}"
    with trace_log.timed("geocoder.tier", phase="here", address=addr_tag) as t:
        result = _here_geocode(here_query, city=city, state=state)
        t["hit"] = result is not None
        if result is not None:
            t["result_type"] = result.location_type
    if result is not None:
        log.info("Tier 4 (HERE) HIT for %r in %s — resultType=%s",
                 here_query, city, result.location_type)
        return result

    return None


# =====================================================================
# Public entry point — dispatches intersection-aware vs legacy chain
# =====================================================================


def geocode(address: str, *, api_key: Optional[str] = None) -> Optional[GeocodeResult]:
    """Thin wrapper around `_geocode_impl` that emits a per-address
    `geocoder.address_total` trace event with total wall time + final
    source tier (or `miss` if all tiers failed). Combined with the
    per-tier events from `_intersection_chain`, this gives a complete
    breakdown for every address.
    """
    from . import trace_log
    with trace_log.timed("geocoder.address_total", address=address) as t:
        try:
            result = _geocode_impl(address, api_key=api_key)
        except Exception as exc:
            t["error"] = f"{type(exc).__name__}: {exc}"
            raise
        if result is None:
            t["outcome"] = "miss"
        else:
            t["outcome"] = "hit"
            t["source"] = result.location_type
            t["confidence"] = result.confidence
        return result


def _geocode_impl(address: str, *, api_key: Optional[str] = None) -> Optional[GeocodeResult]:
    """Internal implementation. Dispatch:
      - If the address parses as "<X> and <Y>, <city>, <state>" → run the
        INTERSECTION chain: Overpass (OSM) → Autocomplete → Overpass-canonical
        → HERE. Each tier hits a strictly more permissive / more expensive
        matcher than the previous; first success wins.
      - On miss from the intersection chain (or for non-intersection
        addresses like single street numbers), fall back to the LEGACY
        chain: Geocoding API variants → Places Find Place → APPROXIMATE
        last-resort.

    Returns None only if EVERYTHING failed. Raises on hard errors
    (auth/billing) that persisted across every attempt.
    """
    key = api_key or get_google_geocoding_key()
    if not key:
        raise GeocoderUnavailable(
            "No Google Geocoding API key saved. Open Settings to add one."
        )

    # Intersection chain first — preferred when the input parses cleanly
    # as a cross-street pair within a known locality.
    parsed = _parse_intersection(address)
    if parsed is not None:
        try:
            result = _intersection_chain(parsed, key)
        except Exception as exc:
            # Defensive: anything unexpected in the new chain falls
            # through to legacy rather than failing the whole geocode.
            log.warning("Intersection chain raised %s (%s) — falling through to legacy.",
                        type(exc).__name__, exc)
            result = None
        if result is not None:
            return result
        log.info(
            "Intersection chain missed for %r — falling through to legacy "
            "Geocoding API variants.", address,
        )

    variants = _query_variants(address)
    last_error: Optional[GeocodingError] = None
    approximate_fallback: Optional[GeocodeResult] = None

    # Step 1: Geocoding API variants.
    for i, query in enumerate(variants, 1):
        try:
            result = _geocode_once(query, key)
        except GeocodingError as exc:
            last_error = exc
            log.info("Geocoder variant %d/%d failed (%s): trying next phrasing.", i, len(variants), exc)
            continue
        if result is None:
            log.info("Geocoder variant %d/%d returned no results: %r", i, len(variants), query)
            continue
        if result.location_type == "APPROXIMATE":
            # Hold onto it as a last-resort fallback; don't return yet —
            # Places will probably produce something more precise.
            if approximate_fallback is None:
                approximate_fallback = result
            log.info(
                "Geocoder variant %d/%d returned APPROXIMATE (city-level): %r — holding as last-resort.",
                i, len(variants), query,
            )
            continue
        # Real intersection-grade hit.
        if i > 1:
            log.info("Geocoder succeeded on variant %d/%d: %r", i, len(variants), query)
        return result

    # Step 2: Places find-place fallback on the ORIGINAL address. Places
    # is more forgiving on intersection text than the Geocoding API.
    try:
        places_result = _places_find_once(address, key)
        if places_result is not None:
            log.info("Places find-place succeeded after Geocoding variants failed: %r", address)
            return places_result
    except GeocodingError as exc:
        log.info("Places find-place fallback failed (%s)", exc)
        last_error = last_error or exc

    # Step 3: city-level Geocoding fallback if we have one. Better than
    # returning None — at least the map pin lands in the right city,
    # which lets the user nudge it to the right intersection manually.
    if approximate_fallback is not None:
        log.info("Falling back to APPROXIMATE Geocoding result: %s", approximate_fallback.formatted_address)
        return approximate_fallback

    if last_error is not None:
        raise last_error
    return None


def _places_find_once(address: str, key: str) -> Optional[GeocodeResult]:
    """Single Places API "Find Place from Text" call. Returns the top-match
    place's geometry, or None if no candidates found.

    The Places API tolerates fuzzy intersection text the Geocoding API
    rejects — e.g., "Apple Blossom Mall Drive and Pleasant Valley Road,
    Winchester, VA" returns the canonical intersection place even when
    Geocoding ZERO_RESULTSes on the same string. Tagged with
    location_type='PLACES_FIND' so downstream can flag it as medium
    confidence (the Place API's "top match" is usually right but
    occasionally lands on a nearby business instead of the intersection
    itself — the user should glance at the map before relying on it).
    """
    params = {
        "input": address,
        "inputtype": "textquery",
        "fields": "geometry,formatted_address,place_id",
        "key": key,
    }
    try:
        r = _SESSION.get(PLACES_FIND_PLACE_URL, params=params, timeout=_REQ_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise GeocodingError(f"Network error calling Places API: {exc}") from exc

    data = r.json()
    status = data.get("status", "UNKNOWN_ERROR")
    if status == "ZERO_RESULTS":
        return None
    if status != "OK":
        raise GeocodingError(f"Places API status: {status}")
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    top = candidates[0]
    geom = top.get("geometry") or {}
    loc = geom.get("location") or {}
    if "lat" not in loc or "lng" not in loc:
        return None
    return GeocodeResult(
        latitude=float(loc["lat"]),
        longitude=float(loc["lng"]),
        formatted_address=top.get("formatted_address", ""),
        location_type="PLACES_FIND",
        place_id=top.get("place_id"),
    )


def _geocode_once(address: str, key: str) -> Optional[GeocodeResult]:
    """Single Geocoding API call. Returns None on ZERO_RESULTS, raises on errors."""
    params = {"address": address, "key": key}
    try:
        r = _SESSION.get(GEOCODE_URL, params=params, timeout=_REQ_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise GeocodingError(f"Network error calling Geocoding API: {exc}") from exc

    data = r.json()
    status = data.get("status", "UNKNOWN_ERROR")

    if status == "ZERO_RESULTS":
        return None
    if status == "OK":
        first = data["results"][0]
        geom = first["geometry"]
        loc = geom["location"]
        return GeocodeResult(
            latitude=float(loc["lat"]),
            longitude=float(loc["lng"]),
            formatted_address=first.get("formatted_address", ""),
            location_type=geom.get("location_type", "APPROXIMATE"),
            place_id=first.get("place_id"),
        )

    err_msg = data.get("error_message", "no detail provided")
    raise GeocodingError(f"Geocoding API returned status={status}: {err_msg}")
