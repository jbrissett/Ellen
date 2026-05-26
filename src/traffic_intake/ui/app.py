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
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QSystemTrayIcon,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import config, kmz as kmz_module, kmz_rediff
from ..kml_export import build_kml, build_kmz
from ..models import StudyRequest
from ..mymaps import CreateMapResult
from ..qchub import CreateOrderResult
from .chat_panel import ChatPanel
from .drop_zone import DropZone
from .extraction_panel import ExtractionPanel
from .floating_reply import FloatingReplyWidget

# Floating reply widget is disabled 2026-05-25 after a hard Qt6Core
# access-violation crash (0xc0000005) was traced to the moment Ellen's
# warmup turn finished and the widget would attempt to construct/show
# from the chat-finished signal. Likely a Qt thread-safety issue with
# the worker→UI handoff; needs investigation before re-enabling.
# Set this back to True to restore the widget once the threading
# issue is properly fixed (would also need to be on a branch + stress-
# tested per test-ship-test). Tray notifications still fire either way.
FLOATING_REPLY_ENABLED = False
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

        # Accept drops anywhere on the window so the user can drop an
        # email onto the toolbar, the chat panel, the gap between
        # widgets, etc. — not just the narrow drop_zone widget. The
        # dragEnter/drop methods below delegate to drop_zone's existing
        # path-resolution + signal, so the rest of the app sees the same
        # event flow as before. Per user feedback 2026-05-25: the whole
        # left area should be the drop target, and the small drop_zone
        # target was missing some drops outright.
        self.setAcceptDrops(True)

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
        # Clicking the TOAST itself (not the tray icon) should also bring
        # Ellen forward. Without this wiring, the toast was visual-only —
        # user had to alt-tab manually after dismissing it. Established
        # 2026-05-26: "clicking the notification does not bring ellen to
        # the foreground."
        self._tray.messageClicked.connect(self._bring_to_front)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:
        """Bring the main window forward when the user clicks the tray icon."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._bring_to_front()

    def _is_user_focused_on_us(self) -> bool:
        """True iff the user is actively looking at our main window
        right now. Used to gate non-urgent toast notifications so they
        don't fire when the user is already watching the chat.

        On Windows we ask the OS directly via GetForegroundWindow() —
        Qt's isActiveWindow() and applicationState() both failed to
        catch real-world "user is in another app" cases (verified
        2026-05-26: both reported active when the user had alt-tabbed
        to Outlook). The native HWND comparison matches what the user
        actually sees.

        Minimized window → not focused (regardless of HWND check).
        Non-Windows → fall back to isActiveWindow().
        """
        if self.isMinimized():
            return False
        if sys.platform != "win32":
            return self.isActiveWindow()
        try:
            import ctypes
            fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
            our_hwnd = int(self.winId())
            return fg_hwnd == our_hwnd
        except Exception:
            # ctypes / shell32 unavailable in an exotic environment —
            # fall back to Qt's check.
            return self.isActiveWindow()

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
        always: bool = False,
    ) -> None:
        """Surface a Windows toast via the system tray icon. No-op if
        the tray isn't available. Always updates the status bar too so
        the info is visible without the toast.

        Toast gating: by default the toast only fires when the main
        window is UNFOCUSED. If the user is already looking at the
        window they don't need an OS-level interruption; the chat
        panel + status bar carry the same info. Set `always=True` to
        force the toast (rare — used for genuinely-urgent failures
        where we want attention even on the active window).
        Established 2026-05-26 user direction: "toast on every
        artifact-landed moment WHEN WINDOW UNFOCUSED."
        """
        self.status(message)
        if getattr(self, "_tray", None) is None:
            return
        # Urgent failures always toast — even if the window is active —
        # so the user notices red-flag conditions. Non-urgent toasts
        # only fire when the user isn't already watching our window.
        # First attempt (2026-05-26 AM) used isActiveWindow() — fired
        # toasts when user was already focused (Qt's "active window"
        # is per-process, not per-OS-foreground). Second attempt used
        # QApplication.applicationState() — still fired when focused
        # (user-reported, 2026-05-26 PM). The reliable check on
        # Windows is the OS-native GetForegroundWindow(): compare its
        # returned HWND to our window's HWND. No Qt indirection,
        # matches what the user actually sees.
        if not always and not urgent and self._is_user_focused_on_us():
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

        GATED on FLOATING_REPLY_ENABLED — currently False after a Qt
        crash 2026-05-25. The body below runs only when re-enabled;
        otherwise this is a no-op. Tray notifications still fire from
        the per-job handlers (_on_mymaps_finished, _on_qchub_finished,
        etc.) so the user isn't left in silence.
        """
        if not FLOATING_REPLY_ENABLED:
            return
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

        Also pins `setMinimumSize(800, 600)` so child widgets coming and
        going visible cannot raise the implicit window minimum and force
        Qt to grow the window. Per user direction 2026-05-25: window sizing
        is a USER concern; Ellen must never reshape herself.
        """
        # Absolute floor — applies whether or not we have a screen. Stops
        # the splitter's child minimums from leaking up to the window.
        self.setMinimumSize(800, 600)
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

    def sizeHint(self):
        """Override Qt's default sizeHint so child visibility changes cannot
        trigger an `adjustSize()` that reshapes the window.

        Default behavior: QMainWindow.sizeHint() asks the central widget for
        its preferred size; when a child becomes visible the preferred size
        grows; Qt's layout system then nudges the window to match. The user
        sees that as "Ellen shape-shifts depending on what state she's in."

        Override: before the first show(), return Qt's natural hint so the
        startup geometry is correct; after that, return the current size so
        adjustSize() is a no-op. The user can still drag the window edges —
        that path doesn't go through sizeHint().
        """
        if not self.isVisible():
            return super().sizeHint()
        return self.size()

    def _build_central(self) -> None:
        # Layout refactored 2026-05-26 per user direction: kill the bulky
        # 3-tab extraction inspector (Summary / Locations / JSON) — same
        # data is reachable via Ellen's tools, and the tabs were crowding
        # the chat. New shape is a thin fixed-width left column + chat
        # dominating the right.
        #
        # Left column (~320px):
        #   * DropZone — taller than before since the tabs are gone;
        #     visually communicates "big drop target" without claiming the
        #     whole window. Window-wide drop forwarding (MainWindow.dropEvent
        #     -> drop_zone) still lets you drop ANYWHERE including the chat.
        #   * Compact StatusSummary strip — one paragraph: project, location
        #     count, geocoding confidence, warnings.
        #   * Action buttons stacked VERTICALLY (the column is narrow).
        #
        # Right column: chat panel takes all remaining space.
        left = QWidget()
        # HARD CAP the left strip's width. Wide action-button labels
        # ("Create MyMaps map…") were forcing the splitter to give the
        # column ~half the window. Capping at 260px keeps the chat
        # dominant regardless of child size hints. User can still drag
        # the splitter wider if they really want to.
        left.setMaximumWidth(260)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)

        self.drop_zone = DropZone()
        self.drop_zone.fileDropped.connect(self.on_file_dropped)
        self.drop_zone.diagnosticMessage.connect(self.status)
        # Drop zone takes most of the vertical room — "big drop target" was
        # explicit user direction. stretch=1 with no competing stretchers
        # below means it absorbs all remaining vertical space.
        left_layout.addWidget(self.drop_zone, 1)

        # Compact status replaces the tabs. Same setRequest/request API.
        self.extraction_panel = ExtractionPanel()
        left_layout.addWidget(self.extraction_panel, 0)

        self.action_row = self._build_action_row()
        left_layout.addWidget(self.action_row, 0)

        # Right pane: chat sidebar (persistent — always visible)
        self.chat_panel = ChatPanel()
        self.chat_panel.sendRequested.connect(self.on_chat_send)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.chat_panel)
        # Left column: pinned narrow so the chat dominates. setStretchFactor
        # 0:1 means the LEFT keeps its size hint and the chat eats every
        # extra pixel of window width. Users can still drag the splitter to
        # widen the left if they want; the default is chat-heavy.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 1160])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        # Belt-and-suspenders: tell the splitter "don't push your sizeHint
        # up to the window." Combined with sizeHint() override above, this
        # makes child visibility / content changes inert at the window level.
        # `Ignored` is right here — the splitter still fills whatever space
        # the window gives it, but never asks for more.
        splitter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

        self.setCentralWidget(splitter)

    def _build_action_row(self) -> QWidget:
        # Compact icon row — three small buttons that fit horizontally in
        # the narrow (260px) left strip. Glyphs are Unicode pictographs
        # rendered with the system's color-emoji font (Windows 11 ships
        # Segoe UI Emoji). Tooltips carry the full action labels for
        # discoverability; the visible glyph is the "small icon" the user
        # asked for 2026-05-26.
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        def _icon_btn(glyph: str, tooltip: str, handler) -> QPushButton:
            btn = QPushButton(glyph)
            btn.setToolTip(tooltip)
            btn.setEnabled(False)
            btn.setFixedSize(56, 40)
            f = btn.font()
            f.setPointSize(16)
            btn.setFont(f)
            btn.clicked.connect(handler)
            return btn

        self.btn_map = _icon_btn("🗺", "Create MyMaps map", self.on_create_map)
        self.btn_qchub = _icon_btn("📋", "Create qchub order", self.on_create_qchub_order)
        self.btn_email = _icon_btn("✉", "Draft response email", self.on_draft_email)
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
        # extraction_panel and action_row stay visible — only their content
        # resets. See _build_central for why we no longer toggle visibility.
        self.extraction_panel.setRequest(None)
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
        # Trace event: marks the moment the user clicked Send. The next
        # `ui.worker_started` event tells us how long Qt event loop + UI
        # validation took; `chat.first_api_call_end` (from usage_tracker)
        # tells us how long the API call itself took including TLS / cold
        # start. Together they quantify the "ghost gap" between user
        # send and Ellen's first visible response token.
        from .. import trace_log
        trace_log.event("ui.send_clicked", msg_len=len(message or ""))

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
        trace_log.event("ui.worker_started", phase="chat")
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
        # Notify when Ellen's turn produced a new estimate PDF version.
        # Track the last-notified PDF path on self; when artifacts
        # show a different one, the user has a fresh PDF in Downloads
        # worth flagging. Established 2026-05-26: post-edit recap PDFs
        # were silent, the user had to hunt for the new file in
        # Downloads rather than getting a toast.
        pdf_notified_this_turn = False
        current_pdf = self._artifacts.get("estimate_pdf_path")
        last_pdf = getattr(self, "_last_notified_pdf", None)
        if current_pdf and current_pdf != last_pdf:
            from pathlib import Path as _P
            self.notify(
                "Estimate PDF updated",
                f"New version saved: {_P(current_pdf).name}. Open it in Downloads.",
            )
            self._last_notified_pdf = current_pdf
            pdf_notified_this_turn = True

        # Notify when Ellen ends her turn with a question / request for
        # user input. Heuristic: the last assistant text ends with '?'
        # (after stripping trailing whitespace). Same detection logic
        # the floating-reply widget uses; this is the toast-equivalent
        # while that widget is disabled. Skip if we already toasted for
        # a new PDF in this same turn — one ping per turn is enough.
        if not pdf_notified_this_turn:
            last_text = self._extract_last_assistant_text().rstrip()
            if last_text and last_text.endswith("?"):
                # Trim to a one-liner toast preview.
                preview = _shorten(last_text.split("\n")[-1].strip(), 140)
                self.notify("Ellen has a question", preview)
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
        # Reset the progress-forwarding gate so this new run's pre-ready
        # log messages reach drop_zone busy state. _on_qchub_finished will
        # set this back to True when the order submits.
        self._qchub_ready_emitted = False
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
        # Stop re-busying the drop_zone after the order is ready. The qchub
        # worker keeps logging through the post-submit manual-finish window
        # (every edit-session call, the eventual "End-session requested by
        # Ellen — closing qchub browser") and each log fired this callback,
        # re-setting drop_zone status. When end_session triggered the worker
        # to exit, the LAST log line ("End-session requested...") stayed
        # pinned in the busy state with no finalize signal to clear it.
        # Fix: only forward to drop_zone busy state UNTIL the order is
        # marked ready; subsequent logs go to the status bar only.
        # Established 2026-05-26 user report: drop_zone stuck on
        # "End-session/closing qchub" after qchub had already closed.
        if not getattr(self, "_qchub_ready_emitted", False):
            self.drop_zone.setBusy(True, message)

    def _on_qchub_finished(self, result: CreateOrderResult) -> None:
        self._qchub_ready_emitted = True
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
            # estimate_total intentionally NOT exposed to Ellen — per user
            # direction 2026-05-25 the chat surface must never carry the
            # grand total (the user reads it from the PDF). Same reason
            # _scrub_dollars_from_estimate_result strips per-line dollars
            # before tool results return.
            self._artifacts["estimate_line_count"] = len(result.estimate.lines)
            # Keep the full structured estimate too (Ship 2's get_estimate
            # tool will read this), but scrub dollar fields so Ellen can't
            # quote them. The scrubber rewrites `total`, `line_total`,
            # `unit_price`, `quantity`, and `extra_rate` to "<see PDF>"
            # both at the top level (the Estimate's grand `total`) and
            # nested inside each line dict.
            from ..chat import _scrub_dollars_from_estimate_result
            self._artifacts["estimate"] = _scrub_dollars_from_estimate_result(
                result.estimate.model_dump(exclude_none=True)
            )

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
            # No dollar total in the chat system note — user opens the PDF
            # for the number. Per feedback 2026-05-25: Ellen has been wrong
            # about totals on multiple prior runs, and the user always
            # opens the PDF anyway, so the chat surface adds risk with
            # zero upside. Keep the line-count for context.
            summary = f"&nbsp;&nbsp;<b>Estimate:</b> {len(est.lines)} line(s)"
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
            else:
                # No PDF yet — initial capture deferred so Ellen's first
                # re_capture produces the ONE PDF (user 2026-05-26 wanted
                # to avoid the v1+v2 clutter). Ellen will produce it
                # shortly via the synthetic post-qchub turn; user sees the
                # PDF link land in the chat then.
                parts.append("&nbsp;&nbsp;<i>Estimate PDF coming after Ellen applies any pending changes…</i>")
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

        # Fire a synthetic post-qchub turn so Ellen actually acts on any
        # pending pre-submit pricing/subtype instructions instead of just
        # idling until the user reminds her. Same pattern as
        # `_fire_warmup_turn` after extraction. Established 2026-05-26
        # run-20260526-120226: Ellen acknowledged "both are Large"
        # pre-submit, qchub submitted, the system note explicitly told
        # her the modal was open and to apply pending instructions —
        # she still wrote "I'll apply when the modal confirms open" and
        # sat there until the user re-asked. The synthetic turn forces
        # her to respond now, with the modal genuinely open, while her
        # acknowledgment of the user's instruction is still fresh in
        # her conversation history.
        if (
            self._has_api_key()
            and getattr(self, "_qchub_edit_session", None) is not None
            and result.estimate is not None
            and result.estimate.lines
        ):
            self._fire_post_qchub_turn(result)

    def _fire_post_qchub_turn(self, result: "CreateOrderResult") -> None:
        """Synthetic chat turn fired right after the qchub completion
        system-note posts. Tells Ellen the modal is open, asks her to
        scan her own conversation history for pending instructions, and
        apply them now via the batched edit pattern.
        """
        order_id = result.order_id or "(no ID captured)"
        line_count = len(result.estimate.lines) if result.estimate else 0
        message = (
            f"[SYSTEM] qchub order {order_id} just submitted successfully. "
            f"The estimate modal is OPEN with {line_count} parsed line(s). "
            "ALL edit tools (set_estimate_subtype, set_estimate_rate, "
            "apply_rate_to_location, apply_rate_to_all_locations, "
            "apply_rate_to_rest_of_group, re_capture_estimate) are LIVE "
            "RIGHT NOW. The qchub_edit_session is wired — do not hedge "
            "with 'when the modal is up' / 'as soon as it confirms open' / "
            "etc. IT IS UP.\n\n"
            "IMPORTANT: the initial capture deliberately DID NOT download "
            "a PDF (to avoid producing two PDFs — pre-edit v1 + post-edit "
            "v2). Your call to re_capture_estimate produces the FIRST and "
            "ONLY PDF (saved as Estimate_N.pdf, no version suffix). You "
            "MUST call re_capture_estimate at least once before closing "
            "— without it, the user gets no PDF in Downloads at all.\n\n"
            "Scan THIS conversation's prior turns for any pricing or "
            "subtype instructions the user has stated (e.g., 'all are "
            "Large', '$320 per TMC', 'the roundabout is Complex'). For "
            "EACH such instruction:\n"
            "  1. Fire the relevant edit tool calls in THIS response "
            "(parallel — batch them; per the BATCH MULTI-ROW rule).\n"
            "  2. Then in your NEXT response, call re_capture_estimate "
            "exactly ONCE (this produces the user's PDF).\n"
            "  3. Then post the close: '[concise summary of what landed]. "
            "Anything else?'\n\n"
            "If there are NO pending instructions: call re_capture_estimate "
            "now (single tool call this turn), then post the close in your "
            "next turn: 'Order N submitted, estimate PDF in Downloads. "
            "Anything else?'. Do NOT invent edits to justify the recap.\n\n"
            "Important qchub quirk: the estimate modal renders TMC rows "
            "at 'Standard' by default REGARDLESS of what subtype was "
            "set at the group level pre-submit. If the user has "
            "indicated a non-Standard subtype (Large / Complex) and "
            "you can see TMC rows that need updating, that's a pending "
            "instruction — apply it now even though you 'already set' "
            "the subtype on the StudyLocation pre-submit. Those are "
            "two different state surfaces."
        )
        self.chat_panel.setBusy(True)
        run_chat(
            message,
            self._chat_history,
            self.extraction_panel.request(),
            self._artifacts,
            on_text_delta=self.chat_panel.appendAssistantDelta,
            on_tool_result=self.chat_panel.appendToolResult,
            on_action_request=self._on_chat_action_request,
            on_finished=self._on_chat_finished,
            on_failed=self._on_chat_failed,
            qchub_edit_session=getattr(self, "_qchub_edit_session", None),
        )

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
        self.notify(
            "Email draft ready",
            f"Outlook draft opened — to {result.to}. Review and click Send when ready.",
        )
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

    # ----- window-wide drop forwarding -----
    # The drop_zone widget knows how to resolve a drop (URL list, Outlook
    # FileGroupDescriptor, etc.) and emits fileDropped on success. We
    # accept drops at the window level too so any drop anywhere — toolbar
    # gap, chat panel, status bar, you name it — reaches the same code
    # path. Without this, drops outside the drop_zone's bounding rect
    # were silently ignored, which the user reported as "drops aren't
    # working all of a sudden" on 2026-05-25.

    def dragEnterEvent(self, event):
        # Delegate to the drop_zone's accept logic so the criteria stays
        # in one place (file URLs OR Outlook FGD). drop_zone also flips
        # its hover style for visual feedback.
        self.drop_zone.dragEnterEvent(event)

    def dragMoveEvent(self, event):
        # Keep accepting throughout the drag; without this Qt re-checks
        # acceptance on each move and can flip to "denied" cursor as the
        # user moves across child widgets. Uses drop_zone's canonical
        # FGD format list so the criteria stays in one place.
        from .drop_zone import FGD_MIME_CANDIDATES
        mime = event.mimeData()
        if mime.hasUrls() or any(mime.hasFormat(f) for f in FGD_MIME_CANDIDATES):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_zone.dragLeaveEvent(event)

    def dropEvent(self, event):
        # Reuse drop_zone's resolve + emit so the rest of the app sees
        # the same fileDropped signal it always has.
        self.drop_zone.dropEvent(event)

    def on_file_dropped(self, path: Path) -> None:
        if not self._has_api_key():
            QMessageBox.warning(
                self, "API key required",
                "No Anthropic API key saved. Open Settings to add one before extracting.",
            )
            return

        # KMZ/KML re-drop fast path: when the user drops a KMZ that came
        # FROM Ellen's own MyMaps export (detected via the ellen_loc_id
        # ExtendedData marker) AND a StudyRequest is currently active,
        # treat it as an edit-map re-drop instead of new-job extraction.
        # Otherwise fall through to the existing email/KMZ extraction path
        # so third-party KMZs (or new-job KMZs) keep working unchanged.
        if path.suffix.lower() in (".kmz", ".kml") and self.extraction_panel.request() is not None:
            if self._try_handle_kmz_rediff(path):
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
        # No visibility toggle — both panels stay visible at all times.
        # setRequest swaps the extraction panel's inner state from
        # "empty placeholder" to "tabs with data"; the action row just
        # enables its buttons below.
        self.extraction_panel.setRequest(request)
        n = request.total_locations
        subj_short = _shorten(request.email_subject or "(no subject)", 60)
        # "Pre-run done" framing per user direction 2026-05-26 — the
        # toast lands the moment extraction + geocoding completes (this
        # IS the pre-run). Ellen's warmup summary follows ~10s later;
        # if she ends with a question, the chat-finished handler fires
        # its own "Ellen has a question" toast.
        self.notify(
            "Pre-run done",
            f"{n} location{'s' if n != 1 else ''} extracted from {subj_short!r}. Ready for review.",
        )
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

    def _try_handle_kmz_rediff(self, path: Path) -> bool:
        """If `path` is a re-drop of Ellen's exported MyMaps KMZ, apply the
        unambiguous edits (coord moves, renames) directly to the active
        StudyRequest and surface a chat turn for the rest. Returns True when
        the file was handled as a rediff, False to fall through to extraction.
        """
        try:
            data = path.read_bytes()
            if path.suffix.lower() == ".kmz":
                placemarks = kmz_module.parse_kmz_bytes(data)
            else:  # .kml
                placemarks = kmz_module.parse_kml_bytes(data)
        except Exception as exc:
            # Parse failure isn't a rediff — let the regular extraction
            # pipeline complain about it instead of silently dropping the file.
            self.status(f"Couldn't parse {path.name} as KMZ ({exc}); falling through.")
            return False

        if not kmz_rediff.is_rediff_candidate(placemarks):
            # No ellen_loc_id markers — not from Ellen's export. Could be a
            # third-party KMZ the user wants to attach; let extraction handle it.
            return False

        request = self.extraction_panel.request()
        assert request is not None  # guarded by the call site
        diff = kmz_rediff.compute_rediff(request, placemarks)

        if not diff.has_changes:
            self.chat_panel.appendSystemNote(
                "Re-drop matched the active map exactly — no edits detected."
            )
            return True

        # Apply the unambiguous edits in place. The display will refresh below.
        moves, renames = kmz_rediff.apply_unambiguous(request, diff)
        self.extraction_panel.setRequest(request)
        self.status(
            f"Applied {moves} move(s) and {renames} rename(s) from {path.name}."
        )

        # Build the rich human-facing detail block for Ellen's synthetic turn.
        # She'll narrate it back to the user and ask about new/missing pins.
        applied_lines: list[str] = []
        for m in diff.moved:
            applied_lines.append(
                f"  - Location[{m.loc_id}] {m.site_name!r}: moved {m.distance_m:.0f}m "
                f"(now at {m.new_lat:.5f},{m.new_lng:.5f})"
            )
        for r in diff.renamed:
            applied_lines.append(
                f"  - Location[{r.loc_id}]: renamed {r.old_name!r} → {r.new_name!r} "
                "(applied to BOTH site_name and address_or_intersection)"
            )
        pending_lines: list[str] = []
        for n in diff.new:
            pending_lines.append(
                f"  - NEW pin {n.name!r} at {n.latitude:.5f},{n.longitude:.5f} — "
                "needs study_kind, subtype, time_windows, and group context "
                "before add_locations"
            )
        for mp in diff.missing:
            pending_lines.append(
                f"  - REMOVED pin Location[{mp.loc_id}] {mp.site_name!r} — "
                "confirm with user before calling remove_locations"
            )

        message_parts: list[str] = [
            f"[SYSTEM] The user just dropped an edited KMZ ({path.name}). "
            f"Re-drop diff: {moves} move(s), {renames} rename(s), "
            f"{len(diff.new)} new pin(s), {len(diff.missing)} removed pin(s), "
            f"{diff.unchanged_count} unchanged.",
        ]
        if applied_lines:
            message_parts.append(
                "Already applied to the StudyRequest (no action needed from you):"
            )
            message_parts.extend(applied_lines)
        if pending_lines:
            message_parts.append(
                "Pending — please walk the user through these and apply via "
                "add_locations / remove_locations after they respond:"
            )
            message_parts.extend(pending_lines)
        else:
            message_parts.append(
                "No pending items. Acknowledge the edits and ask whether to "
                "proceed with the qchub order."
            )
        message_parts.append(
            "Keep your reply tight (under 8 lines). Don't dump the raw coords back "
            "at the user — they just edited them. State what landed, then ask the "
            "specific question for any pending items."
        )

        rediff_message = "\n".join(message_parts)
        self.chat_panel.setBusy(True)
        run_chat(
            rediff_message,
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
        return True

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
            "missing data), (4) geocoding confidence — call get_request and inspect each "
            "location's `estimate.confidence` and `estimate.source`. Group results: how "
            "many came back high vs medium vs low confidence (or no estimate). If ANY "
            "location is medium / low / missing, list those by site name in your opening "
            "so the user knows which map pins to verify before sending — accuracy on these "
            "is critical (one bad pin = a returned-trip cost). (5) Then a forward-looking "
            "question — 'Want me to proceed with the map and qchub order, or adjust "
            "anything first?'. Read the email body first so your summary reflects what "
            "the client actually wrote, not just what the extractor captured. "
            "Stay tight — under 10 lines if you can. No greeting like 'Hi' — the welcome "
            "banner already handled that. Just dive in. "
            "REMINDER: this is the OPENING summary turn, NOT a task-completion turn. Close "
            "with the forward-looking action question above — do NOT use 'anything else?' "
            "here (that phrase is reserved for AFTER a deliverable lands)."
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
