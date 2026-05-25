"""LLM-powered extraction from parsed emails into structured StudyRequests.

Single unified pipeline. When the email has a KMZ, that's the location list —
the LLM only enriches each placemark with study details from body/docs. When
no KMZ, the LLM enumerates locations from body/docs as before.

When the email is forwarded, the original message is the primary signal; the
forwarder's added text is treated as a secondary annotation (special
instructions to data entry, etc).
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Callable, Optional

import anthropic
import httpx
from anthropic import Anthropic

from . import documents, geocoder, kmz
from .config import get_api_key
from .models import LocationEstimate, StudyKind, StudyLocation, StudyRequest, TubeSubtype
from .parser import Attachment, ParsedEmail

log = logging.getLogger(__name__)

# Model fallback chain — mirror of `chat.MODELS["auto"]`. Sonnet first
# (best balance of accuracy + cost for structured extraction); Opus
# next (higher capacity, more expensive, useful when Sonnet stalls or
# 529s); Haiku as last-resort (always available, lower accuracy on
# complex extractions — accepting that as fallback over a hard failure).
MODEL_CHAIN = ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"]
MODEL = MODEL_CHAIN[0]  # preserved for back-compat with code that imports MODEL
MAX_TOKENS = 32000  # plenty for ~100 enriched locations
MAX_TOKENS_LARGE = 60000  # for big KMZs (>50 placemarks)

# Per-event read timeout for the streaming extraction call. Streaming
# emits SSE events as Claude generates tokens, so this is the gap
# between events rather than a wall-clock cap. 60s is plenty of headroom
# for a slow first-token on vision-heavy extractions.
PER_REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# Per-model retry budget. In a single-model run this is the full
# retry count; in fallback mode it's per-model (chain has 3 models →
# worst case 3 × 3 = 9 attempts across the chain).
PER_MODEL_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SEC = 2.0

# Compatibility export — older callers / tests may still reference RETRY_ATTEMPTS.
RETRY_ATTEMPTS = PER_MODEL_ATTEMPTS


def _short_model_name(model_id: str) -> str:
    """Human-readable label for status messages."""
    if "opus" in model_id:
        return "Opus"
    if "haiku" in model_id:
        return "Haiku"
    if "sonnet" in model_id:
        return "Sonnet"
    return model_id


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.APITimeoutError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        sc = getattr(exc, "status_code", None)
        if sc == 529 or (sc is not None and 500 <= sc < 600):
            return True
        if "overloaded" in str(exc).lower():
            return True
    return False


def _stream_with_retry(
    client: Anthropic,
    *,
    models: Optional[list[str]] = None,
    progress: Optional[Callable[[str], None]] = None,
    **kwargs,
):
    """Run the extraction request with retry + model-fallback.

    Uses `messages.stream()` so SSE events drive progress updates (the
    user sees "Claude responding…" tick as tokens stream). Returns the
    final Anthropic Message via `stream.get_final_message()`.

    For each model in the chain (default MODEL_CHAIN):
      - Up to PER_MODEL_ATTEMPTS tries with exponential backoff on
        retryable errors (rate limit, 5xx, overloaded, timeout).
      - On retry exhaustion for the current model, fall forward to the
        next model in the chain.
      - On non-retryable error: re-raise immediately.

    Returns the final Anthropic Message. Raises the last exception if
    every model exhausts its budget.
    """
    chain = models or MODEL_CHAIN
    last_exc: Optional[BaseException] = None

    for model_idx, model in enumerate(chain):
        is_last_model = model_idx == len(chain) - 1
        for attempt in range(1, PER_MODEL_ATTEMPTS + 1):
            if progress is not None:
                if attempt == 1:
                    progress(f"Sending to Claude ({_short_model_name(model)})…")
                else:
                    progress(
                        f"Retrying with Claude ({_short_model_name(model)}) — "
                        f"attempt {attempt}/{PER_MODEL_ATTEMPTS}…"
                    )

            try:
                import time
                _t_start = time.monotonic()
                with client.messages.stream(
                    model=model,
                    timeout=PER_REQUEST_TIMEOUT,
                    **kwargs,
                ) as stream:
                    # Drain the text stream so SSE events keep the
                    # connection alive; we don't care about the deltas
                    # here (extraction is a tool call, not a text reply).
                    for _ in stream.text_stream:
                        pass
                    msg = stream.get_final_message()
                # Record usage to the global JSONL log so per-run cost can
                # be reconstructed over time. Best-effort — never fail the
                # extraction if the record write hiccups.
                try:
                    from . import usage_tracker
                    usage_tracker.record(
                        phase="extraction",
                        model=model,
                        usage_obj=getattr(msg, "usage", None),
                        duration_ms=int((time.monotonic() - _t_start) * 1000),
                        meta={"attempt": attempt, "fallback_model_idx": model_idx},
                    )
                except Exception:
                    pass
                return msg
            except BaseException as exc:
                last_exc = exc
                if not _retryable(exc):
                    raise  # non-recoverable (auth, validation, etc.)
                log.warning(
                    "Extractor call to %s failed (attempt %d/%d): %s",
                    model, attempt, PER_MODEL_ATTEMPTS, exc,
                )
                if attempt == PER_MODEL_ATTEMPTS:
                    if not is_last_model:
                        next_model = chain[model_idx + 1]
                        if progress is not None:
                            progress(
                                f"{_short_model_name(model)} exhausted "
                                f"({PER_MODEL_ATTEMPTS} attempts) — "
                                f"switching to {_short_model_name(next_model)}."
                            )
                        log.warning(
                            "Extractor falling back %s → %s",
                            model, next_model,
                        )
                    break  # exit retry loop, advance to next model
                if progress is not None:
                    progress(
                        f"{_short_model_name(model)} attempt {attempt} failed "
                        f"({type(exc).__name__})…"
                    )
                delay = RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                time.sleep(delay)

    assert last_exc is not None
    raise last_exc


# ---------- tool schemas ----------

_LOCATION_PROPS = {
    "site_name": {
        "type": "string",
        "description": "Concise label for map pin, e.g. 'US 41 & Toledo Blade Blvd'. Use & and abbreviated suffixes.",
    },
    "raw_text": {"type": "string", "description": "Exact text from the email naming this location."},
    "address_or_intersection": {
        "type": "string",
        "description": "MyMaps-search-friendly form: 'Street A and Street B, City, State'.",
    },
    "study_kind": {"type": "string", "enum": ["turning_movement", "tube"]},
    "tmc_subtype": {
        "type": "string",
        "enum": ["standard", "large", "complex"],
        "description": "Only for turning_movement. Default 'standard' — user adjusts after seeing lane count.",
    },
    "tube_subtype": {
        "type": "string",
        "enum": ["volume", "volume_class", "volume_speed", "volume_speed_class"],
        "description": (
            "Only for tube. Default 'volume'. "
            "'speed' mentioned → 'volume_speed'. "
            "'class'/'classification'/'classification count'/'13-bin'/'fhwa' → 'volume_class'. "
            "Both speed AND classification → 'volume_speed_class'. "
            "When the email says 'volume only' or just 'volume count', stay on 'volume'."
        ),
    },
    "time_windows": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "start": {"type": "string", "description": "24h HH:MM"},
                "end": {"type": "string", "description": "24h HH:MM"},
                "total_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "REQUIRED whenever the count runs longer than one "
                        "calendar day. Examples: 72-hour count → 72; "
                        "7-day, 24-hour count → 168; 1-week → 168; "
                        "2-week → 336. OMIT for single-day windows "
                        "(AM/PM peaks, single 24-hour day) — start/end "
                        "alone are enough there. The qchub Tube form "
                        "uses this to compute Duration + unit "
                        "(72 → 3 Days; 168 → 7 Days; 24 → 1 Day)."
                    ),
                },
                "raw_text": {"type": "string"},
                "flag": {
                    "type": "string",
                    "description": "Fill only if email's literal text is contradictory.",
                },
            },
            "required": ["label", "start", "end"],
        },
    },
    "study_dates": {"type": "string"},
    "estimate": {
        "type": "object",
        "description": (
            "Best-effort lat/lon. (1) KMZ match → confidence='high', source='kmz'. "
            "(2) Aerial image matched → 'high', source='vision'. "
            "(3) Well-known intersection from text → 'medium', source='text_only'. "
            "(4) Obscure → 'low'. Omit entirely if you can't place it."
        ),
        "properties": {
            "latitude": {"type": "number"},
            "longitude": {"type": "number"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "source": {"type": "string", "enum": ["kmz", "vision", "text_only"]},
            "notes": {"type": "string"},
        },
        "required": ["latitude", "longitude", "confidence", "source"],
    },
}

EXTRACTION_TOOL = {
    "name": "record_study_request",
    "description": "Record the parsed contents of a traffic study request email. Call exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "client_company": {"type": "string"},
            "client_contact_name": {"type": "string"},
            "client_contact_email": {"type": "string"},
            "client_project_number": {"type": "string"},
            "jurisdiction": {"type": "string"},
            "locations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": _LOCATION_PROPS,
                    "required": ["site_name", "raw_text", "address_or_intersection", "study_kind", "time_windows"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["locations"],
    },
}

# NOTE: signature-block fields (client_contact_phone, client_company_address_*,
# client_company_city/state/zip) are intentionally NOT part of this extraction
# schema. Most orders (~90%) don't need them — the user/company is already in
# qchub. Pulling them on every extraction wastes tokens + latency.
# Instead, qchub auto-create derives phone + address from email_body via the
# regex helpers `extract_first_phone` + `extract_address_from_signature` in
# qchub.py, on-demand only when a missing user/company is detected.


SYSTEM_PROMPT = """\
You are a back-office assistant for Quality Counts, a traffic data collection firm. \
You read incoming client request emails and extract every detail needed to enter the job \
into our order-entry system and to plot the study locations on a map.

