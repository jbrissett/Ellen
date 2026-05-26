"""Build a styled KMZ file from a StudyRequest.

KMZ is a zip-wrapped KML XML file with attachments (icons, images). For our
purposes a single `doc.kml` is enough. Pin color convention:
  - Red paddle  → turning movement counts (TMC)
  - Yellow paddle → tube counts

Google MyMaps' Import feature reads our IconStyle and applies the colors when
the map is created, so this same file works as both a manual deliverable AND
as the upload payload for MyMaps automation.

Locations without an estimate (lat/lon) are skipped — the caller can warn the
user about those separately.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from .models import StudyKind, StudyLocation, StudyRequest, TMCSubtype, TubeSubtype


@dataclass
class KmzBuildResult:
    data: bytes
    placemark_count: int
    skipped_unplaced: int  # locations without an estimate
    map_title: str


# KML and KMZ build results share the same shape — `data` is UTF-8 KML XML
# for KML, or a zipped doc.kml for KMZ. Caller picks based on consumer
# (MyMaps wants KMZ; qchub wants KML).
KmlBuildResult = KmzBuildResult


def build_kmz(request: StudyRequest, *, map_title: Optional[str] = None) -> KmzBuildResult:
    """Generate a styled KMZ for the given request.

    `map_title` defaults to a derived title — caller can override.
    """
    title = map_title or _default_title(request)
    kml_text = _build_kml(request, title)
    skipped = sum(1 for loc in request.locations if loc.estimate is None)
    placemark_count = len(request.locations) - skipped

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml_text)
    return KmzBuildResult(
        data=buf.getvalue(),
        placemark_count=placemark_count,
        skipped_unplaced=skipped,
        map_title=title,
    )


def build_kml(request: StudyRequest, *, map_title: Optional[str] = None) -> KmlBuildResult:
    """Generate the raw KML XML for the given request (no zip wrapper).

    qchub's UPLOAD KML control accepts .kml directly — we build the same XML
    that goes inside a KMZ's doc.kml, just unzipped. Identical pin styling.
    """
    title = map_title or _default_title(request)
    kml_text = _build_kml(request, title)
    skipped = sum(1 for loc in request.locations if loc.estimate is None)
    placemark_count = len(request.locations) - skipped
    return KmlBuildResult(
        data=kml_text.encode("utf-8"),
        placemark_count=placemark_count,
        skipped_unplaced=skipped,
        map_title=title,
    )


def build_kml_for_locations(
    locations: list[StudyLocation],
    *,
    layer_name: str,
    description: Optional[str] = None,
    request: Optional[StudyRequest] = None,
) -> KmlBuildResult:
    """Generate raw KML for a SUBSET of locations — one study group's worth.

    Used per qchub study group: we create the group in qchub, then upload
    the KML for just THAT group's locations (so qchub binds them to the
    just-selected group). Per John's workflow 2026-05-14: one KML per
    group, uploaded while that group is selected.

    `layer_name` becomes the <Document><name>, which MyMaps surfaces as
    the layer label when this KML is imported as its own layer. qchub
    ignores the document name but still accepts the file fine.

    `request` (optional): when provided, each emitted placemark also
    carries an `ellen_loc_id` ExtendedData field with the location's
    index in `request.locations`. This lets a user-edited KMZ re-drop
    match its placemarks back to the active StudyRequest (see kmz.py
    parser + kmz_rediff). Omit for non-MyMaps consumers (e.g. tests)
    that don't need the round-trip ID.
    """
    loc_ids = _build_loc_ids(locations, request)
    kml_text = _build_kml_from_locations(layer_name, description, locations, loc_ids=loc_ids)
    skipped = sum(1 for loc in locations if loc.estimate is None)
    placemark_count = len(locations) - skipped
    return KmlBuildResult(
        data=kml_text.encode("utf-8"),
        placemark_count=placemark_count,
        skipped_unplaced=skipped,
        map_title=layer_name,
    )


def _build_loc_ids(
    locations: list[StudyLocation], request: Optional[StudyRequest],
) -> Optional[list[Optional[int]]]:
    """Map each location to its StudyRequest.locations index, or None.

    Returns None if no request was provided (no IDs to emit). Otherwise
    returns a parallel list with `request.locations` indices — using
    `is` identity, so renaming or coord-patching the same object still
    keeps the same ID across multiple KML builds in one session.
    """
    if request is None:
        return None
    id_for: dict[int, int] = {id(loc): idx for idx, loc in enumerate(request.locations)}
    return [id_for.get(id(loc)) for loc in locations]


def build_map_title(request: StudyRequest) -> str:
    """Suggested title for the map / KML <name>. Public for callers that just
    need the title without building the full KML (e.g. mymaps.py multi-layer
    flow which builds per-group KMLs but still wants a top-level map name).
    """
    parts: list[str] = []
    if request.jurisdiction:
        parts.append(request.jurisdiction)
    if request.client_project_number:
        parts.append(f"#{request.client_project_number}")
    if not parts:
        parts.append(request.email_subject or "Traffic Study Sites")
    return " — ".join(parts)


# Backward-compat alias; existing internal callers used _default_title.
_default_title = build_map_title


def _build_kml(request: StudyRequest, title: str) -> str:
    # Full-request build: location index IS the ellen_loc_id.
    loc_ids: list[Optional[int]] = list(range(len(request.locations)))
    return _build_kml_from_locations(title, request.notes, request.locations, loc_ids=loc_ids)


def _build_kml_from_locations(
    title: str,
    description: Optional[str],
    locations: list[StudyLocation],
    *,
    loc_ids: Optional[list[Optional[int]]] = None,
) -> str:
    """Shared KML emission used by both full-request and per-group builders.

    `loc_ids` (optional): parallel list to `locations`; when provided,
    each placemark gets an `ellen_loc_id` ExtendedData field carrying
    its index in the StudyRequest. Missing IDs (None entries) emit no
    ExtendedData for that placemark.
    """
    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<kml xmlns="http://www.opengis.net/kml/2.2">')
    out.append("<Document>")
    out.append(f"<name>{xml_escape(title)}</name>")
    if description:
        out.append(f"<description>{xml_escape(description)}</description>")

    # Pin styles. Mapped from our StudyRequest model to approximations of the
    # user's "USE THESE" legend (see memory project_map_pin_taxonomy). Google's
    # paddle library doesn't have all colors so we approximate where needed.
    # User refines pins manually in MyMaps post-import as part of back-office.
    out.append(_PIN_STYLES.strip())

    for i, loc in enumerate(locations):
        if loc.estimate is None:
            continue
        loc_id = loc_ids[i] if loc_ids is not None else None
        out.append(_placemark_xml(loc, loc_id=loc_id))

    out.append("</Document>")
    out.append("</kml>")
    return "\n".join(out)


def _style_id_for_location(loc: StudyLocation) -> str:
    """Map (study_kind, subtype) to a KML <Style id> defined in _PIN_STYLES.

    Style IDs match MyMaps' native `icon-{shape}-{rgb}` convention so MyMaps
    renders the user's chosen pin shape+color on import. Sourced from the
    user's reference legend map (mid=1WpiETd7VGtgJt24i9GorU34FQu8btg4,
    pulled 2026-05-14). Shape codes: 1899=teardrop, 1501=ring,
    1502=marker variant, 1592=hexagon.

    Defaults assume the most common configuration when subtype is missing.
    """
    if loc.study_kind == StudyKind.TURNING_MOVEMENT:
        sub = loc.tmc_subtype or TMCSubtype.STANDARD
        if sub == TMCSubtype.COMPLEX:
            return "icon-1502-880E4F"      # Complex - 3+ Camera Intersection (maroon marker)
        if sub == TMCSubtype.LARGE:
            return "icon-1501-FF5252-nodesc"  # Large - 2 Camera TMC (red ring)
        return "icon-1899-FF5252"          # Standard - 2 Camera TMC (red teardrop)
    if loc.study_kind == StudyKind.SURVEY:
        return "icon-1592-795548-nodesc"   # Special Study (brown hexagon)
    # tube
    sub = loc.tube_subtype or TubeSubtype.VOLUME
    if sub in (TubeSubtype.VOLUME_SPEED, TubeSubtype.VOLUME_SPEED_CLASS):
        return "icon-1899-9C27B0-nodesc"   # Radar (Single Device) (purple teardrop)
    return "icon-1899-0F9D58"              # 1-3 Tube (green teardrop)


def _placemark_xml(loc: StudyLocation, *, loc_id: Optional[int] = None) -> str:
    style_id = _style_id_for_location(loc)
    name = loc.site_name or "(unnamed)"
    desc = _location_description(loc)
    assert loc.estimate is not None
    lat = loc.estimate.latitude
    lon = loc.estimate.longitude
    # ExtendedData is preserved by MyMaps round-trip — user edits a pin,
    # exports as KMZ, our parser reads the same ellen_loc_id back and matches
    # the placemark to the original StudyLocation. Only emitted when loc_id
    # is set (caller passes via _build_kml_from_locations).
    extended = ""
    if loc_id is not None:
        extended = (
            "<ExtendedData>"
            f'<Data name="ellen_loc_id"><value>{loc_id}</value></Data>'
            "</ExtendedData>"
        )
    return (
        "<Placemark>"
        f"<name>{xml_escape(name)}</name>"
        f"<description><![CDATA[{desc}]]></description>"
        f"<styleUrl>#{style_id}</styleUrl>"
        f"{extended}"
        f"<Point><coordinates>{lon:.6f},{lat:.6f},0</coordinates></Point>"
        "</Placemark>"
    )


# Pin styles match the user's reference MyMaps legend map (2026-05-14
# pull from mid=1WpiETd7VGtgJt24i9GorU34FQu8btg4). Style IDs use MyMaps'
# native `icon-{shape}-{rgb}[-nodesc]` convention so MyMaps recognizes
# them on import and renders the correct shape+color. Colors in
# `<color>` use KML's AABBGGRR ordering (alpha-blue-green-red — REVERSE
# of standard #RRGGBB hex). Icon href is the gstatic stock blank that
# MyMaps composites the shape/color onto internally.
_STOCK_ICON = "https://www.gstatic.com/mapspro/images/stock/503-wht-blank_maps.png"

_PIN_STYLES = """
<!-- TMC pins -->
<Style id="icon-1899-FF5252">
  <IconStyle>
    <color>ff5252ff</color>
    <scale>1.0</scale>
    <Icon><href>%(icon)s</href></Icon>
  </IconStyle>
  <LabelStyle><scale>0.9</scale></LabelStyle>
