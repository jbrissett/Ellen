"""Drag-and-drop zone that accepts .eml/.msg from Explorer and, when possible,
direct drags from Outlook.

Outlook drag-drop is unreliable across versions — the recommended fallback is
the "Import from Outlook" button (see outlook_picker.py) which uses COM and
works regardless of what Outlook sends through the clipboard. We still try
drag-drop because it's the more natural UX.

Strategy:
  1. URL drops — covers File Explorer always, and recent Outlook (which writes
     a temp .msg and includes its path in text/uri-list).
  2. FileGroupDescriptor + FileContents — for Outlook versions that only
     provide a binary stream.
  3. On failure, we report what mime types WERE present so we can teach the
     parser to handle a new shape.
"""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

# Qt exposes Windows clipboard formats with several spellings depending on
# version and locale. Try all known variants.
FGD_MIME_CANDIDATES = (
    'application/x-qt-windows-mime;value="FileGroupDescriptorW"',
    'application/x-qt-windows-mime;value="FileGroupDescriptor"',
    "application/x-qt-windows-mime;value=FileGroupDescriptorW",
    "application/x-qt-windows-mime;value=FileGroupDescriptor",
)
FC_MIME_CANDIDATES = (
    'application/x-qt-windows-mime;value="FileContents"',
    "application/x-qt-windows-mime;value=FileContents",
)


