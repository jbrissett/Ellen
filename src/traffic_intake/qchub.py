"""Drive qchub.qualitycounts.net via Playwright to create a new order.

Same architecture as mymaps.py — persistent Chromium/Edge profile, snapshot
diagnostics at every step, fail-loud with screenshots.

Order creation flow (from the user's walkthrough video, frames 01-43):
  1. Sign in to qchub
  2. Click NEW ORDER → opens "Request an Estimate" modal
  3. Fill: User (Client Contact), Office, QC Contact, Company, Project Name,
     Desired Delivery Date, Client Project Number, Comments, Attachments
  4. CONTINUE → studies + map page
  5. UPLOAD KML/KMZ → our generated KMZ
  6. Add Study Groups per unique (study_kind × subtype × time_windows) combo
  7. SUBMIT REQUEST → order created with auto-assigned ID
  8. Click PREVIEW → capture the estimate snapshot

Company/User lookup strategy (per user direction): try domain match, then
company-name match, then ask user. Auto-create flows are v2 — for now, raise
QchubCompanyNotFound / QchubUserNotFound so user can create manually first.

Subtype mapping is in `_qchub_subtype_label` (see memory project_qchub_data_model
for the full enum lists from the dropdown screenshots).
"""
from __future__ import annotations

import datetime
import json
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import httpx
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from playwright.sync_api import (
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from . import config, kml_export
from .models import (
    Estimate,
    EstimateLine,
    StudyKind,
    StudyLocation,
    StudyRequest,
    SurveySubtype,
    TMCSubtype,
    TubeSubtype,
)

ProgressCallback = Callable[[str], None]

QCHUB_BASE_URL = "https://qchub.qualitycounts.net/"


# ---------- result + errors ----------

@dataclass
class CreateOrderResult:
    order_id: Optional[str] = None
    order_url: Optional[str] = None
    estimate_snapshot: Optional[Path] = None
    diagnostic_dir: Optional[Path] = None
    note: str = ""
    # Populated when the post-submit estimate capture succeeded. None if we
    # didn't reach that step (e.g., submit failed) or if PREVIEW didn't open
    # an estimate panel.
    estimate: Optional["Estimate"] = None
    # Live session reference for Ship 2 estimate edits. The worker thread
    # owning the Playwright sync context dispatches commands from this
    # session's queue while the browser stays open post-capture. Ellen
    # tools push commands here to edit subtypes/rates and re-download.
    # None on the failure paths or once the browser has closed.
    edit_session: Optional["QchubEditSession"] = None


# ---------- estimate-edit session (Ship 2 — live browser command dispatch) ----------
#
# Playwright's sync context is single-threaded: only the worker thread that
# launched the browser may drive it. To let Ellen (running in the chat worker
# thread) make estimate edits against the live browser, we use a command
# queue + reply slot per request. The qchub worker's poll loop drains
# the queue and executes commands on the live `page`.

@dataclass
class _EditCommand:
    kind: str  # "get_lines" | "set_subtype" | "set_rate" | "apply_rate_to_all" | "re_capture"
    payload: dict
    reply: "queue.Queue[tuple[str, Any]]" = field(default_factory=lambda: queue.Queue(maxsize=1))
    cid: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class QchubEditSession:
    """Cross-thread bridge to the live qchub browser session.

    Ellen calls methods on this object from the chat worker thread. Each
    call pushes an _EditCommand onto `command_queue` and blocks waiting
    for the reply slot. The qchub worker thread (which owns the
    Playwright sync context) drains the queue and runs the command on
    the live `page`.

    `ended` is set when the qchub worker is shutting down — pending and
    future calls fail fast with a "session ended" message rather than
    hanging.
    """

    def __init__(self, order_id: Optional[str]) -> None:
        self.order_id = order_id
        self.command_queue: "queue.Queue[_EditCommand]" = queue.Queue()
        self.ended = threading.Event()
        # Set by `close()` from another thread (typically the chat worker
        # when Ellen's `end_session` tool fires). The dispatch loop polls
        # this and exits cleanly, which lets _run tear down the browser.
        self.request_close = threading.Event()

    def close(self) -> None:
        """Request a graceful shutdown of the live qchub session.

        Sets BOTH flags atomically:
          - `request_close`: tells the dispatch loop to break out and
            actually close the browser tab (the dispatch loop polls
            this every 0.5s).
          - `ended`: makes any subsequent _dispatch() call fail FAST
            with "session has ended" instead of queueing a command
            that would wait 60s for a reply the now-exiting loop will
            never produce. Without this, a tool call landing in the
            0–500ms window between request_close being set and the
            dispatch loop actually processing it would hang for 60s.

        Safe to call multiple times — both events are idempotent.
        Called by Ellen's `end_session` tool when the user signals
        the order work is done.
        """
        self.request_close.set()
        self.ended.set()

    def _dispatch(self, kind: str, payload: dict, *, timeout: float = 60.0) -> Any:
        if self.ended.is_set():
            raise RuntimeError("qchub session has ended — the browser was closed or timed out.")
        cmd = _EditCommand(kind=kind, payload=payload)
        self.command_queue.put(cmd)
        try:
            status, value = cmd.reply.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"qchub session command {kind!r} timed out after {timeout:.0f}s.")
        if status == "ok":
            return value
        raise RuntimeError(str(value))

    # ----- public API (thread-safe; called from Ellen's tool layer) -----

    def get_lines(self) -> list[dict]:
        """Re-parse the live Estimate modal and return current line state."""
        return self._dispatch("get_lines", {})

    def list_subtype_options(self, line_index: int) -> dict:
        """Return the full list of <option> values available in a given
        estimate row's subtype <select>. Use BEFORE calling set_subtype
        when you're unsure of the exact option text for a group.

        Returns: {line_index, current, options: [str, ...]}
        """
        return self._dispatch("list_subtype_options", {"line_index": int(line_index)})

    def set_subtype(self, line_index: int, subtype: str) -> dict:
        """Change the subtype <select> on a given line. Returns the new line state."""
        return self._dispatch("set_subtype", {"line_index": line_index, "subtype": subtype})

    def set_rate(self, line_index: int, unit_price: float, extra_rate: float = 0.0) -> dict:
        """Update both price fields on a Tube/TMC estimate row.

        - `unit_price` writes the base study amount (Field 1).
        - `extra_rate` writes the per-additional-unit rate (Field 2);
          defaults to 0 for fixed-amount studies.

        Survey rows have only the base field; `extra_rate` is silently
        ignored for them (the JS reports `extra_input_present: False`).
        """
        return self._dispatch("set_rate", {
            "line_index": line_index,
            "unit_price": float(unit_price),
            "extra_rate": float(extra_rate),
        })

    def apply_rate_to_all_matching(
        self, subtype_contains: str, unit_price: float, extra_rate: float = 0.0,
    ) -> dict:
        """Bulk-set base + per-unit price on every matching line.
        Returns {updated: int, lines: list[dict]}.
        """
        return self._dispatch("apply_rate_to_all", {
            "subtype_contains": subtype_contains,
            "unit_price": float(unit_price),
            "extra_rate": float(extra_rate),
        })

    def re_capture(self) -> dict:
        """Click SAVE RATES (if dirty), re-parse the modal, re-trigger
        PREVIEW + download. Returns {pdf_path, total, lines, version}.
        """
        return self._dispatch("re_capture", {}, timeout=90.0)


class QchubError(Exception):
    pass


class QchubLoginRequired(QchubError):
    pass


class QchubCompanyNotFound(QchubError):
    def __init__(self, name: str, domain: Optional[str] = None):
        super().__init__(
            f"Company not found in qchub. Tried name={name!r}, domain={domain!r}. "
            "Create the Company in qchub first, then re-run."
        )
        self.name = name
        self.domain = domain


class QchubUserNotFound(QchubError):
    def __init__(self, email: str):
        super().__init__(
            f"Client User with email {email!r} not found in qchub. "
            "Add the user in qchub first, then re-run."
        )
        self.email = email


# ==========================================================================
# AUTO-CREATE — data shapes + pre-create dupe-check helpers
# ==========================================================================
# Drives qchub's /Admin/Companies and /Admin/Users pages to register a
# missing client when the order-creation flow hits QchubCompanyNotFound /
# QchubUserNotFound. See project_qchub_auto_create.md for the full
# operational walkthrough (Noah Smith, 2026-05-18) and the locked-in
# selector map. The actual form-driving lives in `_create_company_via_admin`
# / `_create_client_user_via_admin` further down — this section is the
# pure-Python data layer that the chat side feeds.

# Required-by-form values qchub doesn't validate strictly. Per Noah's
# walkthrough: required fields accept almost any plausible value. Phone
# is required on both forms; we don't have it in the database for clients
# so we stamp a sentinel and surface a chat warning at create time.
#
# Internal storage format is "XXX-XXX-XXXX" — matches what
# `extract_first_phone` returns and what the regression tests assert
# on. Converted to qchub's required `(XXX) XXX-XXXX` shape at the
# form-fill boundary via `_format_phone_for_qchub`.
_PHONE_SENTINEL = "000-000-0000"


def _format_phone_for_qchub(phone: Optional[str]) -> str:
    """Convert a phone number into qchub's required `(XXX) XXX-XXXX`
    form (parens around area code, single space, hyphen between
    exchange and subscriber).

    Why this exists: qchub's Angular form validator on the PhoneNumber
    input REJECTS the `XXX-XXX-XXXX` shape we use internally and
    keeps the Save button disabled — observed run-20260521-205349:
    Save click timed out against `<button disabled type="submit">Save</button>`
    after all other fields had filled cleanly.

    Strategy: strip everything that isn't a digit, take the last 10
    digits, format. Tolerates "+1 804-402-9254", "(804) 402-9254",
    "804.402.9254" — anything with 10 digits in it.

    Returns the input unchanged if it doesn't have at least 10 digits
    (signals an upstream bug; better to let qchub surface a clean
    validation error than silently produce garbage).
    """
    if not phone:
        return phone or ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return phone
    last10 = digits[-10:]
    return f"({last10[:3]}) {last10[3:6]}-{last10[6:]}"


@dataclass
class CompanyInfo:
    """Everything the Add Company modal needs.

    Required by the qchub form (see project_qchub_auto_create.md selector
    map): name, phone, email, address_1, city, state, zip. The form
    also requires a Billing block; we satisfy it via the `sameAsMailing`
    checkbox at fill time, so no separate billing fields here.

    `email` is used for BOTH the AccountPayableEmail and BillingEmailAddress
    fields — per Noah, putting the client's email in both is the working
    pattern (the labels are confusing but qchub doesn't enforce billing
    distinction).
    """
    name: str
    email: str
    address_1: str
    city: str
    state: str            # full state name OR 2-letter abbrev; normalized at fill time
    zip_code: str
    phone: str = _PHONE_SENTINEL
    # Optional / defaulted
    address_2: str = ""
    fax: str = ""
    comments: str = ""
    # If set, creates this as a BRANCH of an existing company instead of a
    # top-level company. Leave None for the common case.
    parent_company_match: Optional[str] = None


@dataclass
class UserInfo:
    """Everything the Add Client User modal needs.

    Note `company_match_text`: this is the text we'll match against the
    CompanyID dropdown options in the user form. Set it to the company
    name we just created (or the matched existing company name). Token
    overlap match — same logic as the order-creation User dropdown.

    Username auto-fills from email per Noah; we don't touch it.
    """
    email: str
    first_name: str
    last_name: str
    company_match_text: str
    phone: str = _PHONE_SENTINEL
    fax: str = ""


@dataclass
class CompanyNearMatch:
    """A pre-create dupe-check hit: an existing qchub company that looks
    similar enough to the one we're about to create that the user
    should confirm before we proceed."""
    option_text: str         # full dropdown text, e.g. "3J Consulting - Beaverton, OR"
    overlap_tokens: set[str]
    score: float             # 0.0–1.0, higher = more similar


def find_company_near_matches(
    candidate_name: str,
    existing_options: list[str],
    *,
    max_matches: int = 5,
) -> list[CompanyNearMatch]:
    """Pre-create dupe-check: scan existing company dropdown options for
    near-matches to `candidate_name`. Returns the top N by token overlap.

    Reuses `_tokenize_company` so corporate-suffix noise (Inc/LLC/etc.)
    doesn't drive false positives. Subset-match (either direction) scores
    1.0; otherwise Jaccard similarity of the cleaned token sets.

    Caller passes the options it read from the `ParentCompanyID` dropdown
    on the Add Company form. The list grows as QC onboards more clients;
    nothing here depends on a fixed size.
    """
    cand_tokens = _tokenize_company(candidate_name)
    if not cand_tokens:
        return []
    matches: list[CompanyNearMatch] = []
    for opt in existing_options:
        opt_tokens = _tokenize_company(opt)
        if not opt_tokens:
            continue
        overlap = cand_tokens & opt_tokens
        if not overlap:
            continue
        if cand_tokens.issubset(opt_tokens) or opt_tokens.issubset(cand_tokens):
            score = 1.0
        else:
            score = len(overlap) / len(cand_tokens | opt_tokens)
        matches.append(CompanyNearMatch(
            option_text=opt, overlap_tokens=overlap, score=score,
        ))
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:max_matches]


# US state abbreviation → full-name map. The qchub State dropdown shows
# full names ("Alabama", "Alaska", …) and the email-signature regex we
# fall back to commonly captures 2-letter abbrevs. Normalize before fill.
_US_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


# Recognize US-format phone-number-like sequences. Tolerant of common
# separators (space, dot, dash, slash) and optional country code / parens.
# Negative lookarounds keep us from matching the middle of a longer
# digit run (e.g., order IDs or coordinates).
_PHONE_NUMBER_RE = re.compile(
    r"""
    (?<![\d.])                    # not preceded by a digit or dot (avoid IPs / decimals)
    (?:\+?1\s*[-.\s]?\s*)?        # optional country code
    \(?(\d{3})\)?                 # area code, optionally parenthesized
    [\s.\-/]+
    (\d{3})                       # exchange
    [\s.\-/]+
    (\d{4})                       # subscriber
    (?!\d)                        # not followed by another digit
    """,
    re.VERBOSE,
)

# Lines starting with one of these labels carry FAX or TTY numbers, not
# voice phones. Skip them when scanning for the contact phone.
_NON_VOICE_LINE_PREFIX = re.compile(
    r"^\s*(?:fax|f|tty)\s*[:.\s]",
    re.IGNORECASE,
)


def extract_first_phone(text: Optional[str]) -> Optional[str]:
    """Pull the first voice phone number out of `text`, normalized to
    `XXX-XXX-XXXX`. Returns None when no phone is found.

    Per user direction 2026-05-18 PM: email signatures often have
    multiple labeled phones (`M:`, `O:`, `Direct:`, `Mobile:`, …); use
    the FIRST one in order of appearance. Skip lines that start with a
    fax / TTY label — those aren't voice phones.

    Caller falls back to `_PHONE_SENTINEL` ("000-000-0000") when this
    returns None.
    """
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _NON_VOICE_LINE_PREFIX.match(stripped):
            continue
        match = _PHONE_NUMBER_RE.search(stripped)
        if match:
            area, exchange, sub = match.groups()
            return f"{area}-{exchange}-{sub}"
    return None


# Address-line regex: "City, ST 12345" or "City, ST 12345-6789" on its own
# line. The street address is then the previous non-blank line. Tolerant
# of leading whitespace; case-sensitive on the state code (US conventions).
_ADDRESS_CITY_STATE_ZIP_RE = re.compile(
    r"^[\t ]*([^,\n]{2,80}),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$",
    re.MULTILINE,
)


def extract_address_from_signature(
    body: Optional[str],
) -> tuple[str, str, str, str]:
    """Best-effort parse of street/city/state/zip from an email body.

    Strategy:
      1. Find a 'City, ST 12345' line (with or without ZIP+4).
      2. Take the previous non-blank line as the street, IF it looks
         address-like (contains a digit OR mentions 'P.O. Box').
      3. Prefer the LAST such match in the body — signatures are at the
         end and may be preceded by similar-looking text in a quoted
         reply / forwarded email.

    Returns a 4-tuple of strings. Missing pieces come back as empty
    strings so the caller can decide whether to bail or use defaults.

    Works for the most common signature shapes:
      Acme Engineering
      8401 Arlington Blvd
      Fairfax, VA 22031
    and for suite/PO Box variants. Single-line "pipe-separated"
    signatures (`Name | Title | Firm | Address`) are out of scope —
    let Ellen surface those cases manually if they ever come up.
    """
    if not body:
        return ("", "", "", "")
    matches = list(_ADDRESS_CITY_STATE_ZIP_RE.finditer(body))
    if not matches:
        return ("", "", "", "")
    match = matches[-1]
    city = match.group(1).strip()
    state = match.group(2)
    zip_code = match.group(3)
    pre = body[:match.start()].rstrip("\r\n \t")
    prev_nl = pre.rfind("\n")
    street_candidate = pre[prev_nl + 1:].strip() if prev_nl >= 0 else pre.strip()
    if street_candidate and (
        re.search(r"\d", street_candidate) or
        re.search(r"P\.?\s*O\.?\s+Box", street_candidate, re.IGNORECASE)
    ):
        return (street_candidate, city, state, zip_code)
    return ("", city, state, zip_code)


def split_full_name(full_name: Optional[str]) -> tuple[str, str]:
    """Split a contact name into (first, last). Handles both:
      - "Last, First [Middle]"  → ("First Middle", "Last")
      - "First [Middle...] Last" → ("First", "Last") — middle dropped

    Empty / unparseable returns ("", "").
    """
    if not full_name:
        return ("", "")
    s = full_name.strip()
    if not s:
        return ("", "")
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        last = parts[0]
        first = parts[1] if len(parts) > 1 else ""
        return (first, last)
    parts = s.split()
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], parts[-1])


def normalize_state_to_full_name(state: Optional[str]) -> str:
    """Map a state value to qchub's State dropdown label (full name).

    Accepts: full name (any case), 2-letter abbrev (any case), empty/None.
    Returns: matching full name, or the input unchanged if not recognized
    (caller decides whether to fail or fall through to user confirmation).
    """
    s = (state or "").strip()
    if not s:
        return ""
    if len(s) == 2 and s.upper() in _US_STATE_NAMES:
        return _US_STATE_NAMES[s.upper()]
    for full in _US_STATE_NAMES.values():
        if full.lower() == s.lower():
            return full
    return s


# ---------- subtype mapping (StudyRequest → qchub dropdown label) ----------
# Defaults are documented in memory project_qchub_data_model. These are the
# initial values our app picks; the qchub user (or chat) refines from there.

_TMC_DEFAULT_LABEL = {
    TMCSubtype.STANDARD: "Turn Count -- Standard",
    TMCSubtype.LARGE: "Turn Count -- Large",
    TMCSubtype.COMPLEX: "Turn Count -- Complex",
}

# Tube subtype labels confirmed against the qchub Tube Counts dropdown
# (screenshot 2026-05-14, edit order 176376). The dropdown shows the
# abbreviated names below — NOT the older verbose forms ('Volume -- Volume
# Radar Count', 'Speed, Class, Volume -- Radar 1-3 Lanes') seen in earlier
# memory entries. We pick by `_select_by_match` which does contains-match,
# so the label is matched into the dropdown's <option> text.
_TUBE_DEFAULT_LABEL = {
    TubeSubtype.VIDEO_ATR_VOLUME: "Video ATR - Volume",  # ATR video volume counts
    TubeSubtype.VOLUME: "Volume",
    TubeSubtype.VOLUME_CLASS: "Volume, Class",
    TubeSubtype.VOLUME_SPEED: "Volume, Speed",
    TubeSubtype.VOLUME_SPEED_CLASS: "Volume, Speed, Class",
}

# Survey > Service Subtype labels — full dropdown captured from
# screenshots 2026-05-17 PM. Listed in qchub's alphabetical dropdown
# order. Both 'Custom Video Survey' and 'Custom Non-Video Survey'
# surface a Custom Survey NAME input — provide via `survey_custom_name`
# on the StudyLocation.
_SURVEY_DEFAULT_LABEL = {
    SurveySubtype.BLUETOOTH_SURVEY: "Bluetooth Survey",
    SurveySubtype.DATAPOINT_SUBSCRIPTION: "DataPoint Subscription",
    SurveySubtype.DELAY_STUDY: "Delay Study",
    SurveySubtype.EQUIPMENT_RENTAL: "Equipment Rental",
    SurveySubtype.FLOATING_CAR_TRAVEL_TIME: "Floating Car Travel Time Survey",
    SurveySubtype.HANDHELD_RADAR_SURVEY: "Handheld Radar Survey",
    SurveySubtype.HISTORICAL_DATA: "Historical Data",
    SurveySubtype.HORIZONTAL_CURVE_ADVISORY_SPEED: "Horizontal Curve Advisory Speed Survey",
    SurveySubtype.INTERVIEW_SURVEY: "Interview Survey",
    SurveySubtype.LICENSE_PLATE_OD: "License Plate O-D Study",
    SurveySubtype.OCCUPANCY_SURVEY: "Occupancy Survey",
    SurveySubtype.PARKING_STUDY: "Parking Study",
    SurveySubtype.PEDESTRIAN_VOLUME: "Pedestrian Volume Counts",
    SurveySubtype.QUEUE_STUDY: "Queue Study",
    SurveySubtype.ROAD_INVENTORY: "Road Inventory Surveys",
    SurveySubtype.SATURATION_FLOW_RATE: "Saturation Flow Rate Study",
    SurveySubtype.SUPPORT_SERVICES: "Support Services",
    SurveySubtype.TRANSIT_SURVEY: "Transit Survey",
    SurveySubtype.VEHICULAR_GAP_STUDY: "Vehicular Gap Study (Video)",
    SurveySubtype.VIDEO_SURVEILLANCE: "Video Surveillance",
    # Both Custom subtypes' option text ends with "..." in the qchub
    # dropdown (the "..." indicates clicking opens a name-input prompt).
    # The label must match EXACTLY for select_option to succeed — verified
    # via DevTools dump 2026-05-17 PM.
    SurveySubtype.CUSTOM_NON_VIDEO_SURVEY: "Custom Non-Video Survey...",  # surfaces a Custom Survey name input
    SurveySubtype.CUSTOM_VIDEO_SURVEY: "Custom Video Survey...",          # surfaces a Custom Survey name input
}


def _qchub_subtype_label(loc: StudyLocation) -> str:
    if loc.study_kind == StudyKind.TURNING_MOVEMENT:
        return _TMC_DEFAULT_LABEL[loc.tmc_subtype or TMCSubtype.STANDARD]
    if loc.study_kind == StudyKind.SURVEY:
        return _SURVEY_DEFAULT_LABEL.get(
            loc.survey_subtype or SurveySubtype.VEHICULAR_GAP_STUDY, "Vehicular Gap Study (Video)"
        )
    return _TUBE_DEFAULT_LABEL[loc.tube_subtype or TubeSubtype.VOLUME]


# ---------- snapshot diagnostics (same pattern as mymaps.py) ----------

