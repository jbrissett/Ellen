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

from .config import get_google_geocoding_key

log = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
PLACES_NEW_SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_NEW_AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
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


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str
    # Source tier (richer than the original Google-only location_type):
    #   OSM_NODE                — Overpass found the actual intersection node (best)
    #   PLACES_NEW_INTERSECTION — Places (New) searchText, types=['intersection']
    #   PLACES_NEW_CANONICAL    — same as above, after Autocomplete canonicalize pre-pass
    #   PLACES_NEW_POI          — Places (New) searchText, nearest POI fallback
    #                             (when no intersection-typed place existed)
    #   ROOFTOP / RANGE_INTERPOLATED / GEOMETRIC_CENTER — legacy Geocoding API
    #   PLACES_FIND             — legacy Places Find Place from Text fallback
    #   APPROXIMATE             — legacy Geocoding city-level pin (last resort)
    location_type: str
    place_id: Optional[str] = None

    @property
    def confidence(self) -> str:
        """Map source tier to our high/medium/low scale."""
        if self.location_type in ("OSM_NODE", "ROOFTOP", "RANGE_INTERPOLATED"):
            # OSM_NODE = literal intersection node coords; Google ROOFTOP/RANGE
            # = full address resolution. Both are tightly-bound.
            return "high"
        if self.location_type in (
            "GEOMETRIC_CENTER", "PLACES_NEW_INTERSECTION",
            "PLACES_NEW_CANONICAL", "PLACES_FIND",
        ):
            # Within-intersection precision but not nailed to a specific
            # node. Usually fine for a map pin; user should glance.
            return "medium"
        if self.location_type == "PLACES_NEW_POI":
            # Nearest POI to the named streets — usually within ~50m of
            # the actual crosswalk. Useful as a "general location" pin
            # the user can nudge, but always flag for verification.
            return "low"
        return "low"  # APPROXIMATE


