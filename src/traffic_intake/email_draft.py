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
    threaded_reply: bool  # True if Outlook's ReplyAll was used (best path)
    note: str = ""
    # How we landed on the draft — useful in the chat status message + diagnostics.
    # Values: "openshareditem", "inbox-search", "manual-forwarded", "manual-fallback"
    reply_path: str = ""
    is_forwarded: bool = False


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

    Goal (per user direction 2026-05-24): the draft should appear as a
    Reply All to the CLIENT, with the original email thread inline below
    our composed body and the estimate PDF attached. Behavior depends on
    whether the source `.eml` was a fresh client message or a forwarded
    one (QC staffer → data entry):

      Non-forwarded:
        Tier A — OpenSharedItem(.msg-or-.eml) + ReplyAll. Outlook
                 handles threading, recipient inheritance, and the
                 quoted body for free. `.msg` works reliably; `.eml`
                 sometimes works, sometimes doesn't.
        Tier B — Search the user's Inbox for the original message
                 (by Subject + Sender), call ReplyAll on the found
                 MailItem. Works when OpenSharedItem fails but the
                 message is still in Outlook.
        Tier C — Manual CreateItem with To=email_from,
                 CC=email_to+email_cc minus QC, Subject="Re: ...",
                 body=ours + manually-quoted original. Always works
                 but no real threading headers.

      Forwarded:
        Always Tier C-manual, populated from the parser's
        `original_*` fields (re-parsed here from the source .eml).
        ReplyAll on a forwarded message would reply to the QC
        forwarder, not the client — so we skip the Outlook paths
        entirely and construct a synthetic reply targeted at
        `original_from`.

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

    # Re-parse the source .eml to extract forwarded-message metadata.
    # StudyRequest doesn't currently carry `original_*` fields — the
    # parser produces them on ParsedEmail but the extractor doesn't
    # propagate them. One-time re-parse here is cheap and keeps the
    # StudyRequest model unchanged.
    is_forwarded = False
    fwd_from: Optional[str] = None
    fwd_to: Optional[str] = None
    fwd_subject: Optional[str] = None
    fwd_date: Optional[str] = None
    fwd_body: str = ""
    source_path = artifacts.get("source_email_path")
    if source_path:
        source_p = Path(str(source_path))
        if source_p.exists():
            try:
                from .parser import parse_email_file, prepare_for_extraction
                parsed = parse_email_file(source_p)
                prepare_for_extraction(parsed)
                is_forwarded = bool(parsed.is_forwarded)
                fwd_from = parsed.original_from
                fwd_to = parsed.original_to
                fwd_subject = parsed.original_subject
                fwd_date = parsed.original_date
                fwd_body = parsed.original_body or ""
            except Exception:
                # Re-parse failure isn't fatal; we treat as non-forwarded
                # and proceed with email_* fields from the StudyRequest.
                pass

    # ----- branch on forwarded -----
    mail = None
    threaded = False
    reply_path = ""
    our_address = _extract_outlook_user_address(outlook)

    if is_forwarded:
        # Manual construction targeting the ORIGINAL client, not the
        # forwarder. ReplyAll on the forwarded message would land on the
        # QC staffer who did the forwarding.
        mail = _build_manual_reply(
            outlook,
            to_override=to,
            cc_override=cc,
            subject_override=subject,
            target_from=fwd_from,
            target_to_header=fwd_to,
            target_cc_header=None,  # parser doesn't extract original_cc
            target_subject=fwd_subject,
            target_date=fwd_date,
            target_body=fwd_body,
            our_address=our_address,
            body_html=body,
        )
        reply_path = "manual-forwarded"

    else:
        # Tier A: OpenSharedItem + ReplyAll (Outlook native).
        if source_path:
            source_p = Path(str(source_path))
            if source_p.exists():
                try:
                    original = outlook.Session.OpenSharedItem(str(source_p))
                    if getattr(original, "Class", None) == 43:  # olMail
                        mail = original.ReplyAll()
                        threaded = True
                        reply_path = "openshareditem"
                except Exception:
                    mail = None  # fall through

        # Tier B: search Inbox for the original message.
        if mail is None:
            found = _find_outlook_message_for_request(outlook, request)
            if found is not None:
                try:
                    mail = found.ReplyAll()
                    threaded = True
                    reply_path = "inbox-search"
                except Exception:
                    mail = None  # fall through

        # Tier C: manual fallback with quoted original.
        if mail is None:
            mail = _build_manual_reply(
                outlook,
                to_override=to,
                cc_override=cc,
                subject_override=subject,
                target_from=request.email_from,
                target_to_header=request.email_to,
                target_cc_header=request.email_cc,
                target_subject=request.email_subject,
                target_date=request.email_date.isoformat() if request.email_date else None,
                target_body=request.email_body or "",
                our_address=our_address,
                body_html=body,
            )
            reply_path = "manual-fallback"

    # ----- fill body + attachment (common path) -----
    # For Tier A / B (Outlook ReplyAll), HTMLBody already contains the
    # quoted original — prepend ours.
    # For Tier C (manual), _build_manual_reply already set HTMLBody to
    # ours + quoted-original, so we just need to attach the PDF.
    try:
        _ = mail.GetInspector  # forces signature insertion in CreateItem path
        if reply_path in ("openshareditem", "inbox-search"):
            existing = mail.HTMLBody or ""
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
    if reply_path == "manual-fallback":
        note_bits.append(
            "Couldn't find the original email in Outlook — built the reply "
            "manually with the thread quoted inline. Threading headers "
            "(In-Reply-To / References) are NOT set, so the client's "
            "mail client may show it as a new thread."
        )
    if reply_path == "manual-forwarded":
        note_bits.append(
            "Source was a forwarded email — reply was constructed "
            "manually so it targets the original client, not the QC "
            f"forwarder. To: {getattr(mail, 'To', '') or '(empty)'}."
        )

    return DraftResult(
        to=str(getattr(mail, "To", "") or ""),
        cc=str(getattr(mail, "CC", "") or ""),
        subject=str(getattr(mail, "Subject", "") or ""),
        attachment_path=str(pdf_path) if pdf_path else None,
        map_url=resolved_map,
        threaded_reply=threaded,
        note=" ".join(note_bits),
        reply_path=reply_path,
        is_forwarded=is_forwarded,
    )


