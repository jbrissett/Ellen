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
REQUEST_TIMEOUT_SEC = 15


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str
    location_type: str  # "ROOFTOP" / "RANGE_INTERPOLATED" / "GEOMETRIC_CENTER" / "APPROXIMATE"
    place_id: Optional[str] = None

    @property
    def confidence(self) -> str:
        """Map Google's location_type to our high/medium/low scale."""
        if self.location_type in ("ROOFTOP", "RANGE_INTERPOLATED"):
            return "high"
        if self.location_type == "GEOMETRIC_CENTER":
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
    """Generate alternate phrasings to maximize Geocoder hit rate."""
    variants: list[str] = [address]

    # 1. Replace full suffixes with abbreviations
    abbreviated = address
    for full, abbr in _SUFFIX_ABBREV.items():
        abbreviated = re.sub(rf"\b{full}\b", abbr, abbreviated)
    if abbreviated != address:
        variants.append(abbreviated)

    # 2. Swap "and" → "&" (Geocoder accepts either, but Google's caching can differ)
    if " and " in address.lower():
        variants.append(re.sub(r"\s+and\s+", " & ", address, flags=re.IGNORECASE))

    # 3. For "<A> and <B>, <city>" — try reversed order
    m = re.match(r"^(.+?)\s+and\s+(.+?),\s*(.+)$", address, re.IGNORECASE)
    if m:
        a, b, rest = m.groups()
        variants.append(f"{b.strip()} and {a.strip()}, {rest.strip()}")

    # De-duplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def geocode(address: str, *, api_key: Optional[str] = None) -> Optional[GeocodeResult]:
    """Look up a single address with phrasing-variant retries.

    Returns None on ZERO_RESULTS for ALL variants. Raises on hard errors
    (auth/billing) that persist across variants.
    """
    key = api_key or get_google_geocoding_key()
    if not key:
        raise GeocoderUnavailable(
            "No Google Geocoding API key saved. Open Settings to add one."
        )

    variants = _query_variants(address)
    last_error: Optional[GeocodingError] = None

    for i, query in enumerate(variants, 1):
        try:
            result = _geocode_once(query, key)
        except GeocodingError as exc:
            last_error = exc
            log.info("Geocoder variant %d/%d failed (%s): trying next phrasing.", i, len(variants), exc)
            continue
        if result is not None:
            if i > 1:
                log.info("Geocoder succeeded on variant %d/%d: %r", i, len(variants), query)
            return result
        # None result — try next variant
        log.info("Geocoder variant %d/%d returned no results: %r", i, len(variants), query)

    # All variants exhausted
    if last_error is not None:
        raise last_error
    return None


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
