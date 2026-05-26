"""Compact status summary for the extracted StudyRequest.

Refactored 2026-05-26 from a 3-tab inspector (Summary / Locations / JSON)
to a single compact status block. The tabs were dead weight — the same
data is accessible via Ellen's tools (`get_request`, `list_locations`,
`get_kmz_placemarks`), and they crowded the chat. The widget retains
the public API (`setRequest`, `request`, `requestChanged`) so the rest
of the app continues to work unchanged.

Renders when a StudyRequest is loaded:
  Project 2026-32 — Zephyrhills, FL
  4 locations (4 TMC) · 1 high, 3 medium confidence
  ⚠ 2 need verification

Empty state: italic "No email loaded · drop one to begin".
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..models import StudyKind, StudyRequest


class ExtractionPanel(QWidget):
    requestChanged = Signal(object)  # kept for back-compat; not emitted today

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._request: Optional[StudyRequest] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        # Match the chat panel's slightly larger font for legibility.
        f = self.label.font()
        f.setPointSize(11)
        self.label.setFont(f)
        layout.addWidget(self.label)

        # Soft background so the strip is visually distinct from drop_zone
        # above and the action buttons below.
        self.setObjectName("status_summary")
        self.setStyleSheet(
            "#status_summary { background: #f3f4f6; border: 1px solid #e2e4e7; "
            "border-radius: 6px; }"
        )

        self.setRequest(None)

    def setRequest(self, request: Optional[StudyRequest]) -> None:
        self._request = request
        if request is None:
            self.label.setText(
                "<span style='color:#888; font-style:italic;'>"
                "No email loaded — drop an .eml to begin."
                "</span>"
            )
            return
        self.label.setText(self._render(request))

    def request(self) -> Optional[StudyRequest]:
        return self._request

    def _render(self, r: StudyRequest) -> str:
        title_bits: list[str] = []
        if r.client_project_number:
            title_bits.append(_esc(f"#{r.client_project_number}"))
        if r.jurisdiction:
            title_bits.append(_esc(r.jurisdiction))
        if not title_bits and r.email_subject:
            title_bits.append(_esc(_shorten(r.email_subject, 70)))
        title = " — ".join(title_bits) if title_bits else "(no project info)"

        # Location breakdown by study kind.
        n_total = len(r.locations)
        by_kind: dict[str, int] = {}
        for loc in r.locations:
            label = (
                "TMC" if loc.study_kind == StudyKind.TURNING_MOVEMENT
                else "tube" if loc.study_kind == StudyKind.TUBE
                else "survey"
            )
            by_kind[label] = by_kind.get(label, 0) + 1
        kind_bits = " + ".join(f"{n} {k}" for k, n in by_kind.items())
        loc_line = f"{n_total} location{'' if n_total == 1 else 's'}"
        if kind_bits:
            loc_line += f" ({kind_bits})"

        # Geocoding confidence summary.
        n_high = sum(1 for l in r.locations if l.estimate and l.estimate.confidence == "high")
        n_med = sum(1 for l in r.locations if l.estimate and l.estimate.confidence == "medium")
        n_low = sum(1 for l in r.locations if l.estimate and l.estimate.confidence == "low")
        n_none = sum(1 for l in r.locations if l.estimate is None)
        conf_bits: list[str] = []
        if n_high:
            conf_bits.append(f"{n_high} high")
        if n_med:
            conf_bits.append(f"{n_med} medium")
        if n_low:
            conf_bits.append(f"{n_low} low")
        if n_none:
            conf_bits.append(f"{n_none} no coords")
        conf_line = ", ".join(conf_bits) if conf_bits else "no geocoding"

        # Quick warnings.
        warnings: list[str] = []
        if n_low or n_none:
            warnings.append(
                f"⚠ {n_low + n_none} need verification on the map"
            )
        flagged_windows = sum(
            1 for loc in r.locations for tw in loc.time_windows if tw.flag
        )
        if flagged_windows:
            warnings.append(f"⚠ {flagged_windows} time window(s) flagged")

        parts = [
            f"<div style='font-weight:600;'>{title}</div>",
            f"<div style='color:#444; margin-top:2px;'>{_esc(loc_line)}</div>",
            f"<div style='color:#666; font-size:10pt;'>{_esc(conf_line)}</div>",
        ]
        for w in warnings:
            parts.append(
                f"<div style='color:#a40000; margin-top:3px;'>{_esc(w)}</div>"
            )
        return "".join(parts)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _shorten(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"
