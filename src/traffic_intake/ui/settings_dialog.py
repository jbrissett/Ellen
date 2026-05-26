"""Settings dialog — user-specific credentials + preferences.

Refactored 2026-05-26 from the wider "every API key as a field" panel
into a focused dialog. The shared API keys (Anthropic, Google,
HERE) are baked at install time and resolved via config.py's
env > keyring > baked fallback; users don't enter them here. What
remains is genuinely per-user state:

  - qchub login (per-user QC credentials)
  - Google MyMaps sign-in (per-user; opens a visible Edge for
    one-time Google sign-in, then the shared browser profile
    persists the session for every future MyMaps run)
  - Headless toggle (per-user preference)

The chat-model dropdown and the "Show MyMaps confirmation" checkbox
were both removed — model is fixed to the "auto" fallback chain, and
the MyMaps confirm dialog is gone (Google sign-in now lives here in
Settings instead).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import config


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Ellen — Settings")
        self.resize(560, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Per-user credentials and preferences. Stored in Windows "
            "Credential Manager (never leave this machine)."
        ))

        form = QFormLayout()
        layout.addLayout(form)

        # ----- qchub login -----
        self.qchub_user_input = QLineEdit()
        self.qchub_pass_input = QLineEdit()
        self.qchub_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        creds = config.get_qchub_credentials()
        if creds:
            self.qchub_user_input.setText(creds[0])
            self.qchub_pass_input.setPlaceholderText("(saved) — paste a new password to overwrite")
        form.addRow("qchub username:", self.qchub_user_input)
        form.addRow("qchub password:", self.qchub_pass_input)

        # ----- Google MyMaps sign-in -----
        # Visual divider before the Google section so it doesn't feel
        # like it's part of qchub.
        layout.addSpacing(8)
        google_header = QLabel("<b>Google account (for MyMaps)</b>")
        layout.addWidget(google_header)
        google_note = QLabel(
            "MyMaps requires a Google sign-in. Click below to open Edge to "
            "Google's sign-in page; the saved session is shared with every "
            "future MyMaps run (headless or visible)."
        )
        google_note.setWordWrap(True)
        google_note.setStyleSheet("color:#666;")
        layout.addWidget(google_note)

        # Status row: current account + last sign-in timestamp.
        signin_settings = QSettings("Quality Counts", "Traffic Intake")
        saved_email = config.get_google_mymaps_email()
        last_signin_ts = signin_settings.value("google_signin_last_attempted_at", "", type=str)
        status_text = self._format_google_status(saved_email, last_signin_ts)
        self.google_status_label = QLabel(status_text)
        self.google_status_label.setStyleSheet("padding:4px 0;")
        layout.addWidget(self.google_status_label)

        google_btn_row = QHBoxLayout()
        self.btn_google_signin = QPushButton("Sign in to Google for MyMaps…")
        self.btn_google_signin.clicked.connect(self._on_google_signin_clicked)
        google_btn_row.addWidget(self.btn_google_signin)
        # Email field — informational. User can type the email of the
        # account they signed in with so the status line reads cleanly.
        # We don't auto-detect (would need a network call) — trust the
        # user's input.
        self.google_email_input = QLineEdit()
        self.google_email_input.setPlaceholderText("(your Google email — optional, for display)")
        if saved_email:
            self.google_email_input.setText(saved_email)
        google_btn_row.addWidget(self.google_email_input, 1)
        google_btn_row_widget = QWidget()
        google_btn_row_widget.setLayout(google_btn_row)
        layout.addWidget(google_btn_row_widget)

        # ----- Headless mode -----
        layout.addSpacing(8)
        from ..runtime_settings import is_headless_mode
        self.headless_checkbox = QCheckBox(
            "Run browsers invisibly (headless mode) — recommended for everyday use"
        )
        self.headless_checkbox.setChecked(is_headless_mode())
        self.headless_checkbox.setToolTip(
            "On: MyMaps and qchub run in the background — you'll get a "
            "notification when Ellen needs you, but the browsers don't pop up.\n"
            "Off: Browsers open visibly so you can watch the automation. "
            "Useful for diagnostics or first-time verification."
        )
        layout.addWidget(self.headless_checkbox)

        # ----- Save / Cancel -----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addStretch(1)
        layout.addWidget(buttons)

    @staticmethod
    def _format_google_status(email: str | None, last_attempted: str) -> str:
        if email and last_attempted:
            return f"Signed in as: <b>{email}</b> &nbsp;·&nbsp; last sign-in: {last_attempted}"
        if email:
            return f"Signed in as: <b>{email}</b>"
        if last_attempted:
            return f"<i>Sign-in attempted: {last_attempted}</i> — close the Edge window when done."
        return "<i>Not signed in yet.</i>"

    def _on_google_signin_clicked(self) -> None:
        """Open Edge to Google sign-in with the shared MyMaps profile.
        User signs in manually + closes the browser when done; the saved
        cookies persist so future MyMaps runs can pick up the session.
        """
        from ..google_signin import launch_google_signin
        ok, message = launch_google_signin()
        if not ok:
            QMessageBox.warning(self, "Couldn't launch Edge", message)
            return
        # Record the attempt timestamp so the status line shows recency
        # even before the user types in their email.
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        QSettings("Quality Counts", "Traffic Intake").setValue(
            "google_signin_last_attempted_at", ts,
        )
        # Update the status label in place so the user sees recognition.
        saved_email = self.google_email_input.text().strip() or config.get_google_mymaps_email()
        self.google_status_label.setText(self._format_google_status(saved_email, ts))
        QMessageBox.information(
            self, "Sign in to Google",
            message + "\n\nWhen you're done signing in, close the Edge window "
            "and click Save to persist your email (optional).",
        )

    def save(self) -> None:
        # qchub credentials
        user = self.qchub_user_input.text().strip()
        pw = self.qchub_pass_input.text()
        if user and pw:
            try:
                config.set_qchub_credentials(user, pw)
            except Exception as exc:
                QMessageBox.critical(self, "Save failed", str(exc))
                return
        elif user and not pw:
            existing = config.get_qchub_credentials()
            if existing and existing[0] != user:
                # Username changed; we need a password too.
                QMessageBox.warning(
                    self, "Missing password",
                    "Username changed but no password entered. Both must be saved together.",
                )
                return

        # Google MyMaps email (optional, display-only).
        new_email = self.google_email_input.text().strip()
        if new_email:
            try:
                config.set_google_mymaps_email(new_email)
            except Exception as exc:
                QMessageBox.critical(self, "Save failed", str(exc))
                return

        # Headless preference
        from ..runtime_settings import set_headless_mode
        set_headless_mode(self.headless_checkbox.isChecked())

        self.accept()
