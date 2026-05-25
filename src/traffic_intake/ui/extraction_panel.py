"""Tabbed view that shows an extracted StudyRequest — summary, locations grid,
and raw JSON. Editable in the locations grid (site name, study kind, subtype).
"""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..models import StudyKind, StudyRequest, TMCSubtype, TubeSubtype


class ExtractionPanel(QWidget):
    requestChanged = Signal(object)  # emitted when user edits anything

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._request: Optional[StudyRequest] = None

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.tabs.addTab(self.summary_view, "Summary")

        self.locations_table = QTableWidget()
        self.locations_table.setColumnCount(8)
        self.locations_table.setHorizontalHeaderLabels(
            ["#", "Site name", "Kind", "Subtype", "Times", "Conf.", "Lat", "Lon"]
        )
        self.locations_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.locations_table.horizontalHeader().setStretchLastSection(False)
        self.locations_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.locations_table.cellChanged.connect(self._on_cell_changed)
        self.tabs.addTab(self.locations_table, "Locations")

        self.json_view = QPlainTextEdit()
        self.json_view.setReadOnly(True)
        font = self.json_view.font()
        font.setFamily("Consolas")
        self.json_view.setFont(font)
        self.tabs.addTab(self.json_view, "Raw JSON")

        self.empty_label = QLabel("Drop an email above to begin.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #999; font-style: italic; padding: 24px;")
        layout.addWidget(self.empty_label)
        self.tabs.setVisible(False)

    def setRequest(self, request: Optional[StudyRequest]) -> None:
        self._request = request
        if request is None:
            self.tabs.setVisible(False)
            self.empty_label.setVisible(True)
            return
        self.empty_label.setVisible(False)
        self.tabs.setVisible(True)
        self._populate_summary(request)
        self._populate_locations(request)
        self._populate_json(request)

    def request(self) -> Optional[StudyRequest]:
        return self._request

    def _populate_summary(self, r: StudyRequest) -> None:
        flagged_windows = sum(
            1 for loc in r.locations for tw in loc.time_windows if tw.flag
        )
        flags_html = ""
        if flagged_windows:
            flags_html = (
                f'<p style="color:#a00;"><b>⚠ {flagged_windows} time window(s) flagged</b> — '
                f"review on Locations tab before proceeding.</p>"
            )

        unplaced = sum(1 for loc in r.locations if loc.estimate is None)
        unplaced_html = ""
        if unplaced:
            unplaced_html = (
                f'<p style="color:#a60;"><b>⚠ {unplaced} location(s) without coordinates</b> — '
                f"will need manual pinning on the map.</p>"
            )

        attach_bits = []
        if r.has_kmz_attachment:
            attach_bits.append("KMZ (used for coordinates)")
        if r.has_aerial_image:
            attach_bits.append("aerial image (used for vision-based location)")
        attach_line = ", ".join(attach_bits) or "none"

        html = f"""
        <h2 style="margin:0;">{_esc(r.email_subject)}</h2>
        <p style="color:#666; margin-top:2px;">From {_esc(r.email_from)} &nbsp;·&nbsp; {r.email_date.isoformat() if r.email_date else ''}</p>
        {flags_html}
        {unplaced_html}
        <table cellpadding="4" cellspacing="0">
          <tr><td style="color:#666;">Client:</td><td>{_esc(r.client_company or '—')}</td></tr>
          <tr><td style="color:#666;">Contact:</td><td>{_esc(r.client_contact_name or '—')} &lt;{_esc(r.client_contact_email or '—')}&gt;</td></tr>
          <tr><td style="color:#666;">Project #:</td><td>{_esc(r.client_project_number or '—')}</td></tr>
          <tr><td style="color:#666;">Jurisdiction:</td><td>{_esc(r.jurisdiction or '—')}</td></tr>
          <tr><td style="color:#666;">Locations:</td><td>{r.total_locations}</td></tr>
          <tr><td style="color:#666;">Attachments used:</td><td>{_esc(attach_line)}</td></tr>
        </table>
        <h3>Notes</h3>
        <div style="white-space: pre-wrap;">{_esc(r.notes or '(none)')}</div>
        """
        self.summary_view.setHtml(html)

    def _populate_locations(self, r: StudyRequest) -> None:
        self.locations_table.blockSignals(True)
        self.locations_table.setRowCount(len(r.locations))
        for row, loc in enumerate(r.locations):
            self.locations_table.setItem(row, 0, _readonly(str(row + 1)))

            name_item = QTableWidgetItem(loc.site_name)
            self.locations_table.setItem(row, 1, name_item)

            kind_combo = QComboBox()
            for k in StudyKind:
                kind_combo.addItem(k.value, k)
            kind_combo.setCurrentText(loc.study_kind.value)
            kind_combo.currentTextChanged.connect(lambda v, ridx=row: self._on_kind_changed(ridx, v))
            self.locations_table.setCellWidget(row, 2, kind_combo)

            self._set_subtype_widget(row, loc)

            times_text = ", ".join(
                (f"{tw.label}: {tw.start}–{tw.end}" + (" ⚠" if tw.flag else ""))
                for tw in loc.time_windows
            )
            self.locations_table.setItem(row, 4, _readonly(times_text))

            est = loc.estimate
            if est is None:
                conf = "(none)"
                lat_s = lon_s = "—"
            else:
                conf = f"{est.confidence} ({est.source})"
                lat_s = f"{est.latitude:.5f}"
                lon_s = f"{est.longitude:.5f}"
            self.locations_table.setItem(row, 5, _readonly(conf))
            self.locations_table.setItem(row, 6, _readonly(lat_s))
            self.locations_table.setItem(row, 7, _readonly(lon_s))

        self.locations_table.resizeColumnsToContents()
        self.locations_table.blockSignals(False)

    def _set_subtype_widget(self, row: int, loc) -> None:
        combo = QComboBox()
        if loc.study_kind == StudyKind.TURNING_MOVEMENT:
            for v in TMCSubtype:
                combo.addItem(v.value, v)
            combo.setCurrentText((loc.tmc_subtype or TMCSubtype.STANDARD).value)
        else:
            for v in TubeSubtype:
                combo.addItem(v.value, v)
            combo.setCurrentText((loc.tube_subtype or TubeSubtype.VOLUME).value)
        combo.currentTextChanged.connect(lambda v, ridx=row: self._on_subtype_changed(ridx, v))
        self.locations_table.setCellWidget(row, 3, combo)

    def _populate_json(self, r: StudyRequest) -> None:
        self.json_view.setPlainText(json.dumps(r.model_dump(mode="json"), indent=2, default=str))

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._request is None or col != 1:  # only site_name is text-editable
            return
        new = self.locations_table.item(row, col).text()
        self._request.locations[row].site_name = new
        self._populate_json(self._request)
        self.requestChanged.emit(self._request)

    def _on_kind_changed(self, row: int, value: str) -> None:
        if self._request is None:
            return
        loc = self._request.locations[row]
        loc.study_kind = StudyKind(value)
        if loc.study_kind == StudyKind.TURNING_MOVEMENT:
            loc.tube_subtype = None
            loc.tmc_subtype = loc.tmc_subtype or TMCSubtype.STANDARD
        else:
            loc.tmc_subtype = None
            loc.tube_subtype = loc.tube_subtype or TubeSubtype.VOLUME
        self._set_subtype_widget(row, loc)
        self._populate_json(self._request)
        self.requestChanged.emit(self._request)

    def _on_subtype_changed(self, row: int, value: str) -> None:
        if self._request is None:
            return
        loc = self._request.locations[row]
        if loc.study_kind == StudyKind.TURNING_MOVEMENT:
            loc.tmc_subtype = TMCSubtype(value)
        else:
            loc.tube_subtype = TubeSubtype(value)
        self._populate_json(self._request)
        self.requestChanged.emit(self._request)


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