Inputs you may receive:
- Email metadata (subject, sender, date, etc.)
- Email body text. If the email was forwarded, you will see TWO sections:
    * FORWARDER NOTES — text the QC staffer added before forwarding (often special instructions to data entry; sometimes empty)
    * ORIGINAL MESSAGE — the client's original request; THIS is the authoritative source for the study list and parameters
- Attached document text (Word docs, PDFs as document blocks)
- Aerial / reference images (use vision to refine location estimates)
- KMZ-derived coordinates if the client attached a KMZ

CALL the record_study_request tool exactly once.

RULES:
- If KMZ placemarks are provided, those ARE the location list. Output exactly one location per placemark, IN THE ORDER they are listed. Use the placemark name as raw_text and lat/lon as the estimate (confidence='high', source='kmz'). Fill in study_kind / subtype / time_windows from the body and documents wherever the body/docs say something specific about that placemark. When nothing specific is found for a placemark, default to study_kind='tube', tube_subtype='volume', empty time_windows — the user will fix in the UI.
- If NO KMZ is provided, enumerate every distinct location from the body and documents. Bullet lists almost always mean separate locations.
- Slashes inside a street pair (e.g. 'Daughtery Road/Walt Loop Road') usually denote an alternate name for the same road, NOT a separate location.
- TMC sub-type defaults to 'standard'.
- Tube sub-type rules:
    * default = volume only
    * 'speed' mentioned → volume_speed
    * 'class', 'classification', 'classification count', '13-bin', or 'fhwa' mentioned → volume_class
    * both speed AND classification → volume_speed_class
    * "72-hour classification count" / "72-hr class count" → volume_class (the 'classification' word is the subtype cue; duration is a SEPARATE field — see below)
