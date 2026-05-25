"""Parse Outlook emails — both .eml (RFC822) and .msg (Outlook binary).

Extracts headers, body, AND attachments (KMZ, images, other) so downstream
extraction can use vision and KMZ coordinates.
"""
from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional


@dataclass
class Attachment:
    """A single attachment or inline part — image, KMZ, KML, or other."""
    filename: str
    content_type: str  # e.g. "image/png", "application/vnd.google-earth.kmz"
    data: bytes
    content_id: Optional[str] = None  # for inline references like [cid:image001.png@…]
    is_inline: bool = False

    @property
    def category(self) -> str:
        ct = self.content_type.lower()
        fn = self.filename.lower()
        if ct.startswith("image/"):
            return "image"
        if fn.endswith(".kmz") or ct == "application/vnd.google-earth.kmz":
            return "kmz"
        if fn.endswith(".kml") or ct == "application/vnd.google-earth.kml+xml":
            return "kml"
        if fn.endswith(".docx") or ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return "docx"
        if fn.endswith(".pdf") or ct == "application/pdf":
            return "pdf"
        return "other"


@dataclass
class ParsedEmail:
    subject: str
    from_: str
    to: str
    cc: Optional[str]
    date: Optional[datetime]
    body_text: str
    attachments: list[Attachment] = field(default_factory=list)
    # Forwarded-email layering. If the body has a forwarded section, these
    # are populated by clean_body_for_llm()/split_forwarded_body(); otherwise
    # forwarder_added_text is "" and original_body == body_text.
    forwarder_added_text: str = ""
    original_body: str = ""
    is_forwarded: bool = False
    original_from: Optional[str] = None
    original_to: Optional[str] = None
    original_date: Optional[str] = None
    original_subject: Optional[str] = None

    def kmz_attachments(self) -> list[Attachment]:
        return [a for a in self.attachments if a.category in ("kmz", "kml")]

    def image_attachments(self, min_bytes: int = 30_000) -> list[Attachment]:
        """Inline/attached images. Filters tiny logos and social icons by size.

        ~30 KB cutoff drops most signature logos while keeping reference aerials.
        """
        return [a for a in self.attachments if a.category == "image" and len(a.data) >= min_bytes]

    def docx_attachments(self) -> list[Attachment]:
        return [a for a in self.attachments if a.category == "docx"]

    def pdf_attachments(self) -> list[Attachment]:
        return [a for a in self.attachments if a.category == "pdf"]


def parse_email_file(path: Path) -> ParsedEmail:
    suffix = path.suffix.lower()
    if suffix == ".eml":
        return _parse_eml(path)
    if suffix == ".msg":
        return _parse_msg(path)
    raise ValueError(f"Unsupported email format: {suffix} (expected .eml or .msg)")


def _parse_eml(path: Path) -> ParsedEmail:
    with path.open("rb") as f:
        msg: EmailMessage = email.message_from_binary_file(f, policy=email.policy.default)  # type: ignore[assignment]

    body_text = _extract_body_text(msg)
    date_obj: Optional[datetime] = None
    if msg["Date"]:
        try:
            date_obj = email.utils.parsedate_to_datetime(msg["Date"])
        except (TypeError, ValueError):
            date_obj = None

    attachments = _collect_eml_attachments(msg)

    return ParsedEmail(
        subject=str(msg["Subject"] or ""),
        from_=str(msg["From"] or ""),
        to=str(msg["To"] or ""),
        cc=str(msg["Cc"]) if msg["Cc"] else None,
        date=date_obj,
        body_text=body_text,
        attachments=attachments,
    )


def _extract_body_text(msg: EmailMessage) -> str:
    text_part = msg.get_body(preferencelist=("plain",))
    if text_part is not None:
        return _decode(text_part)
    html_part = msg.get_body(preferencelist=("html",))
    if html_part is not None:
        from bs4 import BeautifulSoup
        return BeautifulSoup(_decode(html_part), "html.parser").get_text(separator="\n")
    return ""


