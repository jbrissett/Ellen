"""Draft a reply email to the client with the captured estimate PDF + map link.

Uses Outlook COM (same pattern as outlook_picker.py). We open the draft in
Outlook for the USER to review and send manually — never auto-send.

**Threading + recipient preservation (2026-05-15):** when the source email
is available on disk, we open it in Outlook and call `.ReplyAll()` so the
draft inherits the proper `In-Reply-To` / `References` headers AND the
original To+CC participants automatically. This matches the user's
real-world reply pattern (sample: Re: Lakeland N Socrum Loop Road).
Falls back to a `CreateItem` + manually-populated recipients path if the
source isn't openable in Outlook.

Body template follows the sample's structure:
    Hi <FirstName>,

    Thanks for sending this over!

    I have attached the estimate and the project map for your review.
    I will get this one on the schedule to collect <deployment_schedule>.

    MAP: <map_share_url>

    Please let me know if you have any questions.

    Have a great <day-context>!

Outlook's auto-signature gets appended below our body (signature insertion
is preserved by calling GetInspector before we write HTMLBody).
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import StudyRequest


class EmailDraftError(Exception):
    """Outlook isn't running, COM/pywin32 isn't available, or the draft
    couldn't be created. The caller surfaces this in the UI."""


@dataclass
class DraftResult:
    to: str
    cc: str
    subject: str
    attachment_path: Optional[str]
    map_url: Optional[str]
    threaded_reply: bool  # True if Outlook's ReplyAll was used
    note: str = ""


# ---------- name + subject + body helpers ----------

def _first_name(full: Optional[str]) -> Optional[str]:
    if not full:
        return None
    s = full.strip()
    if not s:
        return None
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            return parts[1].split()[0]
    return s.split()[0]


def _default_subject(request: StudyRequest) -> str:
    """Build the reply subject. Used only on the fallback path — when we
    use Outlook's ReplyAll, Outlook handles the 'Re: ' prefix itself.
    """
    orig = (request.email_subject or "Traffic study estimate").strip()
    if not orig.lower().startswith("re:"):
        orig = f"Re: {orig}"
    return orig


def _day_signoff(now: Optional[_dt.datetime] = None) -> str:
    """Pick a contextual sign-off — 'great weekend' on Fri, otherwise
    'great day'. Mirrors the sample's tone.
    """
    now = now or _dt.datetime.now()
    if now.weekday() == 4:  # Friday
        return "Have a great weekend!"
    return "Have a great day!"


def _default_body_html(
    request: StudyRequest,
    *,
    map_url: Optional[str],
    deployment_schedule: Optional[str] = None,
    has_pdf: bool = True,
) -> str:
    """Default reply body. Follows the sample template (Lakeland N Socrum
    Loop Road, 2026-05-01). Personable, professional, references the PDF
    + map + schedule. Outlook's auto-signature appends below.
    """
    first = _first_name(request.client_contact_name) or "there"
    schedule = (deployment_schedule or "as soon as we can fit it in").strip()
    bits: list[str] = []
    bits.append(f"<p>Hi {first},</p>")
    bits.append("<p>Thanks for sending this over!</p>")
    if has_pdf and map_url:
        bits.append(
            "<p>I have attached the estimate and the project map for your review. "
            f"I will get this one on the schedule to collect {schedule}.</p>"
        )
        bits.append(f'<p>MAP: <a href="{map_url}">{map_url}</a></p>')
    elif has_pdf:
        bits.append(
            "<p>I have attached the estimate for your review. "
            f"I will get this one on the schedule to collect {schedule}.</p>"
        )
    elif map_url:
        bits.append(
            "<p>I have put together the project map for your review and will follow up with "
            f"the priced estimate shortly. We are looking at collecting {schedule}.</p>"
        )
        bits.append(f'<p>MAP: <a href="{map_url}">{map_url}</a></p>')
    else:
        bits.append(
            f"<p>I have the request in hand and will follow up shortly with the "
            f"priced estimate and project map. We are looking at collecting {schedule}.</p>"
        )
    bits.append("<p>Please let me know if you have any questions.</p>")
    bits.append(f"<p>{_day_signoff()}</p>")
    return "".join(bits)


