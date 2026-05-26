"""Parse KMZ / KML attachments into a list of named placemarks.

KMZ = zip-wrapped KML. KML = XML with <Placemark> elements containing
<Point><coordinates>lon,lat,alt</coordinates></Point>.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET


@dataclass
class Placemark:
    name: Optional[str]
    description: Optional[str]
    latitude: float
    longitude: float
    # Set when the placemark's <ExtendedData> contains an `ellen_loc_id`
    # entry — written by kml_export when building a KMZ from a StudyRequest,
    # read back here on a user-edited re-drop to match the placemark to its
    # original StudyLocation. None on third-party / new KMZs.
    ellen_loc_id: Optional[int] = None


_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def parse_kmz_bytes(data: bytes) -> list[Placemark]:
    """Take raw KMZ file bytes, return all point placemarks inside."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            return []
        kml_names.sort(key=lambda n: (0 if n.lower() == "doc.kml" else 1, n))
        with zf.open(kml_names[0]) as f:
            kml_bytes = f.read()
    return parse_kml_bytes(kml_bytes)


def parse_kml_bytes(data: bytes) -> list[Placemark]:
    """Parse a KML XML document into placemarks."""
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)  # strip default ns for simpler XPath
    root = ET.fromstring(text)

    out: list[Placemark] = []
    for pm in root.iter("Placemark"):
        name_el = pm.find("name")
        desc_el = pm.find("description")
        point = pm.find("Point")
        if point is None:
            continue
        coords_el = point.find("coordinates")
        if coords_el is None or not coords_el.text:
            continue
        # KML coordinate format: "lon,lat[,alt]" — note the unusual order.
        first = coords_el.text.strip().splitlines()[0].strip()
        parts = first.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        out.append(
            Placemark(
                name=(name_el.text.strip() if name_el is not None and name_el.text else None),
                description=(desc_el.text.strip() if desc_el is not None and desc_el.text else None),
                latitude=lat,
                longitude=lon,
                ellen_loc_id=_extract_ellen_loc_id(pm),
            )
        )
    return out


def _extract_ellen_loc_id(pm: ET.Element) -> Optional[int]:
    """Read the round-trip <ExtendedData><Data name='ellen_loc_id'><value>N</value>...
    out of a placemark, if present. None for third-party KMZs.
    """
    ed = pm.find("ExtendedData")
    if ed is None:
        return None
    for data_el in ed.findall("Data"):
        if data_el.get("name") != "ellen_loc_id":
            continue
        value_el = data_el.find("value")
        if value_el is None or not value_el.text:
            return None
        try:
            return int(value_el.text.strip())
        except ValueError:
            return None
    return None
