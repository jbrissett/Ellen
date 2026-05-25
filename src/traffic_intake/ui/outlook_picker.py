"""Read the currently-selected email out of a running Outlook via COM.

Outlook need NOT be the foreground window — the COM API exposes the open
windows and their selections regardless of which app has focus. The user can
click an email in Outlook, switch to our app, click "Import from Outlook",
and we'll still find the selection.

We try three sources in order:
  1. ActiveInspector — when the user has an email open in its own popup window
  2. ActiveExplorer.Selection — when the user has an email highlighted in the inbox list
  3. Walk Application.Explorers — covers multi-window setups where ActiveExplorer
     happens to point at the wrong window

We save the chosen item as a Unicode .msg file (preserves attachments) and
return the path for parsing.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional


class OutlookUnavailable(Exception):
    """Outlook isn't running, or COM/pywin32 isn't available."""


class NoSelection(Exception):
    """Outlook is running but we couldn't find a selected or open email."""


class NewOutlookDetected(OutlookUnavailable):
    """The user is running the NEW Outlook (olk.exe), which has no COM API.

    Classic Outlook's COM server may still respond, but with zero Explorers —
    that's our signal. The fix is either drag-drop from new Outlook OR toggle
    'New Outlook' off in the new-Outlook upper-right corner.
    """


_OL_MSG_UNICODE = 9  # OlSaveAsType — Unicode MSG, preserves headers + attachments
_MAIL_ITEM_CLASS = 43


def import_selected_email() -> Path:
    """Save the user's current Outlook email (selected or open) to a temp .msg."""
    try:
        import win32com.client
    except ImportError as exc:
        raise OutlookUnavailable(f"pywin32 not installed: {exc}")

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
    except Exception as exc:
        raise OutlookUnavailable(
            f"Couldn't connect to Outlook (is it running?): {exc}"
        )

    item = _find_mail_item(outlook)
    if item is None:
        # Distinguish "new Outlook is running" from "classic Outlook has no selection".
        # New Outlook (olk.exe) is a separate app — classic Outlook's COM server still
        # responds but reports 0 Explorers because the user's mail windows are owned
        # by olk.exe, not outlook.exe.
        try:
            explorers_count = outlook.Explorers.Count
            inspectors_count = outlook.Inspectors.Count
        except Exception:
            explorers_count = -1
            inspectors_count = -1

        if explorers_count == 0 and inspectors_count == 0 and _new_outlook_is_running():
            raise NewOutlookDetected(
                "You're using the new Microsoft Outlook (olk.exe), which doesn't "
                "support automation. Two workarounds:\n\n"
                "  • Drag the email from new Outlook into the app's drop zone — "
                "new Outlook drops as a temporary .eml file.\n\n"
                "  • Toggle 'New Outlook' OFF in the upper-right corner of your "
                "Outlook window to switch back to Classic Outlook — Import from "
                "Outlook then works fully.\n\n"
                "A future build will add Microsoft Graph API support so this "
                "button works in new Outlook too."
            )

        raise NoSelection(
            "No email selected or open in Outlook. Click an email in your inbox "
            "(or open one in its own window), then come back and try again. "
            "You do NOT need to switch to Outlook first — the selection is read "
            "from the COM API."
        )

    tmp_dir = Path(tempfile.gettempdir()) / "traffic-intake-outlook"
    tmp_dir.mkdir(exist_ok=True)
    safe_subject = _safe_filename(getattr(item, "Subject", "") or "outlook-email")
    target = tmp_dir / f"{safe_subject}.msg"

    try:
        item.SaveAs(str(target), _OL_MSG_UNICODE)
    except Exception as exc:
        raise OutlookUnavailable(
            f"Failed to save Outlook item to {target}: {exc}. "
            "If Outlook is in Cached Exchange mode, the item may still be downloading."
        )
    return target


def _find_mail_item(outlook) -> Optional[object]:
    """Try Inspector → ActiveExplorer.Selection → all Explorers in turn."""
    # 1. Email open in its own window
    try:
        inspector = outlook.ActiveInspector()
        if inspector is not None:
            current = inspector.CurrentItem
            if current is not None and _is_mail_item(current):
                return current
    except Exception:
        pass

    # 2. Email selected in active inbox/folder window
    try:
        explorer = outlook.ActiveExplorer()
        if explorer is not None:
            selection = explorer.Selection
            if selection is not None and selection.Count > 0:
                candidate = selection.Item(1)
                if _is_mail_item(candidate):
                    return candidate
    except Exception:
        pass

    # 3. Walk all open Outlook windows for any selection or open mail item
    try:
        explorers = outlook.Explorers
        for i in range(1, explorers.Count + 1):
            try:
                explorer = explorers.Item(i)
                selection = explorer.Selection
                if selection is not None and selection.Count > 0:
                    candidate = selection.Item(1)
                    if _is_mail_item(candidate):
                        return candidate
            except Exception:
                continue
    except Exception:
        pass

    try:
        inspectors = outlook.Inspectors
        for i in range(1, inspectors.Count + 1):
            try:
                ins = inspectors.Item(i)
                candidate = ins.CurrentItem
                if candidate is not None and _is_mail_item(candidate):
                    return candidate
            except Exception:
                continue
    except Exception:
        pass

    return None


def _is_mail_item(item) -> bool:
    """Outlook MailItem has Class == 43. Some items (MeetingItem, ReportItem)
    aren't mail. We accept only MailItem-class objects.
    """
    cls = getattr(item, "Class", None)
    if cls is None:
        # No Class attribute — be permissive; SaveAs will fail loudly if it's wrong
        return True
    return cls == _MAIL_ITEM_CLASS


def _safe_filename(s: str) -> str:
    bad = '<>:"/\\|?*\n\r\t'
    out = "".join("_" if ch in bad else ch for ch in s).strip()
    return out[:120] or "outlook-email"


def _new_outlook_is_running() -> bool:
    """Detect whether olk.exe (new Outlook) is in the process list."""
    try:
        import psutil
    except ImportError:
        # Fallback: shell out to tasklist
        import subprocess
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq olk.exe"],
                text=True,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            return "olk.exe" in out.lower()
        except Exception:
            return False
    else:
        for p in psutil.process_iter(attrs=["name"]):
            try:
                if (p.info["name"] or "").lower() == "olk.exe":
                    return True
            except Exception:
                continue
        return False