def _new_run_dir() -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = config.app_data_dir() / "qchub-diagnostics" / f"run-{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_step_snapshot(page: Optional[Page], run_dir: Path, step_index: int, step: str) -> None:
    if page is None:
        return
    sub = run_dir / f"{step_index:02d}-{step}"
    sub.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(sub / "screenshot.png"), full_page=True)
    except Exception:
        pass
    try:
        (sub / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        (sub / "url.txt").write_text(getattr(page, "url", "unknown"), encoding="utf-8")
    except Exception:
        pass


def _kill_orphan_browsers_on_profile(profile_dir: Path, log: ProgressCallback) -> None:
    """Kill any msedge/chrome.exe processes whose command line references our
    profile dir. These leftover processes from a crashed or improperly-closed
    previous run hold the profile lock and make launch_persistent_context fail.

    Windows-only (where this manifests). No-op elsewhere.
    """
    if sys.platform != "win32":
        return
    profile_str = str(profile_dir)
    # Escape backslashes + single quotes for PowerShell -Command embedding.
    ps_profile = profile_str.replace("'", "''")
    ps_script = (
        "$procs = Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" "
        "-ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.CommandLine -like '*{ps_profile}*' }}; "
        "foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; "
        "$procs.Count"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        count_text = (result.stdout or "").strip().splitlines()[-1] if result.stdout else "0"
        try:
            count = int(count_text)
        except ValueError:
            count = 0
        if count > 0:
            log(f"Killed {count} orphan browser process(es) holding the profile.")
            # Give Windows a beat to release the file locks before we launch.
            time.sleep(1.5)
    except Exception as exc:
        log(f"(Couldn't scan for orphan browser processes: {exc} — proceeding)")


def _save_failure_snapshot(page: Optional[Page], run_dir: Path, step: str, exc: BaseException) -> None:
    if page is None:
        return
    sub = run_dir / f"FAILURE-{step}"
    sub.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(sub / "screenshot.png"), full_page=True)
    except Exception:
        pass
    try:
        (sub / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        (sub / "details.txt").write_text(
            f"Step: {step}\nURL: {getattr(page, 'url', 'unknown')}\n"
            f"Exception: {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------- public entry ----------

def create_qchub_order(
    request: StudyRequest,
    *,
    qc_office: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    on_ready: Optional[Callable[["CreateOrderResult"], None]] = None,
    login_timeout_sec: int = 300,
    manual_finish_timeout_sec: int = 2 * 60 * 60,
) -> CreateOrderResult:
    """Drive qchub end-to-end to create an order for the given StudyRequest.

    Per-group KMLs are built internally (kml_export.build_kml_for_locations)
    and uploaded one-at-a-time after each CREATE GROUP succeeds — that's
    John Goodwin's documented workflow (2026-05-14). The pre-refactor flow
    used a single bulk KML at the end, which left qchub unable to assign
    locations to study groups.

    After the automation reaches the stop point (modal filled + all groups
    created + their KMLs uploaded), `on_ready(result)` is invoked so the
    caller can report success immediately. The function then BLOCKS, keeping
    the browser open for up to `manual_finish_timeout_sec`, so the user can
    verify and click SUBMIT REQUEST without the Playwright context tearing
    down their session.

    Raises QchubCompanyNotFound or QchubUserNotFound if entities don't exist
    yet. All other failures raise QchubError with a diagnostic snapshot path.
    """
    log = progress or (lambda s: None)
    profile_dir = config.app_data_dir() / "qchub-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        return _run(
            p, request, profile_dir, qc_office, log,
            login_timeout_sec,
            on_ready=on_ready,
            manual_finish_timeout_sec=manual_finish_timeout_sec,
        )


def _run(
    p: Playwright,
    request: StudyRequest,
    profile_dir: Path,
    qc_office: Optional[str],
    log: ProgressCallback,
    login_timeout_sec: int,
    *,
    on_ready: Optional[Callable[["CreateOrderResult"], None]] = None,
    manual_finish_timeout_sec: int = 2 * 60 * 60,
) -> CreateOrderResult:
    run_dir = _new_run_dir()
    log(f"(qchub diagnostics → {run_dir})")

    # Persist the StudyRequest as JSON at run start, and tee every log() call
    # to run.log on disk. Both are critical for diagnosing complex orders
    # where the on-page state alone can't tell us whether the upstream
    # request was shaped correctly. Added 2026-05-14 after UDOT/Avenue
    # Consultants run produced 1 group when 4 were expected — there was no
    # way to tell whether `_add_study_groups` saw 4 groups or 1.
    try:
        (run_dir / "request.json").write_text(
            request.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
    except Exception as exc:
        log(f"(Couldn't write request.json: {exc})")

    run_log_path = run_dir / "run.log"
    try:
        run_log_fh = run_log_path.open("a", encoding="utf-8")
    except Exception as exc:
        log(f"(Couldn't open run.log: {exc})")
        run_log_fh = None

    if run_log_fh is not None:
        _original_log = log
        def _tee_log(msg: str) -> None:
            try:
                run_log_fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}\n")
                run_log_fh.flush()
            except Exception:
                pass
            _original_log(msg)
        log = _tee_log

    # Kill any orphan Edge/Chrome processes still holding our profile from a
    # prior run. Without this, launch_persistent_context fails with a confusing
    # 'Target page, context or browser has been closed' — the binary launches,
    # finds the profile locked, dies. (Diagnosed 2026-05-13.)
    _kill_orphan_browsers_on_profile(profile_dir, log)

    log("Launching browser…")
    context = None
    last_err: Optional[BaseException] = None
    for channel in ("msedge", None):
        try:
            from .runtime_settings import is_headless_mode
            kwargs: dict = dict(
                user_data_dir=str(profile_dir),
                # Headless toggle is a user-settable preference (Settings
                # dialog) — production / mass-deploy installs default ON
                # so qchub automation runs invisibly; dev / diagnostic
                # runs default OFF so the user can watch.
                headless=is_headless_mode(),
                viewport={"width": 1400, "height": 900},
                accept_downloads=True,
                # Drop the --enable-automation switch. User confirmed
                # 2026-05-14 that Edge surfaces the "controlled by automated
                # test software" banner AND suppresses downloads in this
                # session. Removing the flag hides the banner and lets
                # qchub's PDF downloads come through normally. The qchub
                # estimate-capture flow also falls back to a network
                # response listener if Edge still doesn't fire a download
                # event — see `_capture_estimate`.
                ignore_default_args=["--enable-automation"],
                # Additional belt-and-suspenders so Edge's automation
                # heuristics don't kick in for unrelated reasons (e.g.,
                # AutomationControlled feature blocking some JS APIs).
                args=["--disable-blink-features=AutomationControlled"],
            )
            if channel:
                kwargs["channel"] = channel
            context = p.chromium.launch_persistent_context(**kwargs)
            break
        except BaseException as exc:
            last_err = exc
            continue
    if context is None:
        raise QchubError(f"Couldn't launch browser. Last error: {last_err}")

    page: Optional[Page] = None
    step = "launch"
    step_idx = 0
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(30_000)

        step = "navigate"
        log(f"Opening {QCHUB_BASE_URL}…")
        page.goto(QCHUB_BASE_URL, wait_until="domcontentloaded")
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "sign-in"
        page = _ensure_signed_in(context, page, log, login_timeout_sec)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "open-new-order"
        log("Opening NEW ORDER…")
        _open_new_order(page, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        # Fill the Request Estimate modal. If qchub raises a "not found" for
        # the company or the client user, run the silent auto-create flow
        # and retry ONCE. Per user direction 2026-05-18 PM: new clients are
        # routine — no confirm dialog, just do it. If auto-create itself
        # fails (missing address in signature, qchub form rejected the
        # save), the exception re-raises and the existing missing-entity
        # modal in app.py shows as a last-resort fallback.
        step = "fill-request-estimate"
        log("Filling Request Estimate modal…")
        auto_create_attempted = False
        while True:
            try:
                _fill_request_estimate(page, request, qc_office, log)
                break
            except (QchubCompanyNotFound, QchubUserNotFound) as exc:
                if auto_create_attempted:
                    log(
                        f"⚠ {type(exc).__name__} still raised after auto-create — "
                        "giving up. The 'add manually' modal will fall through."
                    )
                    raise
                auto_create_attempted = True
                log(
                    f"⚠ {type(exc).__name__}: {exc} — kicking off silent auto-create."
                )
                step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, "auto-create-triggered")
                ok = auto_create_for_missing_entity(
                    page, request, log, run_dir,
                )
                step_idx += 1; _save_step_snapshot(
                    page, run_dir, step_idx,
                    "auto-create-success" if ok else "auto-create-failed",
                )
                if not ok:
                    log("Auto-create couldn't complete — surfacing original exception.")
                    raise
                _return_to_dashboard_and_open_new_order(page, log)
                # Loop back to retry _fill_request_estimate.
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "continue-to-studies"
        log("Continuing to studies/map page…")
        _click_continue(page, request, qc_office, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        # qchub auto-opens the Add Study Group dialog after Continue. For
        # each planned group: configure it, CREATE GROUP, and immediately
        # upload that group's KML while the group is the active selection.
        # This is John Goodwin's documented workflow (2026-05-14) — replaces
        # the older pattern of "create all groups, then one bulk KML upload"
        # which left qchub unable to assign locations to groups.
        step = "add-study-groups-and-upload-kmls"
        log("Adding study groups (each with its own KML upload)…")
        _add_study_groups(page, request, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        # Submit the order. _submit_request handles the "Your order has been
        # submitted" dialog and reads the order ID from the /Admin/Orders/...
        # redirect URL.
        step = "submit-request"
        log("Submitting request…")
        order_id, order_url = _submit_request(page, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        # If we got an order ID, open the Estimate modal and capture its
        # priced lines. This is the first step in the review loop — Ellen
        # surfaces the captured estimate in chat so the user can see the
        # current subtypes/prices/quantities without leaving the app.
        # Editing-via-Ellen is a follow-up (Ship 2/3).
        estimate: Optional[Estimate] = None
        if order_id:
            step = "capture-estimate"
            try:
                estimate = _capture_estimate(page, order_id, run_dir, log)
            except Exception as exc:
                log(f"(Estimate capture failed: {exc} — proceeding without it)")
            step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        if order_id:
            note_parts = [f"Order {order_id} submitted."]
            if estimate and estimate.lines:
                total_str = (
                    f"~${estimate.total:,.2f}" if estimate.total is not None else "total unknown"
                )
                note_parts.append(
                    f"Estimate captured ({len(estimate.lines)} line(s), {total_str})."
                )
            elif estimate:
                note_parts.append("Estimate captured (no lines parsed — see HTML).")
            note_parts.append("Browser left open for verification.")
            note = " ".join(note_parts)
        else:
            note = "Submit clicked but order ID wasn't captured. Verify in the browser."

        # Create the edit session BEFORE firing on_ready, so the chat
        # layer can attach to it immediately. The session's command queue
        # will be drained by `_dispatch_edit_commands_until_done` below.
        edit_session = QchubEditSession(order_id=order_id)

        result = CreateOrderResult(
            order_id=order_id,
            order_url=order_url or page.url,
            estimate_snapshot=Path(estimate.screenshot_path) if (estimate and estimate.screenshot_path) else None,
            diagnostic_dir=run_dir,
            note=note,
            estimate=estimate,
            edit_session=edit_session,
        )

        # Fire the success callback NOW so the caller can post the chat note
        # while we keep the browser session alive in the background.
        if on_ready is not None:
            try:
                on_ready(result)
            except Exception as exc:
                log(f"(on_ready callback raised: {exc})")

        # Stay alive: poll the edit-session command queue while also
        # checking for browser-closed. Replaces the old wait_for_event
        # so Ellen can drive subtype/rate edits + re-capture against
        # the live page.
        _dispatch_edit_commands_until_done(
            context, page, edit_session, order_id, log,
            timeout_sec=manual_finish_timeout_sec,
        )

        return result
    except QchubError as exc:
        _save_failure_snapshot(page, run_dir, step, exc)
        # Attach the diagnostic path to the message so the UI can show it
        if not str(exc).endswith(str(run_dir)):
            exc.args = ((exc.args[0] if exc.args else str(exc)) + f" (diagnostics: {run_dir})",)
        raise
    except BaseException as exc:
        _save_failure_snapshot(page, run_dir, step, exc)
        raise QchubError(f"Step '{step}' failed: {type(exc).__name__}: {exc} — diagnostics: {run_dir}") from exc
    finally:
        # Leave browser open so the user can verify / continue manually
        if run_log_fh is not None:
            try:
                run_log_fh.close()
            except Exception:
                pass


# ---------- step implementations (placeholder selectors — first run will reveal real ones) ----------

def _ensure_signed_in(context, page: Page, log: ProgressCallback, timeout_sec: int) -> Page:
    """Detect login form by element presence (URL doesn't change between
    login / dashboard). If a login form is visible:
      - If we have saved credentials, auto-fill and click Sign In
      - Otherwise wait for the user to sign in manually
    Then wait for the dashboard to load (NEW ORDER button as the signal).
    """
    import time

    # Has the dashboard already loaded? Look for the NEW ORDER green button text.
    if _dashboard_visible(page):
        log("Already signed in (dashboard detected).")
        return page

    # We're on the login page if the username/email input is visible.
    if not _login_form_visible(page):
        # Give the page a moment to settle, then re-check
        page.wait_for_timeout(1500)
        if _dashboard_visible(page):
            log("Already signed in.")
            return page

    creds = config.get_qchub_credentials()
    if creds is not None:
        username, password = creds
        log("Auto-filling qchub credentials from saved keyring…")
        try:
            page.get_by_placeholder(re.compile(r"username|email", re.IGNORECASE)).first.fill(username, timeout=8_000)
            page.get_by_placeholder(re.compile(r"password", re.IGNORECASE)).first.fill(password, timeout=8_000)
            page.get_by_role("button", name=re.compile(r"^\s*sign\s*in\s*$", re.IGNORECASE)).first.click(timeout=8_000)
        except Exception as exc:
            log(f"(Auto-fill failed: {exc} — switching to manual sign-in)")
    else:
        log("No qchub credentials saved — please sign in manually in the browser window.")

    log("Waiting for qchub dashboard…")
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        for candidate in context.pages:
            try:
                if _dashboard_visible(candidate):
                    log("Dashboard detected.")
                    return candidate
            except Exception:
                continue
        time.sleep(1.0)

    raise QchubLoginRequired(
        f"Didn't reach the qchub dashboard within {timeout_sec}s. "
        "If sign-in failed (wrong credentials, MFA prompt, etc.), fix it and try again."
    )


def _login_form_visible(page: Page) -> bool:
    try:
        return page.get_by_placeholder(re.compile(r"username|email", re.IGNORECASE)).first.is_visible(timeout=1_500)
    except Exception:
        return False


def _dashboard_visible(page: Page) -> bool:
    """The dashboard reliably shows a green 'NEW ORDER' button in the top-left of
    the Order List panel. Use that as the signed-in indicator.
    """
    try:
        return page.get_by_text(re.compile(r"^\s*new\s+order\s*$", re.IGNORECASE)).first.is_visible(timeout=1_500)
    except Exception:
        return False


def _open_new_order(page: Page, log: ProgressCallback) -> None:
    """Click NEW ORDER on the dashboard.

    From video frame 01: a green "NEW ORDER" button is visible in the top-left
    of the Order List panel. Selectors here are placeholders to be refined after
    first end-to-end snapshot run.
    """
    candidates = [
        re.compile(r"^\s*new order\s*$", re.IGNORECASE),
        re.compile(r"^\s*\+\s*new order\s*$", re.IGNORECASE),
        re.compile(r"^\s*request an estimate\s*$", re.IGNORECASE),
    ]
    for pattern in candidates:
        try:
            page.get_by_role("button", name=pattern).first.click(timeout=8_000)
            return
        except (PlaywrightTimeoutError, Exception):
            continue
    for pattern in candidates:
        try:
            page.get_by_text(pattern).first.click(timeout=5_000)
            return
        except (PlaywrightTimeoutError, Exception):
            continue
    raise QchubError("Couldn't find a NEW ORDER button on the dashboard.")


def _fill_request_estimate(
    page: Page,
    request: StudyRequest,
    qc_office: Optional[str],
    log: ProgressCallback,
) -> None:
    """Fill the Request Estimate modal — HYBRID Playwright + computer-use.

    Native HTML <select> popups are OS-rendered and don't appear in Playwright
    screenshots, so computer-use can't see the options to click. We use
    Playwright `select_option` (via _select_by_match) for the dropdowns —
    that works fine, bypasses the popup-invisibility problem entirely. We
    use computer-use only for the duplicate-id text inputs that Playwright
    selectors can't reliably target (Project Name has two `id="projectName"`
    inputs; the visible one varies by render).

    Order matters:
      1. Playwright fills *User → triggers QC Contact cascade
      2. Playwright fills *Office (independent of cascade)
      3. Playwright waits for QC Contact options → fills
      4. Playwright fills *Company if not auto-populated
      5. computer-use fills Project Name + Client Project Number + Comments
         (text inputs only — no dropdown interaction)
      6. Python verifies all required fields are set
    """
    project_name = _build_project_name(request)
    user_variants = _user_match_variants(request)
    office_variants = [qc_office] if qc_office else _build_office_variants(request)
    qc_variants = _build_qc_contact_variants(request)
    company_variants = _company_match_variants(request) if request.client_company else []

    log(f"Project name: {project_name!r}")
    log(f"User candidates: {user_variants!r}")
    log(f"Office candidates: {office_variants!r}")
    log(f"QC Contact candidates: {qc_variants!r}")
    log(f"Company candidates: {company_variants!r}")

    # ---- Step 1: User (triggers QC Contact cascade) ----
    # IMPORTANT: User must be matched STRICTLY by email. The dropdown is a
    # global list of every qchub user (~thousands); name-based contains-match
    # picks the wrong person from a different company when names collide
    # (observed 2026-05-13: BGE client request landed on a Faller, Davis user).
    email = (request.client_contact_email or "").strip()
    if email:
        log(f"Selecting *User by email {email!r}…")
        # Timeout bumped 15→30s 2026-05-25 after observing a real
        # client run where the User dropdown didn't load in 15s,
        # causing a false "user not found" that triggered an
        # unnecessary auto-create. The User dropdown carries 6000+
        # entries — slower than other dropdowns on slow networks.
        _wait_for_dropdown_loaded(page, "*User", log, timeout_sec=30)
        if not _select_by_match(page, "*User", [email], log=log):
            raise QchubUserNotFound(email)
    elif user_variants:
        # No email available — fall back to name match, but log a warning
        # since name collisions are likely in a global dropdown.
        log(f"⚠ No client_contact_email — falling back to name match: {user_variants!r}")
        # Timeout bumped 15→30s 2026-05-25 after observing a real
        # client run where the User dropdown didn't load in 15s,
        # causing a false "user not found" that triggered an
        # unnecessary auto-create. The User dropdown carries 6000+
        # entries — slower than other dropdowns on slow networks.
        _wait_for_dropdown_loaded(page, "*User", log, timeout_sec=30)
        if not _select_by_match(page, "*User", user_variants, log=log):
            raise QchubUserNotFound(user_variants[0] if user_variants else "(unknown)")

    # Brief settle for the User cascade. The longer (60s) pre-wait for the
    # QC Contact dropdown to populate was removed 2026-05-14 after profiling —
    # QC Contact options don't actually populate until Office is set, so the
    # pre-wait always timed out at the full 60s before we'd even attempted
    # Office. The sticky-retry helper used for both Office and QC Contact
    # already handles cascade timing on its own (2s wait between attempts).
    # Pure savings: ~60s off the per-order critical path.
    page.wait_for_timeout(500)

    # ---- Step 3: Office (sticky retry — cascade tends to wipe post-set) ----
    if office_variants:
        log(f"Selecting *Office (with sticky-retry to survive cascade wipe)…")
        _select_and_verify_sticks(page, "*Office", office_variants, log)

    # ---- Step 4: QC Contact — always attempt (sticky retry + first-option
    # fallback). Per user direction 2026-05-14: WHO is non-essential; just make
    # sure SOMETHING is set so qchub accepts the form.
    log("Selecting *QC Contact (any option — first available)…")
    _select_and_verify_sticks(
        page, "*QC Contact", qc_variants or [], log,
        use_first_option_fallback=True,
    )

    # ---- Step 5: Company (auto-populated by cascade, often disabled/locked) ----
    # qchub locks the Company field after the User-cascade narrows it to one
    # option (e.g., 'BGE, Inc. - Katy'). The select is disabled so we can't set
    # it via UI — we just verify the auto-populated value matches our request.
    if company_variants:
        company_real_opts = _read_company_options(page)
        if not company_real_opts:
            domain = (request.client_contact_email or "").split("@", 1)[-1] or None
            raise QchubCompanyNotFound(request.client_company, domain)
        if not _company_matches_variants(company_real_opts, company_variants):
            domain = (request.client_contact_email or "").split("@", 1)[-1] or None
            raise QchubError(
                f"qchub auto-populated *Company with {company_real_opts!r} after picking "
                f"User by email {request.client_contact_email!r}, but that doesn't match the "
                f"request ({request.client_company!r}, domain {domain!r}). Wrong User picked, "
                "or the User in qchub is associated with a different Company. Check the "
                "User's Company in qchub admin."
            )
        log(f"(*Company auto-populated by cascade: {company_real_opts!r})")

    # ---- Step 6: Text inputs via direct Playwright fills ----
    # Project Name has a duplicate id="projectName" in the DOM (template +
    # live), so we select by visible placeholder and take the LAST match — the
    # rendered live input. Client Project Number has the same risk; same fix.
    # This used to be a computer-use call, but the screenshot tokens chewed
    # through the API rate limit on multi-turn fills.
    log(f"Filling Project Name: {project_name!r}")
    _fill_text_input(page, "*Project Name", project_name, log)
    if request.client_project_number:
        log(f"Filling Client Project Number: {request.client_project_number!r}")
        _fill_text_input(page, "Client Project Number", request.client_project_number, log)

    # ---- Step 7: Verify all required fields ----
    # The sticky-retry helper handles cascade-wipe directly, so no separate
    # last-mile refill is needed here. _click_continue still does a final
    # defensive sticky refill right before clicking, in case anything wiggled
    # between verify and click (e.g., a focus change from the text fill).
    _verify_required_fields(page, log)


def _wait_for_select_options_loaded(
    select_locator, log: ProgressCallback,
    *, min_options: int = 50, timeout_sec: float = 15.0,
    label: str = "select",
) -> list[str]:
    """Wait until an async-populated `<select>` has its options loaded
    from qchub's server, then return ALL option text contents.

    Why this exists: the admin-form selects (ParentCompanyID on the
    Add Company modal, CompanyID on the Add Client User modal, etc.)
    start with just a placeholder `<option>` and are populated by
    Angular from a server query a few hundred ms to a few seconds
    AFTER the modal opens. Cold-profile runs (no warm cache) have
    been observed taking up to 3s. Reading too early returns just
    the placeholder — caller then concludes "company not in
    dropdown" and either skips a dupe check or fails verification.

    Polls until `option.count() >= min_options` OR timeout. Returns
    whatever options exist at exit (caller decides if that's enough).
    Logs a one-line progress trace so we can see in the run log
    whether the dropdown loaded fast (warm) or slow (cold).
    """
    import time
    deadline = time.monotonic() + timeout_sec
    last_count = -1
    waited_at_least_once = False
    while time.monotonic() < deadline:
        try:
            count = select_locator.locator("option").count()
        except Exception:
            count = 0
        if count >= min_options:
            if waited_at_least_once:
                log(f"  ({label} loaded: {count} options)")
            try:
                return select_locator.locator("option").all_text_contents()
            except Exception:
                return []
        if count != last_count:
            log(f"  ({label} has {count} options — waiting for more…)")
            last_count = count
            waited_at_least_once = True
        time.sleep(0.3)
    log(
        f"  ⚠ {label} only reached {last_count} options in {timeout_sec}s "
        f"(target was {min_options}+); reading what we have."
    )
    try:
        return select_locator.locator("option").all_text_contents()
    except Exception:
        return []


def _wait_for_dropdown_loaded(page: Page, placeholder: str, log: ProgressCallback, timeout_sec: float = 20.0) -> bool:
    """Wait until the visible enabled <select placeholder="..."> has real options.

    qchub renders multiple template instances of the same placeholder; we filter
    to `:visible:not([disabled])` to target only the live one. Cascade Ajax for
    QC Contact takes 8–15s observed, so we long-poll with a generous timeout.
    """
    import time
    selector = f'select[placeholder="{placeholder}"]:visible:not([disabled])'
    deadline = time.monotonic() + timeout_sec
    last_count = -1
    while time.monotonic() < deadline:
        try:
            loc = page.locator(selector).first
            opts = loc.locator("option").all_text_contents()
            real_opts = [o for o in opts if o.strip() and o.strip() != placeholder]
            if real_opts:
                if last_count != len(real_opts):
                    log(f"({placeholder!r} has {len(real_opts)} options)")
                return True
            if last_count != len(real_opts):
                log(f"({placeholder!r} still empty — waiting for cascade…)")
                last_count = len(real_opts)
        except Exception:
            pass
        time.sleep(0.4)
    log(f"(Timed out after {timeout_sec}s waiting for {placeholder!r} options)")
    return False


def _select_by_match(page: Page, placeholder: str, variants: list[str], *, log: ProgressCallback) -> bool:
    """Select an option in the visible enabled <select> by best-match across variants.

    For each variant, pass 1 tries exact (case-insensitive) match, pass 2 tries
    contains-match (skipping ≤2-char variants to avoid false positives like 'mix'
    matching inside 'Mathew Yeoman'). Uses Playwright `select_option` with a JS
    fallback that fires the change events Angular's ControlValueAccessor expects.
    """
    selector = f'select[placeholder="{placeholder}"]:visible:not([disabled])'
    try:
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=8_000)
        opts = locator.locator("option").all_text_contents()
    except Exception as exc:
        log(f"(Couldn't read {placeholder!r} options: {exc})")
        return False

    real_opts = [o for o in opts if o.strip() and o.strip() != placeholder]
    if not real_opts:
        log(f"(No real options loaded in {placeholder!r})")
        return False

    # Pass 1: exact (case-insensitive)
    for v in variants:
        v_norm = v.strip().lower()
        if not v_norm:
            continue
        for opt in real_opts:
            if opt.strip().lower() == v_norm:
                if _set_select_value(locator, opt):
                    log(f"({placeholder!r} → {opt!r} [exact: {v!r}])")
                    return True

    # Pass 2: contains (case-insensitive). Skip very-short variants.
    for v in variants:
        v_norm = v.strip().lower()
        if not v_norm or len(v_norm) <= 2:
            continue
        for opt in real_opts:
            if v_norm in opt.lower():
                if _set_select_value(locator, opt):
                    log(f"({placeholder!r} → {opt!r} [contains: {v!r}])")
                    return True

    sample = real_opts[:6]
    log(f"(No {placeholder!r} match for {variants!r}. Sample options: {sample}{' …' if len(real_opts) > 6 else ''})")
    return False


def _read_company_options(page: Page) -> list[str]:
    """Read real (non-placeholder) options from the *Company select, INCLUDING
    when the select is disabled. The cascade locks Company after narrowing it
    to one option, so we have to read the disabled element to verify it.
    """
    try:
        # Don't exclude disabled — the live Company select is often disabled after cascade
        selects = page.locator('select[placeholder="*Company"]:visible').all()
    except Exception:
        return []
    for sel in selects:
        try:
            opts = sel.locator("option").all_text_contents()
            real = [o.strip() for o in opts if o.strip() and o.strip() != "*Company"]
            if real:
                return real
        except Exception:
            continue
    return []


_COMPANY_NOISE_TOKENS = {
    "inc", "llc", "ltd", "co", "corp", "corporation", "company",
    "the", "and", "of", "a",
}


def _tokenize_company(s: str) -> set[str]:
    """Split a firm name into normalized alphanumeric tokens, dropping
    corporate-suffix noise. Keeps single-char tokens because hyphenated
    firms like 'J-U-B' carry meaning in those fragments.
    """
    words = re.findall(r"[a-z0-9]+", (s or "").lower())
    return {w for w in words if w not in _COMPANY_NOISE_TOKENS}


def _company_matches_variants(real_options: list[str], variants: list[str]) -> bool:
    """Token-overlap match between request firm-name variants and the
    cascade-narrowed Company option(s) qchub picked.

    qchub uses ``{Firm} - {City}`` for branch-office labeling (observed:
    'J-U-B Engineers - Salt Lake City', 'BGE, Inc. - Katy'), while our
    requests typically have the parent name ('J-U-B ENGINEERS, Inc.').
    A plain substring check fails on those, even though they're the same
    firm. Match if either side's significant tokens are a subset of the
    other, OR at least 2 significant tokens overlap — which keeps two
    firms that share only a generic word (e.g. both ending in 'Engineers')
    from accidentally matching.
    """
    opt_token_sets = [_tokenize_company(opt) for opt in real_options]
    opt_token_sets = [t for t in opt_token_sets if t]
    for v in variants:
        v_tokens = _tokenize_company(v)
        if not v_tokens:
            continue
        for opt_tokens in opt_token_sets:
            if v_tokens.issubset(opt_tokens) or opt_tokens.issubset(v_tokens):
                return True
            if len(v_tokens & opt_tokens) >= 2:
                return True
    return False


def _select_and_verify_sticks(
    page: Page,
    placeholder: str,
    variants: list[str],
    log: ProgressCallback,
    *,
    max_attempts: int = 5,
    wait_between_ms: int = 2_000,
    use_first_option_fallback: bool = False,
) -> bool:
    """Set a dropdown then verify the value SURVIVES a brief wait.

    qchub's User-cascade fires an async patchValue that resets Office and
    QC Contact AFTER they're set (proven 2026-05-13: snapshots show
    `value="0"` with `ng-dirty` — we touched it, then Angular reset it).
    A single `select_option` can race-lose; the cure is to set, wait for
    the cascade-patch window to pass, verify the value stuck, and retry
    if it didn't. Diminishing-return on attempts: cascade is finite.
    """
    for attempt in range(1, max_attempts + 1):
        matched = _select_by_match(page, placeholder, variants, log=log)
        if not matched and use_first_option_fallback:
            matched = _select_first_option(page, placeholder, log)
        if not matched:
            log(f"  ({placeholder} attempt {attempt}/{max_attempts}: no match — retrying)")
            page.wait_for_timeout(wait_between_ms)
            continue
        page.wait_for_timeout(wait_between_ms)
        actual = _get_dropdown_selected_value(page, placeholder)
        if actual:
            log(f"  ({placeholder} stuck on {actual!r} after attempt {attempt})")
            return True
        log(f"  ({placeholder} got wiped post-set on attempt {attempt}/{max_attempts} — retrying)")
    log(f"  ⚠ {placeholder} never stuck after {max_attempts} attempts")
    return False


def _select_first_option(page: Page, placeholder: str, log: ProgressCallback) -> bool:
    """Pick the first non-placeholder option in the visible enabled <select>.

    Used as a fallback when we can't match a specific target — picks qchub's
    natural default so the form has a value to submit. The user can change it
    manually before final submit if needed.
    """
    selector = f'select[placeholder="{placeholder}"]:visible:not([disabled])'
    try:
        loc = page.locator(selector).first
        opts = loc.locator("option").all_text_contents()
        for opt in opts:
            if opt.strip() and opt.strip() != placeholder:
                if _set_select_value(loc, opt):
                    log(f"({placeholder!r} defaulted to first option: {opt!r})")
                    return True
    except Exception as exc:
        log(f"(Couldn't default {placeholder!r}: {exc})")
    return False


def _fill_text_input(page: Page, placeholder: str, value: str, log: ProgressCallback) -> bool:
    """Fill a text input identified by placeholder. Handles qchub's duplicate
    placeholder rendering (template + live) by taking the LAST visible match.

    Tries (1) Playwright `fill` → `Tab`, then (2) a JS native-setter fallback
    that fires the events Angular's ControlValueAccessor listens for. Returns
    True if the live input reads back the expected value.
    """
    target = (value or "").strip()
    try:
        inputs = page.locator(f'input[placeholder="{placeholder}"]:visible')
        if inputs.count() == 0:
            log(f"(No visible input with placeholder={placeholder!r})")
            return False
        loc = inputs.last  # the live one — template is earlier in document order
    except Exception as exc:
        log(f"(Couldn't locate {placeholder!r}: {exc})")
        return False

    # Strategy 1: standard fill + blur
    try:
        loc.click(timeout=2_000)
        loc.fill("", timeout=1_500)
        loc.fill(value, timeout=3_000)
        loc.press("Tab", timeout=2_000)
        if (loc.input_value(timeout=1_500) or "").strip() == target:
            return True
    except Exception:
        pass

    # Strategy 2: JS native-setter + framework events (Angular reactive forms)
    try:
        loc.evaluate(
            """(el, value) => {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, value);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""",
            value,
        )
        try:
            loc.press("Tab", timeout=1_500)
        except Exception:
            pass
        if (loc.input_value(timeout=1_500) or "").strip() == target:
            return True
    except Exception as exc:
        log(f"(JS fallback for {placeholder!r} failed: {exc})")

    log(f"⚠ Couldn't confirm {placeholder!r} was set to {target!r}")
    return False


def _set_select_value(locator, target_text: str) -> bool:
    """Set a <select> via Playwright `select_option`, with a JS fallback that
    dispatches the change events Angular's reactive form listens for.
    """
    t = (target_text or "").strip()
    try:
        locator.select_option(label=target_text, timeout=3_000)
        sel = locator.evaluate(
            "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''"
        )
        if (sel or "").strip() == t:
            return True
    except Exception:
        pass
    try:
        locator.evaluate(
            """(el, text) => {
                const wanted = text.trim();
                for (let i = 0; i < el.options.length; i++) {
                    if (el.options[i].text.trim() === wanted) {
                        el.selectedIndex = i;
                        el.value = el.options[i].value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""",
            target_text,
        )
        sel = locator.evaluate(
            "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''"
        )
        return (sel or "").strip() == t
    except Exception:
        return False


def _verify_required_fields(page: Page, log: ProgressCallback) -> None:
    """Read the live DOM state of required fields and decide whether to proceed.

    Priority tiers (per user direction 2026-05-13):
      ESSENTIAL: *Office — must be correct. Empty here means the order goes
        to the wrong team. Raise.
      ALSO ESSENTIAL: *User, *Company, *Project Name — without these the
        order can't be created at all. Raise.
      QCHUB-REQUIRED-BUT-NON-ESSENTIAL: *QC Contact — qchub requires SOMETHING
        be set so Continue is accepted, but WHO doesn't matter much (easy to
        change post-creation). Raise only if completely empty (Continue would
        fail anyway), but tolerate first-option-fallback choices without warning.
    """
    state: dict[str, str] = {}
    for placeholder in ("*User", "*Office", "*QC Contact"):
        v = _get_dropdown_selected_value(page, placeholder) or ""
        state[placeholder] = v
    # Company is special: the cascade often locks the select (disabled) after
    # narrowing to one option, so we read the disabled element too.
    company_real_opts = _read_company_options(page)
    state["*Company"] = company_real_opts[0] if company_real_opts else ""
    try:
        pnames = page.locator('input[placeholder="*Project Name"]:visible').all()
        pname = pnames[-1].input_value(timeout=1_500) if pnames else ""
    except Exception:
        pname = ""
    state["*Project Name"] = pname

    essential = ("*User", "*Office", "*Company", "*Project Name")
    essential_empty = [k for k in essential if not state[k].strip()]
    qc_contact_empty = not state["*QC Contact"].strip()

    if essential_empty or qc_contact_empty:
        missing = essential_empty + (["*QC Contact (qchub-required)"] if qc_contact_empty else [])
        log(f"⚠ Pre-Continue verification: empty: {missing}. Filled: { {k:v for k,v in state.items() if v} }")
        raise QchubError(
            f"Modal fill incomplete — empty: {', '.join(missing)}. "
            f"Filled: {state}. Check the diagnostic snapshot's page.html for cause."
        )
    log(f"Pre-Continue verification: all required fields set → {state}")


def _get_dropdown_selected_value(page: Page, placeholder: str) -> Optional[str]:
    """Return the live selectedIndex text of the enabled visible <select>.

    qchub renders TWO selects per placeholder (template + live); the template
    is `disabled=""`. We MUST filter to `:not([disabled])` or we risk reading
    the wrong one and falsely thinking the field is filled. Returns None when
    nothing real is selected on the live element.
    """
    try:
        selects = page.locator(f'select[placeholder="{placeholder}"]:visible:not([disabled])').all()
    except Exception:
        return None
    for sel in selects:
        try:
            text = sel.evaluate(
                "el => { if (el.selectedIndex < 0) return null; const o = el.options[el.selectedIndex]; return o ? o.text : null; }"
            )
            if text and text.strip() and text.strip() != placeholder:
                return text.strip()
        except Exception:
            continue
    return None


def _build_qc_contact_variants(request: StudyRequest) -> list[str]:
    """Match QC Contact against email recipients (To + CC) filtered to @qualitycounts.net.

    qchub's QC Contact dropdown displays names as 'First Last' (e.g. 'Alfredo
    Suarez'), but email headers usually format them as 'Last, First <email>'
    or 'First Last <email>'. We generate both orderings + name components so
    contains-match in _select_by_match catches whichever the dropdown uses.

    Uses `email.utils.getaddresses` for robust parsing — handles unquoted names
    with embedded commas (which most Outlook exports produce) and folded headers.
    """
    from email.utils import getaddresses

    out: list[str] = []
    raw_headers = [h for h in (request.email_to, request.email_cc) if h]
    for name, email in getaddresses(raw_headers):
        name = (name or "").strip()
        email = (email or "").strip()
        if not email or "@" not in email:
            continue
        if not email.lower().endswith("@qualitycounts.net"):
            continue  # internal recipients only

        if name:
            # Flip 'Last, First' → 'First Last' for the more-likely display order.
            if "," in name:
                last, _, first = name.partition(",")
                last, first = last.strip(), first.strip()
                first_last = f"{first} {last}".strip()
            else:
                parts = name.split()
                first = parts[0] if parts else ""
                last = parts[-1] if len(parts) > 1 else ""
                first_last = name
            # Most-likely display order first, then fallbacks.
            for variant in (first_last, f"{last}, {first}" if first and last else "", last, first, name):
                v = variant.strip().strip(",").strip()
                if v and v not in out:
                    out.append(v)

        if email and email not in out:
            out.append(email)
        local = email.split("@", 1)[0]
        if local and local not in out:
            out.append(local)
    return out


# QC Office data — derived from https://www.qualitycounts.net/office (2026-05-13).
# Each staff member can cover multiple offices; we disambiguate by jurisdiction
# city/state hints.
#
# Format: staff name-key (lowercase) → ordered list of (office_label_fragment, hint_keywords)
# The first entry is the "default" pick if no hint matches; later entries override
# when a hint is found in the jurisdiction string.
# Office values MUST match the actual qchub option text exactly (confirmed by
# inspecting the live dropdown 2026-05-13). qchub uses regional "Operations"
# names, NOT city names — e.g., "Florida Operations" not "Tampa", "Texas
# Operations" not "Dallas".
#
# Format: staff name-key (lowercase) → ordered list of (qchub_office_text, hint_keywords)
# Hints disambiguate multi-office staff: first hint match wins, otherwise we
# fall through to the LAST entry (the state-default for that staff member).
_QC_STAFF_OFFICES: dict[str, list[tuple[str, list[str]]]] = {
    # Single-office staff
    "mosdell":   [("Salt Lake City Operations", [])],
    "thomason":  [("Las Vegas Operations", [])],
    "cessna":    [("Tennessee Operations", [])],
    "jones":     [("Tennessee Operations", [])],   # Rodrikas Jones, per user 2026-05-13
    "rjones":    [("Tennessee Operations", [])],
    "berryhill": [("Raleigh Operations", [])],
    "martineau": [("Tampa VRC", [])],               # Erin Martineau — specialized FL VRC work
    # Multi-office staff. ORDER MATTERS: city-specific hints FIRST,
    # state-default LAST (used as fallback when nothing else matches).
    "mix":      [("Charlotte Operations", ["charlotte", "raleigh", "north carolina", "south carolina", "nc", "sc"]),
                 ("Texas Operations",     ["dallas", "fort worth", "ft worth", "dfw", "austin", "texas", "tx"])],
    "yeoman":   [("Louisiana Operations", ["baton rouge", "louisiana", "la"]),
                 ("Atlanta Operations",   ["atlanta", "georgia", "ga"])],
    "franz":    [("Minnesota Operations",                       ["minneapolis", "st paul", "saint paul", "minnesota", "mn"]),
                 ("Corporate, Portland and West Coast Operations", ["spokane", "northern idaho", "boise", "idaho", "id", "honolulu", "hawaii", "hi"]),
                 ("Portland Operations",                        ["portland", "oregon", "or"])],
    "bernard":  [("Washington DC Operations", ["washington", "dc", "virginia", "va", "maryland", "md"]),
                 ("Boston Operations",        ["boston", "massachusetts", "ma", "new england", "connecticut", "ct",
                                               "rhode island", "ri", "vermont", "vt", "new hampshire", "nh", "maine", "me"])],
    "shields":  [("Michigan Operations", ["detroit", "michigan", "mi", "ohio", "oh", "indiana", "in"]),
                 ("Chicago Operations",  ["chicago", "illinois", "il", "wisconsin", "wi"])],
    "durrett":  [("San Francisco / Bay Area Operations", ["san francisco", "bay area", "northern california",
                                                          "oakland", "san jose", "sf"]),
                 ("Southern California Operations",       ["los angeles", "la", "southern california", "san diego",
                                                          "orange county", "california", "ca"])],
    "suarez":   [("Florida Operations", [])],       # All FL (Miami, Tampa, etc.) per user 2026-05-13
}

# State (or state abbreviation, or DOT-agency acronym) → default office, used
# when no QC staff lookup produced a match. Values MUST match actual qchub
# option text. Lookup is whole-word, lowercase regex match against the
# jurisdiction string (see _build_office_variants).
#
# DOT acronyms are included for clients who write "UDOT" / "FDOT" / "TxDOT"
# instead of the state name (observed 2026-05-14: Avenue Consultants UDOT
# project failed because "ut" wasn't a whole word inside "udot"). Only
# UNAMBIGUOUS acronyms are listed — MDOT (MI/MN/MD/ME/MS), ODOT (OR/OH/OK),
# IDOT (IL/IA/ID) are intentionally omitted so we never silently mis-route.
_STATE_TO_DEFAULT_OFFICE: dict[str, str] = {
    "GA": "Atlanta Operations", "Georgia": "Atlanta Operations",
    "GDOT": "Atlanta Operations",
    "AL": "Atlanta Operations", "Alabama": "Atlanta Operations",
    "ALDOT": "Atlanta Operations",
    "TX": "Texas Operations",   "Texas": "Texas Operations",
    "TXDOT": "Texas Operations", "TxDOT": "Texas Operations",
    "LA": "Louisiana Operations", "Louisiana": "Louisiana Operations",
    "LADOTD": "Louisiana Operations",
    "ID": "Corporate, Portland and West Coast Operations", "Idaho": "Corporate, Portland and West Coast Operations",
    "MA": "Boston Operations",  "Massachusetts": "Boston Operations",
    "MassDOT": "Boston Operations",
    "NH": "Boston Operations", "VT": "Boston Operations", "RI": "Boston Operations",
    "NHDOT": "Boston Operations", "VTrans": "Boston Operations", "RIDOT": "Boston Operations",
    "CT": "Boston Operations", "ME": "Boston Operations",
    "ConnDOT": "Boston Operations", "MaineDOT": "Boston Operations",
    "NY": "Boston Operations", "New York": "Boston Operations",
    "NYSDOT": "Boston Operations",
    "NC": "Charlotte Operations", "North Carolina": "Charlotte Operations",
    "NCDOT": "Charlotte Operations",
    "SC": "South Carolina Operations", "South Carolina": "South Carolina Operations",
    "SCDOT": "South Carolina Operations",
    "IL": "Chicago Operations", "Illinois": "Chicago Operations",
    "WI": "Chicago Operations", "Wisconsin": "Chicago Operations",
    "WisDOT": "Chicago Operations",
    "MI": "Michigan Operations", "Michigan": "Michigan Operations",
    "OH": "Michigan Operations", "Ohio": "Michigan Operations",
    "IN": "Michigan Operations", "Indiana": "Michigan Operations",
    "INDOT": "Michigan Operations",
    "HI": "Corporate, Portland and West Coast Operations", "Hawaii": "Corporate, Portland and West Coast Operations",
    "HDOT": "Corporate, Portland and West Coast Operations",
    "CA": "Southern California Operations", "California": "Southern California Operations",
    "Caltrans": "Southern California Operations",
    "NV": "Las Vegas Operations", "Nevada": "Las Vegas Operations",
    "NDOT": "Las Vegas Operations",
    "AZ": "Las Vegas Operations", "Arizona": "Las Vegas Operations",
    "ADOT": "Las Vegas Operations",
    "FL": "Florida Operations", "Florida": "Florida Operations",
    "FDOT": "Florida Operations",
    "MN": "Minnesota Operations", "Minnesota": "Minnesota Operations",
    "MnDOT": "Minnesota Operations",
    "IA": "Minnesota Operations", "Iowa": "Minnesota Operations",
    "ND": "Minnesota Operations", "SD": "Minnesota Operations",
    "TN": "Tennessee Operations", "Tennessee": "Tennessee Operations",
    "TDOT": "Tennessee Operations",
    "KY": "Tennessee Operations", "Kentucky": "Tennessee Operations",
    "KYTC": "Tennessee Operations",
    "OR": "Portland Operations", "Oregon": "Portland Operations",
    "WA": "Portland Operations", "Washington State": "Portland Operations",
    "WSDOT": "Portland Operations",
    "UT": "Salt Lake City Operations", "Utah": "Salt Lake City Operations",
    "UDOT": "Salt Lake City Operations",
    "DC": "Washington DC Operations", "District of Columbia": "Washington DC Operations",
    "DDOT": "Washington DC Operations",
    "VA": "Washington DC Operations", "Virginia": "Washington DC Operations",
    "VDOT": "Washington DC Operations",
    "MD": "Washington DC Operations", "Maryland": "Washington DC Operations",
    "DE": "Washington DC Operations", "Delaware": "Washington DC Operations",
    "DelDOT": "Washington DC Operations",
    "PA": "Washington DC Operations", "Pennsylvania": "Washington DC Operations",
    "PennDOT": "Washington DC Operations",
    "NJ": "Washington DC Operations", "New Jersey": "Washington DC Operations",
    "NJDOT": "Washington DC Operations",
    "WV": "Washington DC Operations", "West Virginia": "Washington DC Operations",
    "WVDOT": "Washington DC Operations", "WVDOH": "Washington DC Operations",
}


def _office_for_staff(qc_contact_variants: list[str], jurisdiction: str) -> Optional[str]:
    """Match office for the recipient using word-boundary hint matching.

    For each office: if any hint matches as a whole word in jurisdiction, return it.
    If nothing matches across all offices, fall back to the LAST entry (the most
    generic / state-default office for that staff member).
    Returns None if no staff member matches the recipient variants at all.
    """
    j_lower = (jurisdiction or "").lower()
    for variant in qc_contact_variants:
        v = variant.lower()
        for staff_key, offices in _QC_STAFF_OFFICES.items():
            if staff_key not in v:
                continue
            for office_label, hints in offices:
                for hint in hints:
                    if re.search(rf"(^|\W){re.escape(hint)}($|\W)", j_lower):
                        return office_label
            # No hint matched — fall back to LAST entry (the generic default)
            return offices[-1][0]
    return None


def _build_office_variants(request: StudyRequest) -> list[str]:
    """Build office-match variants. Priority: QC staff's home office, then
    state default, with the raw jurisdiction text first as a long-shot.
    """
    out: list[str] = []
    j = (request.jurisdiction or "").strip()
    if j:
        out.append(j)

    qc_contacts = _build_qc_contact_variants(request)
    staff_office = _office_for_staff(qc_contacts, j)
    if staff_office and staff_office not in out:
        out.append(staff_office)

    # State-default fallback — must be a whole-word match to avoid e.g. 'al'
    # matching inside 'salt', or 'la' matching inside 'lakeland'.
    j_lower = j.lower()
    for key, hint in _STATE_TO_DEFAULT_OFFICE.items():
        k = key.lower()
        if re.search(rf"(^|\W){re.escape(k)}($|\W)", j_lower):
            if hint not in out:
                out.append(hint)
            break
    return out


def _user_match_variants(request: StudyRequest) -> list[str]:
    """Build a prioritized list of values to try when matching a User option.

    qchub's User dropdown lists EVERY user in the database (not company-filtered)
    and option text is 'Last , First - email@domain' — so email is the only
    unique identifier. Email-first ordering prevents the matcher from picking
    a same-last-name user from the wrong company.

    Variant order:
      1. Full email (unique → exact-match in pass 1)
      2. Email local-part (still distinctive)
      3. 'Last, First' / 'Last' / 'First Last' / raw name (name-based fallback)
    """
    out: list[str] = []
    email = (request.client_contact_email or "").strip()
    if email:
        out.append(email)
        local = email.split("@", 1)[0]
        if local and local not in out:
            out.append(local)
    name = (request.client_contact_name or "").strip()
    if name:
        parts = name.split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            out.append(f"{last}, {first}")
            out.append(last)
        if name not in out:
            out.append(name)
    return out


def _company_match_variants(request: StudyRequest) -> list[str]:
    """Build matchers for Company. Email domain becomes a final fallback signal."""
    out: list[str] = []
    company = (request.client_company or "").strip()
    if company:
        out.append(company)
        # Drop common suffixes for fuzzier match: "Kimley-Horn and Associates, Inc." → "Kimley-Horn"
        core = re.split(r"\s+(and|&|,)", company, maxsplit=1)[0].strip()
        if core and core != company:
            out.append(core)
    domain = (request.client_contact_email or "").split("@", 1)[-1].strip().lower()
    if domain and domain not in out:
        out.append(domain)
        host_root = domain.split(".", 1)[0]
        if host_root and host_root not in out:
            out.append(host_root)
    return out


def _build_project_name(request: StudyRequest) -> str:
    """Build a descriptive project name for qchub from the StudyRequest.

    Priority:
      1. Combined: '[#client_project_number] {jurisdiction} data collection'
      2. '{jurisdiction} data collection' (when no client project number)
      3. Cleaned email subject (strip RE:/FW:/Quote for: noise)
      4. 'Traffic Study' (last resort)

    Future enhancement: pull a more unique descriptor via the chat-LLM feature.
    """
    pieces: list[str] = []
    if request.client_project_number:
        pieces.append(f"#{request.client_project_number.strip()}")

    jurisdiction = (request.jurisdiction or "").strip()
    if jurisdiction:
        pieces.append(f"{jurisdiction} data collection")
    else:
        subject = (request.email_subject or "").strip()
        for prefix in ("RE:", "FW:", "Fwd:", "Re:", "Quote for ", "Quote:", "Request for ", "Request:"):
            if subject.lower().startswith(prefix.lower()):
                subject = subject[len(prefix):].strip()
        pieces.append(subject or "Traffic Study")

    return " — ".join(pieces) if len(pieces) > 1 else pieces[0]


def _click_continue(
    page: Page,
    request: StudyRequest,
    qc_office: Optional[str],
    log: ProgressCallback,
) -> None:
    """Click CONTINUE and verify the modal actually advanced.

    Defensive refill: qchub's Angular form has a delayed cascade that can wipe
    Office and QC Contact selections post-fill (observed 2026-05-13 — fields
    show ng-dirty value="0" by the time Continue is clicked). Right before
    clicking, re-check those two fields and refill if empty.

    qchub shows a pink banner 'Please, verify all required fields' if anything
    is still wrong after the click. We detect that banner so we don't silently
    cascade into a misleading 'SUBMIT REQUEST not found' error at the next step.
    """
    office_variants = [qc_office] if qc_office else _build_office_variants(request)
    qc_variants = _build_qc_contact_variants(request)

    if office_variants and not _get_dropdown_selected_value(page, "*Office"):
        log("(*Office got wiped post-fill — sticky-retry refill before Continue)")
        _select_and_verify_sticks(page, "*Office", office_variants, log)
    if not _get_dropdown_selected_value(page, "*QC Contact"):
        log("(*QC Contact got wiped post-fill — sticky-retry refill before Continue)")
        _select_and_verify_sticks(
            page, "*QC Contact", qc_variants or [], log,
            use_first_option_fallback=True,
        )

    try:
        btn = page.get_by_role("button", name=re.compile(r"^\s*continue\s*$", re.IGNORECASE)).first
        btn.click(timeout=10_000)
    except PlaywrightTimeoutError:
        raise QchubError("Couldn't find CONTINUE button on the Request Estimate modal.")

    # Detect that the modal advanced. Positive signals (we're now on the
    # studies/map page): UPLOAD KML button visible, "Add Study Group" dialog
    # showing, or the map's "RIGHT CLICK ON THE MAP TO ADD A LOCATION" hint.
    # Negative signal: qchub's pink 'verify all required fields' banner.
    import time
    deadline = time.monotonic() + 10
    advance_patterns = [
        re.compile(r"upload\s*kml", re.IGNORECASE),
        re.compile(r"add\s+study\s+group", re.IGNORECASE),
        re.compile(r"right\s+click\s+on\s+the\s+map", re.IGNORECASE),
    ]
    while time.monotonic() < deadline:
        # Negative: validation banner
        try:
            if page.get_by_text(
                re.compile(r"verify\s+all\s+required\s+fields", re.IGNORECASE)
            ).first.is_visible(timeout=300):
                raise QchubError(
                    "qchub rejected CONTINUE — 'Please verify all required fields'. "
                    "Check the diagnostic snapshot to see which field is empty."
                )
        except QchubError:
            raise
        except Exception:
            pass

        # Positive: any of the next-page UI signals
        for pattern in advance_patterns:
            try:
                if page.get_by_text(pattern).first.is_visible(timeout=300):
                    return
            except Exception:
                continue
        time.sleep(0.3)

    raise QchubError(
        "Clicked CONTINUE but didn't see any next-page indicator (UPLOAD KML / "
        "Add Study Group / map prompt) within 10s. Check the diagnostic snapshot."
    )


def _upload_kml(page: Page, kml_path: Path, log: ProgressCallback) -> None:
    """Click the UPLOAD KML button on the studies/map page and attach the KML file.

    qchub's importer expects `.kml`. UI button text is "UPLOAD KML".
    """
    try:
        with page.expect_file_chooser(timeout=15_000) as fc_info:
            try:
                page.get_by_role("button", name=re.compile(r"upload\s*kml", re.IGNORECASE)).first.click(timeout=5_000)
            except Exception:
                # Last-resort text-based click
                page.get_by_text(re.compile(r"upload\s*kml", re.IGNORECASE)).first.click(timeout=5_000)
        fc_info.value.set_files(str(kml_path))
    except PlaywrightTimeoutError:
        try:
            page.locator('input[type="file"]').first.set_input_files(str(kml_path))
        except Exception as exc:
            raise QchubError(f"Couldn't attach KML via UPLOAD KML: {exc}")

    # Wait for locations to render — pin elements should appear on the map.
    page.wait_for_timeout(3_000)


# Standard peaks defined in qchub's Add Study Group dialog. Time strings match
# the HH:MM format used in our StudyRequest TimeWindow model.
_STANDARD_PEAKS = {
    "am": ("07:00", "09:00"),
    "pm": ("16:00", "18:00"),
}

# Default day preset — qchub's day-picker has a built-in "Midweek (T-W-Th)"
# option that selects Tue/Wed/Thu in one click. Per user direction, this is
# the default unless the email specifies otherwise.
_DEFAULT_DAYS = ["Midweek (T-W-Th)"]


def _add_study_groups(page: Page, request: StudyRequest, log: ProgressCallback) -> None:
    """Drive the Add Study Group dialog for each planned (kind × time_windows) group.

    Strategy:
      - If all windows match the AM and/or PM peak (07:00-09:00, 16:00-18:00),
        use Standard AM/PM peak mode with Midweek days.
      - If exactly one non-peak window, use Specific Time with computed
        Duration in Hours.
      - Otherwise log + cancel the dialog so the user finishes manually.

    The dialog auto-opens after Continue (qchub behavior). For the first group
    we expect it open; for subsequent groups we click ADD GROUP to reopen it.
    """
    groups = _group_locations_for_qchub(request)
    if not groups:
        log("No groups planned.")
        _dismiss_add_group_dialog_if_open(page, log)
        return

    log(f"Planning {len(groups)} study group(s).")
    # Track per-group outcomes so we can validate after the loop that every
    # planned group actually made it into qchub. Prior versions trusted
    # `_drive_one_group` to either succeed or raise — but several paths
    # silently early-return (no time windows, can't auto-configure, ADD
    # TIME PERIOD never committed), and an external Info Alert blocking
    # the next ADD GROUP click would silently drop the rest of the loop.
    # The user observed this in test order 176458 (one TMC group landed,
    # two tube groups were skipped, the modal proceeded to SUBMIT REQUEST
    # with a 7-line estimate instead of 36).
    group_outcomes: list[tuple[int, str, str]] = []  # (i, layer_name, "ok" | reason)
    for i, g in enumerate(groups, 1):
        windows = g["time_windows"]
        layer_name = _group_layer_name(g)
        log(
            f"--- Group {i}/{len(groups)}: kind={g['study_kind']}, "
            f"times={[(w.label, w.start, w.end) for w in windows]}, "
            f"locations={len(g['locations'])}, layer={layer_name!r} ---"
        )
        outliers = g.get("subtype_outliers") or []
        if outliers:
            log(
                f"  Group default subtype = {g['subtype_label']!r}; "
                f"{len(outliers)} location(s) have a different individual subtype "
                f"and will need post-submit estimate-page correction: "
                + ", ".join(f"{o['site_name']} ({o['actual_subtype']})" for o in outliers)
            )
        if not _ensure_add_group_dialog_open(page, log):
            reason = "couldn't open Add Study Group dialog (likely blocked by Info Alert)"
            log(f"  ⚠ {reason} — recording as failed and trying next group.")
            group_outcomes.append((i, layer_name, reason))
            # Try to clear whatever's blocking before the next iteration so
            # we don't cascade-skip every remaining group.
            _dismiss_info_alert(page, log)
            _dismiss_add_group_dialog_if_open(page, log)
            continue

        # Build this group's KML so we can upload it right after CREATE GROUP.
        try:
            kml_result = kml_export.build_kml_for_locations(
                g["locations"],
                layer_name=layer_name,
            )
            kml_bytes: Optional[bytes] = kml_result.data
            log(
                f"  Built per-group KML: {kml_result.placemark_count} pin(s) "
                f"({kml_result.skipped_unplaced} skipped — no coordinates)"
            )
            if kml_result.placemark_count == 0:
                log(
                    "  (No geocoded locations in this group — group will be created "
                    "but no KML will be uploaded.)"
                )
                kml_bytes = None
        except Exception as exc:
            log(f"  ⚠ Couldn't build KML for this group ({exc}); creating group without locations")
            kml_bytes = None

        try:
            created = _drive_one_group(
                page, g["study_kind"], windows, log,
                subtype_label=g.get("subtype_label"),
                kml_bytes=kml_bytes,
                group_label=layer_name,
                survey_custom_name=g.get("survey_custom_name"),
            )
            if created:
                group_outcomes.append((i, layer_name, "ok"))
            else:
                group_outcomes.append((i, layer_name, "driver early-returned (see prior log)"))
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            log(f"⚠ Group {i} driver failed: {reason} — cancelling its dialog")
            _dismiss_add_group_dialog_if_open(page, log)
            group_outcomes.append((i, layer_name, reason))

    # qchub auto-re-opens the Add Study Group dialog after the last CREATE
    # GROUP, anticipating the next group. If we don't dismiss it before the
    # next step (SUBMIT REQUEST), its backdrop overlay blocks the button.
    _dismiss_add_group_dialog_if_open(page, log)

    # Post-loop validation: every planned group must have made it. Raise
    # loud if any failed so the caller surfaces "this order is incomplete"
    # to the user instead of silently submitting a partial request.
    failed = [(i, name, reason) for i, name, reason in group_outcomes if reason != "ok"]
    if failed:
        failure_lines = "\n".join(
            f"  - Group {i} ({name}): {reason}" for i, name, reason in failed
        )
        raise QchubError(
            f"Only {len(groups) - len(failed)} of {len(groups)} planned study "
            f"groups were created in qchub. Missing groups:\n{failure_lines}\n\n"
            "Re-run the order — these failures usually clear on a second pass "
            "once any blocking dialog state is fresh. If they don't, the qchub "
            "diagnostic page.html snapshots will show which form field qchub "
            "rejected."
        )


def _group_layer_name(g: dict) -> str:
    """Human-readable label for a planned study group — used as the KML's
    <Document><name> (which MyMaps surfaces as the layer name) and in
    diagnostic logs.

    Format: '{kind upper} {subtype} ({first window})' — e.g.
      'TMC Turn Count -- Standard (07:00-19:00)'
      'TUBE Volume, Speed (3 day count)'
      'SURVEY Vehicular Gap Study (Video) (07:00-19:00)'
      'TMC Turn Count -- Standard (1 day count)'  ← full-day case

    Full-day windows (24 hours, or `00:00-23:59` which the LLM emits as the
    closest expressible "all day") are labeled '({N} day count)' instead
    of the literal '00:00-23:59' that earlier versions produced, since the
    map layer name was leaking the 23:59 quirk into client-facing artifacts.
    """
    kind = (g.get("study_kind") or "").upper()
    subtype = g.get("subtype_label", "") or ""
    windows = g.get("time_windows") or []
    if not windows:
        win = "no time window"
    else:
        w = windows[0]
        total_hours = getattr(w, "total_hours", None)
        hours, mins = _duration_h_m(w.start, w.end, total_hours)
        # Detect full-day intent: total_hours set to a multiple of 24,
        # OR computed duration is 24h0m (start/end implied a full day).
        full_days = None
        if total_hours and total_hours % 24 == 0:
            full_days = total_hours // 24
        elif hours == 24 and mins == 0:
            full_days = 1
        if full_days:
            win = f"{full_days} day count" if full_days > 1 else "1 day count"
        else:
            win = f"{w.start}-{w.end}"
            if total_hours:
                win += f", {total_hours}h total"
        if len(windows) > 1:
            win += f" (+{len(windows) - 1} more)"
    return f"{kind} {subtype} ({win})".strip()


def _is_add_group_dialog_open(page: Page) -> bool:
    """True if a `div.modal.fade.in` whose body mentions 'Add Study Group' is
    currently shown. Scoping to the `.in` class avoids false positives on
    dormant modals that sit in the DOM with `display:none`.
    """
    try:
        return page.locator("div.modal.fade.in").filter(
            has_text=re.compile(r"Add\s+Study\s+Group", re.IGNORECASE)
        ).first.is_visible(timeout=1_000)
    except Exception:
        return False


def _clear_blockers_before_add_group(page: Page, log: ProgressCallback) -> None:
    """Clear modals that can block the ADD GROUP click between groups.

    Two known blockers seen on multi-group runs (run-20260519-091613):
      1. `div.modal.loading_popup.in` — qchub's spinner while async work
         finishes after a CREATE GROUP / KML upload. Wait for it to hide.
      2. `Info Alert` modal ("Now please, add at least one location…") —
         qchub re-pops it between groups too, not just after the first
         CREATE GROUP. Dismiss it via its Continue button.

    Best-effort; silent on miss. Called before every ADD GROUP click.
    """
    # 1. Loading popup — wait up to 10s for it to disappear.
    try:
        loading = page.locator("div.modal.loading_popup.in").first
        if loading.is_visible(timeout=500):
            log("  (loading_popup is up — waiting up to 10s for it to clear…)")
            loading.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass
    # 2. Info Alert (re-uses the same dismiss helper used post-CREATE-GROUP).
    _dismiss_info_alert(page, log)


def _ensure_add_group_dialog_open(page: Page, log: ProgressCallback) -> bool:
    """Make sure the Add Study Group dialog is showing. Returns True on success."""
    if _is_add_group_dialog_open(page):
        return True
    # Clear any blocking modal first (loading_popup mid-KML-upload, or a
    # post-create Info Alert qchub re-pops between groups).
    _clear_blockers_before_add_group(page, log)
    # Try clicking ADD GROUP (the orange button next to Studies on the studies/map page).
    try:
        page.get_by_role("button", name=re.compile(r"^\s*add\s*group\s*$", re.IGNORECASE)).first.click(timeout=5_000)
    except Exception as exc:
        log(f"(Couldn't click ADD GROUP: {exc})")
        return False
    try:
        page.get_by_text("Add Study Group", exact=True).first.wait_for(state="visible", timeout=5_000)
        return True
    except Exception:
        return False


def _dismiss_add_group_dialog_if_open(page: Page, log: ProgressCallback) -> None:
    """Click Cancel in the active Add Study Group modal if it's open.

    Scoped to `div.modal.fade.in` — a bare `.first.click()` on Cancel would
    sometimes hit the dormant Edit Study Group modal's Cancel (same text,
    earlier in document order), leaving the visible modal still open.
    """
    if not _is_add_group_dialog_open(page):
        return
    try:
        modal = page.locator("div.modal.fade.in").filter(
            has_text=re.compile(r"Add\s+Study\s+Group", re.IGNORECASE)
        ).first
        modal.get_by_role("button", name=re.compile(r"^cancel$", re.IGNORECASE)).click(timeout=3_000)
        log("Dismissed Add Study Group dialog (Cancel).")
    except Exception as exc:
        log(f"(Couldn't dismiss dialog: {exc})")


def _active_modal(page: Page):
    """The Add Study Group modal that's currently visible.

    qchub renders the Add modal and a dormant Edit modal in the same DOM, so
    bare `.first` locators tend to hit the wrong subtree (document order, not
    visible-first — see `feedback_first_is_not_visible.md`). Scoping to
    `div.modal.fade.in` is the cure.
    """
    return page.locator("div.modal.fade.in").filter(
        has_text=re.compile(r"Add\s+Study\s+Group", re.IGNORECASE)
    ).first


def _drive_one_group(
    page: Page, study_kind: str, windows: list, log: ProgressCallback,
    *, subtype_label: Optional[str] = None,
    kml_bytes: Optional[bytes] = None,
    group_label: Optional[str] = None,
    survey_custom_name: Optional[str] = None,
) -> bool:
    """Configure and create one study group via the open dialog, then
    optionally upload that group's KML so its locations bind to it.

    `study_kind` is the StudyKind value ('turning_movement' / 'tube' /
    'survey'). `subtype_label` is the qchub-side label to pick in the
    secondary dropdown that appears after the study-type radio: for Survey,
    this is REQUIRED (qchub won't let CREATE GROUP succeed without a
    Service Subtype); for Tube, it overrides the default 'Volume'; for
    TMC, it's currently a no-op (we leave TMC at qchub's default).

    `kml_bytes` — if provided, uploaded via UPLOAD KML right after CREATE
    GROUP succeeds. The just-created group is the active selection in
    qchub, so the imported locations bind to it. This implements John
    Goodwin's workflow (2026-05-14): one KML per study group, uploaded
    while that group is selected, rather than one bulk KML at the end
    (which leaves qchub unable to assign locations to groups).

    Critical ordering observed 2026-05-14: ADD TIME PERIOD MUST commit a row
    to the Added Time Period table BEFORE CREATE GROUP is clicked. Without a
    committed row, qchub silently rejects CREATE GROUP, resets the form, and
    keeps the dialog open — causing every downstream step (KML upload,
    SUBMIT REQUEST) to fail with a modal-backdrop block.
    """
    modal = _active_modal(page)

    # Study type radio. Maps to qchub's three Study Type options.
    radio_value = {
        "turning_movement": "Turn",
        "tube": "Tube",
        "survey": "Survey",
    }.get(study_kind, "Turn")
    log(f"Setting study type → {radio_value}")
    modal.locator(f"input[name='optradio'][value='{radio_value}']").check(timeout=3_000)
    page.wait_for_timeout(400)  # let Angular render the secondary subtype dropdown

    # Secondary subtype dropdown — Survey requires it, Tube benefits from it.
    if study_kind == "survey":
        resolved_survey_label = subtype_label or "Vehicular Gap Study (Video)"
        subtype_picked = _pick_survey_subtype(modal, resolved_survey_label, log)
        if not subtype_picked:
            # SOFT FAILURE (2026-05-17 PM): qchub's Service Subtype <select>
            # has a non-disabled placeholder option (value="33", text
            # "Service Subtype") that's selected from mount. If our pick
            # fails for any reason, qchub will still accept CREATE GROUP
            # with the placeholder as the "subtype." That produces a
            # functional-but-wrong group the user can correct on the
            # Estimate page, which is far better than blowing up the
            # whole order. Prior hard-fail behavior (2026-05-17 AM) made
            # this single failure tank the entire order. Log loudly so
            # the user knows the group needs attention.
            log(
                f"⚠ Couldn't set Survey subtype {resolved_survey_label!r} — "
                "proceeding with qchub's placeholder default. The group "
                "WILL be created, but its subtype is wrong and needs to "
                "be fixed manually on the Estimate page (or have Ellen "
                "use set_estimate_subtype after the order completes)."
            )
        # When the Survey subtype is "Custom Video Survey..." (or "Custom
        # Non-Video Survey..."), qchub surfaces an extra Custom Survey
        # name input that becomes the line item's description on the
        # estimate. Fill it only if we actually got the custom subtype
        # picked — skipping it on placeholder fallback avoids touching a
        # field that isn't visible.
        if subtype_picked and "Custom" in resolved_survey_label:
            name_value = (survey_custom_name or group_label or "Custom Survey").strip()
            _fill_survey_custom_name(modal, page, name_value, log)
    elif study_kind == "tube" and subtype_label:
        _pick_tube_subtype(modal, page, subtype_label, log)

    # Pick time-period strategy. qchub's Add Time Period form layout depends
    # on Study Type:
    #   - TMC and Survey share the same form: Standard AM/PM peak radio
    #     (with AM/PM checkboxes + day pickers) OR Specific Time radio
    #     (with Start Time + Hours + day picker).
    #   - Tube has NO radio — just Duration + Days/Hours unit + Start Day.
    if not windows:
        log(
            f"⚠ Group has 0 time windows — qchub requires at least one per group. "
            "Set time windows on these locations (set_location_time_windows_for_indices) "
            "and re-run, or configure this group manually in qchub. Cancelling for now."
        )
        _dismiss_add_group_dialog_if_open(page, log)
        return False

    if study_kind == "tube":
        # Tube form: one window (most common case). For 72-hr, 1-week etc.
        # the TimeWindow should carry total_hours; we convert to the right
        # (Duration, Unit) pair for the qchub form.
        w = windows[0]
        duration, unit = _tube_duration_and_unit(w)
        log(f"Tube window {w.label!r} (start={w.start}, end={w.end}, total_hours={w.total_hours}) → Duration={duration} {unit}.")
        _fill_tube_time_period(modal, page, duration=duration, unit=unit, days=_DEFAULT_DAYS, log=log)
        # Tube path: one ADD TIME PERIOD click → expect 1 row.
        if not _add_time_period_until_row_appears(modal, page, log, attempts=3, expected_count=1):
            log("⚠ ADD TIME PERIOD didn't commit a row after 3 attempts — cancelling this group.")
            _dismiss_add_group_dialog_if_open(page, log)
            return False
    else:
        # TMC and Survey share the same time-period form. Three sub-strategies:
        #   1. ALL windows match the canonical 07:00-09:00 AM and/or
        #      16:00-18:00 PM peaks → use Standard mode (one ADD TIME PERIOD
        #      click commits both AM and PM at once via checkboxes).
        #   2. One OR MORE Specific Time windows (any combination of non-
        #      standard hours, e.g. 06:00-09:00 + 16:00-19:00) → use Specific
        #      Time radio and call ADD TIME PERIOD once per window, verifying
        #      the row count increments each time.
        #   3. Group has no usable windows (start/end missing) → bail.
        # Sub-strategy 2 replaced the previous "len(windows) == 1 only" path
        # that silently bailed for 2+ non-standard windows (observed test
        # run-20260517-102422: TMC with 06:00-09:00 + 16:00-19:00 got skipped).
        peak_matches = [_match_standard_peak(w) for w in windows]
        if windows and all(m is not None for m in peak_matches):
            log("All windows are standard AM/PM peaks — using Standard mode.")
            modal.locator("input[name='timePeriod'][value='standar']").check(timeout=3_000)
            page.wait_for_timeout(300)
            has_am = any(m == "am" for m in peak_matches)
            has_pm = any(m == "pm" for m in peak_matches)
            if has_am:
                _check_peak_and_pick_days(modal, page, "AM", _DEFAULT_DAYS, log)
            if has_pm:
                _check_peak_and_pick_days(modal, page, "PM", _DEFAULT_DAYS, log)
            if not _add_time_period_until_row_appears(modal, page, log, attempts=3, expected_count=1):
                log("⚠ ADD TIME PERIOD didn't commit a row after 3 attempts — cancelling this group.")
                _dismiss_add_group_dialog_if_open(page, log)
                return False
        elif windows and all(w.start and w.end for w in windows):
            log(f"Filling {len(windows)} Specific Time period(s) for this group.")
            modal.locator("input[name='timePeriod'][value='specific']").check(timeout=3_000)
            page.wait_for_timeout(300)
            for j, w in enumerate(windows, 1):
                hours, minutes = _duration_h_m(w.start, w.end, getattr(w, "total_hours", None))
                log(
                    f"  Period {j}/{len(windows)}: {w.label!r} {w.start}-{w.end} "
                    f"(total_hours={getattr(w, 'total_hours', None)}) "
                    f"→ Start={w.start}, {hours}h {minutes}m, Midweek."
                )
                _fill_specific_time(
                    page, start_time=w.start, hours=hours, minutes=minutes,
                    days=_DEFAULT_DAYS, log=log,
                )
                if not _add_time_period_until_row_appears(
                    modal, page, log, attempts=3, expected_count=j,
                ):
                    log(
                        f"⚠ Period {j}/{len(windows)} ({w.label!r}) didn't commit "
                        "to Added Time Period table after 3 attempts — cancelling this group."
                    )
                    _dismiss_add_group_dialog_if_open(page, log)
                    return False
        else:
            log(
                f"⚠ Can't auto-configure {len(windows)} window(s) — at least one "
                f"is missing start/end: {[(w.label, w.start, w.end) for w in windows]}. "
                "Cancelling dialog so you can configure this group manually."
            )
            _dismiss_add_group_dialog_if_open(page, log)
            return False

    # For Survey, the Service Subtype dropdown can get wiped by cascade
    # events fired during the time-period UI interactions (observed
    # run-20260519-082700: subtype reverted from 'Video Surveillance' to
    # the 'Service Subtype' placeholder between pick + CREATE GROUP).
    # Re-check right before CREATE GROUP and re-pick once if it's been
    # wiped. See feedback_set_and_verify_sticks.md.
    if study_kind == "survey" and subtype_label:
        current = _survey_subtype_current_text(modal)
        if current != subtype_label:
            log(
                f"⚠ Service Subtype is {current!r} right before CREATE GROUP "
                f"(expected {subtype_label!r}) — cascade-wiped during time-period fill; re-picking."
            )
            if not _pick_survey_subtype(modal, subtype_label, log):
                log(
                    f"⚠ Couldn't re-pick Service Subtype {subtype_label!r} after wipe — "
                    "cancelling this group; qchub would reject CREATE GROUP."
                )
                _dismiss_add_group_dialog_if_open(page, log)
                return False

    # CREATE GROUP closes the dialog on success. Two ways to confirm success:
    #   (a) The Add Study Group modal is no longer visible (it closed).
    #   (b) qchub popped its post-create Info Alert ("Now please, add at
    #       least one location to be able to submit the request.") — this
    #       only appears AFTER a successful CREATE GROUP, so its presence
    #       is a positive success signal even if the modal close animation
    #       is still mid-transition.
    # The OLD logic was: wait 800ms, then "is modal still open?" → fail.
    # That hit a false-positive on run-20260519-082700: CREATE GROUP
    # actually succeeded (Info Alert appeared in the failure-snapshot
    # page.html), but our 800ms check caught the modal mid-close-animation
    # and we mistakenly rolled back. Now we wait up to 4s, polling both
    # signals — first one to fire wins.
    log("Clicking CREATE GROUP…")
    modal.get_by_role(
        "button", name=re.compile(r"^\s*create\s*group\s*$", re.IGNORECASE)
    ).click(timeout=5_000)

    import time as _t
    deadline = _t.time() + 4.0
    created = False
    while _t.time() < deadline:
        page.wait_for_timeout(200)
        # Positive signal: Info Alert popped — group was created.
        try:
            info_alert_visible = page.locator("div.modal.fade.in").filter(
                has_text=re.compile(r"add\s+at\s+least\s+one\s+location", re.IGNORECASE)
            ).first.is_visible(timeout=200)
        except Exception:
            info_alert_visible = False
        if info_alert_visible:
            created = True
            break
        # Negative-of-still-open signal: Add Study Group modal closed.
        if not _is_add_group_dialog_open(page):
            created = True
            break

    if not created:
        log("⚠ CREATE GROUP didn't close the dialog within 4s — qchub rejected the form.")
        _dismiss_add_group_dialog_if_open(page, log)
        raise QchubError(
            "CREATE GROUP failed: dialog stayed open after click. "
            "Check the diagnostic page.html for which field qchub flagged."
        )

    # The "Info Alert" pops up after the first group with a CONTINUE button.
    _dismiss_info_alert(page, log)

    # Per John Goodwin's workflow: upload THIS group's KML while it's still
    # the active selection in qchub. qchub may auto-reopen the Add Study
    # Group dialog after CREATE GROUP (anticipating the next group); its
    # backdrop would block the UPLOAD KML button on the studies page, so
    # dismiss any open dialog first.
    if kml_bytes is not None:
        _dismiss_add_group_dialog_if_open(page, log)
        label = f" ({group_label})" if group_label else ""
        log(f"Uploading {len(kml_bytes)}-byte KML for this group{label}…")
        with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
            f.write(kml_bytes)
            tmp_path = Path(f.name)
        try:
            _upload_kml(page, tmp_path, log)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return True


def _tube_duration_and_unit(window) -> tuple[int, str]:
    """Convert a TimeWindow to qchub's Tube form (Duration, Unit) pair.

    Per user direction 2026-05-25: tube counts in qchub are billed
    + scheduled in Days, not Hours. Default unit is "Days"; we only
    fall back to "Hours" for the rare sub-day count (e.g., a 4-hour
    morning sample). A 24-hour window (single full day) goes in as
    "1 Day" — not "24 Hours" — even though both render equivalently
    in qchub's UI.

    NOTE on unit values: qchub's <select> uses singular VALUES
    ("Day", "Hour") even though the visible display text is plural
    ("Days", "Hours"). We return the singular form because the form
    fill code calls `select_option(value=...)`. Don't change to plural
    without also changing the select call.

    Priority:
      1. If `total_hours` is set on the window (multi-day counts), prefer
         Day when the duration is a whole number of days (≥24h AND
         divisible by 24). Sub-day or weird-duration counts go in as
         Hour. Examples:
           total_hours=168 → (7, "Day")    # 7-day count
           total_hours=72  → (3, "Day")    # 72-hour classification
           total_hours=24  → (1, "Day")    # single full day
           total_hours=8   → (8, "Hour")   # 8-hour sample
           total_hours=36  → (36, "Hour")  # weird duration; not whole days
      2. If total_hours is missing, compute span from start–end. A
         single-day full-day window (00:00–23:59) counts as 1 Day;
         anything else falls back to Hours.
    """
    if window.total_hours and window.total_hours > 0:
        h = int(window.total_hours)
        if h >= 24 and h % 24 == 0:
            return h // 24, "Day"
        return h, "Hour"

    # Fallback path — total_hours not set. The extractor SHOULD set it
    # for multi-day counts (extractor schema now requires it); this
    # branch catches single-day windows + extractor misses.
    h = _hours_between(window.start, window.end)
    # 24-hour window == 1 full day. Express in Day so the qchub
    # form value matches how operations thinks about it.
    if h == 24:
        return 1, "Day"
    return h, "Hour"


def _fill_tube_time_period(
    modal, page: Page, *, duration: int, unit: str, days: list[str], log: ProgressCallback,
) -> None:
    """Fill the Tube Counts time-period form: Duration + Days/Hours + Start Day.

    Form structure (observed 2026-05-14, screenshot + saved page.html):
      [Duration <input number min=1>] [unit <select> Days/Hours] [Start Day <ss-multiselect>]
    No timePeriod radio — Tube has only this one form.
    """
    # Duration input: the visible number input inside the Tube panel. The
    # input doesn't have a stable id, so we locate by being inside the
    # 'Add Time Period' panel and being a number input.
    panel = modal.locator(
        "div.panel:has(h3.panel-title:has-text('Add Time Period'))"
    ).first
    try:
        panel.locator("input[type='number']").first.fill(str(duration), timeout=3_000)
        log(f"  Filled Duration = {duration}")
    except Exception as exc:
        log(f"(Couldn't fill Duration field: {exc})")

    # Day/Hour unit selector: native <select> with options "Day" and "Hour".
    # Note the option VALUE is "Day"/"Hour" (singular) but display text is "Days"/"Hours".
    # Use CSS :has() to scope cleanly to the unit select (not e.g. a Study
    # Subtype select that might also live in the modal).
    try:
        unit_select = panel.locator("select:has(option[value='Hour'])").first
        unit_select.select_option(value=unit, timeout=3_000)
        log(f"  Set unit = {unit}")
    except Exception as exc:
        log(f"(Couldn't set Day/Hour selector: {exc})")

    # Start Day picker: ss-multiselect-dropdown, single visible one IN THE
    # PANEL. Critical: scope to `panel`, NOT `modal`. The modal also contains
    # the Tube subtype dropdown (Volume / Volume,Class / etc.) as its own
    # ss-multiselect-dropdown — scoping to modal would pick THAT as picker[0]
    # instead of the Start Day picker (observed in run-20260514-151726).
    _select_days_in_picker(panel, page, picker_index=0, days=days, log=log)


def _force_close_open_dropdowns(page: Page, log: ProgressCallback) -> None:
    """Close any open dropdown menu inside the active study-group modal by
    triggering the dropdown widget's NATURAL click-outside-to-close handler.

    Why this exists at all: qchub's day picker (and subtype dropdowns) are
    `ss-multiselect-dropdown` widgets — they DON'T auto-close on item click
    because the design assumes you might pick more than one option. Their
    open menus float over sibling buttons like ADD TIME PERIOD, so without
    a close action between pick and next-click, the next click lands on a
    menu item instead of the intended target.

    Strategy: click on `div.modal-header` — a non-interactive area inside
    the active modal but outside any dropdown wrapper. Bootstrap/Angular
    dropdowns register a document click handler that fires on any click
    outside the dropdown's host element and closes the menu. The modal-
    header click is BOTH outside every dropdown (so the dropdowns close)
    AND inside the modal (so the modal doesn't dismiss).

    What we deliberately avoid:
    - `page.keyboard.press('Escape')` — BS3 modals close on Escape (broke
      order 176406)
    - `document.body.click()` — BS3 modals close on outside-click (broke
      order 176404)
    Both trigger qchub's modal-dismiss handlers as side effects.

    Fallback JS class-cleanup runs after, in case a dropdown widget doesn't
    listen for outside-clicks (no harm if it does).
    """
    # 1. Natural close: click the modal header (safe spot inside the modal).
    try:
        page.locator("div.modal.fade.in div.modal-header").first.click(timeout=2_000)
    except Exception as exc:
        log(f"  (modal-header click failed during force-close: {exc})")
    page.wait_for_timeout(150)

    # 2. Belt-and-suspenders JS cleanup. Pure CSS-class/style hide — touches
    # only dropdown widgets, never the modal or its keyboard/click handlers.
    try:
        page.evaluate(
            """() => {
                document.querySelectorAll('.dropdown.open').forEach(d => d.classList.remove('open'));
                document.querySelectorAll('.dropdown.show').forEach(d => d.classList.remove('show'));
                document.querySelectorAll('ul.dropdown-menu, [role="menu"]').forEach(m => {
                    if (m.offsetParent) m.style.display = 'none';
                });
            }"""
        )
    except Exception as exc:
        log(f"  (force-close JS cleanup failed: {exc})")
    page.wait_for_timeout(100)

    try:
        leftover = page.locator("[role='menuitem']:visible").count()
        if leftover > 0:
            log(f"  ⚠ {leftover} dropdown menuitem(s) still visible after force-close")
    except Exception:
        pass


def _survey_subtype_current_text(modal) -> str:
    """Read the currently-selected option text of the Survey Service
    Subtype <select>. Returns '' if the select isn't there or no option
    is selected. Used by `_pick_survey_subtype` (verify-after-pick) and
    by `_drive_one_group` (verify-before-CREATE-GROUP) to detect
    cascade wipes — qchub's time-period UI fires async events that have
    been observed to reset this dropdown back to the placeholder
    ('Service Subtype', value 33) several seconds after a successful
    select_option call. See feedback_set_and_verify_sticks.md.
    """
    try:
        sel = modal.locator("select").first
        return (sel.evaluate(
            "el => (el.options[el.selectedIndex] && el.options[el.selectedIndex].text || '').trim()"
        ) or "").strip()
    except Exception:
        return ""


def _pick_survey_subtype(modal, label: str, log: ProgressCallback) -> bool:
    """Pick the Service Subtype option in the Survey Study Type panel.

    qchub renders Survey's subtype as a NATIVE <select>; verified
    2026-05-17 PM via DevTools: when Survey radio is active, the modal
    contains EXACTLY ONE <select> element (TMC has none, Tube has a
    different one for a different study kind — and we only call this
    function from the Survey branch). That makes `modal.locator("select").first`
    the simplest reliable locator. The select mounts within ~80ms of
    the radio click and populates its 23 options within ~600ms (one of
    them being the placeholder "Service Subtype" plus 22 real subtypes).

    Earlier versions used a filtered locator (`select containing an
    option matching X`) — that turned out to be the actual bug: the
    filter chain wasn't matching during the populating window, AND
    Playwright's `has=` semantics with an outer-rooted inner locator
    are subtler than they look. The `.first` approach sidesteps both.

    Stages:
      1. Wait up to 8s for the first <select> in the modal to attach.
      2. Poll up to 6s for the desired option label to appear in the
         option list (Angular cascades them in asynchronously).
      3. Pick via `select_option(label=...)`; on label-finicky failure,
         fall back to a JS native-setter.
      4. Verify-after-pick: wait 400ms, then re-read the select's
         current option text. If it doesn't match `label`, retry up to
         3 times. This catches the cascade-wipe pattern documented in
         feedback_set_and_verify_sticks.md — qchub fires async events
         that can revert the dropdown to the placeholder shortly after
         a successful pick. (Note: a cascade fired LATER, after time-
         period interactions, can still wipe this; the caller re-checks
         right before CREATE GROUP and re-picks if needed.)
      5. Return False on every failure path; caller decides whether to
         hard-fail the group or proceed with the placeholder.
    """
    # Stage 1: the Survey-active modal has exactly one <select>; just
    # grab it. No filter needed (the prior option-text filter caused
    # the timeouts observed in run-20260517-102422 / 215952 / 221142).
    sel = modal.locator("select").first
    try:
        sel.wait_for(state="attached", timeout=8_000)
    except Exception as exc:
        log(
            f"  ⚠ Service Subtype <select> never mounted within 8s after "
            f"Survey radio check ({type(exc).__name__}). Subtype {label!r} NOT set."
        )
        return False

    # Stage 2: poll for the real options to populate. Angular cascades
    # the option list in asynchronously; the select can exist with only
    # the placeholder for a few hundred ms before the real options land.
    import time as _t
    deadline = _t.time() + 6.0
    target = label.strip()
    found_target = False
    while _t.time() < deadline:
        try:
            opts = sel.locator("option").all_text_contents()
            if any(o.strip() == target for o in opts):
                found_target = True
                break
        except Exception:
            pass
        _t.sleep(0.25)
    if not found_target:
        try:
            opts = sel.locator("option").all_text_contents()
            log(
                f"  ⚠ Option {label!r} never appeared in Service Subtype "
                f"dropdown within 6s. Available: {opts[:25]}"
            )
        except Exception:
            log(f"  ⚠ Option {label!r} never appeared (and couldn't enumerate).")
        return False

    # Stages 3 + 4: pick with verify-after-pick retry. The cascade-wipe
    # bug (run-20260519-082700) shows up here: select_option returns OK
    # synchronously, but ~hundreds of ms later Angular fires a cascade
    # that reverts the select back to the placeholder ("Service Subtype",
    # value 33). Re-pick until the value sticks for 400ms post-pick, or
    # give up after 3 attempts.
    import time as _t
    for attempt in range(1, 4):
        picked = False
        try:
            sel.select_option(label=label, timeout=2_000)
            picked = True
        except Exception as exc:
            log(f"  (select_option failed for Service Subtype on attempt {attempt}: {exc} — trying JS fallback)")
            try:
                result = sel.evaluate(
                    """(el, label) => {
                        const opt = Array.from(el.options).find(o => o.text.trim() === label);
                        if (!opt) return {ok: false, reason: 'no option matched',
                                          available: Array.from(el.options).map(o => o.text.trim())};
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                        setter.call(el, opt.value);
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return {ok: true};
                    }""",
                    label,
                )
                if isinstance(result, dict) and result.get("ok"):
                    picked = True
                else:
                    log(
                        f"  ⚠ JS fallback couldn't pick Service Subtype {label!r}: "
                        f"{result.get('reason') if isinstance(result, dict) else result}"
                        + (f" (available: {result['available'][:10]}…)" if isinstance(result, dict) and result.get("available") else "")
                    )
            except Exception as exc2:
                log(f"  ⚠ JS fallback errored on attempt {attempt}: {exc2}")

        if not picked:
            if attempt == 3:
                log(f"  ⚠ Couldn't pick Service Subtype {label!r} after 3 attempts.")
                return False
            _t.sleep(0.3)
            continue

        # Wait for any immediate cascade to fire, then verify.
        _t.sleep(0.4)
        current = _survey_subtype_current_text(modal)
        if current == label:
            if attempt == 1:
                log(f"  Picked Service Subtype: {label!r}")
            else:
                log(f"  Picked Service Subtype: {label!r} (took {attempt} attempts; cascade wiped earlier picks)")
            return True
        log(
            f"  Service Subtype reverted to {current!r} after pick attempt {attempt} "
            f"(expected {label!r}) — retrying."
        )
        _t.sleep(0.4)

    return False


def _fill_survey_custom_name(modal, page: Page, name: str, log: ProgressCallback) -> bool:
    """When Survey subtype is 'Custom Video Survey' (or any 'Custom ...'
    variant), qchub adds a 'Custom Survey' name input that becomes the
    line-item label on the estimate. Fill it.

    Selector strategy (screenshot 2026-05-17 053613): a single visible
    text input under a panel whose heading contains 'Custom Survey'.
    Falls back to any text input whose placeholder hints custom-survey.
    """
    log(f"  Custom Survey selected — filling name: {name!r}")
    page.wait_for_timeout(500)  # let the input render after subtype select
    candidates = [
        # Heading-scoped (preferred): input inside the panel whose title is 'Custom Survey'.
        modal.locator("div.panel:has(h3:has-text('Custom Survey')) input[type='text']").first,
        # Placeholder-based fallback.
        modal.locator("input[placeholder*='custom survey' i]").first,
        modal.locator("input[placeholder*='name for the custom' i]").first,
    ]
    for inp in candidates:
        try:
            inp.fill(name, timeout=2_000)
            log(f"  Filled Custom Survey name: {name!r}")
            return True
        except Exception:
            continue
    log(f"  ⚠ Couldn't find the Custom Survey name input; leaving blank (qchub may reject CREATE GROUP)")
    return False


def _pick_tube_subtype(modal, page: Page, label: str, log: ProgressCallback) -> bool:
    """Pick the Tube Counts subtype from its dropdown.

    qchub renders Tube's subtype as a bootstrap dropdown button (screenshot
    2026-05-14): click `button.dropdown-toggle` to open it, then click the
    option whose text contains `label` (e.g. 'Volume, Speed'). Falls back
    to native select_option if the dropdown is actually a real <select>.
    """
    # First try native <select> path (handles either rendering)
    selects = modal.locator("select").filter(
        has=modal.locator("option").filter(has_text=re.compile(r"^\s*Volume\s*$", re.IGNORECASE))
    )
    if selects.count() > 0:
        try:
            selects.first.select_option(label=label, timeout=2_000)
            log(f"  Picked Tube subtype (native select): {label!r}")
            return True
        except Exception:
            pass

    # Bootstrap dropdown path: click the toggle, then click the option.
    # Scope to the Study Type panel so we don't grab a day-picker dropdown.
    try:
        study_type_panel = modal.locator(
            "div.panel:has(h3.panel-title:has-text('Study Type'))"
        ).first
        toggle = study_type_panel.locator("button.dropdown-toggle").first
        toggle.click(timeout=3_000)
        page.wait_for_timeout(300)
        # Options render inside the panel as labels with checkboxes/text
        page.get_by_text(label, exact=True).first.click(timeout=3_000)
        page.wait_for_timeout(200)
        log(f"  Picked Tube subtype (bootstrap dropdown): {label!r}")
        # Force-close so the menu doesn't overlay the day picker / ADD TIME
        # PERIOD button on the next step. Observed in run-20260514-162045
        # (Group 4 Volume,Class): the subtype menu stayed open, then
        # blocked the day picker toggle click.
        _force_close_open_dropdowns(page, log)
        return True
    except Exception as exc:
        log(f"  ⚠ Couldn't pick Tube subtype {label!r}: {exc}")
        return False


def _add_time_period_until_row_appears(
    modal, page: Page, log: ProgressCallback, *, attempts: int = 3,
    expected_count: int = 1,
) -> bool:
    """Click ADD TIME PERIOD and verify a NEW row landed in the Added Time
    Period table. Retry on failure — the click can fire before Angular has
    registered the day/peak selections, in which case qchub silently no-ops.

    `expected_count` is the row count we expect to see AFTER this click
    succeeds. For the first period it's 1 (empty → 1). For the Nth period
    in a multi-period group it's N (so we detect "the click added a row"
    even though the table wasn't empty to start with). Default 1 preserves
    legacy single-period behavior at every existing call site.

    Returns True if the table reaches `expected_count` rows after one of
    the attempts; False if not after all `attempts` tries.
    """
    # The Added Time Period table inside this modal. qchub only renders
    # <tbody> when rows exist — Angular `*ngFor`-style. So we count <tr>
    # directly inside the panel's <table>; that works for both empty
    # (count == 0) and populated (count >= 1) states.
    rows = modal.locator(
        "div.panel:has(h3.panel-title:has-text('Added Time Period')) table tr"
    )
    button = modal.get_by_role(
        "button", name=re.compile(r"add\s*time\s*period", re.IGNORECASE)
    )
    # Settle BEFORE the first click attempt. Per user observation 2026-05-14:
    # qchub's day-picker cascades day selection into the time period form
    # asynchronously — the day shows checked in the dropdown but Angular
    # hasn't propagated it to the underlying form model yet. Clicking ADD
    # TIME PERIOD too fast hits the form mid-cascade and silently no-ops
    # (or qchub raises "needs to select a date"). 1.5s is empirical.
    page.wait_for_timeout(1_500)
    for attempt in range(1, attempts + 1):
        log(f"Clicking ADD TIME PERIOD (attempt {attempt}/{attempts}, expecting row count → {expected_count})…")
        try:
            button.click(timeout=5_000)
        except Exception as exc:
            log(f"(ADD TIME PERIOD click failed: {exc})")
        page.wait_for_timeout(800)
        row_count = rows.count()
        if row_count >= expected_count:
            log(f"  Added Time Period table now has {row_count} row(s).")
            return True
        log(f"  (Still at {row_count} row(s) — waiting for the new row to commit)")
        page.wait_for_timeout(1_200)
    return False


def _match_standard_peak(tw) -> Optional[str]:
    """Return 'am' / 'pm' if the time window exactly matches a qchub preset, else None."""
    for name, (ps, pe) in _STANDARD_PEAKS.items():
        if tw.start == ps and tw.end == pe:
            return name
    return None


def _duration_h_m(start: str, end: str, total_hours: Optional[int] = None) -> tuple[int, int]:
    """Compute (hours, minutes) duration for qchub's Specific Time fields.

    Priority:
      1. If `total_hours` is supplied (extractor sets it for 24h+ counts),
         use it directly: returns (total_hours, 0).
      2. Else compute from start/end HH:MM. Treats any window within 1
         minute of 24 hours (e.g. 00:00–23:59 — the LLM's "full day"
         expression since 24:00 isn't a valid time) as exactly 24h00m.
         Otherwise returns (minutes // 60, minutes % 60), so a 7:00–9:30
         window correctly yields (2, 30) instead of dropping the half.

    Minimum (1, 0). Bad input → (1, 0).
    """
    if total_hours is not None and total_hours > 0:
        return (total_hours, 0)
    try:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
    except ValueError:
        return (1, 0)
    minutes = (eh * 60 + em) - (sh * 60 + sm)
    if minutes <= 0:
        # Overnight or same-start-end is rare here; treat as full day.
        minutes = 24 * 60
    elif minutes >= 24 * 60 - 1:
        # Within 1 min of 24h → user meant 24h (00:00–23:59 case).
        minutes = 24 * 60
    return (max(1, minutes // 60), minutes % 60)


def _hours_between(start: str, end: str) -> int:
    """Backwards-compat wrapper — returns hours only, ignoring minutes.
    Prefer `_duration_h_m` in new call sites.
    """
    h, _m = _duration_h_m(start, end)
    return h


def _check_peak_and_pick_days(modal, page: Page, peak: str, days: list[str], log: ProgressCallback) -> None:
    """Check the AM or PM Peak checkbox then pick days in its adjacent dropdown.

    Scoped to the active modal so we don't reach into the dormant Edit Study
    Group modal's controls (which share label text).
    """
    log(f"Checking {peak} Peak + days={days}")
    label = modal.locator("label").filter(
        has_text=re.compile(rf"{peak}\s*Peak\s*\(", re.IGNORECASE)
    ).first
    label.locator("input[type='checkbox']").check(timeout=3_000)
    page.wait_for_timeout(200)
    # The day pickers in the Standard panel are ordered AM (0), PM (1).
    picker_index = 0 if peak == "AM" else 1
    _select_days_in_picker(modal, page, picker_index, days, log)


def _select_days_in_picker(modal, page: Page, picker_index: int, days: list[str], log: ProgressCallback) -> None:
    """Open the n-th visible day-multi-select inside `modal` and click the
    first item in `days` that matches a real menu option. Scoping to the
    modal avoids picking pickers from the dormant Edit Study Group modal.

    `days` is a list of CANDIDATE labels — we try each in order and stop on
    the first that resolves. This lets one call work across pickers with
    different label conventions (TMC has "Midweek (T-W-Th)" preset; Tube
    uses individual day names like "Tuesday"). If none match, we log every
    actually-visible menu option so the next diagnostic shows the truth.

    Always force-closes the dropdown on exit. A left-open dropdown's menu
    items intercept pointer events on subsequent ADD TIME PERIOD clicks —
    observed cascading failure in run-20260514-151726.
    """
    pickers = modal.locator("ss-multiselect-dropdown:visible button.dropdown-toggle")
    if picker_index >= pickers.count():
        log(f"(Day-picker[{picker_index}] not found — got {pickers.count()} visible in modal)")
        return
    picker = pickers.nth(picker_index)
    try:
        picker.click(timeout=3_000)
    except Exception as exc:
        log(f"(Couldn't open day picker[{picker_index}]: {exc})")
        return
    page.wait_for_timeout(300)

    # Try each candidate label; stop on first success.
    #
    # qchub renders the Midweek option as 'Midweek  (T-W-Th)' (two spaces,
    # observed run-20260514-223411). exact=True against single-space text
    # was missing it and cascading into a cancelled group + empty submit.
    # Match strategy:
    #   1) exact-match (cheap, preserves the happy path)
    #   2) whitespace-tolerant regex (handles internal multi-spaces)
    #   3) enumerate visible menu items and click the one whose collapsed
    #      whitespace matches our day — robust against any padding qchub
    #      throws at us
    def _whitespace_pattern(s: str) -> re.Pattern[str]:
        # "Midweek (T-W-Th)" → r"^\s*Midweek\s+\(T-W-Th\)\s*$"
        parts = [re.escape(tok) for tok in s.strip().split()]
        return re.compile(r"^\s*" + r"\s+".join(parts) + r"\s*$", re.IGNORECASE)

    def _collapse_ws(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip()).lower()

    # CRITICAL: scope clicks to the currently-open dropdown menu, NOT to
    # `page`. When we process picker[N>0], previously-opened pickers'
    # `ul.dropdown-menu` elements are STILL in the DOM (Angular keeps
    # them rendered but hidden via CSS). `page.get_by_text(...).first`
    # picks document-order, which is picker[0]'s hidden Midweek option —
    # the click "succeeds" against a hidden element and the open picker
    # never gets a day checked. Observed run-20260514-231550: AM Peak
    # got Midweek, PM Peak did not. Scope to `:visible` to isolate the
    # menu that's actually open right now.
    open_menu = page.locator("ul.dropdown-menu:visible").last
    clicked = False
    for day in days:
        try:
            open_menu.get_by_text(day, exact=True).first.click(timeout=1_500)
            log(f"  Picked day {day!r} in picker[{picker_index}].")
            clicked = True
            break
        except Exception:
            pass
        try:
            open_menu.get_by_text(_whitespace_pattern(day)).first.click(timeout=1_500)
            log(f"  Picked day {day!r} in picker[{picker_index}] (whitespace-tolerant match).")
            clicked = True
            break
        except Exception:
            pass
        # Last resort: enumerate visible menu items and click by normalized text
        try:
            options = open_menu.locator(
                "a[role='menuitem'], li, label"
            )
            target = _collapse_ws(day)
            count = options.count()
            for i in range(count):
                opt = options.nth(i)
                try:
                    txt = (opt.text_content(timeout=500) or "")
                except Exception:
                    continue
                if _collapse_ws(txt) == target:
                    opt.click(timeout=1_500)
                    log(f"  Picked day {day!r} in picker[{picker_index}] (enumerated match: {txt!r}).")
                    clicked = True
                    break
            if clicked:
                break
        except Exception:
            continue

    if not clicked:
        # Surface what options ARE in the open menu — invaluable next time.
        try:
            menu_items = page.locator(
                "ul.dropdown-menu:visible a[role='menuitem'], "
                "ul.dropdown-menu:visible li, "
                "ul.dropdown-menu:visible label"
            ).all_text_contents()
            menu_items = [m.strip() for m in menu_items if m.strip()]
            log(
                f"  ⚠ None of {days!r} matched any menu item. "
                f"Visible options: {menu_items[:20]}{'…' if len(menu_items) > 20 else ''}"
            )
        except Exception as exc:
            log(f"  ⚠ None of {days!r} matched, and couldn't enumerate menu items: {exc}")

    # ALWAYS force-close the dropdown. Observed cascade failure
    # 2026-05-14 (run-20260514-162045): after picking a day, qchub leaves
    # the menu's <a role="menuitem"> elements visible, and they overlay the
    # ADD TIME PERIOD button on subsequent clicks. Per-attempt: Escape →
    # JS-level Bootstrap-class cleanup → click document body.
    _force_close_open_dropdowns(page, log)


def _fill_specific_time(
    page: Page, *, start_time: str, hours: int, days: list[str],
    log: ProgressCallback, minutes: int = 0,
) -> None:
    """Fill the Specific Time panel (Start Time + Hours + Minutes + Day(s)).

    Visible panel layout observed 2026-05-14 (run-20260514-000557):
      [Start Time text]  [Hours number]  [Minutes number]  [Select day(s) multi]
    There is also a HIDDEN legacy panel with Duration + Day/Hour + Start Day —
    ignored. We scope locators to the active modal (`div.modal.fade.in`) so the
    Edit-Study-Group modal sitting dormant in the DOM doesn't steal matches.

    Both Hours and Minutes are now filled (2026-05-17). Prior versions left
    Minutes unset, which silently truncated any sub-hour duration AND
    rendered a full-day (00:00–23:59 → 23h59m) as 23 hours flat. Callers
    should compute the pair via `_duration_h_m` which rounds the
    full-day case correctly.
    """
    modal = _active_modal(page)

    # Start Time — id pattern is `search-to-date-N` (1-based per group).
    log(f"Filling Start Time: {start_time!r}")
    _fill_start_time_input(modal, start_time, log)

    # Hours (whole hours of total duration). Stable id `#duration_hours`.
    try:
        modal.locator("input#duration_hours").first.fill(str(hours), timeout=3_000)
        log(f"  Filled Hours = {hours}")
    except Exception as exc:
        log(f"(Couldn't fill Hours field: {exc})")

    # Minutes (remainder of total duration after whole hours).
    # Always written explicitly — leaving it untouched can carry over a
    # stale value from a prior group's modal state.
    try:
        modal.locator("input#duration_minutes").first.fill(str(minutes), timeout=3_000)
        log(f"  Filled Minutes = {minutes}")
    except Exception as exc:
        log(f"(Couldn't fill Minutes field: {exc})")

    # Day(s) — only visible day-multi-select in the Specific Time panel.
    _select_days_in_picker(modal, page, picker_index=0, days=days, log=log)


def _fill_start_time_input(modal, value: str, log: ProgressCallback) -> bool:
    """Fill the Start Time text input in the active study-group modal.

    The input is `<input id="search-to-date-N">` where N is 1-based per group.
    It's a plain text input backed by a JS timepicker — try Playwright `.fill`
    first, then a JS native-setter fallback that fires the input/change/blur
    events Angular's ControlValueAccessor listens for.
    """
    target = (value or "").strip()
    try:
        loc = modal.locator("input[id^='search-to-date-']").first
    except Exception as exc:
        log(f"(Couldn't locate Start Time input: {exc})")
        return False

    try:
        loc.click(timeout=2_000)
        loc.fill("", timeout=1_500)
        loc.fill(value, timeout=3_000)
        loc.press("Tab", timeout=2_000)
        if (loc.input_value(timeout=1_500) or "").strip() == target:
            return True
    except Exception:
        pass

    try:
        loc.evaluate(
            """(el, v) => {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, v);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""",
            value,
        )
        if (loc.input_value(timeout=1_500) or "").strip() == target:
            return True
    except Exception as exc:
        log(f"(Start Time JS fallback failed: {exc})")

    log(f"⚠ Couldn't confirm Start Time set to {target!r}")
    return False


def _dismiss_info_alert(page: Page, log: ProgressCallback) -> None:
    """Dismiss the 'Now please, add at least one location…' Info Alert that
    qchub pops after CREATE GROUP and at other workflow checkpoints.

    The DOM has multiple `div.modal.fade` variants of this alert (Angular keeps
    them around). Only the one with the `.in` class is visible. A naive
    `.first.is_visible()` check inspects a HIDDEN variant first, returns False,
    and we silently skip the click — leaving the real alert blocking the page.
    Scope the search to `div.modal.fade.in` to avoid that trap.
    """
    try:
        alert = page.locator("div.modal.fade.in").filter(
            has_text=re.compile(r"add\s+at\s+least\s+one\s+location", re.IGNORECASE)
        ).first
        if alert.is_visible(timeout=2_000):
            alert.get_by_role("button", name=re.compile(r"^\s*continue\s*$", re.IGNORECASE)).first.click(timeout=3_000)
            log("Dismissed Info Alert.")
    except Exception:
        pass


def _group_locations_for_qchub(request: StudyRequest) -> list[dict[str, Any]]:
    """Group a StudyRequest's locations into qchub-friendly study groups.

    Grouping keys by study kind:
      - **TUBE**:   (study_kind, tube_subtype, time-windows-signature) — each
                    distinct group-level Tube subtype (Volume vs Volume,Class
                    vs Video ATR Volume etc.) is a SEPARATE qchub Study Group,
                    even when time windows match. Updated 2026-05-17 per user
                    direction: each billable subtype is its own deliverable, so
                    it gets its own group (and its own MyMaps layer).
      - **TMC**:    (study_kind, time-windows-signature) — TMC has NO group-stage
                    secondary dropdown in qchub. Standard/Large/Complex/etc. are
                    set per-row on the Estimate modal post-submit. So we keep
                    TMC locations with matching time windows in ONE group, then
                    flag any non-majority subtypes as `subtype_outliers` for
                    post-submit correction via `set_estimate_subtype`.
      - **SURVEY**: (study_kind, time-windows-signature, survey_subtype,
                    custom_name) — Survey rows on the Estimate modal are
                    price-only (no per-row subtype dropdown), so subtype
                    variance MUST be expressed as separate groups at
                    order-creation time.

    `subtype_label` is the majority subtype across the group's locations (ties
    broken by first-seen, so the result is deterministic run-to-run). After the
    tube-by-subtype and survey-by-subtype splits, those groups are homogeneous
    by construction so their `subtype_outliers` list will always be empty; the
    field is kept on the group dict for uniform downstream consumption with TMC.
    """
    from collections import Counter
    groups: dict[tuple, dict[str, Any]] = {}
    for loc in request.locations:
        tw_sig = tuple((tw.label, tw.start, tw.end) for tw in loc.time_windows)
        if loc.study_kind == StudyKind.SURVEY:
            # Survey: split by full (subtype, custom_name) since estimate-modal
            # can't re-categorize survey rows.
            survey_label = _qchub_subtype_label(loc)
            custom = (loc.survey_custom_name or "").strip().lower()
            key = (loc.study_kind, tw_sig, survey_label, custom)
        elif loc.study_kind == StudyKind.TUBE:
            # Tube: split by tube_subtype too — each Tube subtype is its own
            # qchub Study Group (different billable deliverable, different
            # group-modal dropdown value, different MyMaps layer).
            tube_label = _qchub_subtype_label(loc)
            key = (loc.study_kind, tw_sig, tube_label)
        else:
            # TMC: no group-stage subtype; outliers handled on the Estimate modal.
            key = (loc.study_kind, tw_sig)
        if key not in groups:
            groups[key] = {
                "study_kind": loc.study_kind.value,
                "subtype_label": None,  # resolved after collecting all members
                "survey_custom_name": None,  # filled below for Survey groups
                "time_windows": list(loc.time_windows),
                "locations": [],
            }
        groups[key]["locations"].append(loc)
    for g in groups.values():
        labels = [_qchub_subtype_label(loc) for loc in g["locations"]]
        counts = Counter(labels)
        # Counter.most_common preserves insertion order on ties so the first-
        # seen subtype wins, giving us deterministic behaviour run-to-run.
        majority = counts.most_common(1)[0][0]
        g["subtype_label"] = majority
        outliers = [
            {"site_name": loc.site_name, "actual_subtype": lbl}
            for loc, lbl in zip(g["locations"], labels) if lbl != majority
        ]
        g["subtype_outliers"] = outliers
        # Survey-group-only: carry through the (consistent) custom name.
        if g["locations"] and g["locations"][0].study_kind == StudyKind.SURVEY:
            names = {(loc.survey_custom_name or "").strip() for loc in g["locations"]}
            names.discard("")
            g["survey_custom_name"] = next(iter(names), None)
    return list(groups.values())


def _submit_request(page: Page, log: ProgressCallback) -> tuple[Optional[str], Optional[str]]:
    """Click SUBMIT REQUEST, dismiss the 'Thank you' confirmation, capture order ID.

    Flow observed 2026-05-13:
      1. Click SUBMIT REQUEST → an Info Alert appears: "Thank you for your
         request. Your order has been submitted." with an OK button.
      2. Clicking OK redirects to /Admin/Orders/{order_id} where the new
         order ID is in the URL path.
    """
    # qchub auto-re-opens the Add Study Group dialog after the last group is
    # created, and the Info Alert can resurface after KML upload. Either of
    # those overlays blocks the SUBMIT REQUEST button on the underlying page,
    # producing "Couldn't find SUBMIT REQUEST button" even though the button
    # itself is in the DOM. Clear both defensively before clicking.
    _dismiss_add_group_dialog_if_open(page, log)
    _dismiss_info_alert(page, log)

    try:
        page.get_by_role("button", name=re.compile(r"submit\s*request", re.IGNORECASE)).first.click(timeout=10_000)
    except PlaywrightTimeoutError:
        raise QchubError("Couldn't find SUBMIT REQUEST button.")

    # Wait for + dismiss the success dialog.
    saw_confirmation = False
    try:
        page.get_by_text(
            re.compile(r"order\s+has\s+been\s+submitted", re.IGNORECASE)
        ).first.wait_for(state="visible", timeout=15_000)
        saw_confirmation = True
        log("Order submitted — confirming with OK.")
    except PlaywrightTimeoutError:
        log("(Didn't see the 'order submitted' confirmation within 15s)")

    if saw_confirmation:
        # Scope to the VISIBLE success modal — there are other Info Alert
        # modals in the DOM at this point (dormant, display: none), and a
        # bare `.first` against role=button name=OK can pick the wrong one.
        # The success modal carries the 'order has been submitted' text, so
        # scoping by that text isolates the right OK button. Retry once if
        # the click times out (modal fade animation can fail "stable" check).
        ok_clicked = False
        for attempt in range(2):
            try:
                success_modal = page.locator("div.modal.fade.in").filter(
                    has_text=re.compile(r"order\s+has\s+been\s+submitted", re.IGNORECASE)
                ).first
                success_modal.get_by_role(
                    "button", name=re.compile(r"^\s*ok\s*$", re.IGNORECASE)
                ).first.click(timeout=3_000)
                ok_clicked = True
                break
            except PlaywrightTimeoutError:
                page.wait_for_timeout(500)
                continue
        if not ok_clicked:
            log("(Couldn't click OK on the success modal after 2 attempts — proceeding to URL check)")

    # The page should now redirect to /Admin/Orders/{id}. Wait, then parse the URL.
    try:
        page.wait_for_url(re.compile(r"/Admin/Orders/\d+"), timeout=15_000)
    except PlaywrightTimeoutError:
        log(f"(No /Admin/Orders/... redirect detected. Current URL: {page.url})")

    m = re.search(r"/Admin/Orders/(\d+)", page.url)
    order_id = m.group(1) if m else None
    return order_id, page.url


def _capture_estimate(
    page: Page, order_id: str, run_dir: Path, log: ProgressCallback,
) -> Optional[Estimate]:
    """After SUBMIT REQUEST redirected us to /Admin/Orders/{id}, open the
    Estimate modal and capture the priced lines so the user can review
    (and later edit via Ellen) without leaving the chat.

    Flow per screenshots 2026-05-14 (order 176414):
      1. Click the orange-text ESTIMATE button on the order detail page —
         opens the Estimate MODAL (data is fully rendered inside, no need
         to click PREVIEW for visibility; PREVIEW only generates a PDF).
      2. Wait for the modal to render — look for `Optional Extra Items`
         text which is unique to the estimate modal.
      3. Save a screenshot + HTML snapshot of the modal for diagnostic /
         fallback purposes.
      4. Best-effort parse: each editable row has a `<select>` (subtype)
         next to two `<input type="number">` (unit price + quantity).
         Walk those to extract structured lines.
      5. Optionally trigger PREVIEW to download the PDF artifact, captured
         via `expect_download` so the user's browser doesn't pop a Save
         As dialog.

    Returns None if the modal can't be opened — caller treats it as "no
    estimate captured" rather than failing the whole order.
    """
    # 1. Open the modal via the ESTIMATE button.
    try:
        page.get_by_role(
            "button", name=re.compile(r"^\s*estimate\s*$", re.IGNORECASE)
        ).first.click(timeout=8_000)
    except PlaywrightTimeoutError:
        log("(Couldn't find the ESTIMATE button on the order page — skipping capture.)")
        return None

    # 2. Wait for the modal to actually render. The "Optional Extra Items"
    # heading is unique to the Estimate modal and not present on the
    # underlying order detail page.
    try:
        page.get_by_text(
            re.compile(r"Optional\s+Extra\s+Items", re.IGNORECASE)
        ).first.wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeoutError:
        log("(Estimate modal didn't show 'Optional Extra Items' within 10s — capture may be incomplete.)")

    page.wait_for_timeout(500)  # let the dropdowns finish populating

    # 3. Save HTML + screenshot.
    html_path = run_dir / f"estimate-{order_id}.html"
    png_path = run_dir / f"estimate-{order_id}.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        log(f"(Couldn't save estimate HTML: {exc})")
        html_path = None  # type: ignore[assignment]
    try:
        page.screenshot(path=str(png_path), full_page=True)
    except Exception as exc:
        log(f"(Couldn't save estimate screenshot: {exc})")
        png_path = None  # type: ignore[assignment]

    # 4. Best-effort parse of line items. Delegates to the shared helper
    # `_parse_estimate_rows_from_modal` so the initial capture and Ellen's
    # `get_estimate_lines` tool produce identical descriptions. Without
    # this delegation, the initial-capture parser used the OLD buggy
    # logic (walking up for `input[type="number"]` — qchub uses `text`,
    # so the walk overshot and every row reported the same location;
    # observed run-20260515-102453, order 176430). The helper has the
    # corrected DOM walk (anchored at `div.row.ng-star-inserted`).
    lines: list[EstimateLine] = []
    parse_note: Optional[str] = None
    try:
        modal = _estimate_modal_locator(page)
        lines = _parse_estimate_rows_from_modal(modal)
        if not lines:
            parse_note = "Parser found no rows — modal may have rendered differently than expected."
    except Exception as exc:
        parse_note = f"Parser raised {type(exc).__name__}: {exc}"
        log(f"(Estimate parser failed: {exc} — HTML/screenshot still saved.)")

    grand_total = (
        sum(L.line_total for L in lines if L.line_total is not None)
        if any(L.line_total is not None for L in lines)
        else None
    )

    # 5. Click PREVIEW to trigger the PDF — qchub serves the estimate as
    # `Estimate_{order_id}.pdf`. Capture strategy (parallel — whichever
    # fires first wins):
    #   A. Native download event (`context.on("download", ...)`)
    #      — works when Edge actually fires the download.
    #   B. PDF response on the network (`context.on("response", ...)`)
    #      — works when Edge opens the PDF inline / in a new tab, or
    #        when Edge's safety policy suppresses the download event but
    #        the response was still served.
    # The user reported (2026-05-14) that Edge's session won't surface
    # downloads — likely SmartScreen or Edge-for-Business policy. The
    # response path bypasses Edge's UI entirely.
    #
    # IMPORTANT: the Estimate modal has TWO PREVIEW buttons (top toolbar
    # and bottom footer with identical markup). The TOP one is inert in
    # the rendered modal; the BOTTOM one is the active control. `.first`
    # picks document-order which is the top inert button — observed
    # run-20260514-225436. Use `.last` to hit the footer button.
    pdf_path: Optional[Path] = None
    captured_downloads: list = []
    captured_pdf_responses: list = []

    def _on_download(dl) -> None:
        captured_downloads.append(dl)

    def _on_response(response) -> None:
        if captured_pdf_responses:
            return
        try:
            ctype = (response.headers.get("content-type") or "").lower()
        except Exception:
            return
        if "pdf" in ctype:
            captured_pdf_responses.append(response)

    page.context.on("download", _on_download)
    page.context.on("response", _on_response)
    try:
        clicked = False
        # Strategy A: Playwright locator with auto-scroll + actionability checks.
        try:
            preview_btn = page.get_by_role(
                "button", name=re.compile(r"^\s*preview\s*$", re.IGNORECASE)
            ).last
            try:
                preview_btn.scroll_into_view_if_needed(timeout=3_000)
            except Exception:
                pass
            preview_btn.click(timeout=6_000)
            clicked = True
            log("PREVIEW clicked (strategy A: normal).")
        except Exception:
            pass
        # Strategy B: force=True, bypasses actionability checks (visibility,
        # stability, receives-events). Works when an overlay is intercepting.
        if not clicked:
            try:
                preview_btn = page.get_by_role(
                    "button", name=re.compile(r"^\s*preview\s*$", re.IGNORECASE)
                ).last
                preview_btn.click(timeout=4_000, force=True)
                clicked = True
                log("PREVIEW clicked (strategy B: force).")
            except Exception:
                pass
        # Strategy C: JS-level click, bypasses Playwright's locator
        # resolution entirely. Useful when Angular re-renders the button
        # between resolution and click, making the locator stale.
        if not clicked:
            try:
                result = page.evaluate(
                    """() => {
                        const btns = Array.from(document.querySelectorAll('button'))
                            .filter(b => /^\\s*preview\\s*$/i.test((b.textContent || '').trim()));
                        const visible = btns.filter(b => {
                            const r = b.getBoundingClientRect();
                            const st = window.getComputedStyle(b);
                            return r.width > 0 && r.height > 0
                                && st.visibility !== 'hidden'
                                && st.display !== 'none';
                        });
                        if (visible.length === 0) return {ok: false, total: btns.length};
                        visible[visible.length - 1].click();
                        return {ok: true, total: btns.length, visible: visible.length};
                    }"""
                )
                if result and result.get("ok"):
                    clicked = True
                    log(f"PREVIEW clicked (strategy C: JS, {result.get('visible')}/{result.get('total')} visible).")
                else:
                    log(f"(JS click found no visible PREVIEW button; total in DOM: {result.get('total') if result else 'unknown'})")
            except Exception as exc:
                log(f"(JS click strategy failed: {exc})")
        if not clicked:
            log("(All PREVIEW click strategies failed — PDF skipped)")
            raise PlaywrightTimeoutError("PREVIEW button unclickable")

        # Poll up to 15s for either path.
        waited = 0
        while not captured_downloads and not captured_pdf_responses and waited < 15_000:
            page.wait_for_timeout(500)
            waited += 500

        # Save to the user's Downloads folder — that's where they'd
        # naturally look for the file (run_dir under %LOCALAPPDATA% is
        # opaque). The HTML snapshot in run_dir is still our diagnostic
        # fallback; the PDF is the user-facing artifact.
        downloads_dir = config.downloads_dir()
        if captured_downloads:
            dl = captured_downloads[0]
            suggested = dl.suggested_filename or f"Estimate_{order_id}.pdf"
            pdf_path = downloads_dir / suggested
            dl.save_as(str(pdf_path))
            log(f"Estimate PDF saved to Downloads (via download event): {pdf_path}")
        elif captured_pdf_responses:
            resp = captured_pdf_responses[0]
            saved = False
            try:
                body = resp.body()
                pdf_path = downloads_dir / f"Estimate_{order_id}.pdf"
                pdf_path.write_bytes(body)
                log(f"Estimate PDF saved to Downloads (via response body): {pdf_path}")
                saved = True
            except Exception as exc:
                log(f"(response.body() failed: {exc} — trying refetch)")
            if not saved:
                try:
                    r = page.context.request.get(resp.url)
                    if r.ok:
                        pdf_path = downloads_dir / f"Estimate_{order_id}.pdf"
                        pdf_path.write_bytes(r.body())
                        log(f"Estimate PDF saved to Downloads (via refetch): {pdf_path}")
                    else:
                        log(f"(PDF refetch returned status {r.status} — PDF skipped)")
                except Exception as exc:
                    log(f"(PDF refetch failed: {exc} — PDF skipped)")
        else:
            log("(No download event and no PDF response within 15s — PDF skipped)")
    except PlaywrightTimeoutError:
        pass
    except Exception as exc:
        log(f"(PREVIEW/download capture failed: {exc} — PDF skipped)")
    finally:
        try:
            page.context.remove_listener("download", _on_download)
        except Exception:
            pass
        try:
            page.context.remove_listener("response", _on_response)
        except Exception:
            pass

    estimate = Estimate(
        order_id=order_id,
        order_url=page.url,
        lines=lines,
        total=grand_total,
        html_path=str(html_path) if html_path else None,
        screenshot_path=str(png_path) if png_path else None,
        pdf_path=str(pdf_path) if pdf_path else None,
        parse_note=parse_note,
    )
    log(
        f"Captured estimate: {len(lines)} line(s) parsed"
        + (f", total ~${grand_total:,.2f}" if grand_total is not None else "")
        + f" (HTML: {html_path}, screenshot: {png_path}"
        + (f", PDF: {pdf_path}" if pdf_path else "")
        + ")"
    )
    return estimate


def _parse_money(s) -> Optional[float]:
    """Parse a money/quantity value out of a string. Returns None on empty/invalid."""
    if s is None:
        return None
    txt = str(s).strip().replace("$", "").replace(",", "")
    if not txt:
        return None
    try:
        return float(txt)
    except (TypeError, ValueError):
        return None


# ---------- Ship 2 helpers: live estimate edit + re-capture on existing page ----------

def _estimate_modal_locator(page: Page):
    """Return a Locator for the visible Estimate modal (the one containing
    `Optional Extra Items`)."""
    return page.locator("div.modal.fade.in").filter(
        has_text=re.compile(r"Optional\s+Extra\s+Items", re.IGNORECASE)
    ).first


def _open_estimate_modal_if_needed(page: Page, log: ProgressCallback) -> bool:
    """Make sure the Estimate modal is open. Idempotent.

    Returns True if the modal is visible after the call, False if we
    couldn't get it open.
    """
    try:
        modal = _estimate_modal_locator(page)
        if modal.is_visible(timeout=1_500):
            return True
    except Exception:
        pass
    try:
        page.get_by_role(
            "button", name=re.compile(r"^\s*estimate\s*$", re.IGNORECASE)
        ).first.click(timeout=6_000)
        page.get_by_text(
            re.compile(r"Optional\s+Extra\s+Items", re.IGNORECASE)
        ).first.wait_for(state="visible", timeout=8_000)
        page.wait_for_timeout(400)
        return True
    except Exception as exc:
        log(f"(Couldn't (re-)open Estimate modal: {exc})")
        return False


_SUBTYPE_OPTION_REGEX = r"Turn Count|Volume|Speed|Class|Survey"


def _parse_estimate_rows_from_modal(modal) -> list[EstimateLine]:
    """Walk the visible Estimate modal's row <select> elements and build
    EstimateLine objects.

    qchub DOM structure (observed 2026-05-15, order 176425):
      div.custom-component                      ← ONE location container
        div.row
          div.col-lg-3                          ← "Location N: <site_name>" + city/state
            <b>Location 1:</b> 600 S -- 250 E TMC
            <b>City/State:</b> Smithfield / UT
          div.col-lg-8
            div.row.ng-star-inserted            ← one time-period row
              div.col-lg-4: <b>When:</b> Midweek 7:00AM-9:00AM <b>Duration:</b> 2 Hrs.
              div.col-lg-4: <select>...</select>          ← subtype picker
              <input id="...rate" type="text">             ← unit price
              <input id="...extra" type="text">            ← quantity (always blank?)
            div.row.ng-star-inserted            ← another time-period row (PM peak)
              ...

    Old parser walked up looking for `>= 2 input[type="number"]`. qchub's
    inputs are `type="text"`, so the walk overshot and `row` ended up as a
    high ancestor — every row got the same description, prices came back
    null. New parser:
      1. From each subtype select, ascend to `div.row.ng-star-inserted`
         (the time-period row container).
      2. From that row, ascend to `div.custom-component` (the location
         container) and pull the site name + Location N once.
      3. Read the unit-price input from the row container by id-suffix
         'rate' and the quantity input by id-suffix 'extra'.
    """
    row_data = modal.evaluate(
        r"""(modal) => {
            const rows = [];
            const selects = Array.from(modal.querySelectorAll('select'));
            for (const sel of selects) {
                // Real estimate-row dropdowns always have options in
                // "Kind -- Variant" form. The bottom "Add additional line
                // item" template uses bare category options (Tube Count,
                // Video Count, Survey, Travel & Reimbursement, etc.) —
                // exclude it via the " -- " test on options[0].
                const firstOpt = (sel.options[0]?.text || '').trim();
                if (!/ -- /.test(firstOpt)) continue;
                const subtype = sel.options[sel.selectedIndex]?.text?.trim() || sel.value || null;

                // Walk up to the time-period row container. qchub renders each
                // (location × time period) cross product as `div.row.ng-star-inserted`
                // sitting under the location's `.col-lg-8`. We stop at the
                // closest ancestor that matches that class set.
                let row = sel.closest('div.row.ng-star-inserted');
                if (!row) {
                    // Fallback for layout drift — find ANY ancestor with both a
                    // rate input and our select.
                    let p = sel.parentElement;
                    for (let i = 0; i < 6 && p; i++) {
                        if (p.querySelector('input[id$="rate"]')) { row = p; break; }
                        p = p.parentElement;
                    }
                }
                if (!row) continue;

                // Walk further up to the location container, then read the
                // site name + Location N from its own col-lg-3 panel.
                const locContainer = row.closest('div.custom-component') || row.parentElement;
                let location = null, siteName = null;
                if (locContainer) {
                    const head = locContainer.querySelector('div.col-lg-3');
                    if (head) {
                        const headTxt = (head.innerText || '').replace(/\s+/g, ' ').trim();
                        // headTxt = "Location 1: 600 S -- 250 E TMC City/State: Smithfield / UT"
                        // Pull "Location N: <site>" (stop at "City/State" if present).
                        const m = headTxt.match(/^(Location\s*\d+\s*:\s*)([^]*?)(?:\s*City\/State\s*:.*)?$/i);
                        if (m) {
                            location = (m[1] + (m[2] || '').trim()).trim();
                            siteName = (m[2] || '').trim() || null;
                            // qchub sometimes renders site names as "X -- X" (kind appended).
                            // Collapse "A -- A" to just "A" for readability.
                            if (siteName) {
                                const dedupe = siteName.match(/^(.+?)\s+--\s+\1$/);
                                if (dedupe) siteName = dedupe[1];
                            }
                        } else {
                            location = headTxt;
                        }
                    }
                }

                // Read the "When:" line from the row itself.
                const rowTxt = (row.innerText || '').replace(/\s+/g, ' ').trim();
                let when = null;
                const whenMatch = rowTxt.match(/When:\s*([^]*?)\s*Duration:\s*(\d+)\s*Hrs?\./i);
                if (whenMatch) when = `${whenMatch[1].trim()} (${whenMatch[2]} hr)`;

                // Prices: scope to the row only, by id-suffix.
                const rateInput = row.querySelector('input[id$="rate"]');
                const extraInput = row.querySelector('input[id$="extra"]');
                const unitPrice = rateInput?.value?.trim() || null;
                const quantity = extraInput?.value?.trim() || null;

                rows.push({
                    location, site_name: siteName, when, subtype,
                    unit_price: unitPrice, quantity: quantity,
                    raw_text: rowTxt.slice(0, 400),
                });
            }
            return rows;
        }"""
    )
    out: list[EstimateLine] = []
    for r in row_data or []:
        # Description: lead with site name (clean), then time window, then
        # subtype. Drops redundant City/State and "Location N:" framing so
        # the chat panel renders something scannable. Example output:
        #   "600 S -- 250 E TMC — Midweek 7:00AM-9:00AM (2 hr) — Turn Count -- Standard"
        desc_parts: list[str] = []
        site = (r.get("site_name") or r.get("location") or "").strip()
        if site:
            desc_parts.append(site)
        if r.get("when"):
            desc_parts.append(r["when"])
        if r.get("subtype"):
            desc_parts.append(r["subtype"])
        description = " — ".join(desc_parts) or None
        unit_price = _parse_money(r.get("unit_price"))
        quantity = _parse_money(r.get("quantity"))
        total = (unit_price * quantity) if (unit_price is not None and quantity is not None) else None
        out.append(EstimateLine(
            description=description,
            unit_price=unit_price,
            quantity=quantity,
            line_total=total,
            raw_text=r.get("raw_text"),
        ))
    return out


def _estimate_line_to_dict(L: EstimateLine, index: int) -> dict:
    return {
        "index": index,
        "description": L.description,
        "unit_price": L.unit_price,
        "quantity": L.quantity,
        "line_total": L.line_total,
    }


def _split_site_when_subtype(description: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort split of an EstimateLine description (built by
    `_parse_estimate_rows_from_modal`) back into (site, when, subtype).
    The description uses ' — ' as separator. Used in the chat-panel
    summary and by Ellen when she needs the parts cleanly.
    """
    if not description:
        return (None, None, None)
    parts = [p.strip() for p in description.split(" — ")]
    while len(parts) < 3:
        parts.append("")
    return (parts[0] or None, parts[1] or None, parts[2] or None)


def _js_set_subtype_on_row(page: Page, row_index: int, subtype_match: str) -> dict:
    """Set the <select> on the Nth subtype row to the option whose text
    contains `subtype_match` (case-insensitive). Fires `change` event so
    Angular picks it up.

    Row enumeration: subtype <select> whose first option contains ` -- `
    (real estimate rows always use "Kind -- Variant" format). This
    excludes the bottom "Add additional line item" template row whose
    options are bare categories (Tube Count / Video Count / Survey /
    Travel & Reimbursement / etc.) — matching that one and setting a
    subtype on it silently corrupts the add-line template form.
    """
    return page.evaluate(
        r"""({rowIndex, subtypeMatch}) => {
            const modal = document.querySelector('div.modal.fade.in');
            if (!modal) return {ok: false, error: 'modal not visible'};
            const selects = Array.from(modal.querySelectorAll('select')).filter(sel => {
                const first = (sel.options[0]?.text || '').trim();
                return / -- /.test(first);
            });
            if (rowIndex < 0 || rowIndex >= selects.length) {
                return {ok: false, error: 'row index out of range', n_rows: selects.length};
            }
            const sel = selects[rowIndex];
            const target = subtypeMatch.toLowerCase();
            const matches = Array.from(sel.options).filter(o =>
                (o.text || '').toLowerCase().includes(target)
            );
            if (matches.length === 0) {
                return {ok: false, error: 'no option matched',
                    available: Array.from(sel.options).map(o => o.text)};
            }
            sel.value = matches[0].value;
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            return {ok: true, picked: matches[0].text};
        }""",
        {"rowIndex": row_index, "subtypeMatch": subtype_match},
    )


def _js_set_rate_on_row(
    page: Page, row_index: int, base_price: float, extra_rate: float = 0.0,
) -> dict:
    """Write the two price inputs on a Tube/TMC estimate row.

    qchub Tube and TMC rows have two TEXT inputs (NOT number — that was
    the old bug):
      - `input[id$="rate"]`   — base study amount (Field 1)
      - `input[id$="extra"]`  — per-additional-unit rate (Field 2)
                                 (additional hours for TMC, additional days
                                 for Tube). For a fixed-amount study, this
                                 should be $0.

    Survey rows have only the base-price input; the `extra` lookup returns
    null and we skip it silently.

    Row enumeration matches `_parse_estimate_rows_from_modal`:
      1. Select all `<select>` whose first option text contains ` -- `
         (the canonical "Kind -- Variant" format used by real estimate
         rows). This excludes the bottom "Add additional line item"
         template row, whose options are bare categories (Tube Count,
         Video Count, Survey, Travel & Reimbursement, etc.).
      2. Locate the row container via `closest('div.row.ng-star-inserted')`.
      3. Find the rate + extra inputs by id-suffix within the row.

    Fires `input` / `change` / `blur` so Angular's ngModel re-reads.
    """
    return page.evaluate(
        r"""({rowIndex, basePrice, extraRate}) => {
            const modal = document.querySelector('div.modal.fade.in');
            if (!modal) return {ok: false, error: 'modal not visible'};
            const selects = Array.from(modal.querySelectorAll('select')).filter(sel => {
                const first = (sel.options[0]?.text || '').trim();
                return / -- /.test(first);
            });
            if (rowIndex < 0 || rowIndex >= selects.length) {
                return {ok: false, error: 'row index out of range', n_rows: selects.length};
            }
            const sel = selects[rowIndex];
            let row = sel.closest('div.row.ng-star-inserted');
            if (!row) {
                let p = sel.parentElement;
                for (let i = 0; i < 6 && p; i++) {
                    if (p.querySelector('input[id$="rate"]')) { row = p; break; }
                    p = p.parentElement;
                }
            }
            if (!row) return {ok: false, error: 'row container not found'};
            const rateInput = row.querySelector('input[id$="rate"]');
            const extraInput = row.querySelector('input[id$="extra"]');
            if (!rateInput) return {ok: false, error: 'base-price input not found in row'};
            const setNative = (inp, val) => {
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(inp, String(val));
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                inp.dispatchEvent(new Event('blur', {bubbles: true}));
            };
            setNative(rateInput, basePrice);
            const wroteExtra = (extraInput && extraRate !== null && extraRate !== undefined);
            if (wroteExtra) setNative(extraInput, extraRate);
            return {
                ok: true,
                base: basePrice,
                extra: wroteExtra ? extraRate : null,
                extra_input_present: !!extraInput,
            };
        }""",
        {"rowIndex": row_index, "basePrice": base_price, "extraRate": extra_rate},
    )


def _click_save_rates_if_present(page: Page, log: ProgressCallback) -> bool:
    """Click the SAVE RATES button in the Estimate modal. Best-effort.
    Returns True on success, False otherwise. The button uses the same
    top/bottom toolbar duplication as PREVIEW — use `.last` for the
    active control.
    """
    try:
        page.evaluate(
            r"""() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(b => /save\s*rates/i.test((b.textContent || '').trim()));
                const visible = btns.filter(b => {
                    const r = b.getBoundingClientRect();
                    const st = window.getComputedStyle(b);
                    return r.width > 0 && r.height > 0
                        && st.visibility !== 'hidden' && st.display !== 'none';
                });
                if (visible.length === 0) return false;
                visible[visible.length - 1].click();
                return true;
            }"""
        )
        page.wait_for_timeout(800)
        return True
    except Exception as exc:
        log(f"(SAVE RATES click failed: {exc})")
        return False


def _trigger_preview_and_save_pdf(
    page: Page, order_id: str, log: ProgressCallback, *, version: int = 1,
) -> Optional[Path]:
    """Click PREVIEW (three strategies) + capture PDF via download event
    OR network response listener. Save to Downloads. Returns the PDF
    path on success, None otherwise. `version` > 1 appends `_v{n}`
    suffix so re-captures don't clobber the original.
    """
    captured_downloads: list = []
    captured_pdf_responses: list = []

    def _on_download(dl) -> None:
        captured_downloads.append(dl)

    def _on_response(response) -> None:
        if captured_pdf_responses:
            return
        try:
            ctype = (response.headers.get("content-type") or "").lower()
        except Exception:
            return
        if "pdf" in ctype:
            captured_pdf_responses.append(response)

    page.context.on("download", _on_download)
    page.context.on("response", _on_response)
    pdf_path: Optional[Path] = None
    try:
        clicked = False
        try:
            preview_btn = page.get_by_role(
                "button", name=re.compile(r"^\s*preview\s*$", re.IGNORECASE)
            ).last
            try:
                preview_btn.scroll_into_view_if_needed(timeout=3_000)
            except Exception:
                pass
            preview_btn.click(timeout=6_000)
            clicked = True
        except Exception:
            pass
        if not clicked:
            try:
                preview_btn = page.get_by_role(
                    "button", name=re.compile(r"^\s*preview\s*$", re.IGNORECASE)
                ).last
                preview_btn.click(timeout=4_000, force=True)
                clicked = True
            except Exception:
                pass
        if not clicked:
            try:
                result = page.evaluate(
                    r"""() => {
                        const btns = Array.from(document.querySelectorAll('button'))
                            .filter(b => /^\s*preview\s*$/i.test((b.textContent || '').trim()));
                        const visible = btns.filter(b => {
                            const r = b.getBoundingClientRect();
                            const st = window.getComputedStyle(b);
                            return r.width > 0 && r.height > 0
                                && st.visibility !== 'hidden' && st.display !== 'none';
                        });
                        if (visible.length === 0) return {ok: false};
                        visible[visible.length - 1].click();
                        return {ok: true};
                    }"""
                )
                if result and result.get("ok"):
                    clicked = True
            except Exception:
                pass
        if not clicked:
            log("(All PREVIEW click strategies failed — PDF skipped)")
            return None

        waited = 0
        while not captured_downloads and not captured_pdf_responses and waited < 15_000:
            page.wait_for_timeout(500)
            waited += 500

        downloads = config.downloads_dir()
        suffix = "" if version <= 1 else f"_v{version}"
        if captured_downloads:
            dl = captured_downloads[0]
            base = dl.suggested_filename or f"Estimate_{order_id}.pdf"
            stem = Path(base).stem
            pdf_path = downloads / f"{stem}{suffix}.pdf"
            dl.save_as(str(pdf_path))
            log(f"Estimate PDF saved to Downloads (download event): {pdf_path}")
        elif captured_pdf_responses:
            resp = captured_pdf_responses[0]
            saved = False
            try:
                body = resp.body()
                pdf_path = downloads / f"Estimate_{order_id}{suffix}.pdf"
                pdf_path.write_bytes(body)
                log(f"Estimate PDF saved to Downloads (response body): {pdf_path}")
                saved = True
            except Exception as exc:
                log(f"(response.body() failed: {exc} — trying refetch)")
            if not saved:
                try:
                    r = page.context.request.get(resp.url)
                    if r.ok:
                        pdf_path = downloads / f"Estimate_{order_id}{suffix}.pdf"
                        pdf_path.write_bytes(r.body())
                        log(f"Estimate PDF saved to Downloads (refetch): {pdf_path}")
                    else:
                        log(f"(PDF refetch returned status {r.status} — PDF skipped)")
                except Exception as exc:
                    log(f"(PDF refetch failed: {exc} — PDF skipped)")
        else:
            log("(No download event and no PDF response within 15s — PDF skipped)")
    finally:
        try:
            page.context.remove_listener("download", _on_download)
        except Exception:
            pass
        try:
            page.context.remove_listener("response", _on_response)
        except Exception:
            pass
    return pdf_path


def _execute_edit_command(
    cmd: "_EditCommand", page: Page, order_id: str, log: ProgressCallback, version_holder: list[int],
) -> Any:
    """Handler for one Ellen-issued command. Runs in the qchub worker
    thread on the live page. The session.dispatch_loop catches exceptions
    here and ships them back through the reply slot.
    """
    if not _open_estimate_modal_if_needed(page, log):
        raise RuntimeError("Estimate modal isn't open — can't run command.")

    modal = _estimate_modal_locator(page)

    if cmd.kind == "get_lines":
        lines = _parse_estimate_rows_from_modal(modal)
        return [_estimate_line_to_dict(L, i) for i, L in enumerate(lines)]

    if cmd.kind == "list_subtype_options":
        idx = int(cmd.payload["line_index"])
        # Same row-enumeration as the parser + setters: " -- " in first option
        # text identifies real estimate-row subtype dropdowns and excludes
        # the bottom add-line-item template.
        result = page.evaluate(
            r"""(rowIndex) => {
                const modal = document.querySelector('div.modal.fade.in');
                if (!modal) return {ok: false, error: 'modal not visible'};
                const selects = Array.from(modal.querySelectorAll('select')).filter(sel => {
                    const first = (sel.options[0]?.text || '').trim();
                    return / -- /.test(first);
                });
                if (rowIndex < 0 || rowIndex >= selects.length) {
                    return {ok: false, error: 'row index out of range', n_rows: selects.length};
                }
                const sel = selects[rowIndex];
                const current = sel.options[sel.selectedIndex]?.text?.trim() || null;
                const options = Array.from(sel.options).map(o => (o.text || '').trim()).filter(Boolean);
                return {ok: true, current, options};
            }""",
            idx,
        )
        if not result.get("ok"):
            raise RuntimeError(
                f"Couldn't read options for row {idx}: {result.get('error')}"
                + (f" (rows present: {result.get('n_rows')})" if result.get("n_rows") is not None else "")
            )
        return {
            "line_index": idx,
            "current": result.get("current"),
            "options": result.get("options") or [],
        }

    if cmd.kind == "set_subtype":
        idx = int(cmd.payload["line_index"])
        subtype = str(cmd.payload["subtype"])
        result = _js_set_subtype_on_row(page, idx, subtype)
        if not result.get("ok"):
            raise RuntimeError(
                f"Couldn't set subtype on row {idx}: {result.get('error')}"
                + (f" — available: {result.get('available')}" if result.get("available") else "")
            )
        page.wait_for_timeout(400)  # let Angular cascade the change
        lines = _parse_estimate_rows_from_modal(modal)
        if idx >= len(lines):
            return {"picked": result.get("picked"), "line": None}
        return {"picked": result.get("picked"), "line": _estimate_line_to_dict(lines[idx], idx)}

    if cmd.kind == "set_rate":
        idx = int(cmd.payload["line_index"])
        price = float(cmd.payload["unit_price"])
        # extra_rate defaults to 0.0 — for fixed-amount studies the per-unit
        # field is zeroed out. Ellen passes a non-zero value only when the
        # user explicitly mentions an overrun / per-unit rate.
        extra = float(cmd.payload.get("extra_rate", 0.0))
        result = _js_set_rate_on_row(page, idx, price, extra_rate=extra)
        if not result.get("ok"):
            raise RuntimeError(f"Couldn't set rate on row {idx}: {result.get('error')}")
        page.wait_for_timeout(400)
        lines = _parse_estimate_rows_from_modal(modal)
        if idx >= len(lines):
            return {"set_base": price, "set_extra": extra, "line": None}
        return {
            "set_base": price,
            "set_extra": extra,
            "extra_input_present": result.get("extra_input_present", False),
            "line": _estimate_line_to_dict(lines[idx], idx),
        }

    if cmd.kind == "apply_rate_to_all":
        substr = str(cmd.payload["subtype_contains"]).lower()
        price = float(cmd.payload["unit_price"])
        extra = float(cmd.payload.get("extra_rate", 0.0))
        # Re-parse the current state to find matching row indices
        lines = _parse_estimate_rows_from_modal(modal)
        targets = [
            i for i, L in enumerate(lines)
            if L.description and substr in L.description.lower()
        ]
        updated = 0
        for i in targets:
            result = _js_set_rate_on_row(page, i, price, extra_rate=extra)
            if result.get("ok"):
                updated += 1
            page.wait_for_timeout(150)
        page.wait_for_timeout(400)
        lines = _parse_estimate_rows_from_modal(modal)
        return {
            "updated": updated,
            "matched": len(targets),
            "set_base": price,
            "set_extra": extra,
            "lines": [_estimate_line_to_dict(L, i) for i, L in enumerate(lines)],
        }

    if cmd.kind == "re_capture":
        # Save any pending rate edits, then re-trigger PREVIEW + capture.
        _click_save_rates_if_present(page, log)
        page.wait_for_timeout(800)
        version_holder[0] += 1
        v = version_holder[0]
        pdf_path = _trigger_preview_and_save_pdf(page, order_id, log, version=v)
        lines = _parse_estimate_rows_from_modal(modal)
        total = sum((L.line_total or 0) for L in lines) if lines else None
        return {
            "version": v,
            "pdf_path": str(pdf_path) if pdf_path else None,
            "total": total,
            "lines": [_estimate_line_to_dict(L, i) for i, L in enumerate(lines)],
        }

    raise RuntimeError(f"Unknown edit command: {cmd.kind!r}")


def _dispatch_edit_commands_until_done(
    context, page: Page, session: "QchubEditSession", order_id: Optional[str],
    log: ProgressCallback, *, timeout_sec: int,
) -> None:
    """Replaces the blocking `wait_for_event("close")`. Drains commands
    from `session.command_queue`, executes them on the live page, and
    exits when EITHER (a) the user closed the browser, or (b) the
    `timeout_sec` budget elapsed.
    """
    if order_id is None:
        # No order ID means no estimate modal — nothing for Ellen to edit.
        # Fall back to the simple wait-for-close behaviour.
        try:
            context.wait_for_event("close", timeout=timeout_sec * 1000)
            log("Browser closed by user — qchub session done.")
        except PlaywrightTimeoutError:
            log("Manual-finish timeout reached — closing browser session.")
        except Exception as exc:
            log(f"(post-ready wait ended: {type(exc).__name__}: {exc})")
        return

    version_holder = [1]  # mutable counter so _execute_edit_command can bump
    end_time = time.time() + timeout_sec
    log(
        f"Browser staying open up to {timeout_sec // 60} min "
        "for manual review + Ellen edits. Close the browser when you're done."
    )

    def _context_alive() -> bool:
        try:
            return len(context.pages) > 0
        except Exception:
            return False

    while time.time() < end_time:
        if not _context_alive():
            log("Browser closed by user — qchub session done.")
            break
        if session.request_close.is_set():
            log("End-session requested by Ellen — closing qchub browser.")
            try:
                context.close()
            except Exception:
                pass
            break
        try:
            cmd = session.command_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            log(f"[edit-session {cmd.cid}] {cmd.kind} payload={cmd.payload!r}")
            result = _execute_edit_command(cmd, page, order_id, log, version_holder)
            cmd.reply.put(("ok", result))
            log(f"[edit-session {cmd.cid}] {cmd.kind} → ok")
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            cmd.reply.put(("err", err))
            log(f"[edit-session {cmd.cid}] {cmd.kind} → err: {err}")
    else:
        log("Manual-finish timeout reached — closing browser session.")
    session.ended.set()
    # Drain any commands queued AFTER we exited so the callers don't hang.
    while True:
        try:
            cmd = session.command_queue.get_nowait()
            cmd.reply.put(("err", "qchub session has ended."))
        except queue.Empty:
            break


# ==========================================================================
# AUTO-CREATE — admin form drivers
# ==========================================================================
# Selectors locked-in via DevTools dump 2026-05-18 PM. Operational walkthrough
# by Noah Smith 2026-05-18 (transcript in project_qchub_auto_create.md).
#
# Three public functions:
#   * `read_existing_companies_via_admin(page, log)` — pre-create dupe check
#     (reads the ParentCompanyID dropdown; size grows as QC adds clients).
#   * `create_company_via_admin(page, info, log, run_dir=None)` — fills the
#     Add Company modal, saves, verifies. Returns the picked option text on
#     success (so the caller can use it as `company_match_text` when
#     creating the user).
#   * `create_client_user_via_admin(page, info, log, run_dir=None)` — fills
#     the Add Client User modal, saves, verifies.
#
# Each takes an already-logged-in `page`. The caller (orchestrator) owns
# session lifecycle.

_ADMIN_COMPANIES_URL = "https://qchub.qualitycounts.net/Admin/Companies"
_ADMIN_USERS_URL = "https://qchub.qualitycounts.net/Admin/Users"


def _add_company_modal_locator(page: Page):
    """Locate the Add Company modal — scoped by 'has CompanyName input'.

    qchub renders this as a `<kendo-dialog>`, NOT a Bootstrap modal
    (confirmed run-20260521-114225 page.html: the form lives inside a
    `<kendo-dialog class="medium-modal k-dialog-wrapper">` wrapper, no
    `div.modal.fade.in` ancestor). An earlier version of this locator
    used `div.modal.fade.in` which never matched — the modal would
    open visibly in the browser, the form fields would render, but
    our `modal.wait_for(state="visible")` would time out after 10s
    and we'd bail with "Add Company modal didn't render".

    Scoping by `has=input[name="CompanyName"]` keeps us from matching
    other kendo-dialogs that might be on the page (e.g. the upstream
    Request Estimate modal also has CompanyID dropdowns but no
    CompanyName text input).
    """
    return page.locator("kendo-dialog").filter(
        has=page.locator('input[name="CompanyName"]')
    ).first


def _add_user_modal_locator(page: Page):
    """Locate the Add Client User modal — scoped by 'has Email input
    AND CompanyID select'. Like Add Company, this is a kendo-dialog
    in current qchub, not a Bootstrap modal."""
    return page.locator("kendo-dialog").filter(
        has=page.locator('input[name="Email"]')
    ).filter(
        has=page.locator('select[name="CompanyID"]')
    ).first


def _dismiss_unsaved_changes_prompt(page: Page, log: ProgressCallback) -> None:
    """If qchub's 'Your changes have not been saved' interstitial is
    visible, dismiss it. Best-effort — silent on miss."""
    try:
        prompt = page.locator("div.modal.fade.in").filter(
            has_text=re.compile(r"changes\s+have\s+not\s+been\s+saved", re.IGNORECASE)
        ).first
        if prompt.is_visible(timeout=1_000):
            # Close button is the × in the header.
            close = prompt.locator("button.close, button[aria-label='Close'], span.close").first
            try:
                close.click(timeout=2_000)
                log("  Dismissed 'unsaved changes' interstitial.")
            except Exception:
                # Fall back: press Escape
                page.keyboard.press("Escape")
    except Exception:
        pass


def _close_kendo_dialogs_gracefully(page: Page, log: ProgressCallback) -> int:
    """Close any rendered kendo-dialog by clicking its close button via
    JS so Angular runs its OnDestroy hooks and tears the component
    down cleanly.

    Returns the number of dialogs whose close button was clicked.

    Why JS instead of Playwright locators: Playwright's `is_visible()`
    on a custom element wrapper (`<kendo-dialog>`) returns False in
    some renderings even when the dialog is actively intercepting
    clicks. Observed run-20260521-112224: `page.locator("kendo-dialog")
    .first.is_visible(timeout=300)` returned False so the close
    function exited silently, and seconds later Playwright's own click
    diagnostics showed `<div class="k-overlay"> from <kendo-dialog>
    subtree intercepts pointer events`. The dialog was there; only
    is_visible disagreed. Doing the visibility check + close in one
    `page.evaluate` call sidesteps that quirk entirely.

    Why click-the-X instead of DOM removal: the previous approach
    (`_dismiss_lingering_kendo_dialogs`, reverted 2026-05-18 PM) used
    `element.remove()` to evict the kendo-dialog from the DOM. That
    bypassed Kendo/Angular's destruction lifecycle and left the parent
    component in an inconsistent state — the Add Company modal on
    /Admin/Companies stopped opening (observed run-20260518-183303).
    Clicking the dialog's close button fires Kendo's normal close
    pipeline, which lets Angular run its destruction hooks. The
    Request Estimate modal goes away AND /Admin/* pages remain
    functional afterward.

    Always logs at start + finish so callers can see whether the
    function actually ran and what it found.
    """
    log("  Checking for any open kendo-dialog to close gracefully…")
    try:
        result = page.evaluate(
            """() => {
                const dialogs = Array.from(document.querySelectorAll('kendo-dialog'));
                let rendered = 0;
                let clicked = 0;
                let skipped_admin = 0;
                const notes = [];
                for (const d of dialogs) {
                    // Rendered = has any positive size or visible offsetParent.
                    const isRendered =
                        (d.offsetParent !== null) ||
                        (d.offsetWidth > 0) ||
                        (d.offsetHeight > 0);
                    if (!isRendered) continue;
                    rendered++;
                    // SKIP the Add Company / Add Client User admin dialogs —
                    // those ARE kendo-dialogs (we discovered this in
                    // run-20260521-114225) but they're the dialog we just
                    // opened on purpose. Closing them would defeat the
                    // auto-create. Identified by the presence of their
                    // characteristic required input.
                    if (d.querySelector('input[name="CompanyName"]') ||
                        (d.querySelector('input[name="Email"]') &&
                         d.querySelector('select[name="CompanyID"]'))) {
                        skipped_admin++;
                        notes.push('skipping admin form kendo-dialog (Add Company / Add User)');
                        continue;
                    }
                    // Find a close button — try a few common Kendo Angular shapes.
                    const closeBtn =
                        d.querySelector('button[aria-label="Close" i]') ||
                        d.querySelector('a[aria-label="Close" i]') ||
                        d.querySelector('.k-dialog-titlebar .k-button') ||
                        d.querySelector('button[title*="close" i]') ||
                        (d.querySelector('.k-i-close') &&
                         d.querySelector('.k-i-close').closest('button, a')) ||
                        d.querySelector('.k-dialog-close');
                    if (!closeBtn) {
                        notes.push('found rendered kendo-dialog with no close button');
                        continue;
                    }
                    closeBtn.click();
                    clicked++;
                }
                return { rendered, clicked, skipped_admin, notes };
            }"""
        )
    except Exception as exc:
        log(f"  ⚠ kendo-dialog close JS failed: {type(exc).__name__}: {exc}")
        return 0

    rendered = int(result.get("rendered", 0))
    clicked = int(result.get("clicked", 0))
    skipped_admin = int(result.get("skipped_admin", 0))
    notes = result.get("notes", []) or []
    log(
        f"  Kendo dialogs in DOM: rendered={rendered}, "
        f"close-clicked={clicked}, skipped-admin-forms={skipped_admin}."
    )
    for n in notes:
        log(f"  (kendo close note: {n})")

    if clicked > 0:
        # Give Angular ng-leave animation a beat to complete.
        page.wait_for_timeout(800)
        try:
            remaining = page.evaluate(
                """() => Array.from(document.querySelectorAll('kendo-dialog'))
                    .filter(d => d.offsetParent !== null ||
                                 d.offsetWidth > 0 ||
                                 d.offsetHeight > 0).length"""
            )
        except Exception:
            remaining = None
        if remaining and int(remaining) > 0:
            log(
                f"  ⚠ {remaining} kendo-dialog(s) still rendered 800ms after "
                f"close click — Angular teardown may still be in flight."
            )

    # qchub sometimes pops a Bootstrap "Your changes have not been
    # saved" prompt after closing the Request Estimate kendo modal.
    # Dismiss it now so it doesn't block the next nav.
    _dismiss_unsaved_changes_prompt(page, log)
    return clicked


def read_existing_companies_via_admin(page: Page, log: ProgressCallback) -> list[str]:
    """Open the Add Company modal momentarily to read the
    ParentCompanyID dropdown options, then cancel.

    Used by the orchestrator before creating a company so it can call
    `find_company_near_matches` and surface "this looks like a dupe"
    to the user. Per Noah, qchub has NO duplicate protection on
    company create — this is our only safety rail.
    """
    log(f"Reading existing companies from {_ADMIN_COMPANIES_URL}…")
    if "/Admin/Companies" not in page.url:
        page.goto(_ADMIN_COMPANIES_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(800)

    # Defensive close: the Request Estimate kendo modal can survive
    # the page.goto above (it mounts above Angular's router-outlet),
    # leaving its k-overlay intercepting clicks on this page.
    # Observed run-20260521-112224: ADD COMPANY click timed out
    # against `<div class="k-overlay"> from <kendo-dialog>` for the
    # full 8s. Close it here so the click below can actually fire.
    _close_kendo_dialogs_gracefully(page, log)

    # Open the modal
    add_btn = page.get_by_role("button", name=re.compile(r"^\s*add\s*company\s*$", re.IGNORECASE)).first
    try:
        add_btn.click(timeout=8_000)
    except Exception as exc:
        log(f"⚠ Couldn't open Add Company modal to read existing list: {exc}")
        return []

    modal = _add_company_modal_locator(page)
    try:
        modal.wait_for(state="visible", timeout=8_000)
    except Exception:
        log("⚠ Add Company modal didn't render — can't read existing companies.")
        return []

    # Read ParentCompanyID options. CRITICAL: this <select> is populated
    # ASYNC by qchub via a server query after modal open — on a cold
    # profile (no warm cache) it can take 1-3s for the options to land.
    # Reading immediately after `state="attached"` gets the placeholder
    # only. Observed run-20260521-215210: cold-profile read returned
    # 0 options, missed any existing dupes, and we proceeded to create
    # a duplicate. Poll until options stabilize (or timeout).
    try:
        parent_select = modal.locator('select[name="ParentCompanyID"]').first
        parent_select.wait_for(state="attached", timeout=5_000)
        opts = _wait_for_select_options_loaded(
            parent_select, log,
            min_options=100, timeout_sec=10.0,
            label="ParentCompanyID",
        )
    except Exception as exc:
        log(f"⚠ Couldn't read ParentCompanyID options: {exc}")
        opts = []

    real_opts = [o.strip() for o in opts if o.strip()]
    log(f"  Read {len(real_opts)} existing company option(s) from ParentCompanyID dropdown.")

    # Cancel the modal — we haven't typed anything so cancel is clean.
    try:
        cancel_btn = modal.locator("button.btn.site-btn-inverse").filter(
            has_text=re.compile(r"^\s*cancel\s*$", re.IGNORECASE)
        ).first
        cancel_btn.click(timeout=3_000)
        page.wait_for_timeout(400)
    except Exception as exc:
        log(f"  (cancel click failed: {exc}; attempting Escape)")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    _dismiss_unsaved_changes_prompt(page, log)

    return real_opts


def _fill_add_company_modal(modal, page: Page, info: CompanyInfo, log: ProgressCallback) -> bool:
    """Fill every required field in the Add Company modal. Returns True
    if all fields filled cleanly + lat/long auto-populated; False on
    any failure (caller bails before save)."""
    state_full = normalize_state_to_full_name(info.state)
    if info.state and not state_full:
        log(f"⚠ State {info.state!r} couldn't be normalized to a qchub dropdown label")
        return False
    if state_full and state_full != info.state:
        log(f"  Normalized state {info.state!r} → {state_full!r}")

    # --- Required text fields (mailing block + contact) ---
    fills: list[tuple[str, str]] = [
        ('input[name="CompanyName"]',          info.name),
        ('input[name="PhoneNumber"]',          info.phone),
        ('input[name="AccountPayableEmail"]',  info.email),
        ('input[name="MailingAddress1"]',      info.address_1),
        ('input[name="MailingCity"]',          info.city),
        ('input[name="MailingZip"]',           info.zip_code),
    ]
    for selector, value in fills:
        if not value:
            log(f"⚠ Required field {selector} has empty value — qchub will reject Save")
            return False
        try:
            modal.locator(selector).first.fill(value, timeout=3_000)
        except Exception as exc:
            log(f"⚠ Couldn't fill {selector}: {exc}")
            return False

    # --- State dropdown ---
    if state_full:
        try:
            modal.locator('select[name="MailingStateCode"]').first.select_option(
                label=state_full, timeout=3_000,
            )
            log(f"  Picked Mailing State: {state_full!r}")
        except Exception as exc:
            log(f"⚠ Couldn't pick Mailing State {state_full!r}: {exc}")
            return False

    # --- Optional fields ---
    if info.address_2:
        try:
            modal.locator('input[name="MailingAddress2"]').first.fill(info.address_2, timeout=2_000)
        except Exception:
            pass
    if info.fax:
        try:
            modal.locator('input[name="FaxNumber"]').first.fill(info.fax, timeout=2_000)
        except Exception:
            pass
    if info.comments:
        try:
            modal.locator('textarea[name="companyItem.Comments"]').first.fill(info.comments, timeout=2_000)
        except Exception:
            pass

    # --- Parent company (branches only) ---
    if info.parent_company_match:
        parent_select = modal.locator('select[name="ParentCompanyID"]').first
        try:
            opts = parent_select.locator("option").all_text_contents()
            real_opts = [o.strip() for o in opts if o.strip()]
            matches = find_company_near_matches(info.parent_company_match, real_opts, max_matches=1)
            if matches:
                parent_select.select_option(label=matches[0].option_text, timeout=3_000)
                log(f"  Picked Parent Company: {matches[0].option_text!r}")
            else:
                log(f"⚠ Parent {info.parent_company_match!r} not found in dropdown — creating as top-level")
        except Exception as exc:
            log(f"⚠ Parent company selection failed: {exc}")

    # --- Wait for lat/long auto-populate ---
    # The auto-populate checkbox is checked by default (confirmed by DOM
    # dump). qchub fires Google's GeocodeService.Search against the
    # filled address fields and writes the response into the Latitude /
    # Longitude inputs via JS.
    #
    # The PREVIOUS wait used `input[name="Latitude"]:not([value=""])` +
    # wait_for(state="attached"). That has a subtle bug: the CSS
    # `[value=""]` attribute selector matches the HTML ATTRIBUTE, not
    # the DOM property. Most JS-populated inputs never have a `value`
    # ATTRIBUTE at all — they only get the property set. So
    # `:not([value=""])` matches immediately (attribute is missing,
    # not empty) and the wait returns in zero seconds. Then
    # `input_value()` reads the property which is still '' because
    # the geocoder hasn't responded yet, and we bail with a misleading
    # "didn't auto-populate within 15s" log line. Observed
    # run-20260521-204447: all three attempts logged a 15s wait that
    # actually waited zero seconds.
    #
    # Fix: poll the actual `.value` DOM property via wait_for_function.
    log("  Waiting for lat/long auto-populate (up to 15s)…")
    try:
        page.wait_for_function(
            """() => {
                const lat = document.querySelector('input[name="Latitude"]');
                const lng = document.querySelector('input[name="Longitude"]');
                return lat && lng &&
                       (lat.value || '').trim() !== '' &&
                       (lng.value || '').trim() !== '';
            }""",
            timeout=15_000,
        )
        lat_val = modal.locator('input[name="Latitude"]').first.input_value()
        lng_val = modal.locator('input[name="Longitude"]').first.input_value()
        log(f"  Lat/Long populated: ({lat_val}, {lng_val})")
    except Exception as exc:
        log(f"⚠ Lat/Long didn't auto-populate within 15s ({exc}) — address may be unrecognized")
        return False

    # --- sameAsMailing: auto-fill billing block ---
    try:
        same_cb = modal.locator('input#sameAsMailing').first
        if not same_cb.is_checked(timeout=2_000):
            same_cb.check(timeout=3_000)
            page.wait_for_timeout(600)  # let billing fields populate
            log("  Checked Same-as-Mailing for billing block.")
    except Exception as exc:
        log(f"⚠ Couldn't toggle Same-as-Mailing checkbox: {exc}")
        return False

    # The sameAsMailing checkbox copies address fields but NOT email.
    # BillingEmailAddress is its own required field; fill with same email.
    try:
        modal.locator('input[name="BillingEmailAddress"]').first.fill(info.email, timeout=3_000)
        log("  Filled Billing Email with same email as primary contact.")
    except Exception as exc:
        log(f"⚠ Couldn't fill Billing Email: {exc}")
        return False

    return True


def create_company_via_admin(
    page: Page,
    info: CompanyInfo,
    log: ProgressCallback,
    *,
    run_dir: Optional[Path] = None,
    max_attempts: int = 3,
) -> Optional[str]:
    """Drive the Add Company modal end-to-end with retry-on-verification-fail.
    Returns the new company's dropdown option text on success, None on
    failure after all attempts.

    Per user direction 2026-05-18 PM:
      - Modal not closing within 15s of Save → DON'T treat as failure;
        force-close it and verify against the dropdown (qchub's popup
        is glitchy per Noah — the save usually went through).
      - If verification fails after a force-close → RETRY the whole
        procedure up to `max_attempts` times.

    The returned string is what you pass as `UserInfo.company_match_text`
    when subsequently creating the client user — it's the format qchub
    uses for that user-form CompanyID dropdown.
    """
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            log(f"--- Retry {attempt}/{max_attempts}: {info.name!r} not yet in dropdown ---")
            page.wait_for_timeout(2_000)
        chosen = _try_create_company_once(page, info, log, run_dir=run_dir, attempt=attempt)
        if chosen is not None:
            return chosen
    log(f"⚠ Couldn't auto-create company {info.name!r} after {max_attempts} attempt(s).")
    return None


def _try_create_company_once(
    page: Page,
    info: CompanyInfo,
    log: ProgressCallback,
    *,
    run_dir: Optional[Path],
    attempt: int,
) -> Optional[str]:
    """Single attempt of the Add Company flow. Returns the matched
    dropdown option text on success, None to trigger retry.

    Step layout:
      1. Navigate to /Admin/Companies (idempotent).
      2. Click ADD COMPANY → wait for modal.
      3. Fill required fields + wait for lat/long auto-populate +
         tick sameAsMailing + fill BillingEmailAddress.
      4. Click Save once.
      5. Wait up to 15s for modal to close. If it doesn't, force-close
         (per Noah's glitchy-popup warning) and continue to verify.
      6. Re-read the dropdown via `read_existing_companies_via_admin`
         and check for an exact-subset match against our name. Return
         the option text on hit, None on miss (caller retries).
    """
    log(f"=== Auto-create company attempt {attempt}: {info.name!r} ===")
    snap_base = 90 + (attempt - 1) * 10  # space attempts apart in snapshot indices

    # 1. Navigate
    if "/Admin/Companies" not in page.url:
        log(f"Navigating to {_ADMIN_COMPANIES_URL}…")
        page.goto(_ADMIN_COMPANIES_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
    _dismiss_unsaved_changes_prompt(page, log)
    # Defensive close: the kendo Request Estimate modal can survive a
    # /Admin/Companies navigation and keeps its k-overlay intercepting
    # ALL clicks on this page. Clear it before clicking ADD COMPANY.
    _close_kendo_dialogs_gracefully(page, log)
    # 2. Open modal
    add_btn = page.get_by_role("button", name=re.compile(r"^\s*add\s*company\s*$", re.IGNORECASE)).first
    try:
        add_btn.click(timeout=8_000)
    except Exception as exc:
        log(f"⚠ Couldn't open Add Company modal: {exc}")
        return None

    modal = _add_company_modal_locator(page)
    try:
        modal.wait_for(state="visible", timeout=10_000)
    except Exception:
        log("⚠ Add Company modal didn't render within 10s.")
        return None
    log("Add Company modal opened.")
    if run_dir is not None:
        _save_step_snapshot(page, run_dir, snap_base, f"auto-create-company-a{attempt}-01-modal-open")

    # 3. Fill required fields + wait for lat/long + billing block
    if not _fill_add_company_modal(modal, page, info, log):
        # Bail without saving — Cancel cleanly so qchub doesn't store partial state
        log("  Fill failed — cancelling modal (will retry from clean state).")
        _force_close_company_modal(modal, page, log)
        return None
    if run_dir is not None:
        _save_step_snapshot(page, run_dir, snap_base + 1, f"auto-create-company-a{attempt}-02-filled")

    # 4. Save — click ONCE. Noah's warning: glitchy popup can save N
    # times if we re-click on uncertain state. Click then verify.
    save_btn = modal.locator("button.btn.site-btn").filter(
        has_text=re.compile(r"^\s*save\s*$", re.IGNORECASE)
    ).first
    try:
        save_btn.click(timeout=5_000)
        log("Clicked Save (single click).")
    except Exception as exc:
        log(f"⚠ Save click failed: {exc}")
        _log_ng_invalid_inputs(modal, log, scope_label="Add Company modal")
        # Explicitly Cancel the dialog before bailing. Otherwise the
        # broken modal sits open with its k-overlay intercepting every
        # subsequent click — the next retry's defensive close correctly
        # SKIPS admin form dialogs (so we don't kill the one we just
        # opened on purpose), which means a failed attempt's leftover
        # dialog would block the retry entirely. Observed
        # run-20260521-205349: attempts 2 and 3 timed out on ADD COMPANY
        # because attempt 1's stuck dialog was still up.
        _force_close_company_modal(modal, page, log)
        page.wait_for_timeout(500)
        return None

    # 5. Wait for modal to close. On timeout, force-close per user direction
    # 2026-05-18 PM ("assume it has been added; verify via dropdown").
    modal_closed_cleanly = False
    try:
        modal.wait_for(state="hidden", timeout=15_000)
        modal_closed_cleanly = True
        log("Modal closed cleanly after Save.")
    except Exception:
        log(
            "Modal didn't close within 15s of Save — force-closing (assuming "
            "the save went through per Noah's glitchy-popup warning). "
            "Will verify via dropdown."
        )
        _force_close_company_modal(modal, page, log)
        page.wait_for_timeout(1_000)
    if run_dir is not None:
        tag = "clean" if modal_closed_cleanly else "force-closed"
        _save_step_snapshot(page, run_dir, snap_base + 2, f"auto-create-company-a{attempt}-03-{tag}")

    # 6. SKIP the post-Save verification.
    #
    # Per Noah's "glitchy popup" guidance + user direction 2026-05-21:
    # if Save clicked AND the modal closed (cleanly or after our
    # force-close), trust that the company was created. The natural
    # verifier is the NEXT step — `create_client_user_via_admin`
    # opens the Add Client User form which has a CompanyID dropdown
    # that the user-create flow fuzzy-matches against. If our
    # freshly-created company is there, the match succeeds; if it
    # isn't, the user-create flow surfaces the gap with a clear error.
    # That's one verification path, not two.
    #
    # Why we removed the verify-here step: every variant we tried was
    # either slow or wrong.
    #   - Reading the ParentCompanyID dropdown inside a re-opened
    #     Add Company modal: client-side cached, doesn't reflect
    #     newly-saved companies. Observed run-20260521-210303: same
    #     2504 options returned 3 times in a row, retry loop created
    #     PSI Engineering FIVE TIMES in qchub.
    #   - Searching the /Admin/Companies page's search box: per user
    #     experience the search is slow and unreliable; even known
    #     companies don't surface in reasonable time.
    # The User form's CompanyID dropdown loads fresh per modal open
    # and is the one selector we have to use anyway — let it be the
    # source of truth.
    log(
        f"Save succeeded for {info.name!r}. Skipping company-page verify "
        f"(unreliable); the user-create step's CompanyID dropdown will "
        f"confirm the company exists."
    )
    if run_dir is not None:
        _save_step_snapshot(page, run_dir, snap_base + 3, f"auto-create-company-a{attempt}-04-save-trusted")
    return info.name


def _log_modal_state_on_timeout(modal, log: ProgressCallback, *, scope_label: str) -> dict:
    """When a modal-close wait times out, query and log the modal's
    current state (loading spinner? error toast? ng-invalid fields?)
    so the run log says WHY it didn't close.

    Returns the state dict so the caller can branch on it (e.g.,
    "still processing → wait longer" vs "actual error → bail").

    Established 2026-05-22 alongside `_log_ng_invalid_inputs` after
    observing the user-create modal stuck on a loading spinner past
    15s — old code logged "didn't close, treating as failure" without
    distinguishing "qchub still saving" from "qchub rejected." See
    `feedback_proactive_dom_diagnostics.md`.
    """
    try:
        state = modal.evaluate(
            """(el) => {
                const loadingImg = el.querySelector(
                    '.loading-img, .loading img, img[src*="loading"]'
                );
                const errorToast = el.querySelector(
                    '.alert-danger, .alert-error, .error-msg, [role="alert"]'
                );
                const visibleAlertText = (errorToast &&
                    (errorToast.offsetParent !== null ||
                     errorToast.offsetWidth > 0))
                    ? (errorToast.innerText || '').trim().slice(0, 200)
                    : '';
                const invalidNames = Array.from(
                    el.querySelectorAll('.ng-invalid')
                ).filter(f => ['INPUT', 'SELECT', 'TEXTAREA'].includes(f.tagName))
                 .map(f => f.getAttribute('name') || '(no name)');
                return {
                    stillRendered:
                        el.offsetParent !== null || el.offsetWidth > 0,
                    loadingSpinnerVisible:
                        loadingImg ? (loadingImg.offsetParent !== null) : false,
                    visibleAlertText: visibleAlertText,
                    ngInvalidNames: invalidNames,
                };
            }"""
        )
    except Exception as exc:
        log(f"  (couldn't read {scope_label} state: {exc})")
        return {}
    log(
        f"  {scope_label} state @ timeout: still-rendered={state.get('stillRendered')}, "
        f"loading-spinner={state.get('loadingSpinnerVisible')}, "
        f"ng-invalid={state.get('ngInvalidNames') or '[]'}, "
        f"visible-alert={state.get('visibleAlertText') or '(none)'!r}"
    )
    return state


def _log_ng_invalid_inputs(modal, log: ProgressCallback, *, scope_label: str) -> None:
    """When Angular's form validator keeps a Save button disabled,
    every required input it considers invalid carries `ng-invalid` on
    its class list. Enumerate them and log each one's name + placeholder
    so the actual cause of a Save-fail is in the run log instead of
    requiring page.html spelunking.

    Established 2026-05-22 after the user pointed out that our generic
    "check page.html for ng-invalid inputs" message had let two
    Save-disabled bugs slip past two consecutive ship-test cycles
    (phone-format on Add Company, then RelationshipStatusCode on Add
    Client User). User direction: catch hang-ups proactively — read
    DOM state on failure, log what's wrong, don't hand-wave. See
    `feedback_proactive_dom_diagnostics.md`.

    Safe to call from any failure branch; silent on a healthy form
    (logs "no ng-invalid fields found").
    """
    try:
        invalid_fields = modal.evaluate(
            """(el) => {
                const fields = el.querySelectorAll('.ng-invalid');
                const out = [];
                for (const f of fields) {
                    // Only report actual form controls, not the form/fieldset
                    // wrappers (they inherit ng-invalid when any child is).
                    if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(f.tagName)) continue;
                    out.push({
                        tag: f.tagName.toLowerCase(),
                        name: f.getAttribute('name') || '(no name)',
                        placeholder: f.getAttribute('placeholder') || '',
                        type: f.getAttribute('type') || '',
                    });
                }
                return out;
            }"""
        )
    except Exception as exc:
        log(f"  (couldn't enumerate ng-invalid fields in {scope_label}: {exc})")
        return
    if not invalid_fields:
        log(
            f"  No ng-invalid fields found in {scope_label} — Save may be "
            f"disabled for another reason (custom directive, async validator, "
            f"or overlay intercepting the click)."
        )
        return
    log(f"  ⚠ {scope_label} has {len(invalid_fields)} ng-invalid field(s):")
    for f in invalid_fields:
        desc = f"{f['tag']}[name=\"{f['name']}\"]"
        if f.get("placeholder"):
            desc += f" placeholder={f['placeholder']!r}"
        if f.get("type") and f["tag"] == "input":
            desc += f" type={f['type']!r}"
        log(f"    - {desc}")


def _force_close_company_modal(modal, page: Page, log: ProgressCallback) -> None:
    """Best-effort modal close + interstitial dismissal. Used when a
    fill failed (cancel before save) AND when Save didn't auto-close
    the modal within timeout (force-close and verify externally).
    """
    try:
        cancel_btn = modal.locator("button.btn.site-btn-inverse").filter(
            has_text=re.compile(r"^\s*cancel\s*$", re.IGNORECASE)
        ).first
        cancel_btn.click(timeout=3_000)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    _dismiss_unsaved_changes_prompt(page, log)
    page.wait_for_timeout(400)


def create_client_user_via_admin(
    page: Page,
    info: UserInfo,
    log: ProgressCallback,
    *,
    run_dir: Optional[Path] = None,
) -> bool:
    """Drive the Add Client User modal end-to-end. Returns True on
    verified success.

    Per Noah 2026-05-18: qchub DOES enforce email uniqueness for users.
    If a user already exists with this email, this function logs that
    fact and returns True (idempotent — the user already exists, the
    desired end-state is met).
    """
    log(f"=== Auto-creating client user: {info.email!r} ({info.first_name} {info.last_name}) ===")
    if "/Admin/Users" not in page.url:
        log(f"Navigating to {_ADMIN_USERS_URL}…")
        page.goto(_ADMIN_USERS_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(800)

    _dismiss_unsaved_changes_prompt(page, log)
    # Same defensive close as in the company flow — a leftover kendo
    # overlay would block the Add Client User button click.
    _close_kendo_dialogs_gracefully(page, log)

    # 1. Ensure Client Users tab is active. The dump shows the active tab
    # has class `active`; the inactive one has class `tab-lnk`.
    try:
        active_tab = page.locator(".tab-lnk.active, a.active").filter(
            has_text=re.compile(r"Client\s+Users", re.IGNORECASE)
        ).first
        if not active_tab.is_visible(timeout=2_000):
            inactive_tab = page.locator(".tab-lnk").filter(
                has_text=re.compile(r"Client\s+Users", re.IGNORECASE)
            ).first
            inactive_tab.click(timeout=3_000)
            page.wait_for_timeout(500)
            log("  Activated Client Users tab.")
    except Exception:
        log("  (couldn't verify Client Users tab active — proceeding anyway)")

    # 2. Open the Add Client User modal
    add_btn = page.get_by_role(
        "button", name=re.compile(r"add\s*client\s*user", re.IGNORECASE)
    ).first
    try:
        add_btn.click(timeout=8_000)
    except Exception as exc:
        log(f"⚠ Couldn't open Add Client User modal: {exc}")
        return False

    modal = _add_user_modal_locator(page)
    try:
        modal.wait_for(state="visible", timeout=10_000)
    except Exception:
        log("⚠ Add Client User modal didn't render within 10s.")
        return False
    log("Add Client User modal opened.")
    if run_dir is not None:
        _save_step_snapshot(page, run_dir, 93, "auto-create-user-01-modal-open")

    # 3. Fill required fields
    fills: list[tuple[str, str]] = [
        ('input[name="Email"]',     info.email),
        ('input[name="FirstName"]', info.first_name),
        ('input[name="LastName"]',  info.last_name),
        ('input[name="Phone"]',     info.phone),
    ]
    for selector, value in fills:
        if not value:
            log(f"⚠ Required field {selector} empty — qchub will reject")
            return False
        try:
            modal.locator(selector).first.fill(value, timeout=3_000)
        except Exception as exc:
            log(f"⚠ Couldn't fill {selector}: {exc}")
            return False

    # Username auto-fills from email per Noah; don't touch it.

    # 4. Pick company from the CompanyID dropdown. CRITICAL: this select
    # is populated ASYNC by qchub via a server query after the modal
    # opens — on a cold profile it can take 1-3s for the full list
    # (~3k options) to land. Reading too early returns just the
    # placeholder and we'd think the company isn't there. Observed
    # run-20260521-215210: read fired 1s after modal open on cold
    # cache, saw 0 real options, bailed even though PSI Engineering
    # had been freshly created. Poll until the list is loaded.
    try:
        company_select = modal.locator('select[name="CompanyID"]').first
        company_select.wait_for(state="attached", timeout=5_000)
        opts = _wait_for_select_options_loaded(
            company_select, log,
            min_options=100, timeout_sec=15.0,
            label="User-form CompanyID",
        )
        real_opts = [o.strip() for o in opts if o.strip()]
        matches = find_company_near_matches(info.company_match_text, real_opts, max_matches=1)
        if not matches:
            log(
                f"⚠ Couldn't find company {info.company_match_text!r} in "
                f"CompanyID dropdown (read {len(real_opts)} options)"
            )
            return False
        company_select.select_option(label=matches[0].option_text, timeout=3_000)
        log(f"  Picked Company: {matches[0].option_text!r} (score {matches[0].score:.2f})")
    except Exception as exc:
        log(f"⚠ Couldn't pick company: {exc}")
        return False

    # 5. Optional fax
    if info.fax:
        try:
            modal.locator('input[name="Fax"]').first.fill(info.fax, timeout=2_000)
        except Exception:
            pass

    # 5b. RelationshipStatusCode = Active (REQUIRED by Angular validator
    # even though Active appears first in the dropdown). Earlier comment
    # claimed we could skip this since Active was the default — wrong:
    # the form treats the unselected placeholder as ng-invalid and
    # keeps Save disabled. Observed run-20260521-212300: every other
    # field filled correctly, company fuzzy-matched 1.00, but Save was
    # still `<button disabled type="submit">`.
    try:
        modal.locator('select[name="RelationshipStatusCode"]').first.select_option(
            label="Active", timeout=3_000,
        )
        log("  Picked Status: 'Active'")
    except Exception as exc:
        # Try selecting by value as a fallback in case the option label
        # differs in this qchub version.
        try:
            modal.locator('select[name="RelationshipStatusCode"]').first.select_option(
                index=1, timeout=3_000,  # 0 is usually the placeholder
            )
            log("  Picked Status by index=1 (label-pick failed: %s)" % exc)
        except Exception as exc2:
            log(f"⚠ Couldn't set RelationshipStatusCode: {exc2}")
            return False

    if run_dir is not None:
        _save_step_snapshot(page, run_dir, 94, "auto-create-user-02-filled")

    # 6. Click Save — last <button> in modal per the DOM dump
    try:
        save_btn = modal.get_by_role(
            "button", name=re.compile(r"^\s*save\s*$", re.IGNORECASE)
        ).last
        save_btn.click(timeout=5_000)
        log("Clicked Save.")
    except Exception as exc:
        log(f"⚠ Save click failed: {exc}")
        _log_ng_invalid_inputs(modal, log, scope_label="Add Client User modal")
        # Explicitly Cancel before bailing so the broken modal doesn't
        # block subsequent clicks (same reasoning as in the Company
        # Save-failure path).
        try:
            modal.locator("button.btn.site-btn-inverse").filter(
                has_text=re.compile(r"^\s*cancel\s*$", re.IGNORECASE)
            ).first.click(timeout=2_000)
            page.wait_for_timeout(400)
        except Exception:
            pass
        return False

    # 7. Wait for modal to close OR for the "email already exists" toast.
    # The duplicate path is acceptable (user already exists → desired
    # end state met). The success path is also acceptable.
    #
    # Timeout bumped 15s → 60s after run-20260522-105228: the user
    # modal sat on a loading.gif spinner for >15s while qchub did
    # server-side processing (welcome email? validation?) and our
    # premature timeout killed the flow. 60s is generous for any
    # legitimate processing but still bounds the truly-stuck case.
    try:
        modal.wait_for(state="hidden", timeout=60_000)
        log(f"✓ Client User {info.email!r} created successfully.")
        if run_dir is not None:
            _save_step_snapshot(page, run_dir, 95, "auto-create-user-03-success")
        return True
    except Exception:
        # Check for "email already exists" — that's a success-equivalent
        dup_warning = page.locator("*").filter(
            has_text=re.compile(r"email\s+already\s+(in\s+use|exists)", re.IGNORECASE)
        ).first
        try:
            if dup_warning.is_visible(timeout=1_500):
                log(f"✓ Client User {info.email!r} already exists in qchub — treating as success.")
                # Dismiss the modal so we leave qchub clean.
                try:
                    modal.locator("button.btn.site-btn-inverse").filter(
                        has_text=re.compile(r"^\s*cancel\s*$", re.IGNORECASE)
                    ).first.click(timeout=2_000)
                except Exception:
                    page.keyboard.press("Escape")
                return True
        except Exception:
            pass
        log("⚠ Add Client User modal didn't close within 60s.")
        # Proactive DOM diagnostic: tell us WHY (loading spinner still up?
        # error toast appeared? async ng-invalid kicked in?).
        state = _log_modal_state_on_timeout(
            modal, log, scope_label="Add Client User modal",
        )
        if run_dir is not None:
            _save_step_snapshot(page, run_dir, 95, "auto-create-user-03-stuck-after-save")
        # If qchub is genuinely still processing (spinner up, no error
        # toast, no ng-invalid), trust the Save: force-close and let
        # the order flow's User dropdown be the verifier (mirrors the
        # company "trust Save, verify by proxy" pattern).
        spinner_up = bool(state.get("loadingSpinnerVisible"))
        has_alert = bool(state.get("visibleAlertText"))
        invalid = state.get("ngInvalidNames") or []
        if spinner_up and not has_alert and not invalid:
            log(
                "  Spinner still up with no error toast and no ng-invalid "
                "fields — assuming qchub is still processing the save. "
                "Force-closing and trusting the save; the order flow's "
                "User dropdown will confirm."
            )
            try:
                modal.locator("button.btn.site-btn-inverse").filter(
                    has_text=re.compile(r"^\s*cancel\s*$", re.IGNORECASE)
                ).first.click(timeout=2_000)
            except Exception:
                page.keyboard.press("Escape")
            return True
        log("Treating as failure (alert or invalid field present, or no processing signals).")
        return False


# ==========================================================================
# AUTO-CREATE — orchestrator: drives Company + Client User from a StudyRequest
# ==========================================================================
# Public entry point: `auto_create_for_missing_entity(page, request, log, run_dir)`.
# Called silently by `_run` when QchubCompanyNotFound / QchubUserNotFound fires
# during order creation. Per user direction 2026-05-18 PM: new clients are
# standard procedure; no confirm dialog, no chat prompt — just create them.


def _llm_lookup_company_address(
    name: str, contact_email: str, log: ProgressCallback,
) -> tuple[str, str, str, str]:
    """Fallback for address resolution when the email signature regex
    came up short: ask Claude (Sonnet) with the **web_search** server
    tool to look up the company's office address from their OWN
    website, then verify the result actually came from a URL on the
    company's own domain before accepting.

    Why Sonnet + web_search (not training-data recall): first attempt
    at this (2026-05-18 PM) used Haiku's training-data recall — that
    produced confidently-wrong addresses for 4 of 4 real firms in
    testing (Kimley-Horn wrong city, Dewberry wrong city, PSI
    Engineering wrong company entirely). Recall is unsafe. Per user
    direction 2026-05-21, we DON'T use LLM signature extraction at
    all (slowness / faulty results), but web_search for address-only
    fallback IS in scope.

    Sonnet visits the company's website (URL = `https://{domain}`)
    and reads their contact / about page for the address. The
    orchestrator then verifies that the address returned was sourced
    from a URL on the company's own domain — if it wasn't (e.g., the
    answer came from a directory listing site), we reject it.

    Returns ("", "", "", "") on any failure / low-confidence outcome,
    so the caller falls through to the manual modal path safely.
    """
    try:
        from anthropic import Anthropic
        from .config import get_api_key
    except Exception as exc:
        log(f"  ⚠ Can't import Anthropic for web-search address lookup: {exc}")
        return ("", "", "", "")

    domain = contact_email.split("@", 1)[-1].strip().lower() if "@" in contact_email else ""
    if not domain:
        log("  No email domain — can't run web-search lookup.")
        return ("", "", "", "")

    log(f"  Asking Claude (Sonnet + web search) for {name!r} address (domain {domain!r})…")

    system_prompt = (
        f"You are looking up the US office mailing address for the "
        f"company '{name}' whose own website is at the domain "
        f"'{domain}'. Use the web_search tool.\n\n"
        f"HARD RULES:\n"
        f"1. The address you return MUST be sourced from a page on "
        f"the company's OWN domain ('{domain}'). Do NOT use addresses "
        f"from third-party directories (zoominfo, manta, dnb, "
        f"linkedin company pages, Wikipedia, etc.) — these are often "
        f"wrong or stale.\n"
        f"2. If your only sources are third-party, return empty "
        f"strings for ALL fields and source_url. We'd rather bail "
        f"than store a wrong address.\n"
        f"3. If the company has multiple offices on their site, "
        f"prefer their HQ / main office.\n"
        f"4. State code MUST be the 2-letter US postal code "
        f"(e.g. 'VA', 'FL').\n"
        f"5. ZIP must be 5-digit or ZIP+4 (e.g. '20191' or '20191-1234').\n"
        f"6. Set source_url to the EXACT URL from the company's domain "
        f"where you read the address. This is non-negotiable — without "
        f"a verifiable source URL, return all empties.\n\n"
        f"Output STRICT JSON ONLY (no prose, no markdown):\n"
        f'{{"address_1": "", "city": "", "state": "", "zip": "", "source_url": ""}}'
    )
    user_msg = (
        f"Find the US office mailing address for {name}. "
        f"Their website is at https://{domain} — start by visiting "
        f"their contact / about / locations page."
    )

    # Retry on transient errors (connection blips, rate limits, 5xx).
    # Observed run-20260525-092150: a single APIConnectionError killed
    # the whole auto-create chain. A 2-try loop with brief backoff
    # covers >95% of transient cases without delaying the bad-key
    # path more than ~3s.
    import anthropic as _anthropic
    try:
        client = Anthropic(api_key=get_api_key())
        response = None
        last_exc: Optional[BaseException] = None
        for attempt in range(1, 3):  # 2 tries total
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2000,
                    temperature=0,
                    timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
                    system=system_prompt,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                    messages=[{"role": "user", "content": user_msg}],
                )
                break  # success
            except (
                _anthropic.APIConnectionError,
                _anthropic.RateLimitError,
                _anthropic.APITimeoutError,
            ) as exc:
                last_exc = exc
                log(
                    f"  Web-search call attempt {attempt}/2 failed "
                    f"({type(exc).__name__}); retrying in 2s…"
                )
                time.sleep(2.0)
            except _anthropic.APIStatusError as exc:
                sc = getattr(exc, "status_code", None)
                if sc == 529 or (sc is not None and 500 <= sc < 600):
                    last_exc = exc
                    log(
                        f"  Web-search call attempt {attempt}/2 hit "
                        f"qchub-side {sc}; retrying in 2s…"
                    )
                    time.sleep(2.0)
                else:
                    raise  # 4xx — caller's problem, don't retry
        if response is None:
            # Both attempts failed on transient errors.
            log(
                f"  ⚠ Web-search address lookup failed after 2 tries: "
                f"{type(last_exc).__name__ if last_exc else 'unknown'}: {last_exc}"
            )
            return ("", "", "", "")

        # web_search is a server tool — Claude orchestrates the
        # search internally and returns the final answer as text.
        text_parts = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                t = (getattr(block, "text", "") or "").strip()
                if t:
                    text_parts.append(t)
        text = "\n".join(text_parts).strip()
        if not text:
            log("  Sonnet+web returned no text content; falling through.")
            return ("", "", "", "")

        # Strip code fences if present.
        if text.startswith("```"):
            lines = text.split("\n")
            if lines and lines[-1].startswith("```"):
                text = "\n".join(lines[1:-1])
            else:
                text = "\n".join(lines[1:])
            text = text.strip()
        # Grab the first {...} substring if there's prose around it.
        if not text.startswith("{"):
            m = re.search(r"\{[\s\S]*?\}", text)
            if m:
                text = m.group(0)

        try:
            data = json.loads(text)
        except Exception as exc:
            log(f"  ⚠ Couldn't parse Sonnet JSON response: {exc} (raw: {text[:200]!r})")
            return ("", "", "", "")

        addr_1 = (data.get("address_1") or "").strip()
        city = (data.get("city") or "").strip()
        state = (data.get("state") or "").strip()
        zip_code = (data.get("zip") or "").strip()
        source_url = (data.get("source_url") or "").strip().lower()

        if not all((addr_1, city, state, zip_code)):
            log(
                f"  Sonnet returned incomplete address (street={addr_1!r}, "
                f"city={city!r}, state={state!r}, zip={zip_code!r}, "
                f"source={source_url!r}) — treating as low-confidence; "
                "will fall through."
            )
            return ("", "", "", "")

        # DOMAIN CROSS-CHECK: source_url MUST be on the company's own
        # domain. Reject otherwise — safeguard against the
        # hallucination class we hit on first attempt.
        if not source_url:
            log(
                f"  Sonnet didn't provide a source_url for the address "
                f"({addr_1}, {city}, {state} {zip_code}) — rejecting. "
                "Without a verifiable domain source we can't trust the result."
            )
            return ("", "", "", "")
        if domain not in source_url:
            log(
                f"  Source URL {source_url!r} is NOT on the company's "
                f"domain ({domain!r}) — rejecting the address. "
                "Likely a third-party directory listing, which is unsafe."
            )
            return ("", "", "", "")

        log(
            f"  Domain-verified address: {addr_1}, {city}, {state} {zip_code} "
            f"(sourced from {source_url})"
        )
        return (addr_1, city, state, zip_code)

    except Exception as exc:
        log(f"  ⚠ Web-search address lookup failed: {type(exc).__name__}: {exc}")
        return ("", "", "", "")


def auto_create_for_missing_entity(
    page: Page,
    request: StudyRequest,
    log: ProgressCallback,
    run_dir: Path,
) -> bool:
    """Ensure both the client Company and Client User exist in qchub by
    creating either or both as needed. Returns True if everything is in
    place after the call (newly-created OR already-existing); False if
    a precondition was missing (no usable address in email signature,
    missing company name, etc.) — in which case the caller falls back
    to the legacy "manual create" modal.

    Idempotent: if the company already exists with a matching name, no
    new one is created. Same for the user (qchub enforces email
    uniqueness on Client Users).

    Silent: no chat prompt, no confirm dialog. Per user direction —
    creating a new client is standard procedure, not something to
    interrupt for. Near-matches are LOGGED loudly so a near-dupe is
    visible in chat status after the fact, but the orchestrator
    proceeds to create the new entry.

    Address resolution is a two-tier cascade (per user direction
    2026-05-21):

      TIER 1 — regex over email_body. Fast, deterministic, zero cost.
        Catches the common case of a clean 2-line signature with a
        "City, ST ZIP" block.

      TIER 2 — Sonnet + web_search (domain-cross-checked). When the
        signature doesn't have a parseable address (canonical case:
        Justin Williams / PSI Engineering — signature is phone +
        website only), have Claude visit the company's own website
        and read the contact / about page for the office address.
        The returned `source_url` must be on the company's own
        domain — third-party directory results are rejected to
        defend against hallucinations.

    Phone is regex-only (no LLM tier per user direction). Falls back
    to `_PHONE_SENTINEL` ('000-000-0000') if the signature didn't have
    one. The same phone flows to both the Company form and the User
    form (qchub requires PhoneNumber on both, and a sentinel beats
    failing the create entirely).

    NOTE: skipping LLM-based signature extraction was a deliberate
    2026-05-21 reversal. An earlier attempt added a Haiku tier between
    regex and web-search to handle weird signature layouts (HTML
    embedded, pipe-separated, etc.); it was slow + faulty in practice
    and was rolled back. Web-search for address-only is back in.
    """
    log("=== AUTO-CREATE flow started ===")
    body = request.email_body or ""

    # Close any leftover kendo modal (Request Estimate is still up
    # when we enter this function — qchub raised QchubUserNotFound /
    # QchubCompanyNotFound from inside _fill_request_estimate without
    # closing it). Graceful Kendo close lets Angular run its teardown
    # hooks so the subsequent /Admin/Companies navigation lands on a
    # clean page. DOM-removing the dialog (previous approach) broke
    # Angular state and prevented the Add Company Bootstrap modal from
    # ever rendering — see comment block in `_close_kendo_dialogs_gracefully`.
    _close_kendo_dialogs_gracefully(page, log)

    # ---- Company name + email ----
    name = (request.client_company or "").strip()
    email = (request.client_contact_email or "").strip()
    if not name or not email:
        log(
            f"⚠ Missing company name or contact email on request "
            f"(name={name!r}, email={email!r}) — can't auto-create."
        )
        return False

    # ---- Phone (regex over email_body only — no LLM tier) ----
    phone = extract_first_phone(body) or _PHONE_SENTINEL
    if phone == _PHONE_SENTINEL:
        log(f"  No phone in signature — using sentinel {phone!r}")
    else:
        log(f"  Phone (from signature): {phone}")

    # ---- Address — Tier 1: regex over email_body ----
    address_1, city, state, zip_code = extract_address_from_signature(body)

    # ---- Address — Tier 2: web_search if Tier 1 came up short ----
    if not all((address_1, city, state, zip_code)):
        log(
            f"  Signature regex didn't yield a complete address "
            f"(street={address_1!r}, city={city!r}, state={state!r}, "
            f"zip={zip_code!r}) — trying web search for {name!r}."
        )
        w_street, w_city, w_state, w_zip = _llm_lookup_company_address(name, email, log)
        # OR-fill: keep any partial fields the regex got, fill gaps from web.
        address_1 = address_1 or w_street
        city = city or w_city
        state = state or w_state
        zip_code = zip_code or w_zip

    if not all((address_1, city, state, zip_code)):
        log(
            f"⚠ Couldn't resolve a complete address from signature regex OR "
            f"web search (street={address_1!r}, city={city!r}, "
            f"state={state!r}, zip={zip_code!r}). Auto-create aborting; "
            f"the legacy 'missing entity' modal will fall through for "
            f"manual entry."
        )
        return False
    log(f"  Address resolved: {address_1}, {city}, {state} {zip_code}")

    # --- COMPANY ---
    log(f"--- Company step: {name!r} ---")
    existing = read_existing_companies_via_admin(page, log)
    near = find_company_near_matches(name, existing, max_matches=5)
    exact = [m for m in near if m.score == 1.0]
    if exact:
        company_match = exact[0].option_text
        log(f"  Company already exists as {company_match!r} — skipping create.")
    else:
        # Silent-create. Log near-matches loudly so dupes are visible.
        if near:
            log(
                f"⚠ Near-matches found but proceeding with new entry "
                f"(per silent-create policy). Closest existing: "
                + ", ".join(f"{m.option_text!r} (score {m.score:.2f})" for m in near)
            )
        company_info = CompanyInfo(
            name=name, email=email, phone=_format_phone_for_qchub(phone),
            address_1=address_1, address_2="",
            city=city, state=state, zip_code=zip_code,
        )
        company_match = create_company_via_admin(page, company_info, log, run_dir=run_dir)
        if not company_match:
            log(f"⚠ Auto-create FAILED for company {name!r} — falling back to manual modal.")
            return False

    # --- USER ---
    log(f"--- User step: {email!r} ({request.client_contact_name}) ---")
    first, last = split_full_name(request.client_contact_name)
    if not first and not last:
        # Synthesize from the email local-part as a last resort.
        local = email.split("@", 1)[0]
        if "." in local:
            first, last = local.split(".", 1)
            first = first.capitalize()
            last = last.capitalize()
        else:
            first = local.capitalize()
            last = "Client"
        log(f"  No contact name on request — synthesized from email: {first} {last}")

    user_info = UserInfo(
        email=email,
        first_name=first or "Unknown",
        last_name=last or "Client",
        company_match_text=company_match,
        phone=_format_phone_for_qchub(phone),
    )
    if not create_client_user_via_admin(page, user_info, log, run_dir=run_dir):
        log(f"⚠ Auto-create FAILED for user {email!r} — falling back to manual modal.")
        return False

    log("=== AUTO-CREATE flow complete ===")
    return True


def _return_to_dashboard_and_open_new_order(page: Page, log: ProgressCallback) -> None:
    """After a /Admin/* side-trip for auto-create, return to the qchub
    dashboard and re-open the NEW ORDER modal so the order flow can
    re-fill the form (the User dropdown re-fetches fresh, picking up
    the new client user)."""
    log("Returning to qchub dashboard to retry NEW ORDER…")
    page.goto(QCHUB_BASE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    if not _dashboard_visible(page):
        log("  (dashboard signal didn't appear; will try NEW ORDER click anyway)")
    _open_new_order(page, log)
    page.wait_for_timeout(500)
