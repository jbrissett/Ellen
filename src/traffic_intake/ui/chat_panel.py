"""Persistent chat sidebar — talk to Claude about the extracted StudyRequest.

Phase 1: review/refine state. Action triggering and qchub mid-step intervention
land in later phases.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,  # used for the sidebar title
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class ChatPanel(QWidget):
    """Right-sidebar chat. Emits sendRequested(text) when the user submits a message.
    The owning window runs the worker and feeds the panel via appendAssistantDelta,
    appendToolResult, and finishAssistantTurn.
    """

    sendRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel("<b>Ellen</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setToolTip("Ellen — your traffic-intake assistant")
        layout.addWidget(title)

        self.history = QTextBrowser()
        self.history.setReadOnly(True)
        self.history.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        # Decline file drops so they propagate up to MainWindow's window-wide
        # drop handler instead of being silently swallowed as "insert URL as
        # text." Both QTextBrowser and QLineEdit default to acceptDrops=True
        # for text drops — for a chat panel that should never accept any
        # external drop (file or text), we explicitly opt out. Established
        # 2026-05-26 after user asked why drops on the chat area didn't work.
        self.history.setAcceptDrops(False)
        # Open links via QDesktopServices instead of the built-in handler so
        # file:// paths open in Explorer correctly on Windows.
        self.history.setOpenLinks(False)
        self.history.setOpenExternalLinks(False)
        self.history.anchorClicked.connect(self._on_anchor_clicked)
        # Bump chat font to 12pt — Qt's default (~9pt on Windows) was
        # too small for comfortable reading per user direction 2026-05-24.
        # Applied to the whole panel so input + history + tool results
        # all share the same readable size.
        chat_font = self.font()
        chat_font.setPointSize(12)
        self.history.setFont(chat_font)
        layout.addWidget(self.history, 1)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Tell me what to change — e.g. 'make all TMC sites Large'…"
        )
        self.input.setFont(chat_font)
        self.input.returnPressed.connect(self._on_send)
        # Decline file drops here too — see the QTextBrowser comment above.
        # Without this, dragging an .eml over the input field showed the
        # text-insert cursor but never reached the window-level handler.
        self.input.setAcceptDrops(False)
        self.btn_send = QPushButton("Send")
        self.btn_send.setFont(chat_font)
        self.btn_send.clicked.connect(self._on_send)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.btn_send)
        layout.addLayout(input_row)

        # Internal flag — disable send while a turn is in flight.
        self._busy = False
        self._assistant_started = False
        # "Ellen is thinking…" indicator lives inline in the chat history
        # (not below the input bar). _thinking_anchor_pos records the
        # cursor position just BEFORE the indicator is inserted; on any
        # subsequent append (or on setBusy(False)), we remove everything
        # from that position to the end of the document, deleting the
        # indicator cleanly.
        self._thinking_anchor_pos: int | None = None

        self._show_welcome()

    # ----- public API used by the owning window -----

    def setBusy(self, busy: bool) -> None:
        self._busy = busy
        self.input.setEnabled(not busy)
        self.btn_send.setEnabled(not busy)
        if busy:
            self._show_thinking()
        else:
            self._remove_thinking_if_present()

    def clear(self, *, show_welcome: bool = True) -> None:
        self.history.clear()
        self._assistant_started = False
        self._thinking_anchor_pos = None
        if show_welcome:
            self._show_welcome()

    def appendUserMessage(self, text: str) -> None:
        self._remove_thinking_if_present()
        self._append("\n", style=None)
        self._append("You: ", style="user_label")
        self._append(text + "\n", style="user_body")
        self._assistant_started = False
        self._scroll_to_bottom()

    def appendAssistantDelta(self, chunk: str) -> None:
        """Append a streaming text chunk from Ellen."""
        self._remove_thinking_if_present()
        if not self._assistant_started:
            self._append("\nEllen: ", style="assistant_label")
            self._assistant_started = True
        self._append(chunk, style="assistant_body")
        self._scroll_to_bottom()

    def appendToolResult(self, tool_name: str, result: str) -> None:
        """Tool calls happen behind the scenes — don't clutter the chat with
        per-tool feedback. The model still sees the tool result inside the
        agent loop. Errors are surfaced through Ellen's natural-language reply.
        Kept as a no-op so existing signal wiring stays connected.
        """
        return

    def appendSystemNote(self, html: str) -> None:
        """Post an action-result note rendered as if Ellen reported it.
        Accepts a small HTML fragment so URLs and file paths render clickable.
        """
        self._remove_thinking_if_present()
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        # Bold "Ellen:" prefix, then the HTML body on the same paragraph.
        cursor.insertHtml(
            f"<br/><b>Ellen:</b> {html}<br/>"
        )
        self._assistant_started = False
        self._scroll_to_bottom()

    def finishAssistantTurn(self) -> None:
        """Mark the current assistant turn as complete (next chunk starts a new one)."""
        if self._assistant_started:
            self._append("\n", style=None)
        self._assistant_started = False
        self._scroll_to_bottom()

    # ----- internals -----

    def _on_send(self) -> None:
        if self._busy:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.appendUserMessage(text)
        self.input.clear()
        self.sendRequested.emit(text)

    def _show_welcome(self) -> None:
        self._append(
            "Hi — I'm Ellen. Drop an email on the left and I'll help you review,\n"
            "adjust, and run the deliverables. A few things I can do:\n"
            "  • 'list the sites'\n"
            "  • 'make all TMC sites Large'\n"
            "  • 'use AM 7–9 and PM 4–6 for everything'\n"
            "  • 'set jurisdiction to Charlotte County, FL'\n"
            "  • 'looks good — make the map'\n"
            "  • 'now create the qchub order'\n",
            style="hint",
        )

    def _append(self, text: str, *, style: str | None) -> None:
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        if style == "user_label":
            fmt.setFontWeight(700)
        elif style == "assistant_label":
            fmt.setFontWeight(700)
        elif style == "tool":
            fmt.setForeground(Qt.GlobalColor.darkGray)
            f = fmt.font()
            f.setItalic(True)
            fmt.setFont(f)
        elif style == "hint":
            fmt.setForeground(Qt.GlobalColor.darkGray)
            f = fmt.font()
            f.setItalic(True)
            fmt.setFont(f)
        cursor.setCharFormat(fmt)
        cursor.insertText(text)

    def _show_thinking(self) -> None:
        """Insert 'Ellen is thinking…' at the end of the chat history as
        an italic gray paragraph. Records the cursor position immediately
        before it so we can delete it cleanly later.
        """
        # Idempotent — if already showing, leave it alone.
        if self._thinking_anchor_pos is not None:
            return
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._thinking_anchor_pos = cursor.position()
        cursor.insertHtml(
            '<br/><span style="color: gray; font-style: italic;">Ellen is thinking…</span>'
        )
        self._scroll_to_bottom()

    def _remove_thinking_if_present(self) -> None:
        """Delete the inline thinking indicator if it's at the end of the
        document. No-op if not shown. Called before any append so the
        indicator is replaced by real content, and from setBusy(False)
        as a safety net.
        """
        if self._thinking_anchor_pos is None:
            return
        cursor = self.history.textCursor()
        cursor.setPosition(self._thinking_anchor_pos)
        cursor.movePosition(
            QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor
        )
        cursor.removeSelectedText()
        self._thinking_anchor_pos = None

    def _scroll_to_bottom(self) -> None:
        sb = self.history.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_anchor_clicked(self, url) -> None:
        """Open clicked URLs in the user's default browser. For file:// paths
        this opens File Explorer at that location on Windows."""
        QDesktopServices.openUrl(url)
