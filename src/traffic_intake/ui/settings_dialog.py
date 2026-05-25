"""Settings dialog — manage stored API key and qchub credentials."""
from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

# Chat model preference options surfaced in the dropdown. Key matches what
# `chat.resolve_model_chain` accepts; label is what the user sees.
_CHAT_MODEL_OPTIONS = [
    ("auto",   "Auto — Sonnet → Opus → Haiku (recommended)"),
    ("sonnet", "Sonnet 4.6 only (balanced; default)"),
    ("opus",   "Opus 4.7 only (most capable; ~3× cost)"),
    ("haiku",  "Haiku 4.5 only (cheapest, fastest; weaker at tool sequencing)"),
]


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Ellen — Settings")
        self.resize(540, 280)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Credentials are stored in Windows Credential Manager and never leave this machine."
        ))

        form = QFormLayout()
        layout.addLayout(form)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        try:
            existing = config.get_api_key()
            self.api_key_input.setPlaceholderText(f"(saved: {existing[:12]}…) — paste a new key to overwrite")
        except Exception:
            self.api_key_input.setPlaceholderText("sk-ant-api03-…")

        show_btn = QPushButton("Show")
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda checked: self.api_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        api_row = QHBoxLayout()
        api_row.addWidget(self.api_key_input, 1)
        api_row.addWidget(show_btn)
        api_row_widget = QWidget()
        api_row_widget.setLayout(api_row)
        form.addRow("Anthropic API key:", api_row_widget)

        self.google_key_input = QLineEdit()
        self.google_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        google_existing = config.get_google_geocoding_key()
        if google_existing:
            self.google_key_input.setPlaceholderText(f"(saved: {google_existing[:12]}…) — paste a new key to overwrite")
        else:
            self.google_key_input.setPlaceholderText("AIza…")
        google_show = QPushButton("Show")
        google_show.setCheckable(True)
        google_show.toggled.connect(
            lambda checked: self.google_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        google_row = QHBoxLayout()
        google_row.addWidget(self.google_key_input, 1)
        google_row.addWidget(google_show)
        google_row_widget = QWidget()
        google_row_widget.setLayout(google_row)
        form.addRow("Google Geocoding API key:", google_row_widget)

        self.qchub_user_input = QLineEdit()
        self.qchub_pass_input = QLineEdit()
        self.qchub_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        creds = config.get_qchub_credentials()
        if creds:
            self.qchub_user_input.setText(creds[0])
            self.qchub_pass_input.setPlaceholderText("(saved) — paste a new password to overwrite")
        form.addRow("qchub username:", self.qchub_user_input)
        form.addRow("qchub password:", self.qchub_pass_input)

        # UI preferences
        ttk_settings = QSettings("Quality Counts", "Traffic Intake")

        # Chat model preference — controls which Claude model Ellen uses,
        # with an Auto option that falls back across models when one is
        # overloaded (e.g., the Anthropic-overload outage 2026-05-14).
        self.chat_model_combo = QComboBox()
        for key, label in _CHAT_MODEL_OPTIONS:
            self.chat_model_combo.addItem(label, userData=key)
        current_pref = str(ttk_settings.value("chat_model", "auto"))
        for i in range(self.chat_model_combo.count()):
            if self.chat_model_combo.itemData(i) == current_pref:
                self.chat_model_combo.setCurrentIndex(i)
                break
        form.addRow("Chat model (Ellen):", self.chat_model_combo)

        self.show_mymaps_confirm = QCheckBox("Show 'Create MyMaps map' confirmation dialog")
        self.show_mymaps_confirm.setChecked(
            not ttk_settings.value("skip_mymaps_confirm", False, type=bool)
        )
        layout.addWidget(self.show_mymaps_confirm)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def save(self) -> None:
        api = self.api_key_input.text().strip()
        if api:
            if not api.startswith("sk-ant-"):
                if QMessageBox.question(
                    self, "Unexpected format",
                    "That doesn't look like an Anthropic key (expected to start with 'sk-ant-'). Save anyway?",
                ) != QMessageBox.StandardButton.Yes:
                    return
            try:
                config.set_api_key(api)
            except Exception as exc:
                QMessageBox.critical(self, "Save failed", str(exc))
                return

        google = self.google_key_input.text().strip()
        if google:
            if not google.startswith("AIza"):
                if QMessageBox.question(
                    self, "Unexpected format",
                    "That doesn't look like a Google API key (expected to start with 'AIza'). Save anyway?",
                ) != QMessageBox.StandardButton.Yes:
                    return
            try:
                config.set_google_geocoding_key(google)
            except Exception as exc:
                QMessageBox.critical(self, "Save failed", str(exc))
                return

        # Persist UI preferences
        settings = QSettings("Quality Counts", "Traffic Intake")
        settings.setValue(
            "skip_mymaps_confirm", not self.show_mymaps_confirm.isChecked()
        )
        settings.setValue(
            "chat_model", self.chat_model_combo.currentData() or "auto",
        )

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

        self.accept()