# Module-level cache for (city, state) → bbox lookups. One Geocoding call
# per unique city per process lifetime. Thread-safe via the lock.
_bbox_cache: dict[tuple[str, str], Optional[dict]] = {}
_bbox_cache_lock = threading.Lock()


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
    process lifetime; subsequent lookups hit the in-memory cache. Returns
    a dict like {"low": {"latitude": .., "longitude": ..},
    "high": {"latitude": .., "longitude": ..}} or None if the city
    couldn't be resolved.
    """
    key = (city.strip().lower(), state.strip().upper())
    with _bbox_cache_lock:
        if key in _bbox_cache:
            return _bbox_cache[key]
    params = {
        "address": f"{city}, {state}",
        "components": f"country:US|administrative_area:{state}",
        "key": api_key,
    }
    try:
        r = requests.get(GEOCODE_URL, params=params, timeout=REQUEST_TIMEOUT_SEC)
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
        r = requests.post(
            OVERPASS_URL,
            data={"data": ql},
            timeout=OVERPASS_TIMEOUT_SEC,
            headers={"User-Agent": "Ellen (Quality Counts) traffic-study intake"},
        )
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
        r = requests.post(
            PLACES_NEW_SEARCH_TEXT_URL,
            headers=headers,
            data=json.dumps(body),
            timeout=REQUEST_TIMEOUT_SEC,
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
        r = requests.post(
            PLACES_NEW_AUTOCOMPLETE_URL,
            headers=headers,
            data=json.dumps(body),
            timeout=REQUEST_TIMEOUT_SEC,
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


def _intersection_chain(
    parsed: dict, api_key: str,
) -> Optional[GeocodeResult]:
    """Walk the intersection-aware chain.

    Restructured 2026-05-25 after live testing showed:
      - searchText with `includedType="intersection"` zero-results even
        on known-good intersections (filter removed; see helper docstring)
      - Autocomplete canonicalizes street names reliably ("Apple Blossom
        Mall Drive" → "Apple Blossom Drive"), which often unlocks an
        Overpass hit that the raw names would have missed
      - Overpass with the CANONICAL names returns exact node coords for
        free, so the Autocomplete pre-pass should run BEFORE Places
        searchText, not after it

    Order (accuracy + cost optimized):
      1. Overpass with raw names           — free, exact when works
      2. Autocomplete each street          — paid (~$0.006 / pair)
      3. Overpass with CANONICAL names     — free, exact when works
      4. Places searchText, canonical query — paid (~$0.032)
      5. Places searchText, raw query      — paid (~$0.032) — defense
                                              against Autocomplete failure
      6. (caller) legacy Geocoding fallback

    Returns first hit. None if every tier missed.
    """
    street_a, street_b = parsed["street_a"], parsed["street_b"]
    city, state = parsed["city"], parsed["state"]

    # Resolve the city bbox once up-front. Cached after the first lookup
    # per process, so emails with multiple intersections in the same city
    # only pay this Geocoding call once.
    bbox = _locality_bbox(city, state, api_key)

    # Tier 1: Overpass with raw names.
    result = _overpass_intersection(street_a, street_b, city, state, bbox=bbox)
    if result is not None:
        log.info("Tier 1 (Overpass raw) HIT %r × %r in %s — exact node.",
                 street_a, street_b, city)
        return result

    # Tier 2: Canonicalize each street via Places (New) Autocomplete.
    # This is paid (~$0.003 per call × 2 = ~$0.006 per intersection) but
    # unlocks both Tier 3 (free Overpass retry) and Tier 4 (better Places
    # searchText hit rate).
    canon_a = _places_new_autocomplete_route(street_a, bbox, api_key) or street_a
    canon_b = _places_new_autocomplete_route(street_b, bbox, api_key) or street_b
    canonicalized = (canon_a, canon_b) != (street_a, street_b)
    if canonicalized:
        log.info("Autocomplete canonicalized: %r → %r ; %r → %r",
                 street_a, canon_a, street_b, canon_b)

    # Tier 3: Overpass with CANONICAL names — free, often picks up the
    # cases where the LLM-extracted name had an extra descriptor that
    # threw the OSM regex match off.
    if canonicalized:
        result = _overpass_intersection(canon_a, canon_b, city, state, bbox=bbox)
        if result is not None:
            log.info("Tier 3 (Overpass canonical) HIT %r × %r in %s — exact node.",
                     canon_a, canon_b, city)
            return result

    # Tier 4a: Places (New) searchText, canonical names, intersection-only.
    # Returns exact-intersection coords when Google's place graph has the
    # cross-street indexed as type='intersection' (best paid case).
    query_canon = f"{canon_a} & {canon_b}"
    result = _places_new_search_text(
        query_canon, bbox, api_key, include_intersection_only=True,
    )
    if result is not None:
        if canonicalized:
            result.location_type = "PLACES_NEW_CANONICAL"
        log.info("Tier 4a (Places searchText canonical, intersection) HIT for %r in %s.",
                 query_canon, city)
        return result

    # Tier 4b: Places searchText, canonical names, POI-tolerant.
    # When Google has no intersection-typed entry, returns the nearest
    # POI to the named streets — coords are usually within ~50m of the
    # actual crosswalk. Tagged PLACES_NEW_POI so the user knows to
    # verify the pin (and tracks as "medium" confidence).
    result = _places_new_search_text(
        query_canon, bbox, api_key, include_intersection_only=False,
    )
    if result is not None:
        log.info("Tier 4b (Places searchText canonical, POI fallback) HIT for %r in %s — verify pin.",
                 query_canon, city)
        return result

    # Tier 5: Places searchText with raw names, POI-tolerant — final
    # defense against Autocomplete returning a wrong canonical that
    # broke the search.
    if canonicalized:
        query_raw = f"{street_a} & {street_b}"
        result = _places_new_search_text(
            query_raw, bbox, api_key, include_intersection_only=False,
        )
        if result is not None:
            log.info("Tier 5 (Places searchText raw, POI fallback) HIT for %r in %s — verify pin.",
                     query_raw, city)
            return result
    return None


# =====================================================================
# Public entry point — dispatches intersection-aware vs legacy chain
# =====================================================================


def geocode(address: str, *, api_key: Optional[str] = None) -> Optional[GeocodeResult]:
    """Resolve `address` to lat/lon using the most-accurate-first chain.

    Dispatch:
      - If the address parses as "<X> and <Y>, <city>, <state>" → run the
        INTERSECTION chain: Overpass (OSM) → Places (New) searchText →
        Autocomplete-canonicalize + retry. Each tier hits a strictly more
        permissive / more expensive matcher than the previous; first
        success wins.
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
        r = requests.get(PLACES_FIND_PLACE_URL, params=params, timeout=REQUEST_TIMEOUT_SEC)
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
        r = requests.get(GEOCODE_URL, params=params, timeout=REQUEST_TIMEOUT_SEC)
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