def _decode(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _collect_eml_attachments(msg: EmailMessage) -> list[Attachment]:
    """Walk all parts, capturing inline images and named attachments.

    Email parts marked Content-Disposition: attachment are obvious attachments.
    Inline image parts (referenced by Content-ID in HTML) appear without explicit
    attachment disposition but have a Content-ID header — we capture those too
    since clients often embed aerial reference photos inline.
    """
    out: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower()
        content_id = part.get("Content-ID")

        is_image = content_type.startswith("image/")
        is_attachment = disposition == "attachment"
        is_inline_image = is_image and content_id is not None

        if not (is_attachment or is_inline_image):
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        filename = part.get_filename() or (content_id or "").strip("<>") or f"part-{len(out)}"
        out.append(
            Attachment(
                filename=filename,
                content_type=content_type,
                data=payload,
                content_id=content_id.strip("<>") if content_id else None,
                is_inline=is_inline_image and not is_attachment,
            )
        )
    return out


def _parse_msg(path: Path) -> ParsedEmail:
    import extract_msg

    msg = extract_msg.Message(str(path))
    try:
        date_obj: Optional[datetime] = None
        if msg.date:
            try:
                date_obj = msg.date if isinstance(msg.date, datetime) else email.utils.parsedate_to_datetime(str(msg.date))
            except (TypeError, ValueError):
                date_obj = None

        body_text = msg.body or ""
        if not body_text and msg.htmlBody:
            from bs4 import BeautifulSoup
            html = msg.htmlBody.decode("utf-8", errors="replace") if isinstance(msg.htmlBody, bytes) else msg.htmlBody
            body_text = BeautifulSoup(html, "html.parser").get_text(separator="\n")

        attachments: list[Attachment] = []
        for att in msg.attachments:
            data = att.data if isinstance(att.data, bytes) else None
            if not data:
                continue
            filename = att.longFilename or att.shortFilename or f"attachment-{len(attachments)}"
            content_type = _guess_mime_from_name(filename)
            attachments.append(
                Attachment(
                    filename=filename,
                    content_type=content_type,
                    data=data,
                    content_id=getattr(att, "cid", None),
                    is_inline=False,
                )
            )

        return ParsedEmail(
            subject=msg.subject or "",
            from_=msg.sender or "",
            to=msg.to or "",
            cc=msg.cc or None,
            date=date_obj,
            body_text=body_text,
            attachments=attachments,
        )
    finally:
        msg.close()


def _guess_mime_from_name(filename: str) -> str:
    fn = filename.lower()
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith(".jpg") or fn.endswith(".jpeg"):
        return "image/jpeg"
    if fn.endswith(".gif"):
        return "image/gif"
    if fn.endswith(".kmz"):
        return "application/vnd.google-earth.kmz"
    if fn.endswith(".kml"):
        return "application/vnd.google-earth.kml+xml"
    if fn.endswith(".pdf"):
        return "application/pdf"
    return "application/octet-stream"


_BLANK_LINE_RE = re.compile(r"\n{3,}")

# Common forwarded-message boundary markers in Outlook / Gmail / Apple Mail.
# Matched against a single line of cleaned body text.
_FORWARD_BOUNDARY_RE = re.compile(
    r"""(?ix)
    (?P<marker>
        # Outlook classic divider
        ^-----\s*Original\s+Message\s*-----\s*$ |
        # Outlook "forwarded by" header
        ^-----\s*Forwarded\s+(by|message)\s+.*?-----\s*$ |
        # Apple Mail / Gmail forward header
        ^Begin\s+forwarded\s+message:\s*$ |
        # Plain header-block forward — "From: x@y" followed by Sent/To/Subject in
        # the next few lines. We match the leading "From:" line only and let
        # downstream code verify by looking at the surrounding lines.
        ^From:\s.+$
    )
    """,
    re.VERBOSE | re.MULTILINE,
)

_RECIPIENT_HEADER_RE = re.compile(
    r"^(?:To|Sent|Date|Subject|Cc):\s",
    re.IGNORECASE | re.MULTILINE,
)


def split_forwarded_body(body: str) -> tuple[str, dict]:
    """Detect a forwarded-message boundary and split.

    Returns (cleaned_body, forward_info) where forward_info is a dict with:
      - is_forwarded: bool
      - forwarder_added_text: str
      - original_body: str
      - original_from / original_to / original_date / original_subject: Optional[str]

    cleaned_body is set to original_body when forwarded; otherwise equal to body.
    """
    lines = body.split("\n")
    boundary_idx: Optional[int] = None
    boundary_kind = ""

    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^-----\s*Original\s+Message\s*-----\s*$", stripped, re.IGNORECASE):
            boundary_idx = i
            boundary_kind = "outlook_divider"
            break
        if re.match(r"^-----\s*Forwarded\s+(by|message)\b", stripped, re.IGNORECASE):
            boundary_idx = i
            boundary_kind = "outlook_fwd_header"
            break
        if re.match(r"^Begin\s+forwarded\s+message:\s*$", stripped, re.IGNORECASE):
            boundary_idx = i
            boundary_kind = "apple_gmail"
            break
        # "From: ..." line followed within 3 lines by a Sent:/Date:/To:/Subject: line
        if re.match(r"^From:\s\S", stripped, re.IGNORECASE):
            lookahead = "\n".join(lines[i + 1: i + 5])
            if _RECIPIENT_HEADER_RE.search(lookahead):
                boundary_idx = i
                boundary_kind = "header_block"
                break

    if boundary_idx is None:
        return body, {
            "is_forwarded": False,
            "forwarder_added_text": "",
            "original_body": body,
            "original_from": None,
            "original_to": None,
            "original_date": None,
            "original_subject": None,
        }

    forwarder_text = "\n".join(lines[:boundary_idx]).strip()

    # Within the original-message section, the first few lines are headers
    # (From/Sent/To/Subject) followed by a blank line, then the body.
    after = lines[boundary_idx:]
    if boundary_kind in ("outlook_divider", "outlook_fwd_header", "apple_gmail"):
        # Skip the marker line itself
        after = after[1:]

    headers: dict[str, str] = {}
    body_start_idx = 0
    for j, line in enumerate(after):
        stripped = line.strip()
        if not stripped:
            body_start_idx = j + 1
            break
        m = re.match(r"^(From|Sent|Date|To|Cc|Subject):\s*(.+)$", stripped, re.IGNORECASE)
        if m:
            headers[m.group(1).lower()] = m.group(2).strip()
            continue
        # Some forwards have continuation of prior header lines (indented). Be
        # lenient — treat anything before the first blank line as header area.
        if j == 0:
            continue
    original_body = "\n".join(after[body_start_idx:]).strip()

    return original_body, {
        "is_forwarded": True,
        "forwarder_added_text": forwarder_text,
        "original_body": original_body,
        "original_from": headers.get("from"),
        "original_to": headers.get("to"),
        "original_date": headers.get("sent") or headers.get("date"),
        "original_subject": headers.get("subject"),
    }


def clean_body_for_llm(body: str) -> str:
    """Strip signature/legal boilerplate, tracking URLs, and inline-image tags."""
    body = body.replace("\r\n", "\n").replace("\r", "\n")

    cutoffs = [
        "Connect with us:",
        "Kimley-Horn |",
        "Follow us on our socials",
        "\nResponsive People | Creative Solutions",
        "\nThis message contains confidential",
        "\nensures nondiscrimination",
        "\nis an equal opportunity employer",
    ]
    for marker in cutoffs:
        idx = body.find(marker)
        if 0 < idx < len(body):
            body = body[:idx]

    body = re.sub(r"<https?://link\.edgepilot\.com/[^>]+>", "", body)
    body = re.sub(r"<https?://[^>]+>", "", body)
    body = re.sub(r"\[cid:[^\]]+\]", "[inline image]", body)
    body = _BLANK_LINE_RE.sub("\n\n", body)
    return body.strip()


def prepare_for_extraction(parsed: ParsedEmail) -> ParsedEmail:
    """Clean body, detect forwarded layering, populate derived fields. Mutates in place."""
    parsed.body_text = clean_body_for_llm(parsed.body_text)
    original_body, fwd = split_forwarded_body(parsed.body_text)
    parsed.is_forwarded = fwd["is_forwarded"]
    parsed.forwarder_added_text = fwd["forwarder_added_text"]
    parsed.original_body = original_body
    parsed.original_from = fwd["original_from"]
    parsed.original_to = fwd["original_to"]
    parsed.original_date = fwd["original_date"]
    parsed.original_subject = fwd["original_subject"]
    return parsed