# ---------- helpers for the new flow ----------

def _build_manual_reply(
    outlook,
    *,
    to_override: Optional[str],
    cc_override: Optional[str],
    subject_override: Optional[str],
    target_from: Optional[str],
    target_to_header: Optional[str],
    target_cc_header: Optional[str],
    target_subject: Optional[str],
    target_date: Optional[str],
    target_body: str,
    our_address: Optional[str],
    body_html: str,
):
    """CreateItem a fresh MailItem and populate it to LOOK like a Reply
    All on `target_*` (the message we want to reply to — either the
    StudyRequest's email_from / etc, or the parser's original_* fields
    when the source was forwarded).

    Sets:
      - To: target_from (or override)
      - CC: target_to_header + target_cc_header (minus the new To and
            our own address) + override
      - Subject: 'Re: <target_subject>' (or override)
      - HTMLBody: body_html + manually-quoted target_body block
    """
    try:
        mail = outlook.CreateItem(0)  # olMailItem
    except Exception as exc:
        raise EmailDraftError(f"Outlook.CreateItem failed: {exc}")

    to_addr = (to_override or _email_address_only(target_from) or "").strip()
    if not to_addr:
        raise EmailDraftError(
            "No To address — couldn't determine the original sender. "
            "Pass `to=` to override."
        )
    cc_addrs = _build_cc(
        original_to=target_to_header,
        original_cc=target_cc_header,
        our_address=our_address,
        override=cc_override,
    )
    # Subject: prefix Re: if not already present.
    if subject_override:
        subj = subject_override
    else:
        raw_subj = (target_subject or "Traffic study estimate").strip()
        subj = raw_subj if raw_subj.lower().startswith("re:") else f"Re: {raw_subj}"

    mail.To = to_addr
    if cc_addrs:
        mail.CC = "; ".join(cc_addrs)
    mail.Subject = subj

    # Synthesize the quoted-original block so the draft looks like a real
    # Reply All. Matches Outlook's own format closely enough that the
    # reader's eye flows over it.
    quoted = _build_quoted_original_html(
        from_=target_from,
        sent=target_date,
        to=target_to_header,
        cc=target_cc_header,
        subject=target_subject,
        body=target_body,
    )
    # Body composed; let GetInspector inject the user's signature, then
    # set HTMLBody = our text + the user's signature + quoted original.
    # (Signature lives in `mail.HTMLBody` after GetInspector touches it;
    # we keep it between ours and the quote.)
    _ = mail.GetInspector
    existing = mail.HTMLBody or ""
    mail.HTMLBody = body_html + existing + quoted
    return mail


