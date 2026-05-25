"""Google Geocoding API client.

Used to turn each StudyLocation's `address_or_intersection` (the LLM-friendly
human form, e.g. 'N Socrum Loop Road and Old Combee Road, Lakeland, FL') into
an accurate lat/lon. Replaces Claude's vision/text_only estimates which are
fine for identifying *which* intersection a client meant, but too rough for
client-facing map pins.

For intersection queries we try several phrasings — Google's geocoder is
sensitive to suffix abbreviations and word order, and sometimes a specific
phrasing returns REQUEST_DENIED while a variant succeeds (observed in the wild
with Lakeland addresses post-enable).

Costs: ~$0.005 per geocode. Google gives $200/month free credit which covers
~40K requests/month. QC's volume is a few hundred/month — effectively free.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests

from .config import get_google_geocoding_key

log = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
# Lowered 15s → 10s 2026-05-25. The big accuracy win is in MORE variants +
# Places fallback, not in giving each call longer. With parallelization
# (one thread per address) the per-call ceiling matters more than the
# per-address total. 10s catches most actual responses; LLM-extracted
# addresses that fail at 10s aren't likely to succeed at 15s either.
REQUEST_TIMEOUT_SEC = 10


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str
    location_type: str  # "ROOFTOP" / "RANGE_INTERPOLATED" / "GEOMETRIC_CENTER" / "APPROXIMATE" / "PLACES_FIND"
    place_id: Optional[str] = None

    @property
    def confidence(self) -> str:
        """Map Google's location_type to our high/medium/low scale."""
        if self.location_type in ("ROOFTOP", "RANGE_INTERPOLATED"):
            return "high"
        if self.location_type == "GEOMETRIC_CENTER":
            return "medium"
        if self.location_type == "PLACES_FIND":
            # Places API top-match for fuzzy intersection text. Usually
            # accurate at intersection granularity but not always — flag
            # as medium so downstream UX prompts the user to verify.
            return "medium"
        return "low"  # APPROXIMATE


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


def geocode(address: str, *, api_key: Optional[str] = None) -> Optional[GeocodeResult]:
    """Look up a single address with phrasing-variant retries + Places fallback.

    Strategy (in order):
      1. Geocoding API, walked through every variant in `_query_variants`.
         Stops at first non-None result with confidence >= medium
         (GEOMETRIC_CENTER or better). APPROXIMATE results are kept aside
         as a last-resort fallback, since "city-level pin" is barely
         better than nothing for intersection-targeted studies.
      2. Places API "Find Place from Text" on the ORIGINAL address string.
         Much more tolerant of fuzzy intersection text ("Apple Blossom
         Mall Drive and Pleasant Valley Road, Winchester VA") than the
         strict Geocoding API. This was added 2026-05-25 after the
         Winchester VA email went 1-for-8 on Geocoding-only.
      3. The APPROXIMATE-tier Geocoding result from step 1 if anything
         was found. (Worse than Places hit, better than nothing.)

    Returns None only if EVERYTHING failed. Raises on hard errors
    (auth/billing) that persisted across every attempt.
    """
    key = api_key or get_google_geocoding_key()
    if not key:
        raise GeocoderUnavailable(
            "No Google Geocoding API key saved. Open Settings to add one."
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
