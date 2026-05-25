"""Main window for the Traffic Intake desktop app."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QSystemTrayIcon,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..kml_export import build_kml, build_kmz
from ..models import StudyRequest
from ..mymaps import CreateMapResult
from ..qchub import CreateOrderResult
from .chat_panel import ChatPanel
from .drop_zone import DropZone
from .extraction_panel import ExtractionPanel
from .floating_reply import FloatingReplyWidget
from .outlook_picker import NoSelection, OutlookUnavailable, import_selected_email
from .settings_dialog import SettingsDialog
from .workers import run_chat, run_email_draft, run_extraction, run_mymaps_creation, run_qchub_creation


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ellen")
        self._size_and_center_on_primary_screen()

        # Chat state — persists for the lifetime of the loaded request.
        self._chat_history: list[dict] = []
        # Session artifacts — files/links produced this session, exposed to
        # Ellen via the get_artifacts tool. Cleared on New/Clear and on each
        # new extraction. Mutated in place from the main thread when actions
        # complete; chat worker reads via snapshot in execute_tool.
        self._artifacts: dict = {}
        # Live qchub edit-session bridge (Ship 2). Set after a successful
        # qchub order so Ellen's *_estimate_* tools can drive the still-open
        # browser tab. None outside an active session.
        self._qchub_edit_session = None

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self.setStatusBar(QStatusBar())

        # System tray icon + interactive reply pop-up. Tray gives the
        # user a passive "Ellen is running" indicator in the taskbar
        # corner + handles `showMessage` toasts when background jobs
        # complete. Floating reply widget appears when Ellen asks a
        # question and the app isn't in focus — user can reply directly
        # without finding the app window.
        self._build_system_tray()
        self._floating_reply: FloatingReplyWidget | None = None

        if not self._has_api_key():
            self.status("No Anthropic API key saved — open Settings to add one.")
        else:
            self.status("Ready. Drop an email or use 'Import from Outlook'.")

    # ----- system tray + notifications -----

    def _build_system_tray(self) -> None:
        """Install a system tray icon for background-job notifications.

        Uses the window's own icon; if the platform doesn't support a
        tray (rare on Windows but possible on locked-down machines),
        we degrade silently — `notify()` will skip the toast but the
        in-app status bar still updates.
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return
        icon = self.windowIcon()
        if icon.isNull():
            # No window icon yet (set later by the bootstrap). Use Qt's
            # standard message-box icon as a placeholder so the tray
            # entry still appears.
            from PySide6.QtWidgets import QStyle
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Ellen — workplace assistant")
        # Single-click the tray icon → raise + activate the main window.
        # Right-click for native context menu (close, etc.) added later
        # if needed; left-click is the primary "bring me back" gesture.
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:
        """Bring the main window forward when the user clicks the tray icon."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._bring_to_front()

    def _bring_to_front(self) -> None:
        """Raise + activate the main window, restoring from minimized
        if needed. Used by the tray click and floating-reply 'open chat'.
        """
        self.setWindowState(
            (self.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive
        )
        self.show()
        self.raise_()
        self.activateWindow()

    def notify(
        self,
        title: str,
        message: str,
        *,
        urgent: bool = False,
        timeout_ms: int = 5000,
    ) -> None:
        """Surface a Windows toast via the system tray icon. No-op if
        the tray isn't available. Always updates the status bar too so
        the info is visible without the toast.
        """
        self.status(message)
        if getattr(self, "_tray", None) is None:
            return
        icon_kind = (
            QSystemTrayIcon.MessageIcon.Critical
            if urgent
            else QSystemTrayIcon.MessageIcon.Information
        )
        try:
            self._tray.showMessage(title, message, icon_kind, timeout_ms)
        except Exception:
            # Never let a notification error kill the calling flow.
            pass

    def _maybe_show_floating_reply(self) -> None:
        """If Ellen's last assistant message looks like a question AND
        the main window isn't currently active, surface the floating
        reply widget so the user can answer without alt-tabbing.

        Heuristic for 'question': the last assistant text in chat
        history ends with '?' (after stripping trailing whitespace).
        Quick to ship; if it gets noisy we can tighten later (e.g.,
        require a tool that explicitly marks the question).
        """
        if self.isActiveWindow():
            return  # user is looking at the app — no popup needed
        last_assistant_text = self._extract_last_assistant_text()
        if not last_assistant_text:
            return
        if not last_assistant_text.rstrip().endswith("?"):
            return
        # Replace any existing widget — only one open at a time.
        if self._floating_reply is not None:
            try:
                self._floating_reply.close()
            except Exception:
                pass
            self._floating_reply = None
        widget = FloatingReplyWidget(
            last_assistant_text, on_send=self._on_floating_reply_send,
        )
        self._floating_reply = widget
        widget.show_to_user()
        # NO redundant tray toast — the floating widget IS the
        # notification per user direction 2026-05-24 ("i only need
        # one.. the chat one"). Tray toasts are reserved for
        # non-interactive job-completion events (map ready, order
        # ready, errors).

    def _on_floating_reply_send(self, reply_text: str) -> None:
        """Floating reply widget callback — route the typed reply
        through the same chat send path the main panel uses."""
        self._floating_reply = None
        # Bring the app forward so the chat is visible while Ellen
        # processes the reply; the user can choose to alt-tab away
        # again if they want.
        self._bring_to_front()
        self.on_chat_send(reply_text)

    def _extract_last_assistant_text(self) -> str:
        """Pull the last assistant text block out of chat history."""
        for msg in reversed(self._chat_history or []):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = block.get("text") or ""
                        if t:
                            texts.append(t)
                if texts:
                    return "".join(texts)
            return ""
        return ""

    # ----- layout -----

    def _size_and_center_on_primary_screen(self) -> None:
        """Size the window to half of the available screen area and center it.

        Replaces the older fixed 1500x900 default which overflowed laptop
        screens (≤1366x768) and landed partially off-screen, forcing the
        user to resize before they could interact. Now:
          - target 50% of available screen (per axis)
          - clamp to a usable minimum (800x600) so the panels don't get cramped
          - cap at 1500x900 on very large displays so we don't open huge
          - center on the same screen
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            # Headless / no display — fall back to a sane default and bail.
            self.resize(1000, 700)
            return
        avail = screen.availableGeometry()
        target_w = max(800, min(1500, avail.width() // 2))
        target_h = max(600, min(900, avail.height() // 2))
        self.resize(target_w, target_h)
        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        self.move(frame.topLeft())

    def _build_central(self) -> None:
        # Left pane: drop zone + extraction panel + actions (existing UI)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)

        self.drop_zone = DropZone()
        self.drop_zone.fileDropped.connect(self.on_file_dropped)
        self.drop_zone.diagnosticMessage.connect(self.status)
        left_layout.addWidget(self.drop_zone, 1)

        self.extraction_panel = ExtractionPanel()
        left_layout.addWidget(self.extraction_panel, 2)
        self.extraction_panel.setVisible(False)

        self.action_row = self._build_action_row()
        left_layout.addWidget(self.action_row)
        self.action_row.setVisible(False)

        # Right pane: chat sidebar (persistent — always visible)
        self.chat_panel = ChatPanel()
        self.chat_panel.sendRequested.connect(self.on_chat_send)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.chat_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([900, 600])

        self.setCentralWidget(splitter)

    def _build_action_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        self.btn_map = QPushButton("Create MyMaps map…")
        self.btn_map.setEnabled(False)
        self.btn_map.clicked.connect(self.on_create_map)
        self.btn_qchub = QPushButton("Create qchub order…")
        self.btn_qchub.setEnabled(False)
        self.btn_qchub.clicked.connect(self.on_create_qchub_order)
        self.btn_email = QPushButton("Draft response email…")
        self.btn_email.setEnabled(False)
        self.btn_email.clicked.connect(self.on_draft_email)
        layout.addWidget(self.btn_map)
        layout.addWidget(self.btn_qchub)
        layout.addWidget(self.btn_email)
        layout.addStretch(1)
        return row

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize() * 0.9)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        self.act_outlook = QAction("Import from Outlook", self)
        self.act_outlook.setToolTip("Pull the email currently selected in your open Outlook window.")
        self.act_outlook.triggered.connect(self.on_import_from_outlook)
        toolbar.addAction(self.act_outlook)

        self.act_new = QAction("New / Clear", self)
        self.act_new.setShortcut(QKeySequence("Ctrl+N"))
        self.act_new.setToolTip("Clear the current extraction and start fresh.")
        self.act_new.triggered.connect(self.on_new)
        toolbar.addAction(self.act_new)

        toolbar.addSeparator()

        self.act_open_file = QAction("Open file…", self)
        self.act_open_file.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open_file.triggered.connect(self.on_open)
        toolbar.addAction(self.act_open_file)

        toolbar.addSeparator()

        self.act_settings = QAction("Settings", self)
        self.act_settings.triggered.connect(self.on_settings)
        toolbar.addAction(self.act_settings)

    def _build_menu(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")

        new_act = QAction("&New / Clear", self)
        new_act.setShortcut(QKeySequence("Ctrl+N"))
        new_act.triggered.connect(self.on_new)
        file_menu.addAction(new_act)

        outlook_act = QAction("&Import from Outlook", self)
        outlook_act.triggered.connect(self.on_import_from_outlook)
        file_menu.addAction(outlook_act)

        open_act = QAction("&Open email…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self.on_open)
        file_menu.addAction(open_act)

        file_menu.addSeparator()
        self.act_export_kmz = QAction("&Export KMZ…", self)
        self.act_export_kmz.setShortcut(QKeySequence("Ctrl+E"))
        self.act_export_kmz.setEnabled(False)
        self.act_export_kmz.triggered.connect(self.on_export_kmz)
        file_menu.addAction(self.act_export_kmz)

        self.act_export_kml = QAction("Export &KML…", self)
        self.act_export_kml.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.act_export_kml.setEnabled(False)
        self.act_export_kml.triggered.connect(self.on_export_kml)
        file_menu.addAction(self.act_export_kml)

        file_menu.addSeparator()
        quit_act = QAction("&Quit", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        edit_menu = menu.addMenu("&Edit")
        settings_act = QAction("&Settings…", self)
        settings_act.triggered.connect(self.on_settings)
        edit_menu.addAction(settings_act)

    # ----- handlers -----

    def status(self, msg: str) -> None:
        self.statusBar().showMessage(msg)

    def _has_api_key(self) -> bool:
        try:
            config.get_api_key()
            return True
        except Exception:
            return False

    def on_new(self) -> None:
        """Clear the loaded extraction and reset to drop-zone state."""
        self.extraction_panel.setRequest(None)
        self.extraction_panel.setVisible(False)
        self.action_row.setVisible(False)
        self.drop_zone.setBusy(False)
        for b in (self.btn_map, self.btn_qchub, self.btn_email):
            b.setEnabled(False)
        self.act_export_kmz.setEnabled(False)
        self.act_export_kml.setEnabled(False)
        self._chat_history = []
        self._artifacts = {}
        self._qchub_edit_session = None
        self.chat_panel.clear()
        self.chat_panel.setBusy(False)
        self.status("Cleared. Ready for the next email.")

    # ----- chat -----

    def on_chat_send(self, message: str) -> None:
        request = self.extraction_panel.request()
        if request is None:
            self.chat_panel.appendSystemNote(
                "<i>Drop an email on the left first — I need something to work with.</i>"
            )
            return
        if not self._has_api_key():
            self.chat_panel.appendSystemNote(
                "<i>No Anthropic API key saved — open Settings to add one.</i>"
            )
            return

        self.chat_panel.setBusy(True)
        run_chat(
            message,
            self._chat_history,
            request,
            self._artifacts,
            on_text_delta=self.chat_panel.appendAssistantDelta,
            on_tool_result=self.chat_panel.appendToolResult,
            on_action_request=self._on_chat_action_request,
            on_finished=self._on_chat_finished,
            on_failed=self._on_chat_failed,
            qchub_edit_session=getattr(self, "_qchub_edit_session", None),
        )

    def _on_chat_action_request(self, name: str, args: dict) -> None:
        """The chat assistant asked to trigger an action. Reuse the existing
        button handlers so the user gets the same confirm dialog + progress
        flow as clicking the button directly.
        """
        if name == "create_mymaps_map":
            self.on_create_map()
        elif name == "create_qchub_order":
            self.on_create_qchub_order()
        elif name == "export_kmz":
            self.on_export_kmz()
        elif name == "export_kml":
            self.on_export_kml()
        elif name == "draft_email_reply":
            # Pass through optional overrides Ellen may have supplied.
            self.on_draft_email(
                to=args.get("to") or None,
                cc=args.get("cc") or None,
                subject=args.get("subject") or None,
                body_html=args.get("body_html") or None,
                deployment_schedule=args.get("deployment_schedule") or None,
                map_url=args.get("map_url") or None,
            )
        else:
            self.chat_panel.appendSystemNote(
                f"<i>Unknown action requested: {name!r} — ignoring.</i>"
            )

    def _on_chat_finished(self, new_history: list) -> None:
        self._chat_history = new_history
        self.chat_panel.finishAssistantTurn()
        self.chat_panel.setBusy(False)
        # Refresh the extraction display in case the assistant edited state.
        current = self.extraction_panel.request()
        if current is not None:
            self.extraction_panel.setRequest(current)
        # If Ellen ended the session this turn (end_session tool fired),
        # drop our reference to the now-dead qchub edit session so the
        # NEXT chat turn doesn't pass it back to the worker.
        if self._artifacts.get("session_ended") and getattr(self, "_qchub_edit_session", None) is not None:
            self._qchub_edit_session = None
            self.chat_panel.appendSystemNote(
                "<i>Session closed. Drop another email to start a new one.</i>"
            )
        # If Ellen asked a question AND the user isn't looking at the
        # app, surface the floating reply widget so they can answer
        # without finding the window. (See _maybe_show_floating_reply
        # for the question-detection heuristic.)
        self._maybe_show_floating_reply()

    def _on_chat_failed(self, message: str) -> None:
        self.chat_panel.appendSystemNote(
            f"<i>Something went wrong: {message}</i>"
        )
        self.chat_panel.setBusy(False)

    def on_create_map(self) -> None:
        request = self.extraction_panel.request()
        if request is None:
            return
        unplaced = sum(1 for loc in request.locations if loc.estimate is None)
        placed = request.total_locations - unplaced
        if placed == 0:
            QMessageBox.warning(
                self, "Nothing to map",
                "None of the locations have coordinates yet. Set lat/lon first.",
            )
            return

        settings = QSettings("Quality Counts", "Traffic Intake")
        if not settings.value("skip_mymaps_confirm", False, type=bool):
            msg_text = (
                f"An Edge window will open to create the map in Google MyMaps. "
                f"{placed} pin(s) will be added."
            )
            if unplaced:
                msg_text += f" {unplaced} without coordinates will be skipped."
            msg_text += (
                "\n\nFirst-run only: sign into Google in the Edge window when it appears. "
                "Don't close the window during automation — only close it when the success dialog appears.\n\nContinue?"
            )

            dlg = QMessageBox(self)
            dlg.setWindowTitle("Create MyMaps map")
            dlg.setIcon(QMessageBox.Icon.Question)
            dlg.setText(msg_text)
            dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            dlg.setDefaultButton(QMessageBox.StandardButton.Yes)
            cb = QCheckBox("Don't show this again")
            dlg.setCheckBox(cb)

            choice = dlg.exec()
            if choice != QMessageBox.StandardButton.Yes:
                return
            if cb.isChecked():
                settings.setValue("skip_mymaps_confirm", True)
                self.status("MyMaps confirmation hidden — re-enable in settings later if needed.")

        self.btn_map.setEnabled(False)
        self.drop_zone.setBusy(True, "Creating MyMaps map (browser window opening)…")
        self.status("Creating MyMaps map…")
        # Clear any stale outcome from a prior MyMaps run so artifacts cleanly
        # reflect "in flight" while this run executes. The success/failure
        # callbacks repopulate either mymaps_share_url or mymaps_failed below.
        for key in ("mymaps_share_url", "mymaps_edit_url", "mymaps_title",
                    "mymaps_failed", "mymaps_error"):
            self._artifacts.pop(key, None)
        self._artifacts["mymaps_in_progress"] = True
        run_mymaps_creation(
            request,
            on_progress=self._on_mymaps_progress,
            on_finished=self._on_mymaps_finished,
            on_failed=self._on_mymaps_failed,
        )

    def _on_mymaps_progress(self, message: str) -> None:
        self.status(message)
        self.drop_zone.setBusy(True, message)

    # ----- qchub -----

    def on_create_qchub_order(self) -> None:
        request = self.extraction_panel.request()
        if request is None:
            return

        creds = config.get_qchub_credentials()
        if not creds:
            QMessageBox.warning(
                self, "qchub credentials required",
                "No qchub username/password saved. Open Edit → Settings to add yours. "
                "(Each user enters their own qchub login.)",
            )
            return

        settings = QSettings("Quality Counts", "Traffic Intake")
        if not settings.value("skip_qchub_confirm", False, type=bool):
            msg_text = (
                "An Edge window will open to create the order in qchub. "
                f"It'll fill the Request Estimate form, upload the KMZ ({request.total_locations} pin(s)), "
                "configure study groups, submit the request, and capture the estimate preview.\n\n"
                "If a Company or Client User isn't already in qchub, the run will stop and tell you "
                "to add them manually before re-running (auto-create is a v2 feature).\n\n"
                "⚠ Do NOT close the Edge window during automation.\n\nContinue?"
            )
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Create qchub order")
            dlg.setIcon(QMessageBox.Icon.Question)
            dlg.setText(msg_text)
            dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            dlg.setDefaultButton(QMessageBox.StandardButton.Yes)
            cb = QCheckBox("Don't show this again")
            dlg.setCheckBox(cb)
            if dlg.exec() != QMessageBox.StandardButton.Yes:
                return
            if cb.isChecked():
                settings.setValue("skip_qchub_confirm", True)

        self.btn_qchub.setEnabled(False)
        self.drop_zone.setBusy(True, "Creating qchub order (browser window opening)…")
        self.status("Creating qchub order…")
        run_qchub_creation(
            request,
            qc_office=None,  # TODO: auto-pick from recipient/jurisdiction
            on_progress=self._on_qchub_progress,
            on_finished=self._on_qchub_finished,
            on_failed=self._on_qchub_failed,
            on_missing_company=self._on_qchub_missing_company,
            on_missing_user=self._on_qchub_missing_user,
        )

    def _on_qchub_progress(self, message: str) -> None:
        self.status(message)
        self.drop_zone.setBusy(True, message)

    def _on_qchub_finished(self, result: CreateOrderResult) -> None:
        self.btn_qchub.setEnabled(True)
        self.drop_zone.setBusy(False)
        self.status(f"qchub order created (id={result.order_id or 'unknown'}).")
        # Toast — useful in headless mode where the user has no browser
        # to watch. Mentions the order ID so the notification is
        # actionable on its own.
        self.notify(
            "qchub order created",
            f"Order {result.order_id or 'created'} — estimate PDF saved to Downloads.",
        )

        # Store artifacts so Ellen can refer back.
        self._artifacts["qchub_order_id"] = result.order_id
        self._artifacts["qchub_order_url"] = result.order_url
        self._artifacts["qchub_estimate_snapshot"] = (
            str(result.estimate_snapshot) if result.estimate_snapshot else None
        )
        self._artifacts["qchub_diagnostic_dir"] = (
            str(result.diagnostic_dir) if result.diagnostic_dir else None
        )
        # Live edit session (Ship 2) — the live qchub browser command bridge.
        # Stored OUTSIDE _artifacts so it isn't JSON-dumped to the model:
        # tools access it via a dedicated attribute and only return its
        # results, not the object itself.
        self._qchub_edit_session = result.edit_session
        # Estimate artifacts (Ship 1 of estimate flow). Ellen sees these via
        # get_artifacts; in Ship 2 she'll also have a dedicated `get_estimate`
        # tool that returns the structured EstimateLine list.
        if result.estimate is not None:
            self._artifacts["estimate_html_path"] = result.estimate.html_path
            self._artifacts["estimate_screenshot_path"] = result.estimate.screenshot_path
            self._artifacts["estimate_pdf_path"] = result.estimate.pdf_path
            self._artifacts["estimate_total"] = result.estimate.total
            self._artifacts["estimate_line_count"] = len(result.estimate.lines)
            # Keep the full structured estimate too (Ship 2's get_estimate
            # tool will read this).
            self._artifacts["estimate"] = result.estimate.model_dump(exclude_none=True)

        # Report into the chat instead of a modal.
        parts = []
        parts.append(
            f"qchub order ready"
            + (f" (ID <b>{result.order_id}</b>)" if result.order_id else "")
            + "."
        )
        if result.order_url:
            parts.append(
                f"&nbsp;&nbsp;Link: <a href='{result.order_url}'>{result.order_url}</a>"
            )
        # Estimate summary — terse: line count + grand total + PDF link.
        # Per-line enumeration was removed 2026-05-17 (the user has the PDF
        # for line-by-line review; the dump was 36+ lines on real orders).
        # Ellen can still read the full structured estimate via her tools
        # (`get_estimate_lines`) when the user asks about specific rows.
        if result.estimate is not None and result.estimate.lines:
            est = result.estimate
            summary = f"&nbsp;&nbsp;<b>Estimate:</b> {len(est.lines)} line(s)"
            if est.total is not None:
                summary += f", total <b>${est.total:,.2f}</b>"
            parts.append(summary)
            if est.parse_note:
                parts.append(f"&nbsp;&nbsp;<i>Note: {est.parse_note}</i>")
            if est.pdf_path:
                pp = str(est.pdf_path).replace("\\", "/")
                from pathlib import Path as _P
                fname = _P(est.pdf_path).name
                folder = str(_P(est.pdf_path).parent).replace("\\", "/")
                parts.append(
                    f"&nbsp;&nbsp;<b>Estimate PDF saved to Downloads:</b> "
                    f"<a href='file:///{pp}'>{fname}</a> "
                    f"<span style='color:#666'>(<a href='file:///{folder}'>open folder</a>)</span>"
                )
            # screenshot_path is kept on disk for diagnostics but not surfaced
            # in chat — it's a low-res capture of the modal, no value to the
            # user once the parsed lines + PDF are in front of them.
        elif result.estimate_snapshot:
            # Fallback (older code path / parse failed but screenshot saved)
            sp = str(result.estimate_snapshot).replace("\\", "/")
            parts.append(
                f"&nbsp;&nbsp;Estimate preview: <a href='file:///{sp}'>{result.estimate_snapshot}</a>"
            )
        if result.note:
            parts.append(f"<i>{result.note}</i>")
        self.chat_panel.appendSystemNote("<br/>".join(parts))

    def _on_qchub_failed(self, message: str) -> None:
        self.btn_qchub.setEnabled(True)
        self.drop_zone.setBusy(False)
        self.status("qchub automation failed.")
        self.notify("qchub failed", _shorten(message, 200), urgent=True)
        QMessageBox.critical(self, "qchub failed", message)

    def _on_qchub_missing_company(self, name: str, domain: str) -> None:
        self.btn_qchub.setEnabled(True)
        self.drop_zone.setBusy(False)
        self.status("qchub: company not found.")
        QMessageBox.warning(
            self,
            "Company not in qchub",
            f"The Company '{name}' (domain '{domain}') isn't in qchub yet.\n\n"
            "Auto-create is a v2 feature — for now, please:\n"
            "1. Open qchub manually\n"
            "2. Add the Company (and a Branch + Client User if needed)\n"
            "3. Come back and click 'Create qchub order…' again",
        )

    def _on_qchub_missing_user(self, email: str) -> None:
        self.btn_qchub.setEnabled(True)
        self.drop_zone.setBusy(False)
        self.status("qchub: user not found.")
        QMessageBox.warning(
            self,
            "User not in qchub",
            f"The Client User '{email}' isn't in qchub yet.\n\n"
            "Auto-create is a v2 feature — for now, please add the user manually in qchub, "
            "then come back and click 'Create qchub order…' again.",
        )

    def _on_mymaps_finished(self, result: CreateMapResult) -> None:
        self.btn_map.setEnabled(True)
        self.drop_zone.setBusy(False)
        self.status("MyMaps map created.")
        # Toast — primary signal in headless mode where the map browser
        # isn't visible. Includes the share link so the user can act on
        # the notification directly.
        if result.share_url:
            self.notify(
                "MyMaps map ready",
                f"{result.map_title or 'Map'} — share link copied to clipboard.",
            )
        else:
            self.notify("MyMaps map ready", result.map_title or "Map created.")

        # Store as session artifacts so Ellen can refer back to them.
        self._artifacts["mymaps_title"] = result.map_title
        self._artifacts["mymaps_share_url"] = result.share_url
        self._artifacts["mymaps_edit_url"] = result.map_edit_url
        self._artifacts.pop("mymaps_in_progress", None)
        self._artifacts.pop("mymaps_failed", None)
        self._artifacts.pop("mymaps_error", None)

        # Report into the chat instead of a modal.
        body = f"Map <b>{result.map_title}</b> is ready.<br/>"
        if result.share_url:
            body += (
                f"&nbsp;&nbsp;Share link (view-only): "
                f"<a href='{result.share_url}'>{result.share_url}</a><br/>"
            )
        body += f"&nbsp;&nbsp;Edit: <a href='{result.map_edit_url}'>{result.map_edit_url}</a>"
        if result.note:
            body += f"<br/><i>{result.note}</i>"
        self.chat_panel.appendSystemNote(body)

        # Auto-copy the share link to the clipboard (saves the user a step).
        if result.share_url:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(result.share_url)
            self.status("Share link copied to clipboard.")

    def _on_mymaps_failed(self, message: str) -> None:
        self.btn_map.setEnabled(True)
        self.drop_zone.setBusy(False)
        self.status("MyMaps automation failed.")
        self.notify("MyMaps failed", _shorten(message, 200), urgent=True)
        # Mark the failure in artifacts so Ellen can detect it when the user
        # later asks for the map link. Without this, a failed MyMaps run is
        # indistinguishable from "still running" via `get_artifacts` — Ellen
        # ends up telling the user to check the MyMaps tab herself.
        self._artifacts["mymaps_failed"] = True
        self._artifacts["mymaps_error"] = message
        self._artifacts.pop("mymaps_in_progress", None)
        # Clear any stale success artifacts from a prior run on the same session.
        self._artifacts.pop("mymaps_share_url", None)
        self._artifacts.pop("mymaps_edit_url", None)
        self._artifacts.pop("mymaps_title", None)
        # Also surface a clear failure note into chat so Ellen sees it on the
        # next turn (the chat panel reads system notes alongside her messages).
        self.chat_panel.appendSystemNote(
            f"<b>MyMaps creation failed.</b> {message}<br/>"
            "<i>Ellen — when the user asks about the map, tell them it failed "
            "and ask if they'd like to retry or export a KMZ as fallback.</i>"
        )
        ans = QMessageBox.warning(
            self,
            "MyMaps failed",
            f"{message}\n\nWould you like to export a .kmz file instead, as the fallback deliverable?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.on_export_kmz()

    def on_export_kml(self) -> None:
        """Save raw KML (unzipped XML) — the format qchub's UPLOAD KML wants."""
        from PySide6.QtWidgets import QFileDialog
        request = self.extraction_panel.request()
        if request is None:
            return
        result = build_kml(request)
        suggested = _safe_filename(request.email_subject or "traffic_study") + ".kml"
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Export KML", suggested, "KML file (*.kml);;All files (*)"
        )
        if not path_str:
            return
        out = Path(path_str)
        out.write_bytes(result.data)
        self._artifacts["kml_path"] = str(out)
        self._artifacts["kml_placemark_count"] = result.placemark_count
        if result.skipped_unplaced:
            self.status(
                f"Wrote {out.name} with {result.placemark_count} pin(s); "
                f"{result.skipped_unplaced} location(s) skipped (no coordinates)."
            )
        else:
            self.status(f"Wrote {out.name} with {result.placemark_count} pin(s).")
        sp = str(out).replace("\\", "/")
        skip_note = (
            f" ({result.skipped_unplaced} skipped — no coordinates)"
            if result.skipped_unplaced else ""
        )
        self.chat_panel.appendSystemNote(
            f"KML saved: <a href='file:///{sp}'>{out}</a> — "
            f"{result.placemark_count} pin(s){skip_note}."
        )

    def on_export_kmz(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        request = self.extraction_panel.request()
        if request is None:
            return
        result = build_kmz(request)
        # Sensible default filename from subject + jurisdiction
        suggested = _safe_filename(request.email_subject or "traffic_study") + ".kmz"
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Export KMZ", suggested, "Google Earth KMZ (*.kmz);;All files (*)"
        )
        if not path_str:
            return
        from pathlib import Path
        out = Path(path_str)
        out.write_bytes(result.data)
        self._artifacts["kmz_path"] = str(out)
        self._artifacts["kmz_placemark_count"] = result.placemark_count
        if result.skipped_unplaced:
            self.status(
                f"Wrote {out.name} with {result.placemark_count} pin(s); "
                f"{result.skipped_unplaced} location(s) skipped (no coordinates)."
            )
        else:
            self.status(f"Wrote {out.name} with {result.placemark_count} pin(s).")
        # Also surface in the chat — KMZ exports are a deliverable artifact.
        sp = str(out).replace("\\", "/")
        skip_note = (
            f" ({result.skipped_unplaced} location(s) skipped — no coordinates)"
            if result.skipped_unplaced else ""
        )
        self.chat_panel.appendSystemNote(
            f"KMZ saved: <a href='file:///{sp}'>{out}</a> — "
            f"{result.placemark_count} pin(s){skip_note}."
        )

    def on_draft_email(
        self, *, to: str | None = None, cc: str | None = None,
        subject: str | None = None, body_html: str | None = None,
        deployment_schedule: str | None = None, map_url: str | None = None,
    ) -> None:
        """Open an Outlook draft REPLY with the latest estimate PDF attached.
        Uses Outlook's ReplyAll on the source email so threading + all
        original parties are preserved. Also reachable from Ellen via the
        `draft_email_reply` action tool.
        """
        request = self.extraction_panel.request()
        if request is None:
            QMessageBox.information(
                self, "No email loaded",
                "Drop an email first — there's nothing to reply to yet.",
            )
            return
        # Warn if no estimate PDF yet — user can still draft a holding reply.
        if not (self._artifacts.get("estimate_pdf_path")):
            ans = QMessageBox.question(
                self, "No estimate PDF",
                "No estimate PDF has been captured yet (qchub order may not have "
                "been submitted). Open the draft without an attachment?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.status("Opening Outlook draft…")
        self.btn_email.setEnabled(False)
        run_email_draft(
            request, self._artifacts,
            to=to, cc=cc, subject=subject, body_html=body_html,
            deployment_schedule=deployment_schedule, map_url=map_url,
            on_finished=self._on_email_draft_finished,
            on_failed=self._on_email_draft_failed,
        )

    def _on_email_draft_finished(self, result) -> None:
        self.btn_email.setEnabled(True)
        self.status(f"Outlook draft opened: To {result.to}.")
        body = (
            f"Outlook draft opened — review and click <b>Send</b> when ready.<br/>"
            f"&nbsp;&nbsp;<b>To:</b> {result.to}<br/>"
            f"&nbsp;&nbsp;<b>Subject:</b> {result.subject}"
        )
        if result.attachment_path:
            ap = str(result.attachment_path).replace("\\", "/")
            from pathlib import Path as _P
            body += (
                f"<br/>&nbsp;&nbsp;<b>Attached:</b> "
                f"<a href='file:///{ap}'>{_P(result.attachment_path).name}</a>"
            )
        if result.note:
            body += f"<br/><i>{result.note}</i>"
        self.chat_panel.appendSystemNote(body)

    def _on_email_draft_failed(self, message: str) -> None:
        self.btn_email.setEnabled(True)
        self.status("Email draft failed.")
        QMessageBox.critical(
            self, "Couldn't open Outlook draft",
            f"{message}\n\n"
            "Make sure Outlook (classic) is installed and running. The new "
            "Outlook (olk.exe) doesn't expose the COM API needed for this.",
        )

    def on_open(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Open email", "", "Email files (*.eml *.msg);;All files (*)"
        )
        if path_str:
            self.on_file_dropped(Path(path_str))

    def on_import_from_outlook(self) -> None:
        if not self._has_api_key():
            QMessageBox.warning(
                self, "API key required",
                "No Anthropic API key saved. Open Settings to add one before extracting.",
            )
            return
        try:
            self.status("Reading selected email from Outlook…")
            path = import_selected_email()
        except NoSelection as exc:
            self.status("Outlook: no email selected.")
            QMessageBox.information(self, "No email selected", str(exc))
            return
        except OutlookUnavailable as exc:
            self.status("Outlook unavailable.")
            QMessageBox.warning(self, "Outlook unavailable",
                                f"{exc}\n\nMake sure Outlook is open and you have an email selected.")
            return
        self.on_file_dropped(path)

    def on_file_dropped(self, path: Path) -> None:
        if not self._has_api_key():
            QMessageBox.warning(
                self, "API key required",
                "No Anthropic API key saved. Open Settings to add one before extracting.",
            )
            return

        self.status(f"Extracting {path.name}…")
        self.drop_zone.setBusy(True, f"Extracting {path.name}…")
        for b in (self.btn_map, self.btn_qchub, self.btn_email):
            b.setEnabled(False)
        self.extraction_panel.setRequest(None)
        # Remember where the source .eml lives so Ellen's `read_attachment`
        # / `list_attachments` tools can re-parse it on demand to access
        # PDF/DOCX/KMZ contents that aren't carried on the StudyRequest.
        self._source_email_path = path
        run_extraction(
            path,
            on_started=lambda name: self._on_extraction_progress(f"Reading {name}…"),
            on_progress=self._on_extraction_progress,
            on_finished=self._on_extraction_finished,
            on_failed=self._on_extraction_failed,
        )

    def _on_extraction_progress(self, message: str) -> None:
        self.status(message)
        self.drop_zone.setBusy(True, message)

    def _on_extraction_finished(self, request: StudyRequest) -> None:
        self.drop_zone.setBusy(False)
        self.extraction_panel.setRequest(request)
        self.extraction_panel.setVisible(True)
        self.action_row.setVisible(True)
        n = request.total_locations
        self.status(f"Extracted {n} location{'s' if n != 1 else ''} from {request.email_subject!r}.")
        for b in (self.btn_map, self.btn_qchub, self.btn_email):
            b.setEnabled(True)
        self.act_export_kmz.setEnabled(True)
        self.act_export_kml.setEnabled(True)
        # Reset chat + artifacts for the new request. Suppress the welcome
        # banner since Ellen's warm-up turn (below) is now the opening
        # message — showing both creates redundancy.
        self._chat_history = []
        self._artifacts = {}
        self._qchub_edit_session = None
        # Make the source .eml path discoverable by Ellen's attachment-reading
        # tools (list_attachments, read_attachment, get_kmz_placemarks).
        if getattr(self, "_source_email_path", None) is not None:
            self._artifacts["source_email_path"] = str(self._source_email_path)
        self.chat_panel.clear(show_welcome=False)
        # Fire the warm-up turn: Ellen reads the email + glances at the request
        # BEFORE the user types anything, so her first visible message is a
        # scope summary instead of a generic welcome. The synthetic user
        # message goes into API history but is NOT rendered in the chat panel
        # (we never call appendUserMessage for it) — only Ellen's response
        # shows.
        if self._has_api_key():
            self._fire_warmup_turn(request)

    def _fire_warmup_turn(self, request: StudyRequest) -> None:
        """Fire a hidden first chat turn so Ellen's opening message is a
        scope summary, not a generic welcome. Called once per new email
        right after extraction completes.
        """
        warmup_message = (
            "An email was just dropped and the system extracted a StudyRequest. "
            "Before the user types anything, give them a brief opening that includes: "
            "(1) the client + project, (2) total location count broken down by study kind, "
            "(3) any heterogeneity worth flagging (mixed subtypes, mixed time windows, "
            "missing data), and (4) the one or two confirmations you'd want from them "
            "before firing anything. Read the email body first so your summary reflects "
            "what the client actually wrote, not just what the extractor captured. "
            "Stay tight — under 8 lines if you can. No greeting like 'Hi' — the welcome "
            "banner already handled that. Just dive in."
        )
        self.chat_panel.setBusy(True)
        run_chat(
            warmup_message,
            self._chat_history,
            request,
            self._artifacts,
            on_text_delta=self.chat_panel.appendAssistantDelta,
            on_tool_result=self.chat_panel.appendToolResult,
            on_action_request=self._on_chat_action_request,
            on_finished=self._on_chat_finished,
            on_failed=self._on_chat_failed,
            qchub_edit_session=getattr(self, "_qchub_edit_session", None),
        )

    def _on_extraction_failed(self, message: str) -> None:
        self.drop_zone.setBusy(False)
        self.status("Extraction failed.")
        self.notify("Extraction failed", _shorten(message, 200), urgent=True)
        QMessageBox.critical(self, "Extraction failed", message)

    def on_settings(self) -> None:
        dlg = SettingsDialog(self)
        dlg.exec()
        if self._has_api_key():
            self.status("Settings saved.")

    def _coming_soon(self) -> None:
        QMessageBox.information(
            self, "Coming soon",
            "This step is part of the next build — MyMaps automation, qchub order entry, "
            "and email drafting will land in upcoming updates.",
        )


def _safe_filename(s: str) -> str:
    bad = '<>:"/\\|?*\n\r\t'
    out = "".join("_" if ch in bad else ch for ch in s).strip()
    return out[:120] or "traffic_study"


def _shorten(s: str, max_chars: int) -> str:
    """Truncate a long message to fit in a toast notification."""
    s = (s or "").strip().replace("\n", " ").replace("\r", " ")
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Ellen")
    app.setOrganizationName("Quality Counts")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