# ---------- attachment / PDF resolution ----------

def _resolve_latest_pdf(request: StudyRequest, artifacts: dict | None) -> Optional[Path]:
    """Pick the latest estimate PDF to attach. Prefers the highest-version
    PDF that actually exists on disk (`Estimate_<id>_v3.pdf` over `_v2`
    over the original). Returns None if no PDF is available.
    """
    if not artifacts:
        return None
    candidate = artifacts.get("estimate_pdf_path")
    if not candidate:
        return None
    p = Path(str(candidate))
    if not p.exists():
        return None
    stem = p.stem
    ext = p.suffix
    parent = p.parent
    best = p
    best_version = 1
    import re
    m = re.match(r"^(.*?)(?:_v(\d+))?$", stem)
    if m:
        base = m.group(1)
        for sibling in parent.glob(f"{base}_v*{ext}"):
            m2 = re.match(rf"^{re.escape(base)}_v(\d+)$", sibling.stem)
            if m2:
                v = int(m2.group(1))
                if v > best_version and sibling.exists():
                    best_version = v
                    best = sibling
    return best


# ---------- main entry point ----------

def draft_reply(
    request: StudyRequest,
    *,
    artifacts: Optional[dict] = None,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    subject: Optional[str] = None,
    body_html: Optional[str] = None,
    deployment_schedule: Optional[str] = None,
    map_url: Optional[str] = None,
) -> DraftResult:
    """Open an Outlook draft reply window with To/CC/Subject/Body/Attachment pre-filled.

    Preferred path: open the source email in Outlook and call ReplyAll so
    threading + all original parties are preserved exactly. Fallback:
    create a fresh MailItem and manually populate To/CC from the source
    email's parsed headers (or user-supplied overrides).

    Returns immediately after the draft opens; the user reviews and sends
    themselves. Raises EmailDraftError if Outlook isn't available.
    """
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise EmailDraftError(f"pywin32 not installed: {exc}")
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
    except Exception as exc:
        raise EmailDraftError(f"Couldn't connect to Outlook (is it running?): {exc}")

    artifacts = artifacts or {}
    pdf_path = _resolve_latest_pdf(request, artifacts)
    resolved_map = (map_url or artifacts.get("mymaps_share_url") or "").strip() or None

    # Body. If caller supplied a custom one (Ellen composed it), use that.
    body = body_html or _default_body_html(
        request,
        map_url=resolved_map,
        deployment_schedule=deployment_schedule,
        has_pdf=pdf_path is not None,
    )

    # Try the ReplyAll path first — preserves threading + all original
    # parties at their original recipient levels (To becomes the original
    # sender, CC becomes original recipients).
    threaded = False
    mail = None
    source_path = artifacts.get("source_email_path")
    if source_path:
        source_p = Path(str(source_path))
        if source_p.exists():
            try:
                original = outlook.Session.OpenSharedItem(str(source_p))
                # MailItem class is 43. OpenSharedItem can return other
                # types (appointments, contacts); reject those.
                if getattr(original, "Class", None) == 43:
                    mail = original.ReplyAll()
                    threaded = True
            except Exception:
                # .eml files often fail OpenSharedItem on Windows because
                # Outlook prefers .msg; fall through to the manual path.
                mail = None

    # Fallback: build a fresh MailItem with manually-populated recipients.
    if mail is None:
        try:
            mail = outlook.CreateItem(0)  # olMailItem
        except Exception as exc:
            raise EmailDraftError(f"Outlook.CreateItem failed: {exc}")
        # To: original sender (or override). The StudyRequest carries the
        # original From in `email_from`.
        resolved_to = (to or _email_address_only(request.email_from) or "").strip()
        if not resolved_to:
            raise EmailDraftError(
                "No To address — request has no email_from. Pass `to=` to override."
            )
        # CC: blend original To + original CC (minus QC's own address /
        # the user's address), plus any explicit override.
        cc_addrs = _build_cc(
            original_to=request.email_to,
            original_cc=request.email_cc,
            our_address=_extract_outlook_user_address(outlook),
            override=cc,
        )
        mail.To = resolved_to
        if cc_addrs:
            mail.CC = "; ".join(cc_addrs)
        mail.Subject = subject or _default_subject(request)

    # In either path: set body BEFORE the signature, preserve attachment.
    try:
        # Force signature insertion (only does anything in the CreateItem path).
        _ = mail.GetInspector
        existing = mail.HTMLBody or ""
        # When ReplyAll: existing contains the quoted original. Prepend our
        # body so it appears at the top with quote below.
        mail.HTMLBody = body + existing

        if pdf_path is not None:
            mail.Attachments.Add(Source=str(pdf_path))

        mail.Display(False)  # modeless: window opens, code returns
    except Exception as exc:
        raise EmailDraftError(f"Filling Outlook draft failed: {exc}")

    note_bits: list[str] = []
    if pdf_path is None:
        note_bits.append("No estimate PDF was attached.")
    if not resolved_map:
        note_bits.append("No MyMaps share link was available — body uses the no-map variant.")
    if not threaded:
        note_bits.append(
            "Couldn't open the source email in Outlook — recipients were "
            "populated manually instead of via ReplyAll, so threading may "
            "not be preserved."
        )

    return DraftResult(
        to=str(getattr(mail, "To", "") or ""),
        cc=str(getattr(mail, "CC", "") or ""),
        subject=str(getattr(mail, "Subject", "") or ""),
        attachment_path=str(pdf_path) if pdf_path else None,
        map_url=resolved_map,
        threaded_reply=threaded,
        note=" ".join(note_bits),
    )