def _build_quoted_original_html(
    *,
    from_: Optional[str],
    sent: Optional[str],
    to: Optional[str],
    cc: Optional[str],
    subject: Optional[str],
    body: str,
) -> str:
    """Render an Outlook-style quoted-original block in HTML.

    Format mirrors what Outlook injects on a real Reply:
      <hr>
      <p><b>From:</b> ...<br>
         <b>Sent:</b> ...<br>
         <b>To:</b> ...<br>
         <b>Cc:</b> ... (omitted if empty)<br>
         <b>Subject:</b> ...</p>
      <p>... body, with newlines → <br> ...</p>
    """
    import html
    def esc(s: Optional[str]) -> str:
        return html.escape(s or "")
    headers_bits = [f"<b>From:</b> {esc(from_)}"]
    if sent:
        headers_bits.append(f"<b>Sent:</b> {esc(sent)}")
    if to:
        headers_bits.append(f"<b>To:</b> {esc(to)}")
    if cc:
        headers_bits.append(f"<b>Cc:</b> {esc(cc)}")
    if subject:
        headers_bits.append(f"<b>Subject:</b> {esc(subject)}")
    headers_html = "<br>".join(headers_bits)
    body_html = esc(body).replace("\n", "<br>")
    return (
        '<hr style="border:none; border-top:1px solid #ccc; margin:18px 0;">'
        f'<p style="font-family:Calibri,Arial,sans-serif; font-size:11pt;">'
        f'{headers_html}</p>'
        f'<div style="font-family:Calibri,Arial,sans-serif; font-size:11pt;">'
        f'{body_html}</div>'
    )


def _find_outlook_message_for_request(outlook, request) -> Optional[object]:
    """Search the user's default Inbox for an Outlook MailItem matching
    the StudyRequest's subject + sender. Returns the MailItem or None.

    Uses DASL `Restrict` on Subject (server-side indexed). Walks at most
    the first 20 matches and picks one whose SenderEmailAddress contains
    the parsed-out sender email (substring tolerance handles display-name
    differences like 'Smith, John <jsmith@x.com>' vs the raw address).

    This is the Tier-B fallback when OpenSharedItem(.eml) fails. Returns
    None on any error (control flow continues to Tier C manual).
    """
    if not request.email_subject or not request.email_from:
        return None
    sender_email = _email_address_only(request.email_from) or ""
    if not sender_email:
        return None
    try:
        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(6)  # olFolderInbox
    except Exception:
        return None
    # DASL restriction on Subject. Escape single quotes by doubling.
    safe_subj = request.email_subject.replace("'", "''")
    flt = "@SQL=\"urn:schemas:httpmail:subject\" = '" + safe_subj + "'"
    try:
        results = inbox.Items.Restrict(flt)
    except Exception:
        return None
    sender_lower = sender_email.lower()
    try:
        count = int(getattr(results, "Count", 0) or 0)
    except Exception:
        count = 0
    if count == 0:
        return None
    # Walk results, return first matching sender. Cap iteration for safety.
    for i in range(1, min(count, 20) + 1):
        try:
            item = results.Item(i)
            if getattr(item, "Class", None) != 43:
                continue
            item_sender = (item.SenderEmailAddress or "").lower()
            if not item_sender:
                continue
            if sender_lower in item_sender or item_sender in sender_lower:
                return item
        except Exception:
            continue
    return None


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