</Style>
<Style id="icon-1501-FF5252-nodesc">
  <IconStyle>
    <color>ff5252ff</color>
    <scale>1.0</scale>
    <Icon><href>%(icon)s</href></Icon>
  </IconStyle>
  <LabelStyle><scale>0.9</scale></LabelStyle>
</Style>
<Style id="icon-1502-880E4F">
  <IconStyle>
    <color>ff4f0e88</color>
    <scale>1.0</scale>
    <Icon><href>%(icon)s</href></Icon>
  </IconStyle>
  <LabelStyle><scale>0.9</scale></LabelStyle>
</Style>
<!-- Tube pins -->
<Style id="icon-1899-0F9D58">
  <IconStyle>
    <color>ff589d0f</color>
    <scale>1.0</scale>
    <Icon><href>%(icon)s</href></Icon>
  </IconStyle>
  <LabelStyle><scale>0.9</scale></LabelStyle>
</Style>
<Style id="icon-1899-9C27B0-nodesc">
  <IconStyle>
    <color>ffb0279c</color>
    <scale>1.0</scale>
    <Icon><href>%(icon)s</href></Icon>
  </IconStyle>
  <LabelStyle><scale>0.9</scale></LabelStyle>
</Style>
<!-- Survey / Special Study -->
<Style id="icon-1592-795548-nodesc">
  <IconStyle>
    <color>ff485579</color>
    <scale>1.0</scale>
    <Icon><href>%(icon)s</href></Icon>
  </IconStyle>
  <LabelStyle><scale>0.9</scale></LabelStyle>
