"""Always-on-top frameless reply widget — surfaces when Ellen asks a
question while the main app is not in foreground.

Why this exists (user direction 2026-05-24): "if ellen has an ask the
notification/banner/chat pop-up should allow the user to reply directly
in it and not require them to find the application window to respond."

Behavior:
- Frameless, always-on-top, sits at bottom-right of the primary screen
  (above the system tray area).
- Shows Ellen's question + a text input + Send button.
- Send fires a callback that the main app routes through the existing
  chat send path — Ellen sees it as a normal user message.
- Escape, X button, or sending dismisses the widget. Reopening on the
  next Ellen question creates a fresh instance.

Heuristic for "Ellen has a question": main window has a question pending
AND the app is not in foreground. The trigger logic lives in app.py
(_on_chat_finished) — this widget only renders + emits.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# Brand palette — matches splash. Subordinate to the splash; this is a
# transient pop-up so it should look like Ellen, not like a system UI.
_BG = "#FCFCFD"
_BORDER = "#1B47CD"   # QC brand blue
_NAVY = "#1A2540"
_INK = "#1A1A1A"
_DUSK = "#6E6E7A"


class FloatingReplyWidget(QFrame):
    """Pop-up reply prompt that lives outside the main app window.

    Construct with the question text + a `on_send(reply_text)` callback.
    The widget shows itself; the caller doesn't need to wire visibility.
    Closes automatically on send / Escape / X.
    """

    def __init__(
        self,
        question: str,
        on_send: Callable[[str], None],
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        # Window flags must be set after init for QFrame (the
        # constructor doesn't accept `flags=` as a kwarg the way
        # QWidget does). Tool = no taskbar entry; frameless + on-top
        # so the popup behaves like a notification rather than a
        # secondary main window.
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._on_send = on_send
        self.setObjectName("FloatingReply")
        self.setStyleSheet(
            f"""
            QFrame#FloatingReply {{
                background-color: {_BG};
                border: 2px solid {_BORDER};
                border-radius: 8px;
            }}
            QLabel#Header {{
                color: {_NAVY};
                font-family: 'Segoe UI';
                font-size: 13px;
                font-weight: bold;
            }}
            QLabel#Question {{
                color: {_INK};
                font-family: 'Segoe UI';
                font-size: 12px;
            }}
            QLabel#Hint {{
                color: {_DUSK};
                font-family: 'Segoe UI';
                font-size: 10px;
                font-style: italic;
            }}
            QPushButton {{
                background-color: {_BORDER};
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
                font-family: 'Segoe UI';
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #1739a8; }}
            QPushButton#Close {{
                background-color: transparent;
                color: {_DUSK};
                font-size: 14px;
                font-weight: normal;
                padding: 2px 6px;
            }}
            QLineEdit {{
                border: 1px solid #cccccc;
                border-radius: 4px;
                padding: 6px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }}
            """
        )
        # Fixed-ish size — enough room for a one-sentence question and a
        # short reply. Use sizeHint() for height after content settles.
        self.setFixedWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        # --- Header row: "Ellen has a question" + close X ---
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        header_label = QLabel("Ellen has a question")
        header_label.setObjectName("Header")
        header_row.addWidget(header_label, 1)
        close_btn = QPushButton("×")
        close_btn.setObjectName("Close")
        close_btn.setFixedWidth(28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        header_row.addWidget(close_btn)
        outer.addLayout(header_row)

        # --- Question text ---
        # Surface only the actual question(s) — Ellen's turn often
        # opens with several lines of summary before getting to what
        # she needs from the user. Caller passed the full assistant
        # message; we extract just the '?' sentences for display.
        q_label = QLabel(_extract_questions(question))
        q_label.setObjectName("Question")
        q_label.setWordWrap(True)
        outer.addWidget(q_label)

        # --- Reply input + send ---
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type your reply…")
        self._input.returnPressed.connect(self._send_clicked)
        input_row.addWidget(self._input, 1)
        send_btn = QPushButton("Send")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.clicked.connect(self._send_clicked)
        input_row.addWidget(send_btn)
        outer.addLayout(input_row)

        # --- Hint ---
        hint = QLabel("Press Enter to send, Esc to dismiss.")
        hint.setObjectName("Hint")
        outer.addWidget(hint)

        # Position bottom-right of primary screen, with margin above the
        # taskbar / system tray.
        self.adjustSize()
        self._position_bottom_right(margin=24)

        # Pull focus to the input so the user can type immediately.
        self._input.setFocus()

    # ---------- public API ----------

    def show_to_user(self) -> None:
        """Show + raise + activate so it's the keyboard-focused window
        even when the main app is in the background."""
        self.show()
        self.raise_()
        self.activateWindow()

    # ---------- internals ----------

    def _send_clicked(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        try:
            self._on_send(text)
        finally:
            # Close regardless of callback success — the user thinks they
            # sent the reply and shouldn't see the widget linger.
            self.close()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def _position_bottom_right(self, *, margin: int) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()  # excludes taskbar on Windows
        x = avail.x() + avail.width() - self.width() - margin
        y = avail.y() + avail.height() - self.height() - margin
        self.move(QPoint(x, y))


def _extract_questions(text: str, max_chars: int = 500) -> str:
    """Pull just the question(s) out of Ellen's assistant message.

    Why this exists (user direction 2026-05-24): Ellen often opens with
    a summary paragraph or two before asking what she actually needs.
    Showing the full message in the floating popup buried the questions
    below the fold — user had to find the app window to see what was
    being asked, defeating the popup's purpose.

    Strategy:
      1. Strip basic markdown (`**bold**`, `*italic*`, `` `code` ``)
         since QLabel renders the raw asterisks otherwise.
      2. Split on sentence boundaries (.!?) and pick sentences ending
         in '?'.
      3. If no '?' sentences, fall back to the LAST paragraph — that's
         where Ellen's question tends to live when she phrases it as
         a directive ("Confirm the dates work for you.") rather than
         a literal question.

    Returned text is plain-text (no markdown) and capped at max_chars
    with an ellipsis if needed.
    """
    if not text:
        return ""
    cleaned = _strip_markdown(text)

    # Sentence split. Lookbehind keeps the punctuation attached.
    import re
    sentences = re.split(r"(?<=[.!?])\s+", cleaned.strip())
    questions = [s.strip() for s in sentences if s.strip().endswith("?")]
    if questions:
        joined = " ".join(questions)
        if len(joined) <= max_chars:
            return joined
        return joined[: max_chars - 1].rstrip() + "…"

    # No literal questions — use the last non-empty paragraph as a
    # heuristic for "where she's getting at the ask."
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    if paragraphs:
        tail = paragraphs[-1]
        if len(tail) <= max_chars:
            return tail
        return tail[: max_chars - 1].rstrip() + "…"
    # Fallback: just the first max_chars of the message.
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _strip_markdown(s: str) -> str:
    """Strip the markdown markers Ellen tends to emit (bold/italic
    asterisks, single backticks). Plain-text result for QLabel display.
    Order matters: bold (**) before italic (*) so we don't half-strip.
    """
    import re
    out = s
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)  # **bold**
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", out)  # *italic*
    out = re.sub(r"`([^`]+)`", r"\1", out)       # `code`
    out = re.sub(r"_([^_\n]+)_", r"\1", out)     # _italic_ (alt)
    return out