class DropZone(QWidget):
    fileDropped = Signal(Path)
    diagnosticMessage = Signal(str)  # emitted when a drop fails — for status bar
    extractionDirectoryCleanup: list[Path] = []  # temp files to clean on exit

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setObjectName("drop_zone")
        self.setStyleSheet("""
            #drop_zone {
                border: 2px dashed #888;
                border-radius: 10px;
                background: #f8f8f8;
            }
            #drop_zone[hover="true"] {
                border-color: #3a7bd5;
                background: #eaf3ff;
            }
            #drop_zone[busy="true"] {
                border-style: solid;
                border-color: #3a7bd5;
                background: #eaf3ff;
            }
            #drop_zone QLabel { color: #444; font-size: 13px; }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title = QLabel("Drop an Outlook email here")
        self.title.setStyleSheet("font-size: 18px; font-weight: 600; color: #222;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)

        self.subtitle = QLabel(
            "Drag from Outlook, or drop a .eml / .msg file from File Explorer. "
            "After a map is built, also accepts an edited .kmz to apply pin corrections."
        )
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Wrap so long progress messages ("edit-session bec71916] re_capture → ok")
        # don't get clipped at the right edge of the narrow 260px left strip.
        # Established 2026-05-26 from a screenshot showing the progress text
        # bleeding past the drop zone's visible bounds.
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        # Indeterminate progress bar used as a 'working…' indicator. Hidden by default.
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)  # indeterminate (moving stripe)
        self.busy_bar.setFixedWidth(280)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.hide()
        # center-align the progress bar in its row
        bar_row = QWidget()
        bar_row_layout = QVBoxLayout(bar_row)
        bar_row_layout.setContentsMargins(0, 8, 0, 0)
        bar_row_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar_row_layout.addWidget(self.busy_bar)
        layout.addWidget(bar_row)

        self._default_title = self.title.text()
        self._default_subtitle = self.subtitle.text()

    def setHover(self, hover: bool) -> None:
        self.setProperty("hover", "true" if hover else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def setBusy(self, busy: bool, label: str = "") -> None:
        """Show or hide the indeterminate progress indicator.

        While busy, drop events are still accepted (in case the user wants to
        queue another file) but the visual style indicates 'working'.
        """
        self.setProperty("busy", "true" if busy else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        if busy:
            self.title.setText("Working…")
            self.subtitle.setText(label or "Extracting email contents…")
            self.busy_bar.show()
        else:
            self.title.setText(self._default_title)
            self.subtitle.setText(self._default_subtitle)
            self.busy_bar.hide()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        accept = mime.hasUrls() or any(mime.hasFormat(f) for f in FGD_MIME_CANDIDATES)
        if accept:
            event.acceptProposedAction()
            self.setHover(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.setHover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setHover(False)
        path = self._resolve_dropped_path(event)
        if path is None:
            log_path = self._dump_drop_diagnostic(event.mimeData())
            self.subtitle.setText(
                "Couldn't read that drop. Diagnostic saved — see status bar for path."
            )
            self.diagnosticMessage.emit(
                f"Drop diagnostic written to: {log_path}"
            )
            event.ignore()
            return
        event.acceptProposedAction()
        self.fileDropped.emit(path)

    def _dump_drop_diagnostic(self, mime) -> Path:
        """Write every mime format + its content to a temp log file for debugging."""
        import datetime
        tmp_dir = Path(tempfile.gettempdir()) / "traffic-intake-diagnostics"
        tmp_dir.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = tmp_dir / f"drop-{stamp}.txt"

        lines: list[str] = [
            f"Traffic Intake — drop diagnostic ({datetime.datetime.now().isoformat()})",
            "",
            "hasUrls():    " + str(mime.hasUrls()),
            "hasText():    " + str(mime.hasText()),
            "hasHtml():    " + str(mime.hasHtml()),
            "urls():       " + str([u.toString() for u in mime.urls()]),
            "text() head:  " + repr((mime.text() or "")[:300]),
            "",
            "--- All formats and content ---",
        ]
        for fmt in mime.formats():
            data = bytes(mime.data(fmt))
            preview_b = data[:400]
            try:
                preview_utf16 = preview_b.decode("utf-16-le", errors="replace")
            except Exception:
                preview_utf16 = "(decode failed)"
            try:
                preview_utf8 = preview_b.decode("utf-8", errors="replace")
            except Exception:
                preview_utf8 = "(decode failed)"
            lines.append(f"\n[{fmt}]  ({len(data)} bytes)")
            lines.append(f"  bytes head: {preview_b!r}")
            lines.append(f"  utf-8 head: {preview_utf8!r}")
            lines.append(f"  utf16-le:   {preview_utf16!r}")

        log_path.write_text("\n".join(lines), encoding="utf-8")
        return log_path

    def _resolve_dropped_path(self, event: QDropEvent) -> Optional[Path]:
        mime = event.mimeData()

        if mime.hasUrls():
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                p = Path(url.toLocalFile())
                # .eml/.msg = new-job email drop (existing path).
                # .kmz/.kml = re-drop of Ellen's exported map (rediff path,
                # gated on an active StudyRequest by the on_file_dropped handler).
                if p.suffix.lower() in (".eml", ".msg", ".kmz", ".kml"):
                    return p

        fc_format = next((f for f in FC_MIME_CANDIDATES if mime.hasFormat(f)), None)
        if fc_format is None:
            return None

        fgd_format = next((f for f in FGD_MIME_CANDIDATES if mime.hasFormat(f)), None)
        if fgd_format is None:
            return None

        wide = "FileGroupDescriptorW" in fgd_format
        try:
            filename = _parse_filegroupdescriptor(bytes(mime.data(fgd_format)), wide=wide)
            contents = bytes(mime.data(fc_format))
        except Exception:
            return None
        if not contents:
            return None

        tmp_dir = Path(tempfile.gettempdir()) / "traffic-intake-drops"
        tmp_dir.mkdir(exist_ok=True)
        target = tmp_dir / (filename or f"outlook-drop-{len(self.extractionDirectoryCleanup)}.msg")
        if target.suffix.lower() not in (".eml", ".msg"):
            target = target.with_suffix(".msg")
        target.write_bytes(contents)
        self.extractionDirectoryCleanup.append(target)
        return target


def _parse_filegroupdescriptor(data: bytes, *, wide: bool) -> str:
    """Read the first filename out of a FILEGROUPDESCRIPTOR[W] structure.

    Structure (Windows SDK):
        DWORD cItems;
        FILEDESCRIPTOR[W] fgd[cItems];

    FILEDESCRIPTOR[W] = (DWORD dwFlags, CLSID clsid, SIZEL sizel, POINTL pointl,
                         DWORD dwFileAttributes, FILETIME ftCreationTime,
                         FILETIME ftLastAccessTime, FILETIME ftLastWriteTime,
                         DWORD nFileSizeHigh, DWORD nFileSizeLow,
                         WCHAR/CHAR cFileName[MAX_PATH]);
    Filename offset within each descriptor: 72 bytes (computed: 4+16+8+8+4+8+8+8+4+4).
    Length: 520 bytes (260 wchars) for W, 260 bytes for A.
    """
    cnt = struct.unpack_from("<I", data, 0)[0]
    if cnt < 1:
        return ""
    desc_offset = 4
    name_offset = desc_offset + 72
    if wide:
        name_bytes = data[name_offset:name_offset + 260 * 2]
        name = name_bytes.decode("utf-16-le", errors="replace")
    else:
        name_bytes = data[name_offset:name_offset + 260]
        name = name_bytes.decode("latin-1", errors="replace")
    return name.split("\x00", 1)[0]