# ---------- helpers used only on the fallback path ----------

def _email_address_only(s: Optional[str]) -> Optional[str]:
    """'Natalie Gibbons <Natalie.Gibbons@kimley-horn.com>' → 'Natalie.Gibbons@kimley-horn.com'.
    Returns input unchanged if no <> bracketed address found.
    """
    if not s:
        return None
    import re
    m = re.search(r"<([^>]+)>", s)
    return m.group(1).strip() if m else s.strip()


def _split_addresses(s: Optional[str]) -> list[str]:
    """Split a comma/semicolon-separated address header into normalized addresses.
    Uses RFC-aware parsing so quoted display names with commas
    ('"Howell, Christina" <...>') don't get torn in half.
    """
    if not s:
        return []
    import email.utils
    # getaddresses requires ',' separators; normalize ';' (Outlook's
    # preferred separator) to ',' first. It correctly handles quoted
    # display names with internal commas ('"Howell, Christina" <...>').
    normalized = s.replace(";", ",")
    parsed = email.utils.getaddresses([normalized])
    out: list[str] = []
    for name, addr in parsed:
        if not addr:
            continue
        if name:
            out.append(f"{name} <{addr}>")
        else:
            out.append(addr)
    return out


def _build_cc(
    *, original_to: Optional[str], original_cc: Optional[str],
    our_address: Optional[str], override: Optional[str],
) -> list[str]:
    """When ReplyAll isn't available, manually replicate its CC behavior:
    CC = (original To + original CC) minus (the new To recipient and our
    own address), plus any explicit override.
    """
    addresses: list[str] = []
    seen_lower: set[str] = set()
    def add(addr: str) -> None:
        a = addr.strip()
        if not a:
            return
        key = (_email_address_only(a) or a).lower()
        if our_address and key == our_address.lower():
            return
        if key in seen_lower:
            return
        seen_lower.add(key)
        addresses.append(a)
    for src in (override, original_to, original_cc):
        for a in _split_addresses(src):
            add(a)
    return addresses


def _extract_outlook_user_address(outlook) -> Optional[str]:
    """Return the SMTP address of the currently-logged-in Outlook user, or None."""
    try:
        return str(outlook.Session.CurrentUser.AddressEntry.GetExchangeUser().PrimarySmtpAddress)
    except Exception:
        try:
            return str(outlook.Session.CurrentUser.Address)
        except Exception:
            return None
