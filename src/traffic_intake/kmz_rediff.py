"""Diff a user-edited KMZ re-drop against the active StudyRequest.

When the user drops a KMZ that originated from Ellen's own MyMaps export
(detected via the `ellen_loc_id` round-trip marker baked into each
placemark by kml_export), this module computes what changed: pins that
were dragged to new coords, pins that were renamed, pins that were
removed, and brand-new pins the user added.

The result is handed to chat.py as a structured summary so Ellen can
narrate the diff, ask the user about ambiguous cases (new pins need a
study type + group; removed pins need confirmation), and then apply
the moves and renames via the existing tools (`update_location`,
`add_locations`, `remove_locations`).

Phase 1 scope: produce the diff. Application of the diff to the
StudyRequest is the chat layer's responsibility — it has the user-
context to resolve ambiguities.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .kmz import Placemark
from .models import StudyRequest


# Distance threshold (meters) above which a coord change is flagged as
# "moved". Below this, treat as same-spot (covers MyMaps re-export
# coordinate-precision wobble). Pins are considered to have actually
# been dragged when the user moves them at least one pin-width on
# screen, which at typical MyMaps zoom is well above 25m.
MOVED_THRESHOLD_M = 25.0


@dataclass
class MovedPin:
    loc_id: int
    site_name: str
    old_lat: float
    old_lng: float
    new_lat: float
    new_lng: float
    distance_m: float


@dataclass
class RenamedPin:
    loc_id: int
    old_name: str
    new_name: str


@dataclass
class MissingPin:
    """In the StudyRequest but absent from the re-drop KMZ — user
    presumably deleted the pin in MyMaps. Caller should confirm with
    the user before dropping from the qchub order."""
    loc_id: int
    site_name: str


@dataclass
class NewPin:
    """Placemark in the re-drop KMZ with no `ellen_loc_id` — user added
    a new pin in MyMaps that wasn't in the original email. Caller should
    ask for study type/group before adding to the StudyRequest."""
    name: Optional[str]
    latitude: float
    longitude: float


@dataclass
class Rediff:
    moved: list[MovedPin] = field(default_factory=list)
    renamed: list[RenamedPin] = field(default_factory=list)
    missing: list[MissingPin] = field(default_factory=list)
    new: list[NewPin] = field(default_factory=list)
    # Placemarks that matched by ID but had no change (unchanged coord
    # AND unchanged name) — count only, for the "13 of 16 pins unchanged"
    # one-liner Ellen can post.
    unchanged_count: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.moved or self.renamed or self.missing or self.new)

    @property
    def needs_user_input(self) -> bool:
        """True when the diff includes new or missing pins — these
        always require a chat exchange to resolve (study type for new,
        confirm-drop for missing)."""
        return bool(self.new or self.missing)


def is_rediff_candidate(placemarks: list[Placemark]) -> bool:
    """Quick check: does the parsed KMZ look like one of Ellen's own
    exports? True if ANY placemark carries an `ellen_loc_id` marker.

    Used by the drop handler to decide between new-job extraction and
    rediff. A user can re-drop a fully fresh KMZ that has no IDs to
    overwrite Ellen's state — that's the new-job path.
    """
    return any(pm.ellen_loc_id is not None for pm in placemarks)


def compute_rediff(request: StudyRequest, placemarks: list[Placemark]) -> Rediff:
    """Match parsed placemarks against the active StudyRequest's locations
    by `ellen_loc_id` and return a structured diff.
    """
    result = Rediff()

    # Index the StudyRequest by location index for O(1) lookup. Locations
    # that have no estimate also have no map pin to compare against — they
    # weren't emitted in the original KMZ, so they can't be "missing" here.
    locs_by_id: dict[int, int] = {
        idx: idx
        for idx, loc in enumerate(request.locations)
        if loc.estimate is not None
    }
    seen_ids: set[int] = set()

    for pm in placemarks:
        if pm.ellen_loc_id is None:
            result.new.append(NewPin(
                name=pm.name,
                latitude=pm.latitude,
                longitude=pm.longitude,
            ))
            continue

        if pm.ellen_loc_id not in locs_by_id:
            # ID present but doesn't match any current location. Could
            # happen if the user mutated the request between the original
            # build and the re-drop (e.g. via add_locations / remove_locations).
            # Treat as a new pin so we don't silently lose it.
            result.new.append(NewPin(
                name=pm.name,
                latitude=pm.latitude,
                longitude=pm.longitude,
            ))
            continue

        idx = pm.ellen_loc_id
        seen_ids.add(idx)
        loc = request.locations[idx]
        assert loc.estimate is not None  # guarded by the locs_by_id filter

        changed = False

        distance = _haversine_m(
            loc.estimate.latitude, loc.estimate.longitude,
            pm.latitude, pm.longitude,
        )
        if distance >= MOVED_THRESHOLD_M:
            result.moved.append(MovedPin(
                loc_id=idx,
                site_name=loc.site_name,
                old_lat=loc.estimate.latitude,
                old_lng=loc.estimate.longitude,
                new_lat=pm.latitude,
                new_lng=pm.longitude,
                distance_m=distance,
            ))
            changed = True

        # Name compare: placemark <name> ↔ StudyLocation.site_name. We
        # report differences here. The chat layer decides whether to push
        # the new name into address_or_intersection per the project's
        # rename policy (user opted for "treat rename as new address text").
        new_name = (pm.name or "").strip()
        old_name = (loc.site_name or "").strip()
        if new_name and new_name != old_name:
            result.renamed.append(RenamedPin(
                loc_id=idx, old_name=old_name, new_name=new_name,
            ))
            changed = True

        if not changed:
            result.unchanged_count += 1

    # Any location IDs that had a pin in the original but didn't show up
    # in the re-drop = user deleted from the map.
    for idx in locs_by_id:
        if idx in seen_ids:
            continue
        loc = request.locations[idx]
        result.missing.append(MissingPin(loc_id=idx, site_name=loc.site_name))

    return result


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters. Earth radius ≈ 6371 km."""
    R = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def apply_unambiguous(request: StudyRequest, rediff: Rediff) -> tuple[int, int]:
    """Apply the changes that don't need user input: moves and renames.

    Returns (moves_applied, renames_applied) for reporting.

    Moves update `location.estimate.latitude/longitude` and mark the
    estimate's source as "manual" with confidence "high" — the user
    physically dragged the pin to where they want it, that's as
    authoritative as it gets.

    Renames update both `site_name` (the friendly map label) AND
    `address_or_intersection` (the value qchub will use as the site
    name). User asked 2026-05-25 for renames to flow all the way to
    qchub, not just be display labels.

    New and missing pins are NOT touched here — those require a chat
    exchange to resolve (study type for new, confirm-drop for missing).
    """
    from .models import LocationEstimate

    moves = 0
    for m in rediff.moved:
        loc = request.locations[m.loc_id]
        existing_notes = loc.estimate.notes if loc.estimate else None
        loc.estimate = LocationEstimate(
            latitude=m.new_lat,
            longitude=m.new_lng,
            confidence="high",
            source="manual",
            notes=(
                f"User repositioned pin from {m.old_lat:.6f},{m.old_lng:.6f} "
                f"({m.distance_m:.0f}m). Prior: {existing_notes or 'none'}"
            ),
        )
        moves += 1

    renames = 0
    for r in rediff.renamed:
        loc = request.locations[r.loc_id]
        loc.site_name = r.new_name
        loc.address_or_intersection = r.new_name
        renames += 1

    return moves, renames


def summarize(rediff: Rediff) -> str:
    """One-paragraph summary of the diff suitable for posting in chat.

    Used by the drop handler as the system message it surfaces to Ellen
    when a re-drop arrives. Ellen reads this, then walks the user through
    the moved/renamed pins and asks about new/missing ones.
    """
    parts: list[str] = []
    if rediff.moved:
        avg = sum(m.distance_m for m in rediff.moved) / len(rediff.moved)
        parts.append(f"{len(rediff.moved)} pin(s) relocated (avg {avg:.0f}m moved)")
    if rediff.renamed:
        parts.append(f"{len(rediff.renamed)} pin(s) renamed")
    if rediff.missing:
        parts.append(f"{len(rediff.missing)} pin(s) removed from map")
    if rediff.new:
        parts.append(f"{len(rediff.new)} new pin(s) added")
    if rediff.unchanged_count:
        parts.append(f"{rediff.unchanged_count} pin(s) unchanged")
    if not parts:
        return "Re-drop matched the active map exactly — no changes detected."
    return "Re-drop diff: " + "; ".join(parts) + "."