- **Tube DURATION — `time_windows[].total_hours` (REQUIRED for any count longer than one calendar day)**:
    * Always set `total_hours` whenever the email describes a count
      that runs longer than one calendar day, OR describes a count
      explicitly in N-day / N-week language even if the value is a
      multiple of 24h. Conversion table:
        - "24-hour" / "24-hr" → total_hours=24 (single day; technically optional but pass it)
        - "48-hour" / "48-hr" → total_hours=48
        - "72-hour" / "72-hr" / "3-day" → total_hours=72
        - "7-day" / "1-week" / "weeklong" / "7-Day 24-Hour" → total_hours=168
        - "14-day" / "2-week" → total_hours=336
        - "30-day" / "1-month" → total_hours=720
    * If the label says BOTH a day-count AND "24-hour" (e.g.
      "7-Day, 24-Hour with Class"), use the DAY count for total_hours.
      The "24-hour" part means "operated 24 hours per day" — the
      total length is the day count × 24. So "7-Day, 24-Hour" →
      total_hours=168, NOT 24.
    * Single-day peak-period windows (AM peak, PM peak, school-out)
      that are SHORTER than 24 hours: leave total_hours OFF; start/end
      alone are enough.
    * The qchub Tube form bills + schedules in Days, so getting
      total_hours right is critical — under-stating it (e.g., 24
      instead of 168 for a 7-day count) produces an order that quotes
      1/7th the work.