</Style>
""" % {"icon": _STOCK_ICON}


def _location_description(loc: StudyLocation) -> str:
    """HTML-ish description visible when a pin is clicked in MyMaps/Google Earth."""
    lines: list[str] = []
    kind_label = "Turning Movement Count" if loc.study_kind == StudyKind.TURNING_MOVEMENT else "Tube Count"
    subtype = loc.tmc_subtype.value if loc.tmc_subtype else (loc.tube_subtype.value if loc.tube_subtype else "")
    lines.append(f"<b>{kind_label}</b>" + (f" — {subtype}" if subtype else ""))

    if loc.time_windows:
        windows_html = "<br/>".join(
            f"&nbsp;&nbsp;{tw.label}: {tw.start}–{tw.end}" + (" ⚠" if tw.flag else "")
            for tw in loc.time_windows
        )
        lines.append("<br/><b>Time windows:</b><br/>" + windows_html)

    if loc.study_dates:
        lines.append(f"<br/><b>Dates:</b> {loc.study_dates}")

    if loc.raw_text and loc.raw_text != loc.site_name:
        lines.append(f"<br/><br/><i>Email text: {loc.raw_text}</i>")

    if loc.estimate and loc.estimate.notes:
        lines.append(f"<br/><i>{loc.estimate.notes}</i>")

    return "".join(lines)