- If a time window is contradictory (e.g. 'a.m. peak period (7:00 p.m. to 9:00 p.m.)'), populate start/end with your best interpretation AND fill the `flag` field with the contradiction. Never silently rewrite.
- address_or_intersection: use 'and' not '&'; include city + state.

FORWARDER NOTES handling:
- Primary analysis on the ORIGINAL MESSAGE.
- Cross-reference the FORWARDER NOTES for: special instructions ('this is urgent', 'use vendor X for this one', 'split into two orders', etc.), corrections ('client clarified the dates are…'), or who at QC should own this. Surface anything operationally relevant in the top-level `notes`.
- If forwarder added nothing meaningful (just a 'see below' or empty), ignore.

LOCATION ESTIMATE priority (when no KMZ):
  1. Aerial / map image circling the spot → confidence='high', source='vision'
  2. Well-known intersection in a named city → 'medium', source='text_only'
  3. Obscure → 'low'
  4. No basis → omit entirely (UI will prompt for manual pin)

Be conservative: omit fields you genuinely don't know rather than guessing.
"""


# ---------- context assembly ----------

def _image_block(att: Attachment) -> dict:
    media_type = att.content_type or "image/png"
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(att.data).decode("ascii"),
        },
    }


def _collect_kmz_placemarks(parsed: ParsedEmail) -> tuple[list[kmz.Placemark], str]:
    """Return (placemarks, summary_text_for_prompt)."""
    placemarks: list[kmz.Placemark] = []
    lines: list[str] = []
    for att in parsed.kmz_attachments():
        try:
            pms = kmz.parse_kmz_bytes(att.data) if att.category == "kmz" else kmz.parse_kml_bytes(att.data)
        except Exception as exc:
            lines.append(f"  [Could not parse {att.filename}: {exc}]")
            continue
        for pm in pms:
            placemarks.append(pm)
            lines.append(
                f"  {len(placemarks)}. name={pm.name!r} lat={pm.latitude:.6f} lon={pm.longitude:.6f}"
                + (f" desc={pm.description!r}" if pm.description else "")
            )
    summary = "\n".join(lines) if lines else ""
    return placemarks, summary


def _build_user_content(parsed: ParsedEmail, placemark_summary: str) -> tuple[list[dict], bool]:
    """Return (content_blocks, had_aerial_image)."""
    blocks: list[dict] = []

    header_lines = [
        f"Subject: {parsed.subject}",
        f"From: {parsed.from_}",
        f"To: {parsed.to}",
        f"CC: {parsed.cc or ''}",
        f"Date: {parsed.date.isoformat() if parsed.date else ''}",
    ]

    if parsed.is_forwarded:
        header_lines.append("")
        header_lines.append("⚠ THIS EMAIL WAS FORWARDED. Treat the ORIGINAL MESSAGE below as authoritative.")
        if parsed.original_from:
            header_lines.append(f"Original From: {parsed.original_from}")
        if parsed.original_date:
            header_lines.append(f"Original Date: {parsed.original_date}")
        if parsed.original_subject:
            header_lines.append(f"Original Subject: {parsed.original_subject}")
        header_lines.append("")
        header_lines.append("---FORWARDER NOTES---")
        header_lines.append(parsed.forwarder_added_text or "(no extra notes added by forwarder)")
        header_lines.append("---END FORWARDER NOTES---")
        header_lines.append("")
        header_lines.append("---ORIGINAL MESSAGE BODY---")
        header_lines.append(parsed.original_body)
        header_lines.append("---END ORIGINAL MESSAGE BODY---")
    else:
        header_lines.append("")
        header_lines.append("---EMAIL BODY---")
        header_lines.append(parsed.body_text)

    text = "\n".join(header_lines)

    # KMZ context
    if placemark_summary:
        text += "\n\n---KMZ PLACEMARKS (AUTHORITATIVE LOCATION LIST)---\n" + placemark_summary
        text += "\nOutput one location per placemark, in this order."

    # Word doc text
    for att in parsed.docx_attachments():
        try:
            doc_text = documents.docx_to_text(att.data)
        except Exception as exc:
            text += f"\n\n---ATTACHED WORD DOC: {att.filename} (PARSE FAILED: {exc})---"
            continue
        if doc_text:
            text += f"\n\n---ATTACHED WORD DOC: {att.filename}---\n{doc_text}\n---END {att.filename}---"

    # Image attachments listed in text (so model knows what to expect)
    image_atts = parsed.image_attachments()
    if image_atts:
        text += "\n\n---ATTACHED REFERENCE IMAGES---\n" + "\n".join(
            f"  - {a.filename} ({a.content_type}, {len(a.data) // 1024} KB){' [inline]' if a.is_inline else ''}"
            for a in image_atts
        )

    blocks.append({"type": "text", "text": text})

    for att in parsed.pdf_attachments():
        blocks.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(att.data).decode("ascii"),
            },
        })

    for att in image_atts:
        blocks.append(_image_block(att))

    return blocks, bool(image_atts)


# ---------- public entry point ----------

def extract(
    parsed_email: ParsedEmail,
    *,
    api_key: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> StudyRequest:
    """Run extraction. KMZ (if present) provides the authoritative location list;
    LLM enriches it (or enumerates from body/docs when no KMZ).

    Uses the MODEL_CHAIN fallback (Sonnet → Opus → Haiku) so a stalled or
    overloaded model triggers a switch to the next instead of timing out
    the whole extraction. `progress` (if provided) gets streaming heartbeat
    + model-swap status messages so the UI can show elapsed time.

    If the LLM call fails after retries and a KMZ was present, fall back to a
    deterministic KMZ-only import so the user at least gets the locations.
    """
    client = Anthropic(api_key=api_key or get_api_key())
    placemarks, placemark_summary = _collect_kmz_placemarks(parsed_email)
    content_blocks, had_aerial = _build_user_content(parsed_email, placemark_summary)

    max_tokens = MAX_TOKENS_LARGE if len(placemarks) > 50 else MAX_TOKENS

    try:
        # temperature=0 keeps extraction deterministic: same email -> same
        # StudyRequest on re-runs.
        final_message = _stream_with_retry(
            client,
            progress=progress,
            max_tokens=max_tokens,
            temperature=0,
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "record_study_request"},
            messages=[{"role": "user", "content": content_blocks}],
        )
        tool_input = _find_tool_call(final_message, "record_study_request")
        request = StudyRequest(
            email_subject=parsed_email.subject,
            email_from=parsed_email.from_,
            email_to=parsed_email.to,
            email_cc=parsed_email.cc,
            email_date=parsed_email.date,
            email_body=_body_for_chat(parsed_email),
            has_kmz_attachment=bool(placemarks),
            has_aerial_image=had_aerial,
            **tool_input,
        )
    except Exception as exc:
        if not placemarks:
            raise  # No fallback when there's no KMZ — surface the error
        log.warning("LLM extraction failed (%s); falling back to deterministic KMZ-only import.", exc)
        request = _build_kmz_only_fallback(parsed_email, placemarks, exc)

    # Replace LLM-estimated lat/lon with Google Geocoding API for accurate coords.
    # KMZ-sourced coordinates are already authoritative — leave them alone.
    _refine_estimates_via_geocoding(request)

    return request


def _refine_estimates_via_geocoding(request: StudyRequest) -> None:
    """For each location whose coords aren't already from a client-provided KMZ,
    call Google Geocoding on its address string and replace the estimate.

    LLM vision/text_only estimates are useful for *identifying* which intersection
    the client meant; Google's geocoder is what gives accurate map pins.
    Failures here are non-fatal — we keep whatever the LLM provided and log a note.

    Parallelized 2026-05-25 (Winchester VA email hung 5+ minutes on sequential
    lookups). Each location's geocode runs on its own thread. We hard-cap the
    whole phase at PHASE_BUDGET_SEC — any thread still running at the deadline
    is abandoned and its location keeps the LLM-estimated coordinates. The user
    sees a clear "N of M addresses needed manual verification" warning surfaced
    via the location's `estimate.notes`.
    """
    PHASE_BUDGET_SEC = 120  # generous cap; healthy parallel case finishes in <15s

    try:
        api_key = geocoder.get_google_geocoding_key()
    except Exception:
        api_key = None
    if not api_key:
        log.info("No Google Geocoding API key — keeping LLM-estimated coordinates.")
        return

    # Build the work list. Locations with KMZ-sourced coords are trusted and
    # skipped here (we don't waste an API call). Locations with no address
    # string can't be geocoded — flagged and skipped.
    work: list[tuple[int, str]] = []  # (index_in_request.locations, address)
    skipped_kmz = 0
    no_address = 0
    for idx, loc in enumerate(request.locations):
        if loc.estimate and loc.estimate.source == "kmz":
            skipped_kmz += 1
            continue
        if not loc.address_or_intersection:
            no_address += 1
            continue
        work.append((idx, loc.address_or_intersection))

    if not work:
        log.info("Geocoder: nothing to do (%d KMZ-sourced, %d no-address).", skipped_kmz, no_address)
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    geocoded_high = 0
    geocoded_medium = 0
    geocoded_low = 0
    failed = 0

    def _one(idx: int, addr: str):
        try:
            result = geocoder.geocode(addr, api_key=api_key)
            return (idx, addr, result, None)
        except (geocoder.GeocodingError, geocoder.GeocoderUnavailable) as exc:
            return (idx, addr, None, exc)

    # Parallel fan-out. max_workers tracks address count up to a sane
    # ceiling (don't unleash 200 threads on a giant KMZ).
    n_workers = min(len(work), 12)
    started_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_one, idx, addr): (idx, addr) for idx, addr in work}
        for fut in as_completed(futures, timeout=PHASE_BUDGET_SEC):
            try:
                idx, addr, result, exc = fut.result()
            except Exception as result_exc:
                idx, addr = futures[fut]
                exc = result_exc
                result = None
            if exc is not None:
                log.warning("Geocode failed for %r: %s", addr, exc)
                failed += 1
                continue
            if result is None:
                log.info("No geocoding results for %r (all variants + Places fallback exhausted)", addr)
                failed += 1
                continue
            request.locations[idx].estimate = LocationEstimate(
                latitude=result.latitude,
                longitude=result.longitude,
                confidence=result.confidence,
                source="geocoded",
                notes=f"Google {result.location_type}: {result.formatted_address}",
            )
            if result.confidence == "high":
                geocoded_high += 1
            elif result.confidence == "medium":
                geocoded_medium += 1
            else:
                geocoded_low += 1

    elapsed = time.monotonic() - started_at
    # Anything still in flight past PHASE_BUDGET_SEC fell off as_completed
    # with a TimeoutError — count those as failed too. We don't try to
    # interrupt running requests; they'll finish in the background and
    # their result is discarded. The ThreadPoolExecutor.__exit__ would
    # normally wait for them; we let it (max +REQUEST_TIMEOUT_SEC overhang).
    completed_ok = geocoded_high + geocoded_medium + geocoded_low + failed
    abandoned = len(work) - completed_ok
    if abandoned > 0:
        log.warning(
            "Geocoder hit %ds phase budget; %d address(es) abandoned (kept LLM estimates).",
            PHASE_BUDGET_SEC, abandoned,
        )
        failed += abandoned

    log.info(
        "Geocoder summary: %d high, %d medium, %d low confidence, %d skipped (KMZ), "
        "%d no-address, %d failed; %d worker(s), %.1fs wall time.",
        geocoded_high, geocoded_medium, geocoded_low,
        skipped_kmz, no_address, failed, n_workers, elapsed,
    )


def _build_kmz_only_fallback(
    parsed_email: ParsedEmail,
    placemarks: list[kmz.Placemark],
    exc: BaseException,
) -> StudyRequest:
    """When the LLM is unavailable but we have KMZ placemarks, import them
    deterministically so the user at least gets the locations on a map.
    """
    locations: list[StudyLocation] = []
    for pm in placemarks:
        name = pm.name or f"(unnamed @ {pm.latitude:.4f},{pm.longitude:.4f})"
        locations.append(
            StudyLocation(
                site_name=name,
                raw_text=name + (f" — {pm.description}" if pm.description else ""),
                address_or_intersection=name,
                study_kind=StudyKind.TUBE,
                tube_subtype=TubeSubtype.VOLUME,
                time_windows=[],
                estimate=LocationEstimate(
                    latitude=pm.latitude,
                    longitude=pm.longitude,
                    confidence="high",
                    source="kmz",
                    notes=f"Matched KMZ placemark {name!r}",
                ),
            )
        )

    notes = (
        f"(LLM enrichment failed: {type(exc).__name__}. Locations imported directly from KMZ; "
        "study types defaulted to tube/volume — assign correct types in the Locations tab.)"
    )
    return StudyRequest(
        email_subject=parsed_email.subject,
        email_from=parsed_email.from_,
        email_to=parsed_email.to,
        email_cc=parsed_email.cc,
        email_date=parsed_email.date,
        email_body=_body_for_chat(parsed_email),
        locations=locations,
        notes=notes,
        has_kmz_attachment=True,
        has_aerial_image=bool(parsed_email.image_attachments()),
    )


def _body_for_chat(parsed_email: ParsedEmail) -> str:
    """Return the email body text we want Ellen to be able to read.

    For forwarded emails (QC staffer → data entry), `original_body` is the
    client's original message and `forwarder_added_text` is what the QC
    staffer added. We include both — the staffer's notes are operationally
    important (special instructions, scope clarifications). For non-forwarded
    emails the two are equivalent.
    """
    if parsed_email.is_forwarded and parsed_email.original_body:
        parts: list[str] = []
        if parsed_email.forwarder_added_text:
            parts.append("=== FORWARDER NOTES (from QC staffer) ===")
            parts.append(parsed_email.forwarder_added_text.strip())
            parts.append("")
        parts.append("=== ORIGINAL CLIENT MESSAGE ===")
        parts.append(parsed_email.original_body.strip())
        return "\n".join(parts)
    return (parsed_email.body_text or "").strip()


def _find_tool_call(message, name: str) -> dict:
    for block in message.content:
        if block.type == "tool_use" and block.name == name:
            return block.input
    raise RuntimeError(
        f"Claude did not call {name}. Raw response:\n"
        + json.dumps([b.model_dump() for b in message.content], indent=2, default=str)
    )
