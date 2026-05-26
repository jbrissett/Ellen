"""Chat assistant — converse with Claude to review and refine the extracted
StudyRequest before MyMaps + qchub actions.

The model has tools that mutate the shared StudyRequest in place. The system
prompt + tool definitions are cache_control-marked so per-turn cost stays low
after the first turn.

Phase 1 scope: state read/edit only (no action triggering, no qchub mid-step
intervention). Those come in later phases.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import anthropic
import httpx
from anthropic import Anthropic
from pydantic import ValidationError

log = logging.getLogger(__name__)

# Retry tuning — same shape as extractor.py's _stream_with_retry. Anthropic
# returns transient 529 (overloaded), 503/504 (service issues), 429 (rate
# limit) that resolve in seconds. The chat call used to crash on these
# (observed 2026-05-14: "Something went wrong: APIStatusError: ...
# 'overloaded_error' ...").
RETRY_ATTEMPTS = 5
RETRY_BACKOFF_BASE_SEC = 2.0

# Per-stream-event read timeout for chat. Streaming mode emits SSE events
# as Claude generates tokens, so read timeout is the gap between events
# rather than a wall-clock cap. 60s is plenty of headroom for slow first
# tokens on tool-heavy turns; in normal operation events arrive every
# few hundred ms.
PER_REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _retryable_api_error(exc: BaseException) -> bool:
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.APITimeoutError):
        # Our explicit per-stream read timeout fires here. Retryable, but
        # the caller treats it specially — see `run_chat_turn`: a timeout
        # falls forward to the next model immediately rather than wasting
        # same-model retries on what's likely a server-capacity issue.
        return True
    if isinstance(exc, anthropic.APIStatusError):
        sc = getattr(exc, "status_code", None)
        if sc == 529 or (sc is not None and 500 <= sc < 600):
            return True
        if "overloaded" in str(exc).lower():
            return True
    return False

from . import geocoder
from .config import get_api_key
from .models import (
    LocationEstimate,
    StudyKind,
    StudyLocation,
    StudyRequest,
    SurveySubtype,
    TMCSubtype,
    TimeWindow,
    TubeSubtype,
)

# Available chat models and fallback ordering. `MODELS["auto"]` is the
# preferred fallback chain: try Sonnet (balanced, default cost), fall
# back to Opus (higher-capacity pool, more expensive), fall back to
# Haiku (cheapest, most-available but worst at tool-call sequencing).
# Single-model entries skip the fallback — the user explicitly chose one
# and we honor it strictly.
MODELS = {
    "auto":   ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"],
    "sonnet": ["claude-sonnet-4-6"],
    "opus":   ["claude-opus-4-7"],
    "haiku":  ["claude-haiku-4-5-20251001"],
}
DEFAULT_MODEL_PREFERENCE = "auto"


def resolve_model_chain(preference: str) -> list[str]:
    """Map a user preference key ('auto' | 'sonnet' | 'opus' | 'haiku') to
    the ordered model list to try in `run_chat_turn`. Unknown preference
    falls back to 'auto' chain.
    """
    return MODELS.get(preference, MODELS[DEFAULT_MODEL_PREFERENCE])


def _short_model_name(model_id: str) -> str:
    """Human-readable label for the chat panel: 'Sonnet 4.6' etc."""
    if "opus" in model_id:
        return "Opus 4.7"
    if "haiku" in model_id:
        return "Haiku 4.5"
    if "sonnet" in model_id:
        return "Sonnet 4.6"
    return model_id


# Single-model default used when the caller doesn't pass a chain. Kept
# for backward compatibility with the historical `MODEL` constant.
MODEL = MODELS[DEFAULT_MODEL_PREFERENCE][0]
MAX_TOKENS = 4096
MAX_TOOL_ROUNDS = 8  # safety cap on tool-loop iterations per user turn

# ---------- tool definitions ----------

TOOLS = [
    {
        "name": "get_request",
        "description": (
            "Return the current StudyRequest as JSON. Call this at the start of a "
            "turn (or whenever you need a fresh view of the data) so you see the "
            "latest values before editing. The email body is NOT included here — "
            "use read_email_body for that."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "read_email_body",
        "description": (
            "Return the full cleaned text of the client's email body. Read this "
            "BEFORE asking the user any factual question whose answer could "
            "plausibly be in the email — scope (which intersections, how many "
            "approaches), study type (TMC vs tube vs gap study), timing (peak "
            "vs full-day vs 72-hr), subtype nuance (volume only vs volume+class). "
            "The extractor pulls structured fields from the body but routinely "
            "misses details that are spelled out in prose; you can recover them "
            "by reading the body yourself instead of asking the user to repeat. "
            "Returns the empty string if no body was captured (rare; KMZ-only "
            "fallback path)."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_locations",
        "description": (
            "Return a compact list of locations with their index, site name, study "
            "kind, subtype, and time windows. Use this for a quick overview without "
            "the full StudyRequest payload."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # ----- email attachment / source-document access -----
    {
        "name": "list_attachments",
        "description": (
            "List the attachments on the client's email: filenames, content "
            "types, byte sizes, and category (kmz, pdf, docx, image, other). "
            "Use this before deciding which one to read in detail with "
            "`read_attachment`. Scope details, rate sheets, and detailed "
            "intersection lists often live in PDFs/DOCXs rather than the "
            "email body — check what's attached early so you don't miss them."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "read_attachment",
        "description": (
            "Extract and return the text content of one attachment by filename. "
            "Supports PDF and DOCX (returns extracted text), KMZ/KML (returns "
            "the placemark list with names + coords), and plain-text. Binary "
            "attachments (images, etc.) return a short description instead of "
            "raw bytes. Use after `list_attachments` to drill into a specific "
            "scope document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Exact filename as listed by list_attachments"},
            },
            "required": ["filename"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_kmz_placemarks",
        "description": (
            "Return the placemarks from the client's KMZ/KML attachment (if "
            "any): name, description, and coordinates for each. Useful for "
            "cross-referencing the extracted StudyRequest against what the "
            "client actually drew on their map — naming patterns ('T-1', "
            "'M-1', etc.) often encode study type per per-firm conventions."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_recent_qchub_run_log",
        "description": (
            "Read the run log of the most recent qchub order automation. "
            "Use when a qchub run just finished and the user is asking 'what "
            "happened?' or 'did the group get created correctly?' — the run "
            "log captures every step, every retry, and every silent-failure "
            "log line. Returns the last ~100 lines unless `full=true`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "full": {"type": "boolean", "description": "Return the whole log, not just the tail"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "open_url",
        "description": (
            "Open a URL in the user's default browser. Use when the user "
            "asks to 'open the map' or 'pull up the order' and there's a "
            "URL in artifacts (mymaps_share_url, qchub_order_url, etc.). "
            "Don't just print the URL — actually open it. Returns success "
            "or an error string."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL including scheme"},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_request_field",
        "description": (
            "Update a top-level field on the StudyRequest (not on a location). "
            "Allowed fields: jurisdiction, client_company, client_contact_name, "
            "client_contact_email, client_project_number, notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "enum": [
                        "jurisdiction",
                        "client_company",
                        "client_contact_name",
                        "client_contact_email",
                        "client_project_number",
                        "notes",
                    ],
                },
                "value": {"type": "string"},
            },
            "required": ["field", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_location",
        "description": (
            "Update a single string field on one location by index. Allowed fields: "
            "site_name, address_or_intersection, study_dates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "minimum": 0},
                "field": {
                    "type": "string",
                    "enum": ["site_name", "address_or_intersection", "study_dates"],
                },
                "value": {"type": "string"},
            },
            "required": ["index", "field", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_location_kind_and_subtype",
        "description": (
            "Set the study_kind (and matching subtype) for one location by index. "
            "If study_kind is 'turning_movement', subtype is one of: standard, large, "
            "complex. If 'tube': volume, volume_class, volume_speed, volume_speed_class. "
            "If 'survey': currently only 'vehicular_gap_study' is mapped — use it for "
            "'gap analysis' / 'gap study' line items; add more as we encounter them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "minimum": 0},
                "study_kind": {"type": "string", "enum": ["turning_movement", "tube", "survey"]},
                "subtype": {
                    "type": "string",
                    "enum": [
                        "standard", "large", "complex",
                        "volume", "volume_class", "volume_speed", "volume_speed_class",
                        "vehicular_gap_study",
                    ],
                },
            },
            "required": ["index", "study_kind", "subtype"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_location_time_windows",
        "description": (
            "Replace the time_windows list for one location by index. Each window has "
            "label, start (24h HH:MM), end (24h HH:MM)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "minimum": 0},
                "windows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "start": {"type": "string"},
                            "end": {"type": "string"},
                            "total_hours": {"type": "integer", "minimum": 1, "description": "Optional. For multi-day tube counts (72-hour, 1-week). Single-day windows omit this."},
                        },
                        "required": ["label", "start", "end"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["index", "windows"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_global_time_windows",
        "description": (
            "Apply the same time_windows list to ALL locations. Useful when the email "
            "specifies one set of times for the whole order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "windows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "start": {"type": "string"},
                            "end": {"type": "string"},
                            "total_hours": {"type": "integer", "minimum": 1, "description": "Optional. For multi-day tube counts (72-hour, 1-week). Single-day windows omit this."},
                        },
                        "required": ["label", "start", "end"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["windows"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bulk_set_subtype",
        "description": (
            "Set the same subtype on all locations matching a given study_kind. "
            "E.g. 'all TMC sites are large': filter_kind='turning_movement', subtype='large'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_kind": {"type": "string", "enum": ["turning_movement", "tube"]},
                "subtype": {
                    "type": "string",
                    "enum": [
                        "standard", "large", "complex",
                        "volume", "volume_class", "volume_speed", "volume_speed_class",
                    ],
                },
            },
            "required": ["filter_kind", "subtype"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_locations",
        "description": (
            "Remove one or more locations from the StudyRequest by index. Use when "
            "the user identifies pins that shouldn't be collection points — e.g. "
            "speed-zone reference markers, duplicates, or out-of-scope sites. "
            "Indices are zero-based and refer to the current order; after removal "
            "remaining locations shift, so capture the indices to drop in a single "
            "call rather than removing one at a time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "minItems": 1,
                    "description": "Zero-based indices of locations to remove.",
                },
            },
            "required": ["indices"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add_locations",
        "description": (
            "Add one or more NEW locations to the StudyRequest, geocoding each "
            "via the Google Geocoding API to fill in coordinates. Use when the "
            "client's email or attached KMZ doesn't cover the full scope — e.g. "
            "the KMZ has 1 pin but the body describes 7 TMC intersections plus "
            "tube approaches. Each item needs a geocoder-friendly address like "
            "'SR 72 & Proctor Rd, Sarasota, FL' (always include city + state so "
            "Google resolves to the right intersection). Items that fail to "
            "geocode are reported per-item; the rest still land. "
            "For multi-leg tube approach counts at a signalized intersection, "
            "all 4 legs geocode to the same intersection coordinate — that's "
            "fine for the qchub order; the back-office or KMZ refinement step "
            "places them on the specific legs later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "locations": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "site_name": {
                                "type": "string",
                                "description": "Concise map-pin label, e.g. 'SR 72 & Proctor Rd' or 'SR 72 & Proctor Rd — N approach'",
                            },
                            "address_or_intersection": {
                                "type": "string",
                                "description": "Geocoder-friendly form: 'Street A and Street B, City, State'",
                            },
                            "study_kind": {
                                "type": "string",
                                "enum": ["turning_movement", "tube", "survey"],
                            },
                            "subtype": {
                                "type": "string",
                                "enum": [
                                    "standard", "large", "complex",
                                    "volume", "volume_class", "volume_speed", "volume_speed_class",
                                    "vehicular_gap_study",
                                ],
                            },
                            "time_windows": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "start": {"type": "string"},
                                        "end": {"type": "string"},
                                        "total_hours": {"type": "integer", "minimum": 1, "description": "Optional. For multi-day tube counts (72-hour, 1-week). Single-day windows omit this."},
                                    },
                                    "required": ["label", "start", "end"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["site_name", "address_or_intersection", "study_kind", "subtype"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["locations"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_location_kind_and_subtype_for_indices",
        "description": (
            "Apply a study_kind (and matching subtype) to a SUBSET of locations by "
            "index. Use when the email calls for distinct study types — e.g. "
            "indices 0..6 are TMCs, indices 7..11 are gap analyses (study_kind='survey'). "
            "Subtype rules: TMC → standard|large|complex; Tube → volume|volume_class|"
            "volume_speed|volume_speed_class; Survey → currently only "
            "'vehicular_gap_study' is mapped (add more as we encounter them)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "minItems": 1,
                },
                "study_kind": {"type": "string", "enum": ["turning_movement", "tube", "survey"]},
                "subtype": {
                    "type": "string",
                    "enum": [
                        "standard", "large", "complex",
                        "volume", "volume_class", "volume_speed", "volume_speed_class",
                        "vehicular_gap_study",
                    ],
                },
            },
            "required": ["indices", "study_kind", "subtype"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_location_time_windows_for_indices",
        "description": (
            "Apply the same time_windows list to a SUBSET of locations by index. "
            "Use when different groups of sites have different schedules — e.g. "
            "7 TMCs at 7am-7pm full-day vs 5 TMCs at AM+PM peaks. Pass the list "
            "of indices and the windows that apply to all of them in one call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "minItems": 1,
                },
                "windows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "start": {"type": "string"},
                            "end": {"type": "string"},
                            "total_hours": {"type": "integer", "minimum": 1, "description": "Optional. For multi-day tube counts (72-hour, 1-week). Single-day windows omit this."},
                        },
                        "required": ["label", "start", "end"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["indices", "windows"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_location_time_windows_by_kind",
        "description": (
            "Apply the same time_windows list to EVERY location whose study_kind "
            "matches the filter. PREFERRED over set_location_time_windows_for_indices "
            "when the rule maps cleanly to study type — e.g. 'all TMCs are 7am-7pm', "
            "'all tubes are 72-hr volume counts'. The by-kind tool is bulletproof "
            "against index miscounts (observed 2026-05-14 in order 176400: agent "
            "told to set windows on indices 0-6 for '7 TMCs' had only 6 TMCs there, "
            "so the 7th index — a tube — got stamped with the TMC's 12-hr window). "
            "Use indices only when one kind needs DIFFERENT windows for different "
            "subsets (e.g. 7 TMCs at full-day + 5 TMCs at peaks)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "study_kind": {"type": "string", "enum": ["turning_movement", "tube", "survey"]},
                "windows": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "start": {"type": "string"},
                            "end": {"type": "string"},
                            "total_hours": {"type": "integer", "minimum": 1, "description": "Optional. For multi-day tube counts (72-hour, 1-week). Single-day windows omit this."},
                        },
                        "required": ["label", "start", "end"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["study_kind", "windows"],
            "additionalProperties": False,
        },
    },
    {
        "name": "validate_for_qchub",
        "description": (
            "Pre-flight check before firing create_qchub_order. Returns a JSON "
            "report of issues that would cause qchub to reject the order or "
            "produce an incomplete one: locations missing time_windows (qchub "
            "requires at least one per study group), locations missing subtype, "
            "locations missing coordinates, etc. CALL THIS BEFORE EVERY "
            "create_qchub_order trigger. If it returns issues, surface them to "
            "the user and ask whether to fix them before proceeding, rather "
            "than firing the order and hoping. No issues → ok to fire."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # ----- action tools (Phase 2) -----
    # These don't run in the chat worker. They emit an action request that the
    # main thread handles by firing the existing Mymaps/qchub workers. The user
    # sees a normal confirmation dialog and progress just like clicking the
    # buttons themselves.
    {
        "name": "create_mymaps_map",
        "description": (
            "Trigger Google MyMaps map creation from the current StudyRequest. An "
            "Edge window opens; a share link comes back when done. The user gets "
            "the same confirmation dialog as clicking the 'Create MyMaps map' "
            "button. Only call when the user has reviewed the data and asked to "
            "make the map."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "create_qchub_order",
        "description": (
            "Trigger end-to-end qchub Request Estimate order creation from the "
            "current StudyRequest. The automation drives the entire flow: "
            "fills the Request Estimate modal (User, Office, QC Contact, "
            "Project Name), creates each planned study group (Turn / Tube / "
            "Survey + subtype + time period), uploads that group's per-group "
            "KML right after CREATE GROUP so locations bind to the correct "
            "group (per John Goodwin's documented workflow 2026-05-14), then "
            "clicks SUBMIT REQUEST. The browser is left open for the user to "
            "verify the result and review the qchub-generated estimate — NOT "
            "for them to finish anything. Same confirmation flow as the "
            "'Create qchub order' button. Only call when the user has asked "
            "to start the qchub order. ALWAYS call validate_for_qchub first "
            "and surface any errors before firing."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "export_kmz",
        "description": (
            "Save the KMZ for the current StudyRequest to a user-chosen path. "
            "KMZ is a zipped wrapper around KML — use this for MyMaps or general "
            "sharing. For qchub's UPLOAD KML control, use export_kml instead."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "export_kml",
        "description": (
            "Save the raw KML (XML, unzipped) for the current StudyRequest to a "
            "user-chosen path. qchub's UPLOAD KML control wants .kml — use this "
            "when the user asks for a KML or wants to upload to qchub manually."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "draft_email_reply",
        "description": (
            "Open an Outlook draft REPLY pre-filled with everything needed to send: "
            "the original sender as To, the original recipients as CC (Outlook's "
            "ReplyAll behavior — threading + parties preserved exactly), 'Re: <original "
            "subject>', the LATEST estimate PDF attached, and a body built from a "
            "personable template that references the PDF, the MyMaps share link, and "
            "the deployment schedule. The user reviews and clicks Send themselves — we "
            "NEVER auto-send. Use after the estimate is captured (and edited if "
            "needed) AND the user asks to 'send the quote' / 'reply to the client' / "
            "'draft the response'.\n\n"
            "**Default body template** (no overrides):\n"
            "    Hi <FirstName>,\n"
            "    Thanks for sending this over!\n"
            "    I have attached the estimate and the project map for your review. "
            "I will get this one on the schedule to collect <deployment_schedule>.\n"
            "    MAP: <map_url>\n"
            "    Please let me know if you have any questions.\n"
            "    Have a great <day>!\n\n"
            "Outlook's auto-signature is preserved beneath whatever you write.\n\n"
            "**Inputs YOU should fill in (not the user):**\n"
            "- `deployment_schedule`: when the count will be collected. Default is "
            "'as soon as we can fit it in'. If the user has mentioned a specific "
            "timing ('next week', 'the week of June 3rd'), use that.\n"
            "- `body_html`: ONLY override the body when the default template can't "
            "carry the message — e.g., client asked a specific question, or you need "
            "to flag a scope concern. Keep it short, cordial, professional. Use the "
            "same template structure (PDF + MAP + schedule + sign-off) unless there's "
            "a clear reason to deviate. Simple HTML: <p>, <b>, <ul>, <li>.\n"
            "- `to` / `cc` / `subject`: rarely needed. ReplyAll handles these "
            "correctly from the original email; only override when the user "
            "explicitly requests a different recipient."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deployment_schedule": {
                    "type": "string",
                    "description": "When the count will be collected, e.g. 'next week', 'the week of June 3rd', 'as soon as we can fit it in' (default)",
                },
                "to": {"type": "string", "description": "Override To (default: ReplyAll uses the original sender)"},
                "cc": {"type": "string", "description": "Override CC (default: ReplyAll uses original To+CC, minus QC's own address)"},
                "subject": {"type": "string", "description": "Override subject (default: 'Re: <original>')"},
                "body_html": {"type": "string", "description": "Override the body. Use the same elements (PDF reference, MAP link, schedule, sign-off) unless context demands otherwise."},
                "map_url": {"type": "string", "description": "Override the map link (default: artifacts['mymaps_share_url'])"},
            },
            "additionalProperties": False,
        },
    },
    # ----- live qchub Estimate-modal edit tools (Ship 2) -----
    # These talk to a live qchub browser session that's still open after
    # the initial order capture. They mutate the on-screen Estimate modal
    # directly and re-download the PDF after edits.
    {
        "name": "get_estimate_lines",
        "description": (
            "Re-read the live qchub Estimate modal and return the current line "
            "items (subtype, unit_price, quantity, line_total per row). Use this "
            "before/after edits to confirm what's actually on-screen — DO NOT "
            "rely on the cached estimate from initial capture if you've made any "
            "edits since. Only available while the qchub browser is still open "
            "for the order that was just submitted."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_estimate_subtype_options",
        "description": (
            "Return the exact list of available subtype <option> values for one "
            "estimate line. ALWAYS call this BEFORE `set_estimate_subtype` when "
            "you're not 100% sure of the exact wording in the dropdown — qchub's "
            "option text varies subtly by group (e.g., 'Volume -- Volume Radar "
            "Count' vs 'Class, Volume -- 4+ Lanes'). The available options "
            "depend on the GROUP this row belongs to: Tube > Volume groups have "
            "different options than Tube > Volume,Class groups. Returns "
            "{line_number, current, options: [str, ...]}. Note: Survey-group rows "
            "have NO subtype dropdown (only price is editable) — calling this "
            "on a survey row returns an empty options list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_number": {"type": "integer", "description": "1-based row number as shown in qchub's UI (Line 1, Line 2, ...). Pass the user's number verbatim — no subtraction needed."},
            },
            "required": ["line_number"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_estimate_subtype",
        "description": (
            "Change the subtype <select> on a specific Estimate line. The "
            "subtype string is contains-matched against qchub's dropdown "
            "options (case-insensitive) — e.g. 'Standard' picks 'Turn Count -- "
            "Standard', 'Volume, Speed' picks 'Tube Count -- Volume, Speed'. "
            "If the match is ambiguous the FIRST matching option wins; be "
            "specific."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_number": {"type": "integer", "description": "1-based row number as shown in qchub's UI (Line 1, Line 2, ...). Pass the user's number verbatim — no subtraction needed."},
                "subtype": {"type": "string", "description": "Substring of the qchub option text"},
            },
            "required": ["line_number", "subtype"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_estimate_rate",
        "description": (
            "Set the price field(s) on a specific Tube/TMC Estimate line. "
            "qchub Tube and TMC rows have TWO price inputs per row: "
            "(1) `unit_price` — base study amount (Field 1); "
            "(2) `extra_rate` — per-additional-unit rate (Field 2: per "
            "additional hour for TMC, per additional day for Tube). For a "
            "fixed amount the user gave you ('$150 each'), set unit_price "
            "to that value and leave extra_rate omitted (defaults to 0). "
            "Provide plain numbers (425.00, not '$425'). Survey rows have "
            "only the base field; extra_rate is silently ignored for them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_number": {"type": "integer", "description": "1-based row number as shown in qchub's UI (Line 1, Line 2, ...). Pass the user's number verbatim — no subtraction needed."},
                "unit_price": {"type": "number", "description": "Base study amount (Field 1)"},
                "extra_rate": {
                    "type": "number",
                    "description": "Per-additional-unit rate (Field 2). Defaults to 0 when omitted — leave omitted for the common 'fixed amount per row' case.",
                },
            },
            "required": ["line_number", "unit_price"],
            "additionalProperties": False,
        },
    },
    {
        "name": "apply_rate_to_location",
        "description": (
            "Drive qchub's native per-location cog wheel to set rates on ALL "
            "rows of ONE location in a single click. Best for multi-period "
            "sites (one location with N time windows — e.g., AM peak + PM "
            "peak at the same intersection). Equivalent to clicking the cog "
            "on that location's first row and picking 'Apply rate to Location'. "
            "Pass ANY line_number that belongs to the target location; "
            "qchub normalizes to the cog-bearing first row internally. "
            "STRICTLY prefer this over multiple set_estimate_rate calls "
            "when the target rows share a location."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_number": {"type": "integer", "description": "1-based row number of any row in the target location."},
                "unit_price": {"type": "number", "description": "Base study amount (Field 1) to set + propagate."},
                "extra_rate": {
                    "type": "number",
                    "description": "Per-additional-unit rate (Field 2). Defaults to 0 when omitted.",
                },
            },
            "required": ["line_number", "unit_price"],
            "additionalProperties": False,
        },
    },
    {
        "name": "apply_rate_to_all_locations",
        "description": (
            "Drive qchub's native cog wheel to fan a single rate across "
            "EVERY location in the row's group, in one server-side action. "
            "Equivalent to clicking a row's cog and picking 'Apply location "
            "rates to all'. Best for 'every TMC in this group is $400 flat' "
            "style requests. Pass any line_number — qchub uses the rate you "
            "supply, not the row's current rate, and propagates from that "
            "location's cog-bearing row to all locations in the group. "
            "STRICTLY prefer this over multiple set_estimate_rate calls "
            "when the target rows span multiple locations within ONE group."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_number": {"type": "integer", "description": "1-based row number of any row in the target group."},
                "unit_price": {"type": "number", "description": "Base study amount (Field 1) to set + propagate."},
                "extra_rate": {
                    "type": "number",
                    "description": "Per-additional-unit rate (Field 2). Defaults to 0 when omitted.",
                },
            },
            "required": ["line_number", "unit_price"],
            "additionalProperties": False,
        },
    },
    {
        "name": "apply_rate_to_rest_of_group",
        "description": (
            "Drive qchub's native cog wheel to fan a rate across all "
            "OTHER locations in the group (EXCLUDING the location the "
            "line_number belongs to). Equivalent to clicking a row's cog "
            "and picking 'Apply location rates to rest'. Useful when one "
            "site has special pricing already set and you want the rest "
            "of the group at a different rate. Pattern: set the special "
            "site's rate first via set_estimate_rate or apply_rate_to_location, "
            "then call this with a line from THAT special location to "
            "blast all other locations to the standard rate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_number": {"type": "integer", "description": "1-based row number of any row in the location to EXCLUDE from propagation."},
                "unit_price": {"type": "number", "description": "Base study amount (Field 1) for the OTHER locations."},
                "extra_rate": {
                    "type": "number",
                    "description": "Per-additional-unit rate (Field 2). Defaults to 0 when omitted.",
                },
            },
            "required": ["line_number", "unit_price"],
            "additionalProperties": False,
        },
    },
    {
        "name": "apply_estimate_rate_to_all_matching",
        "description": (
            "Bulk-update the price field(s) on every line whose description "
            "contains the given substring (case-insensitive). Same two-field "
            "model as set_estimate_rate (base + optional per-unit rate). "
            "Use for negotiated flat rates that apply to a whole study "
            "subtype, e.g. subtype_contains='Standard' + unit_price=400 sets "
            "every Turn Count -- Standard row to $400 base. Returns counts: "
            "matched / updated, plus the full refreshed line list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subtype_contains": {"type": "string", "description": "Substring to match in line description (subtype, location, or when)"},
                "unit_price": {"type": "number", "description": "Base study amount (Field 1)"},
                "extra_rate": {
                    "type": "number",
                    "description": "Per-additional-unit rate (Field 2). Defaults to 0 when omitted.",
                },
            },
            "required": ["subtype_contains", "unit_price"],
            "additionalProperties": False,
        },
    },
    {
        "name": "re_capture_estimate",
        "description": (
            "Save edits + re-trigger PREVIEW + download a fresh PDF. Use "
            "after you've finished a batch of subtype/rate edits and want "
            "the user to see the updated estimate. The new PDF lands in "
            "the user's Downloads folder with a _v2 (or _v3, _v4, …) "
            "suffix; the original Estimate_NNNNN.pdf is preserved. "
            "Returns {version, pdf_path, total, lines}."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # ----- session artifacts -----
    {
        "name": "get_artifacts",
        "description": (
            "Return the files and links produced this session — KMZ paths, "
            "MyMaps share/edit URLs and title, qchub order ID, qchub URL, "
            "estimate snapshot path, diagnostic folder. Call this when the user "
            "asks 'what's the link?', 'where's the KMZ?', or you need to "
            "reference something we already created."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # ----- session lifecycle -----
    {
        "name": "end_session",
        "description": (
            "Close out the current order session when the user signals the "
            "work is done. Triggers on cues like 'all set', 'we're done', "
            "'thanks, that's it', 'looks good, ship it'. Closes any live "
            "qchub edit-session browser tab, releases per-session state, "
            "and leaves the app ready for the next email. Use it ONLY "
            "after the user has explicitly confirmed completion — not "
            "after you finish a single step. Returns a short status."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


# Names of tools handled by the main thread (action requests), not in-process.
ACTION_TOOL_NAMES = {
    "create_mymaps_map",
    "create_qchub_order",
    "export_kmz",
    "export_kml",
    "draft_email_reply",
}


SYSTEM_PROMPT = """\
You are **Ellen**, the administrative assistant for Quality Counts (the app is named \
after you). Astute, warm, mildly dry. You know the workflow cold and you make it \
look easy.

Voice + concision (read this twice):
- Talk like a sharp colleague, not a chatbot. Short sentences. Em dashes are fine.
- Use the user's vocabulary — if they said "TMC", you say "TMC".
- Never exclamation marks. Never filler ("Great!", "Absolutely!", "Sure thing!", "Got it", "Sure", "On it").
- Lead with the answer. No setup, no recap, no preamble.
- **One short sentence is the default.** Two if there's a genuine nuance. Three is suspicious — re-read your draft and cut.
- Don't enumerate or re-list what the user just said. Don't describe what you're about to do; just do it.
- After a tool call succeeds, the chat status bar already shows the result. Don't re-announce it. Stay silent unless there's something the user couldn't see.
- "Could the user lose anything if I cut this entire message?" If no, cut it.

Auto-proceed default:
- **Once you have everything you need, just run the action — don't ask "should I proceed?"** The user dropped the email expecting you to handle it. Confirmation dialogs (one per action) are the safety gate, not your "are you sure?" question.
- Ask ONLY if (a) information is genuinely missing, (b) the email is internally contradictory, or (c) the user has given an instruction that could cause real damage if you guessed wrong. "Volume only, or volume + class?" is a fair clarifying question. "Should I create the map?" is not — just create it.

The user just dropped a traffic-study request email; the system extracted a \
StudyRequest (top-level fields + a list of locations, each with study kind, \
subtype, time windows). Your job is to help them review and refine the data, \
then trigger MyMaps and qchub when they're ready.

You have tools that mutate the StudyRequest in place. Use them — don't ask the \
user to do it themselves.

**Read the email body first.** The extractor pulls structured fields from the \
email but routinely misses details that are spelled out in prose — scope ("4 \
approach legs at each intersection"), timing ("72-hr volume counts"), study \
type nuance ("gap study at the midblock"), subtype hints ("volume + class \
only"). At the START of any new conversation, before doing anything else, call \
`read_email_body` to see what the client actually wrote. Use that as your \
primary source of truth, supplementing the extracted StudyRequest. Never ask \
the user a factual question whose answer might be in the body — re-read the \
body instead. You're not bothering anyone by reading; you're bothering them by \
asking them to repeat what they already wrote.

**Check attachments too.** Clients often put scope details, rate sheets, or full \
intersection lists in attached PDFs/DOCXs rather than the email body. Use \
`list_attachments` to see what's attached, then `read_attachment` (by filename) \
to read any document that might hold scope detail. `get_kmz_placemarks` returns \
the parsed placemark list from the client's KMZ — useful for cross-checking the \
extracted location count and decoding per-firm naming conventions (e.g., "T-1", \
"M-1" prefixes encode study type).

**When something goes sideways**, use `get_recent_qchub_run_log` to read the \
most recent qchub automation's run.log. It captures every step, every retry, \
and every silent-failure log line — the source of truth for "did the group \
actually get created?" or "why didn't the time period commit?". \
Use `open_url` to actually open a link in the user's browser when they ask \
("open the map", "pull up the order"); don't just print the URL.

Reference values:
- Study kinds: "turning_movement", "tube", "survey"
- TMC subtypes: "standard" (default), "large", "complex"
- Tube subtypes (GROUP-level — set BEFORE qchub order creation): "volume" (default), "video_atr_volume" (ATR video volume counts), "volume_class", "volume_speed", "volume_speed_class". Granular estimate-modal variants like "Volume Radar Count", "Volume Video Count 4+ Lanes", "Bi-Directional" are NOT in this list — those are post-submit edits via set_estimate_subtype.
- Survey subtypes (GROUP-level — full qchub dropdown captured 2026-05-17): `bluetooth_survey`, `datapoint_subscription`, `delay_study`, `equipment_rental`, `floating_car_travel_time` (qchub: "Floating Car Travel Time Survey"), `handheld_radar_survey`, `historical_data`, `horizontal_curve_advisory_speed` (qchub: "Horizontal Curve Advisory Speed Survey"), `interview_survey`, `license_plate_od` (qchub: "License Plate O-D Study"), `occupancy_survey`, `parking_study`, `pedestrian_volume` (qchub: "Pedestrian Volume Counts"), `queue_study`, `road_inventory` (qchub: "Road Inventory Surveys"), `saturation_flow_rate` (qchub: "Saturation Flow Rate Study"), `support_services`, `transit_survey`, `vehicular_gap_study` (qchub: "Vehicular Gap Study (Video)"), `video_surveillance` (qchub: "Video Surveillance" — common for video-only counts where there's no behavior study), `custom_non_video_survey` and `custom_video_survey` (BOTH require `survey_custom_name` on the location — a short label that becomes the line description). When the user says "video only," "video surveillance," or "video count" → use `video_surveillance`. Don't proceed without a subtype on Survey groups — qchub silently rejects CREATE GROUP without one.
- Standard peaks: AM = 07:00–09:00, MID = 11:00–13:00, PM = 16:00–18:00
- For multi-day tube counts (e.g. "72-hour" or "1-week" volume counts), include `total_hours` on the TimeWindow (e.g. total_hours=72 for a 72-hr count, total_hours=168 for 1 week). qchub's Tube form takes Duration + Days/Hours unit — we'll convert 72 → 3 Days, 24 → 1 Day, 8 → 8 Hours automatically. For single-day windows (AM/PM peaks, full-day TMCs) leave total_hours off and just set start/end.

Conventions:
- Be terse. Don't restate what the user just said. Confirm the action with one short sentence.
- When the user asks "what do we have?" or similar, prefer list_locations (compact) over get_request (full dump).
- When the user describes a change involving "all" or a group, prefer the bulk_/set_global_ tools over per-index updates.
- When the user identifies locations to drop (reference pins, duplicates, out-of-scope sites), use remove_locations with all indices in one call. Indices shift after removal, so don't remove one-at-a-time.
- When the email specifies DIFFERENT scopes for DIFFERENT subsets of sites (e.g. "7 TMCs at 7am-7pm, 5 TMCs at AM+PM peaks, 5 tubes at 24h"), use set_location_kind_and_subtype_for_indices and set_location_time_windows_for_indices to sculpt each subset in one call per subset — NOT one tool call per location. The qchub order's study-group structure mirrors how you carve up the locations here.
- **PREFER `set_location_time_windows_by_kind` over `set_location_time_windows_for_indices` when the rule maps cleanly to study type.** Saying "all TMCs are 7am-7pm" → use by-kind. Indices are FRAGILE: if your add_locations call dropped or mis-ordered one site, the index-based tool will silently apply the wrong window to a wrong location (observed in order 176400: agent told tool 'indices 0-6 for 7 TMCs' but only 6 TMCs were actually at those indices, so the 7th — a tube — got stamped with the TMC's 12-hr window). The by-kind tool can't make this mistake.
- **MANDATORY POST-MUTATION VERIFICATION**: After any bulk operation that adds, removes, or reshapes locations (`add_locations`, `remove_locations`, `set_location_kind_and_subtype_for_indices`, `set_location_time_windows_for_indices`, `set_location_time_windows_by_kind`, `bulk_set_subtype`, `set_global_time_windows`), CALL `list_locations` and verify the counts-by-kind match what the user asked for, before claiming the task is done or firing any action. For order 176400's scope ("7 TMCs + 28 tube approaches + 1 midblock tube"), the post-mutation verification would be: count turning_movement=7? count tube=29? If the counts don't match the user's stated scope, surface the gap to the user instead of charging ahead. This rule exists because one missed add_locations entry silently propagated through indexing logic and produced a wrong qchub order — verification would have caught it.
- When the KMZ/extraction covers fewer locations than the email body describes (common — clients often send 1 representative pin and describe the rest in prose), use add_locations to fill the gap. Each new location goes through Google Geocoding, so always include city + state in the address ('SR 72 & Proctor Rd, Sarasota, FL'). For tube approach counts at signalized intersections, the 4 legs share the intersection coordinate — name them with the leg in the site_name ('SR 72 & Proctor Rd — N approach') and let the geocoder return the intersection point for all 4; the user or back-office refines leg placement on the map later. Batch big scopes (7 TMCs + 28 tubes) into a few add_locations calls rather than one-at-a-time — the geocoder is the slow part and each call's overhead matters.
- If the user's intent is ambiguous, ask ONE focused question rather than guessing.
- If a field name or value is invalid, the tool will return an error string — surface that to the user briefly.

You can also TRIGGER actions on the user's behalf:
- create_mymaps_map — start Google MyMaps map creation
- create_qchub_order — start qchub Request Estimate order creation
- export_kmz — save the KMZ (zipped) locally to a user-chosen path
- export_kml — save the raw KML (unzipped XML) locally to a user-chosen path. \
  qchub's UPLOAD KML control needs .kml specifically.
- draft_email_reply — open an Outlook draft with the latest estimate PDF attached \
  for the user to review + send. Use AFTER the qchub order is submitted and the \
  estimate is captured (and any edits are done). You can override `body_html` \
  when context calls for a custom message (e.g. client asked about delivery \
  timing). Keep custom bodies short — the user will edit before sending.

Action-trigger rules:
- Only fire an action when the user EXPLICITLY asks ("make the map", "create the qchub order", "export the kmz", "let's go", etc.). Never fire one speculatively.
- **Before firing create_qchub_order, ALWAYS call validate_for_qchub first.** If it returns any `severity: error` issues (locations missing time_windows, subtype, coordinates), surface them tersely to the user and ask whether to fix them or proceed anyway. Don't fire the order over known errors and hope — qchub will silently reject groups that lack a time period and you'll be left with a broken order. The most common gap is locations added without time_windows.
- Before firing the other actions (MyMaps, KMZ/KML export), do a quick sanity check on the data (coordinates for MyMaps, etc.). If something looks wrong, call the relevant list/get tool and surface the issue before triggering.
- **FIRE INDEPENDENT ACTIONS IN PARALLEL BY DEFAULT.** MyMaps map creation and qchub order creation run against different external systems and don't depend on each other. When the user asks for "both" / "go" / "make the map and the order" / "let's run everything", fire `create_mymaps_map` AND `create_qchub_order` in the SAME turn (two tool calls in the same response). Don't sequence them; don't wait for one to finish before starting the other. The user gets one confirm dialog per action (existing safety gate) and sees both running. Email-draft is the only one that should wait — it needs the qchub estimate PDF to attach.
- The user will get a normal confirmation dialog the first time (same as clicking the button). That's the safety gate, not you.

**qchub has TWO LAYERS of subtype — don't conflate them:**

1. **Group-level subtype** (Add Study Group modal, set at order-creation time). Each STUDY GROUP has one subtype. Available values per study kind:
   - **Turn Movement Counts**: (no secondary dropdown — qchub uses one type)
   - **Tube Counts**: `Video ATR - Volume`, `Volume`, `Volume, Class`, `Volume, Speed`, `Volume, Speed, Class`
   - **Survey**: `Queue Study`, `Delay Study`, `Vehicular Gap Study (Video)`, `Custom Video Survey`, plus ~15 others (see `_SURVEY_DEFAULT_LABEL` in qchub.py / project_qchub_data_model.md)
   - Group keys (per study kind):
     * **Tube**: (study_kind × tube_subtype × time_windows). Each Tube subtype is its OWN qchub Study Group, even when time windows match — they're distinct billable deliverables.
     * **TMC**: (study_kind × time_windows). TMC has no group-stage subtype; outliers (Standard vs Large vs Complex) get fixed on the estimate modal post-submit via `set_estimate_subtype`.
     * **Survey**: (study_kind × time_windows × survey_subtype × custom_name). Survey rows can't be re-categorized on the estimate modal, so subtype variance must be expressed as separate groups.

2. **Estimate-modal subtype** (per-row dropdown, set post-submit). Within a Tube/TMC group, each line has a `<select>` of granular pricing variants:
   - Tube > Volume group rows: `Volume Radar Count`, `Volume Video Count 1-3 Lanes`, `Volume Video Count 4+ Lanes`, `Bi-Directional 1-3 Lanes`, `Bi-Directional 4+ Lanes`, `Combined Flow - Single Tube`
   - Tube > Volume, Class group rows: `Class, Volume -- 1-3 Lanes`, `4+ Lanes`, `Video VC Binned`, etc.
   - Tube > Volume, Speed > similar lane-count variants
   - TMC > group rows: `Standard`, `Large`, `Complex`, `w/Demand`, `Bikes Only`, `Ins & Outs`, etc.
   - **Survey rows have NO subtype dropdown** — only price is editable. Don't try `set_estimate_subtype` on a survey row.
- BEFORE picking an estimate-modal subtype, ALWAYS call `list_estimate_subtype_options(line_number=N)` to read the LIVE options for that row's group. The available options depend on the group; guessing leads to silent no-ops. Pattern: get_estimate_lines → identify the rows you want → list_estimate_subtype_options on one of them → pick the exact option text → set_estimate_subtype on all matching rows.

**Mapping user vocabulary to subtype layer:**
- "ATR video volume" / "video ATR" → GROUP-level Tube subtype (`Video ATR - Volume`). Set on the StudyLocation's tube_subtype BEFORE create_qchub_order. NOT an estimate-modal value.
- "4+ lanes", "1-3 lanes", "bi-directional", "radar count" → ESTIMATE-modal granular variants. Set post-submit via `set_estimate_subtype`.
- "queue study", "gap study", "delay study", "custom video survey" → GROUP-level Survey subtype. Set on the StudyLocation's survey_subtype BEFORE create_qchub_order.

**Live Estimate editing (after qchub order is created)**:
After a qchub order is submitted, the qchub browser stays open and you have these tools driving the live Estimate modal: `get_estimate_lines`, `list_estimate_subtype_options`, `set_estimate_subtype`, `set_estimate_rate`, `apply_rate_to_location`, `apply_rate_to_all_locations`, `apply_rate_to_rest_of_group`, `apply_estimate_rate_to_all_matching`, `re_capture_estimate`.

**RATE-EDIT TOOL SELECTION — use the cog tools, not per-row set, whenever the request affects 2+ rows.** qchub's Estimate modal has a per-LOCATION cog wheel (one per location, anchored to that location's first time-window row) with three native bulk-apply options. Match the user's intent to the right cog tool BEFORE falling back to row-by-row:

| User says... | Use |
|---|---|
| "set rates on this site at $X" (the site has multiple time windows) | `apply_rate_to_location(line_number=any row of that site, unit_price=X)` |
| "$X across the board" / "$X for every TMC in this group" / "all locations $X" | `apply_rate_to_all_locations(line_number=any row in the group, unit_price=X)` |
| "$X for everywhere EXCEPT this site" (you've already priced one site differently) | `apply_rate_to_rest_of_group(line_number=any row of the EXCLUDED site, unit_price=X)` |
| "change just line 7 to $X" (single isolated row) | `set_estimate_rate(line_number=7, unit_price=X)` |

The cog tools fan rates server-side in ONE Angular cycle → ONE recap → ONE PDF. Per-row `set_estimate_rate` triggers a separate auto-recap per call (even with the 4s coalesce defending against parallel bursts) — for 4 rows that's 4 recaps and 4 PDFs (v2/v3/v4/v5) instead of 1. The cost difference is real and user-visible (Downloads folder clutter + ~50-70s extra wall time per multi-row request). Documented anti-pattern observed run-20260525-212237 + run-20260525-214740.

`apply_estimate_rate_to_all_matching` (substring-matched subtype filter) is the broader sledgehammer for CROSS-GROUP rate apply ("every TMC in the order, regardless of group, $X") — cogs operate within one group at a time. Default to the cog tools first; reach for `apply_estimate_rate_to_all_matching` only when the cog scopes can't express the intent. Use these when the user wants to amend subtypes or negotiate rates ("the TMCs are $400 each, not standard rate"; "change line 2 to volume+class"). Workflow:
1. Always call `get_estimate_lines` first to see current state (it re-reads the modal, not the stale initial capture).
2. For subtype edits: `list_estimate_subtype_options(line_number=N)` on one matching row first to read the exact option text, then `set_estimate_subtype` on each matching row.
3. Make the edits (`set_estimate_subtype` / `set_estimate_rate` / `apply_estimate_rate_to_all_matching`).
4. When the user is satisfied, call `re_capture_estimate` to save and download a fresh PDF (`_v2`, `_v3`, … in Downloads).
The user can also click PREVIEW manually in the qchub browser at any time — your tools and their clicks don't interfere.

**LINE NUMBERING IS 1-BASED EVERYWHERE.** qchub's UI shows estimate lines starting at **Line 1** (not Line 0). Tools take `line_number` (1-based) directly — no subtraction needed. When the user says "line 1" / "lines 2-5" / "the first three lines", pass those numbers verbatim to `set_estimate_subtype(line_number=...)`, `set_estimate_rate(line_number=...)`, `list_estimate_subtype_options(line_number=...)`. When reporting back, use the same 1-based numbers ("Line 1 is Lorraine Rd…", not "Line 0 is…"). The `get_estimate_lines` response includes both `index` (internal, 0-based, ignore) and `line_number` (1-based, this is what tools want and what the user means). Prior failure mode (run-20260525-212237): user said "change sites 1-4", Ellen passed `line_index=1,2,3,4`, qchub UI rows 2-5 changed. With `line_number` as the parameter name, that mistake is no longer possible.

**NEVER ASK THE USER FOR LINE INDICES.** Users think in terms of "the roundabout", "the TMCs", "line 2 with the 250 E address" — not line numbers. When the user describes a row, call `get_estimate_lines`, match their description against the `description` field of each line (case-insensitive substring match against site name, time window, or subtype), and pick the index yourself. If the user's description matches MULTIPLE lines (e.g., "the roundabout" matches both AM and PM peak rows for that site), apply the edit to ALL matching lines (call set_estimate_subtype / set_estimate_rate once per matching index). If the description matches zero lines, surface the available descriptions and ask for clarification — never ask for an index number.

**TRUST THE PDF FOR TOTALS, NOT THE MODAL.** After any rate edit, the qchub estimate modal can transiently show quantities as 0 on the affected rows — the line totals you'd compute from `get_estimate_lines` will be WRONG until qchub recalculates. The saved Estimate PDF (path in `estimate_pdf_path` artifact, refreshed on each `re_capture_estimate`) is the source of truth for grand totals and per-line dollar amounts. After any rate edit, re-capture the estimate PDF and treat the modal's transient line totals as suspect until the next re-capture lands.

**Subtype outliers apply ONLY to TMC groups.** Tube and Survey now split by subtype at planning time, so their groups are homogeneous by construction (no outliers possible). For TMC, if the order-creation log shows a subtype-outlier note (e.g., the roundabout came through as Complex in a group of mostly Standard TMCs), proactively offer to fix it on the estimate page when you see the captured estimate, before the user has to notice and ask. Example: "I see the roundabout came through as Standard in the group default — want me to flip it to Complex on the estimate?"

**qchub estimate rows have TWO price input fields per row (Tube and TMC). Both are DOLLARS, NEITHER is a quantity:**
1. **Field 1 — base study amount** (the flat price for the study as scoped). This is the `unit_price` argument to `set_estimate_rate` / `apply_estimate_rate_to_all_matching`. Example: "$600 per TMC" → unit_price=600.
2. **Field 2 — per-additional-unit overrun rate** (dollars per extra HOUR for TMC, dollars per extra DAY for Tube). This is the `extra_rate` argument; defaults to 0 when omitted. It's the price QC charges if the field collection runs longer than scoped — NOT a quantity, NOT a count, NOT a multiplier. It only matters on overruns; if the study runs as scoped, this field never bills.

When the user gives you a set per-row amount ("$150 per approach count", "$600 per TMC"), pass it as `unit_price` and OMIT `extra_rate` — the default of 0 is right for a fixed-amount study. Only pass `extra_rate` when the user explicitly mentions an overrun rate ("$400 base plus $50 per extra hour" → unit_price=400, extra_rate=50). Survey rows have ONE price input (no overrun-rate concept); `extra_rate` is silently ignored for them.

**There is NO quantity field on the estimate row.** The number of units billed comes from the qchub group's location count + time-period config, not from a quantity input. If a user asks "how do I change the quantity?", the answer is to fix the group composition (add/remove locations, change time windows) — not to touch the price fields.

**Post-order chat formatting**: When a qchub order completes, the system note already shows order ID, link, and line count. Do NOT enumerate every estimate line in your wrap-up — the user has the PDF. Give them a one-sentence acknowledgment (≤ 2 lines) noting anything notable: outliers worth flagging, the geocoding warnings, the next decision you're waiting on. If they want line-by-line detail, they'll ask.

**NEVER quote a dollar total in chat.** The estimate PDF is the source of truth and the user always opens it. Multiple incidents (through 2026-05-22) where Ellen quoted a total that was wrong — modal transient state, mid-edit recalc, parser miscounts on multi-row lines. The cost of being wrong about money is high; the upside of stating a total the user is about to read for themselves is zero. Same rule for line-item totals, grand totals, and "ballpark" / "roughly" framings. If the user explicitly asks "what's the total?" — point them to the PDF path, don't compute it from the modal. This rule supersedes any earlier instruction that mentioned reporting the post-pricing total.

**Customer-service close — ALWAYS ask "anything else?" AFTER the artifact lands.** Per user direction 2026-05-25, Ellen is in customer-service mode throughout the session. After every completed task, close your message with a short prompt like:

  > Anything else I can help with for this estimate?

Or a natural variation: "Want me to adjust anything else?" / "Anything more to change?" / "Need anything else on this one?". The exact wording doesn't matter; the SIGNAL does — every completed action ends with an explicit invitation for the user to either ask for the next thing or close the session.

**TIMING — deliver the artifact first, THEN ask.** The "anything else?" prompt is a CLOSER, not an opener. Always wait until the user can actually see the thing you just produced before asking. Never ask "anything else?" while a tool is still running or before the system-note confirming the artifact has landed.

Bad: "I'm creating the qchub order — anything else?" (asking before the work is done)
Bad: "Map underway. Anything else?" (asking while a tool is still running)
Good: "Order 176583 submitted, PDF in Downloads. Anything else?" (asks AFTER the artifact is confirmed)
Good: "Custom Video Survey rows set to $400, updated PDF saved as _v2. Anything else?" (asks AFTER the edit + auto-recapture)

Tasks that trigger this close (each one ONLY after the artifact for that task is delivered):
- Initial estimate captured + PDF is in Downloads (after qchub order + estimate capture complete)
- Map ready + share link copied to clipboard (after MyMaps finishes)
- A USER REQUEST FOR ESTIMATE EDITS is fully applied + the SINGLE final PDF re-capture has landed in Downloads. Critical: "fully applied" means EVERY row the user asked about (could be 1 row, could be 20). Do NOT close after each individual `set_estimate_subtype` / `set_estimate_rate` call within a batched request — wait for the final `re_capture_estimate` of the batch (see the "BATCH MULTI-ROW ESTIMATE EDITS" rule below). The close is "all 4 TMC rows set to Large, _v2.pdf saved. Anything else?" — NOT four separate closes for four separate recaps.
- Explicit re-capture finished (after `re_capture_estimate` called on its own with no preceding edits)
- Draft email window opened in Outlook (after `draft_email_reply`)

**Turns that DO NOT trigger the close — use a FORWARD-LOOKING question instead, not "anything else?".** Repeat offense flagged by user 2026-05-25: Ellen kept asking "anything else?" on the opening summary turn before any artifact had been created. "Anything else?" is wrong there because the user hasn't asked you to do anything yet — there's nothing to be "else" relative to. Use it ONLY when something tangible just landed.

- **The opening summary turn** (the warmup turn that fires automatically after an email is dropped). Close with a forward-looking question that confirms scope before you act: "Want me to make the map and qchub order, or any tweaks first?" / "Ready to proceed, or do you want to adjust the pricing first?" — NEVER "anything else?".
- **Mid-task progress acknowledgments** ("Creating the map now…", "Submitting the order…"). End with a period. No question. The user will see the system-note when the artifact lands.
- **Clarifying questions you raised yourself** ("Did you mean Standard or Complex for the roundabout?"). The question IS the close — don't tack on a redundant "anything else?".
- **Acknowledgment-only replies** ("Got it, applying $400 to all TMC rows…"). The forthcoming task report will carry the close.

The "anything else?" close is reserved for the moment a DELIVERABLE just confirmed in chat as available to the user. If you're not pointing at a fresh PDF / map link / Outlook draft / applied edit, do not use it.

The close prompt is brief — one sentence after your normal task report. Do NOT summarize what just happened; the user saw it. Just: "[one-line task confirmation]. Anything else I can help with for this estimate?"

**End the session when the user says no.** When the user responds to the "anything else?" prompt with any negative or closing cue, call `end_session` immediately. Cues:
  - Pure negatives: "no", "nope", "no thanks", "no thank you", "nah"
  - Closing affirmations: "we're good", "we're done", "all set", "that's it", "all good", "looks good", "perfect", "good to go", "ship it"
  - Polite closings: "thanks", "thank you" (when standalone, not part of "thanks for the X" mid-flow)

Acknowledge once briefly ("got it" / "all set" / "great") and call `end_session` — do NOT summarize what was done. Do NOT fire end_session if the user's reply is a new task, a follow-up question, or any ambiguous response (e.g., "wait", "hmm", "let me think"). When in doubt, ask one clarifying question rather than guessing.

**PRE-SUBMIT PRICING + SUBTYPE INSTRUCTIONS — APPLY THEM ON THE FIRST ESTIMATE.** The user often tells you pricing OR subtype corrections BEFORE the qchub order runs ("approach counts $200 each", "set TMCs at $400 flat", "the roundabout is Complex", "all 4 are Large"). Today the tools (`set_estimate_rate`, `apply_estimate_rate_to_all_matching`, `set_estimate_subtype`) only work on the LIVE estimate modal, which exists AFTER the order is submitted. Don't make the user re-state these after the estimate appears — handle it in one pass:
1. When the user gives a pre-submit pricing OR subtype instruction, acknowledge it ("Got it — $200 per approach row, will apply once the estimate is up" / "Got it — flipping the roundabout to Complex on the estimate"). Don't forget it; treat it as a pending instruction that survives the order run.
2. The instant the qchub order completes and you have the live edit session (`estimate_pdf_path` shows up in artifacts, signaling the modal is open), apply EVERY pending instruction. Pricing → `apply_estimate_rate_to_all_matching` (or `set_estimate_rate` per-row). Subtype → call `list_estimate_subtype_options` on a matching row once to confirm the exact option text, then `set_estimate_subtype` per matching row. **Batch ALL the per-row tool calls in a SINGLE response** (parallel tool calls), THEN call `re_capture_estimate` ONCE at the end. ONE recap covers the whole batch — do NOT recap between per-row edits (see "BATCH MULTI-ROW ESTIMATE EDITS" below).
3. Only AFTER the batch is applied + the single PDF re-captured do you report to the user — confirming what was applied and that the updated PDF is in Downloads. Do NOT quote a dollar total (see "NEVER quote a dollar total in chat" rule below).
4. If you have NO pending instructions, skip steps 2-3 entirely and just report the v1 PDF as usual.

This pattern eliminates the "Ellen ran the estimate, ignored my pricing/subtype correction, then I had to ask again" loop. Capture and apply, don't capture and forget. Also catches the case flagged 2026-05-25 where the user said "make the roundabout Complex" pre-submit and Ellen acknowledged but didn't apply it — that's a pending subtype instruction the same way "$400 flat" is a pending pricing instruction.

**BATCH MULTI-ROW ESTIMATE EDITS — ONE re_capture per user request, not per row.** When the user asks for any change that affects multiple rows ("all 4 are Large", "set TMCs to $400 each", "the three roundabouts are Complex"):
1. Fire ALL the `set_estimate_subtype` / `set_estimate_rate` calls in the SAME response (parallel tool calls — the runtime executes them concurrently).
2. THEN, in the NEXT response after the per-row tool results come back, call `re_capture_estimate` exactly ONCE to refresh the PDF.
3. Report once with the close prompt ("All 4 TMC rows set to Large, updated PDF saved as _v2. Anything else?").

Do NOT call `re_capture_estimate` between per-row edits in the same logical batch. Each recap costs ~17 seconds + creates a redundant PDF file in Downloads. Observed 2026-05-25 estimate 176596: Ellen made 4 `set_estimate_subtype` calls + 4 `re_capture` calls + a stray reset apply_rate, producing v2.pdf through v6.pdf in ~80s when a single batch + single recap would have done it in ~20s with one clean _v2.pdf. The user explicitly flagged this as inefficient.

When a SUBSEQUENT user request asks for ANOTHER multi-row edit (separate turn, separate intent), repeat the same pattern: batch the per-row calls, single recap, single report. Multiple distinct user requests = multiple batches = multiple recaps. ONE request = ONE batch = ONE recap, regardless of how many rows it touches.

Single-row edits are an obvious special case: ONE set_subtype + ONE recap is still the right pattern (the batch happens to have size 1).

**MyMaps result check**: when the user asks about the map link or you reference a map you created, ALWAYS call `get_artifacts` first. If `mymaps_failed=True`, tell the user the map failed (use the `mymaps_error` message) and ask whether to retry or export a KMZ instead. If `mymaps_share_url` is set, that's the link. If neither is set and `mymaps_in_progress=True`, the map is still being built — tell the user to give it a moment and check back. Never tell the user to "check the MyMaps tab" — artifacts is the source of truth.

**KMZ edit-map re-drop protocol.** When the user reviews the MyMaps map you built and finds pins in the wrong spot, they edit the map in MyMaps, export the layer as KMZ, and drop the file back on Ellen. The drop handler detects the re-drop (via an `ellen_loc_id` marker baked into Ellen's exported placemarks) and fires a synthetic user turn that begins with `[SYSTEM] The user just dropped an edited KMZ`. When you see that message:

1. **Moves and renames are ALREADY APPLIED to the StudyRequest before you see the message.** The handler patched `location.estimate.latitude/longitude` (source flipped to "manual", confidence "high") and overwrote both `site_name` and `address_or_intersection` for renames. Do NOT call any tools to re-apply them — that would either no-op or, worse, overwrite the user's edit with stale data. Just acknowledge what landed.

2. **New pins (pin in KMZ with no `ellen_loc_id`) need user input before you call `add_locations`.** The system message lists each new pin with its coords and any name the user gave it. Ask the user for: study_kind (TMC / tube / survey), subtype (the GROUP-level one — the same field you'd normally set at extraction time), time windows, and which existing group it should join (or whether it's a new group). Then call `add_locations` once per pin with the resolved fields.

3. **Missing pins (in StudyRequest but absent from the re-drop KMZ) need user confirmation before you call `remove_locations`.** The user may have deleted by accident, or hidden a layer in MyMaps without intending to drop the location. Surface each missing site by name and ask "confirm dropping these from the qchub order?" before calling `remove_locations`.

4. **Reply format**: keep it tight, under 8 lines. Lead with what landed ("Applied 2 moves and 1 rename."). Then ask the SPECIFIC question for any pending new/missing pins. Don't echo back the coords — the user just typed them by dragging.

5. **After all pending items resolve**, ask the forward-looking question: "Ready to proceed with the qchub order, or any more tweaks?" — NOT "anything else?" (no artifact has landed; this is a scope-confirm moment, same rule as the opening summary turn).

6. **If a re-drop arrives mid-qchub** (the qchub order is already running or the live edit session is open), still apply the patches but note in your reply that the in-progress order won't pick them up — the user will need to re-run or re-submit. Today's flow doesn't support hot-patching a live qchub session.
"""


# ---------- tool execution ----------

def _location_compact(loc: StudyLocation) -> dict:
    return {
        "site_name": loc.site_name,
        "study_kind": loc.study_kind.value,
        "subtype": (loc.tmc_subtype.value if loc.tmc_subtype else None) or (
            loc.tube_subtype.value if loc.tube_subtype else None
        ),
        "time_windows": [
            {"label": tw.label, "start": tw.start, "end": tw.end} for tw in loc.time_windows
        ],
        "lat_lon": (
            [loc.estimate.latitude, loc.estimate.longitude] if loc.estimate else None
        ),
    }


def _apply_kind_and_subtype(loc: StudyLocation, kind: StudyKind, sub: str) -> Optional[str]:
    """Set kind + matching subtype on a location, clearing the other-kind
    subtypes. Returns None on success, or an error string on invalid subtype.
    """
    if kind == StudyKind.TURNING_MOVEMENT:
        try:
            loc.tmc_subtype = TMCSubtype(sub)
        except ValueError:
            return f"Error: {sub!r} is not a valid TMC subtype (standard|large|complex)."
        loc.tube_subtype = None
        loc.survey_subtype = None
    elif kind == StudyKind.TUBE:
        try:
            loc.tube_subtype = TubeSubtype(sub)
        except ValueError:
            return f"Error: {sub!r} is not a valid tube subtype."
        loc.tmc_subtype = None
        loc.survey_subtype = None
    elif kind == StudyKind.SURVEY:
        try:
            loc.survey_subtype = SurveySubtype(sub)
        except ValueError:
            return f"Error: {sub!r} is not a valid survey subtype (currently only 'vehicular_gap_study')."
        loc.tmc_subtype = None
        loc.tube_subtype = None
    else:
        return f"Error: unknown study_kind {kind.value!r}."
    loc.study_kind = kind
    return None


def _summarize_kmz_attachment(att) -> str:
    """Return a readable placemark dump for a KMZ/KML attachment."""
    from . import kmz
    try:
        pms = kmz.parse_kmz_bytes(att.data) if att.category == "kmz" else kmz.parse_kml_bytes(att.data)
    except Exception as exc:
        return f"Error parsing {att.filename!r}: {exc}"
    if not pms:
        return f"({att.filename!r} contains no placemarks.)"
    lines = [f"[{att.filename}] {len(pms)} placemark(s):"]
    for i, pm in enumerate(pms, 1):
        line = f"  {i}. name={pm.name!r} lat={pm.latitude:.6f} lon={pm.longitude:.6f}"
        if pm.description:
            line += f" desc={pm.description!r}"
        lines.append(line)
    return "\n".join(lines)


_RECAP_COALESCE_SEC = 4.0
_recap_state_lock = threading.Lock()
# Per-session coalesce state. Keyed by `id(qchub_edit_session)` so each
# distinct order's recaps coalesce independently (multi-order future-proof).
# Each entry: {"latest_serial": int, "last_real_result": dict | None,
#              "counter": int, "in_flight_lock": threading.Lock}.
_recap_state: dict[int, dict] = {}


def _auto_recapture(qchub_edit_session) -> dict:
    """Chain a re_capture after an estimate edit so the saved PDF is
    always in sync with the latest edit, BUT coalesce bursts of recap
    requests so a batch of parallel edits produces ONE PDF, not N.

    Why this exists (user direction 2026-05-25): after Ellen made an
    edit she'd often stop and let the user open Estimate_NNNNN.pdf —
    which was the stale pre-edit version because re_capture is a
    separate tool call she didn't always make. Auto-chaining here
    means every edit call atomically refreshes the PDF.

    Why coalesce (user direction 2026-05-25 night): per-edit recap was
    producing v2/v3/v4/v5 PDFs for a 4-row batch in run-20260525-212237
    (~80s wasted on N redundant 17s recaps + Downloads clutter). The
    coalesce strategy: each call gets a serial number; sleeps for
    `_RECAP_COALESCE_SEC`; after the sleep, if a NEWER call superseded
    it, return the most recent cached result without recapturing. Only
    the latest serial in a burst window does the actual recap.

    Net effect:
      - Single edit:        sleep 4s, then recap (one PDF). Cost: ~+4s.
      - 4 parallel edits:   all sleep 4s; one recaps (one PDF), three
                            return the same cached result. Cost: ~+4s
                            total instead of 4 × 17s.
      - 4 sequential edits (Ellen still doing the wrong pattern): each
                            edit still triggers its own recap, but the
                            4s coalesce gives the model time to fire a
                            second edit; if it doesn't (truly sequential
                            with model waiting on result), we degrade to
                            the pre-coalesce per-edit cadence + 4s
                            overhead each. Worst case ≈ status quo.

    Returns the re_capture result (version + pdf_path + ...) or an error
    dict if the recapture failed — the EDIT itself already succeeded by
    the time this runs, so we never raise.
    """
    import time as _time

    session_key = id(qchub_edit_session)
    with _recap_state_lock:
        st = _recap_state.setdefault(session_key, {
            "request_counter": 0,
            "last_recap_at_request": 0,
            "last_real_result": None,
            "in_flight_lock": threading.Lock(),
        })
        st["request_counter"] += 1
        my_request = st["request_counter"]
        in_flight_lock = st["in_flight_lock"]

    # Wait for any further recap requests to arrive in this burst. The
    # edit that triggered THIS call has already been applied to qchub
    # (the dispatcher runs the edit synchronously before calling us),
    # so any recap that runs after this sleep window will pick up the
    # edit regardless of which serial drives it.
    _time.sleep(_RECAP_COALESCE_SEC)

    # Coalesce check #1: did a recap already happen AFTER my request was
    # registered? If so, my edit is included in that recap's result and
    # mine would be redundant. Compare last_recap_at_request to my_request.
    with _recap_state_lock:
        if (
            st["last_recap_at_request"] >= my_request
            and st["last_real_result"] is not None
        ):
            return {**st["last_real_result"], "coalesced": True, "serial": my_request}

    # Acquire the in-flight lock so only ONE actual recap runs at a time
    # per session. Other coalesced callers may already be waiting here.
    with in_flight_lock:
        # Coalesce check #2: re-check after grabbing the lock. The recap
        # we were waiting on (if any) may have finished while we blocked.
        with _recap_state_lock:
            if (
                st["last_recap_at_request"] >= my_request
                and st["last_real_result"] is not None
            ):
                return {**st["last_real_result"], "coalesced": True, "serial": my_request}
        try:
            result = qchub_edit_session.re_capture()
            # Record the CURRENT request_counter (which may be higher than
            # my_request if other threads queued during our work) so any
            # peer waiting in the burst window sees their edit covered.
            with _recap_state_lock:
                st["last_real_result"] = result
                st["last_recap_at_request"] = st["request_counter"]
            return result
        except Exception as exc:
            return {
                "error": (
                    f"Edit applied but auto-recapture failed: "
                    f"{type(exc).__name__}: {exc}. "
                    f"The edit IS live in the qchub modal; the saved PDF "
                    f"is the pre-edit version. User can call "
                    f"re_capture_estimate explicitly to refresh."
                ),
            }


def _scrub_dollars_from_estimate_result(result):
    """Replace dollar amounts in an estimate-tool result with sentinels
    so Ellen literally cannot read (and therefore cannot misquote) them.

    Why this exists (user direction 2026-05-25): Ellen kept quoting
    dollar totals in chat despite the system-prompt rule forbidding
    it. The values arrived in tool results — get_estimate_lines and
    re_capture_estimate both included `unit_price`, `line_total`,
    `quantity`, `total` — and the model couldn't help itself when
    asked "did the rates apply?" Replacing those fields with
    "<see PDF>" makes it impossible for Ellen to be wrong about the
    number; she has to point the user at the PDF.

    Preserves fields Ellen NEEDS for orchestration: description,
    raw_text, line_number, index, pdf_path, version — everything useful
    for matching rows + reporting which version of the PDF is current.
    `line_number` is the 1-based row number Ellen uses to address tools;
    `index` is the legacy 0-based internal counter (kept for back-compat).

    Works on dicts, lists of dicts, and nested structures. No-op on
    primitives + on dicts that don't contain any of the scrubbed keys.
    """
    _SCRUB_KEYS = {"unit_price", "line_total", "quantity", "total", "extra_rate"}
    _SENTINEL = "<see PDF>"

    if isinstance(result, dict):
        out = {}
        for k, v in result.items():
            if k in _SCRUB_KEYS:
                out[k] = _SENTINEL
            else:
                out[k] = _scrub_dollars_from_estimate_result(v)
        return out
    if isinstance(result, list):
        return [_scrub_dollars_from_estimate_result(item) for item in result]
    return result


def execute_tool(
    name: str,
    args: dict,
    state: StudyRequest,
    *,
    on_action_request: Optional[Callable[[str, dict], None]] = None,
    artifacts: Optional[dict] = None,
    qchub_edit_session: Optional[Any] = None,
) -> str:
    """Run a tool against the shared state. Returns the result as a string
    (JSON or human-readable). Errors return an "Error: …" string instead of
    raising — the model recovers from those by trying again or asking the user.

    Action tools (create_mymaps_map / create_qchub_order / export_kmz) don't
    execute in-process — they invoke `on_action_request(name, args)` so the
    main thread can fire the existing UI worker flow (with its confirm dialog,
    progress signals, and result handlers).

    `qchub_edit_session` is the live bridge to the qchub browser tab kept
    open after order creation (Ship 2). The five `*_estimate_*` tools call
    methods on it; if None, those tools tersely explain why they can't run.
    """
    try:
        # GLOBAL GATE: once end_session has fired (artifacts.session_ended
        # is set), refuse all further tool calls in this turn. Forces
        # Ellen to text-respond and exit the loop instead of spinning
        # on tool calls that the user has already declared moot.
        if (
            name != "end_session"
            and artifacts is not None
            and artifacts.get("session_ended")
        ):
            return (
                "Error: the session has already been ended via end_session. "
                "No further tools may be called in this turn. Emit a brief "
                "text acknowledgment now and stop."
            )

        if name in ACTION_TOOL_NAMES:
            if on_action_request is None:
                return f"Error: action tool {name!r} called but no main-thread dispatcher is wired."
            on_action_request(name, args)
            human = {
                "create_mymaps_map": "MyMaps map creation",
                "create_qchub_order": "qchub order creation",
                "export_kmz": "KMZ export",
                "export_kml": "KML export",
                "draft_email_reply": "Outlook draft",
            }[name]
            return f"Started {human}. The user will see the standard confirmation dialog and progress in the status bar."

        # ----- Ship 2: live Estimate-modal edits -----
        ESTIMATE_TOOL_NAMES = {
            "get_estimate_lines", "list_estimate_subtype_options",
            "set_estimate_subtype", "set_estimate_rate",
            "apply_estimate_rate_to_all_matching", "re_capture_estimate",
        }
        if name in ESTIMATE_TOOL_NAMES:
            if qchub_edit_session is None:
                return (
                    "Error: no live qchub edit session — either the order hasn't "
                    "been submitted yet, or the qchub browser was closed. Create "
                    "the order first (via create_qchub_order) and don't close the "
                    "browser window until you're done editing."
                )
            if getattr(qchub_edit_session, "ended", None) and qchub_edit_session.ended.is_set():
                return (
                    "Error: the qchub edit session has ended (browser closed or "
                    "timeout). Re-create the order to start a new editable session."
                )
            try:
                # All return paths funnel through `_scrub_dollars_from_estimate_result`
                # so Ellen NEVER sees the raw dollar amounts (unit_price /
                # line_total / quantity / total / extra_rate). The PDF is
                # the only source of truth she should reference for money.
                # See the helper's docstring for the rationale.
                if name == "get_estimate_lines":
                    lines = qchub_edit_session.get_lines()
                    payload = {"order_id": qchub_edit_session.order_id, "lines": lines}
                    return json.dumps(_scrub_dollars_from_estimate_result(payload), indent=2)
                if name == "list_estimate_subtype_options":
                    # line_number is 1-based (qchub UI); internal API is 0-based.
                    result = qchub_edit_session.list_subtype_options(int(args["line_number"]) - 1)
                    return json.dumps(_scrub_dollars_from_estimate_result(result), indent=2)
                # The three edit tools (set_estimate_subtype,
                # set_estimate_rate, apply_estimate_rate_to_all_matching)
                # automatically chain a re_capture so the PDF in
                # Downloads is always fresh after Ellen makes a change.
                # Without this, Ellen routinely "applies $400 to all
                # rows" and then stops — leaving a stale PDF the user
                # opens expecting updated numbers (observed
                # run-20260524-232317). The auto-recap is coalesced via
                # `_auto_recapture` so a burst of parallel edits collapses
                # to ONE PDF write (run-20260525-212237: 4 edits ->
                # v2/v3/v4/v5 PDFs in ~80s; with coalesce: 1 PDF, ~20s).
                if name == "set_estimate_subtype":
                    # line_number is 1-based (qchub UI); internal API is 0-based.
                    edit = qchub_edit_session.set_subtype(
                        int(args["line_number"]) - 1, str(args["subtype"]),
                    )
                    recap = _auto_recapture(qchub_edit_session)
                    return json.dumps(_scrub_dollars_from_estimate_result(
                        {"edit": edit, "recapture": recap}
                    ), indent=2)
                if name == "set_estimate_rate":
                    # line_number is 1-based (qchub UI); internal API is 0-based.
                    edit = qchub_edit_session.set_rate(
                        int(args["line_number"]) - 1,
                        float(args["unit_price"]),
                        extra_rate=float(args.get("extra_rate", 0.0)),
                    )
                    recap = _auto_recapture(qchub_edit_session)
                    return json.dumps(_scrub_dollars_from_estimate_result(
                        {"edit": edit, "recapture": recap}
                    ), indent=2)
                if name == "apply_estimate_rate_to_all_matching":
                    edit = qchub_edit_session.apply_rate_to_all_matching(
                        str(args["subtype_contains"]),
                        float(args["unit_price"]),
                        extra_rate=float(args.get("extra_rate", 0.0)),
                    )
                    recap = _auto_recapture(qchub_edit_session)
                    return json.dumps(_scrub_dollars_from_estimate_result(
                        {"edit": edit, "recapture": recap}
                    ), indent=2)
                # Native-cog bulk-apply trio. Each takes a single line_number
                # belonging to the target location, sets the rate on that
                # location's cog-bearing row, then picks the scoped menu
                # item. qchub fans the rate out server-side -> ONE Angular
                # cycle, ONE recap, ONE PDF, regardless of row count.
                # Documented in project_qchub_estimate_cog_bulk_apply.md.
                if name in ("apply_rate_to_location", "apply_rate_to_all_locations", "apply_rate_to_rest_of_group"):
                    scope = {
                        "apply_rate_to_location": "location",
                        "apply_rate_to_all_locations": "all",
                        "apply_rate_to_rest_of_group": "rest",
                    }[name]
                    edit = qchub_edit_session.apply_rate_via_cog(
                        int(args["line_number"]) - 1,  # 1-based -> 0-based
                        scope,
                        float(args["unit_price"]),
                        extra_rate=float(args.get("extra_rate", 0.0)),
                    )
                    recap = _auto_recapture(qchub_edit_session)
                    return json.dumps(_scrub_dollars_from_estimate_result(
                        {"edit": edit, "recapture": recap}
                    ), indent=2)
                if name == "re_capture_estimate":
                    # Direct re-capture still available for Ellen to
                    # explicitly trigger (e.g., after the user manually
                    # poked at the modal in visible mode). Edits chain
                    # this automatically — manual call is rarely needed.
                    result = qchub_edit_session.re_capture()
                    return json.dumps(_scrub_dollars_from_estimate_result(result), indent=2)
            except Exception as exc:
                return f"Error from qchub edit session ({name}): {type(exc).__name__}: {exc}"

        if name == "get_artifacts":
            snapshot = dict(artifacts) if artifacts else {}
            # Stringify Path objects so json.dumps doesn't choke.
            cleaned = {k: (str(v) if v is not None else None) for k, v in snapshot.items()}
            return json.dumps(cleaned, indent=2) if cleaned else "{}  (nothing produced this session yet)"

        if name == "end_session":
            # Close the live qchub edit-session browser tab if one is up.
            closed_browser = False
            if qchub_edit_session is not None:
                try:
                    qchub_edit_session.close()
                    closed_browser = True
                except Exception as exc:
                    return (
                        f"Tried to end the session but couldn't close the qchub "
                        f"browser cleanly ({type(exc).__name__}: {exc}). HARD STOP — "
                        f"emit one short text acknowledgment now (e.g., 'all set') "
                        f"and DO NOT call any more tools."
                    )
            if artifacts is not None:
                artifacts["session_ended"] = True
            # CRITICAL: this tool result is a HARD STOP signal to Ellen.
            # Without explicit "stop and don't call more tools" guidance,
            # she'd often go on to call re_capture_estimate, get_artifacts,
            # or other follow-ups — each costing a ~5-15s inference round
            # and looking like the chat is "stuck" from the user's side.
            # Observed 2026-05-22 immediately after this tool shipped:
            # user closed the browser via end_session but Ellen kept
            # spinning ("stuck in ending-session state").
            stop_directive = (
                " HARD STOP: emit one short text acknowledgment now "
                "(e.g., 'all set' or 'got it') and STOP. Do NOT call "
                "any more tools, do NOT summarize the work, do NOT "
                "re-capture the estimate. The session is over."
            )
            if closed_browser:
                return "Session ended. Live qchub browser closed." + stop_directive
            return "Session ended. No live qchub browser was open." + stop_directive

        if name == "get_request":
            # Exclude email_body — it can be multi-KB and shouldn't bloat every
            # state check. Ellen reads it via the dedicated read_email_body tool.
            return state.model_dump_json(exclude_none=True, exclude={"email_body"}, indent=2)

        # ----- attachment / source-doc tools -----
        if name in ("list_attachments", "read_attachment", "get_kmz_placemarks"):
            src = (artifacts or {}).get("source_email_path")
            if not src:
                return (
                    "Error: no source email path on record — this can happen if "
                    "the request was loaded from a non-eml source. read_email_body "
                    "still works for the text body."
                )
            from .parser import parse_email_file
            try:
                parsed = parse_email_file(Path(src))
            except Exception as exc:
                return f"Error: couldn't re-parse the source email at {src!r}: {exc}"
            if name == "list_attachments":
                rows = []
                for a in parsed.attachments:
                    rows.append({
                        "filename": a.filename,
                        "category": a.category,
                        "content_type": a.content_type,
                        "bytes": len(a.data),
                        "inline": getattr(a, "is_inline", False),
                    })
                return json.dumps({"count": len(rows), "attachments": rows}, indent=2)
            if name == "read_attachment":
                target = (args or {}).get("filename", "")
                match = next((a for a in parsed.attachments if a.filename == target), None)
                if match is None:
                    avail = [a.filename for a in parsed.attachments]
                    return f"Error: no attachment named {target!r}. Available: {avail}"
                if match.category == "pdf":
                    try:
                        from . import documents
                        text = documents.pdf_to_text(match.data) if hasattr(documents, "pdf_to_text") else None
                    except Exception as exc:
                        text = None
                        err = exc
                    if text is None:
                        return (
                            f"(PDF {match.filename!r}, {len(match.data)//1024} KB — "
                            "no PDF text extractor available; the LLM extractor "
                            "already received this as a vision-readable document "
                            "during extraction, so its content is already reflected "
                            "in the StudyRequest.)"
                        )
                    return text
                if match.category == "docx":
                    from . import documents
                    try:
                        return documents.docx_to_text(match.data)
                    except Exception as exc:
                        return f"Error extracting docx text: {exc}"
                if match.category in ("kmz", "kml"):
                    return _summarize_kmz_attachment(match)
                if match.category == "image":
                    return (
                        f"(Image {match.filename!r}, {match.content_type}, "
                        f"{len(match.data)//1024} KB — binary, not text. The "
                        "extractor used this as visual context; if you need to "
                        "describe what's in it, ask the user.)"
                    )
                # Plain text or unknown — try a UTF-8 decode.
                try:
                    return match.data.decode("utf-8", errors="replace")
                except Exception as exc:
                    return f"(Attachment {match.filename!r} is binary — {len(match.data)//1024} KB)"
            if name == "get_kmz_placemarks":
                kmz = next((a for a in parsed.attachments if a.category in ("kmz", "kml")), None)
                if kmz is None:
                    return "(No KMZ/KML attached to this email.)"
                return _summarize_kmz_attachment(kmz)

        if name == "get_recent_qchub_run_log":
            from . import config as _cfg
            diag_root = _cfg.app_data_dir() / "qchub-diagnostics"
            if not diag_root.exists():
                return "(No qchub-diagnostics folder yet — no runs have happened.)"
            runs = sorted(
                (p for p in diag_root.iterdir() if p.is_dir() and p.name.startswith("run-")),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if not runs:
                return "(No qchub run directories found.)"
            log_path = runs[0] / "run.log"
            if not log_path.exists():
                return f"(Most recent run {runs[0].name!r} has no run.log yet.)"
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                return f"Error reading run.log: {exc}"
            full = bool((args or {}).get("full"))
            if full:
                return f"[{runs[0].name}/run.log]\n{text}"
            tail_lines = text.splitlines()[-100:]
            return f"[{runs[0].name}/run.log — tail 100 lines]\n" + "\n".join(tail_lines)

        if name == "open_url":
            url = str((args or {}).get("url", "")).strip()
            if not url:
                return "Error: no URL provided."
            if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file:///")):
                return f"Error: refused to open non-http/https/file URL: {url!r}"
            import webbrowser
            try:
                ok = webbrowser.open(url, new=2)  # new=2 opens in a new tab if possible
                return f"Opened {url} in the default browser." if ok else f"webbrowser.open returned False for {url}"
            except Exception as exc:
                return f"Error opening {url}: {exc}"

        if name == "read_email_body":
            body = (state.email_body or "").strip()
            if not body:
                return (
                    "(no email body captured — this can happen on the KMZ-only "
                    "fallback path when LLM extraction failed; otherwise "
                    "indicates a parser issue worth flagging)"
                )
            return body

        if name == "validate_for_qchub":
            issues: list[dict] = []
            if not state.locations:
                issues.append({"severity": "error", "message": "No locations in the request."})
            for i, loc in enumerate(state.locations):
                missing: list[str] = []
                if not loc.time_windows:
                    missing.append("time_windows (qchub requires at least one per study group)")
                if loc.study_kind == StudyKind.TURNING_MOVEMENT and loc.tmc_subtype is None:
                    missing.append("tmc_subtype")
                elif loc.study_kind == StudyKind.TUBE and loc.tube_subtype is None:
                    missing.append("tube_subtype")
                elif loc.study_kind == StudyKind.SURVEY and loc.survey_subtype is None:
                    missing.append("survey_subtype")
                if loc.estimate is None:
                    missing.append("coordinates (no estimate)")
                if missing:
                    issues.append({
                        "severity": "error",
                        "index": i,
                        "site_name": loc.site_name,
                        "missing": missing,
                    })
            top_level: list[str] = []
            if not state.client_company:
                top_level.append("client_company")
            if not state.client_contact_email:
                top_level.append("client_contact_email")
            if top_level:
                issues.append({
                    "severity": "warning",
                    "message": f"Missing top-level fields: {top_level}. qchub may still accept the order but downstream actions can struggle.",
                })
            report = {
                "ok": not any(i.get("severity") == "error" for i in issues),
                "location_count": len(state.locations),
                "issue_count": len(issues),
                "issues": issues,
            }
            return json.dumps(report, indent=2)

        if name == "list_locations":
            return json.dumps(
                [{"index": i, **_location_compact(loc)} for i, loc in enumerate(state.locations)],
                indent=2,
            )

        if name == "update_request_field":
            field = args["field"]
            setattr(state, field, args["value"])
            return f"Updated {field} to {args['value']!r}."

        if name == "update_location":
            idx = args["index"]
            if not (0 <= idx < len(state.locations)):
                return f"Error: location index {idx} out of range (have {len(state.locations)})."
            field = args["field"]
            setattr(state.locations[idx], field, args["value"])
            return f"Updated location[{idx}].{field} to {args['value']!r}."

        if name == "set_location_kind_and_subtype":
            idx = args["index"]
            if not (0 <= idx < len(state.locations)):
                return f"Error: location index {idx} out of range."
            kind = StudyKind(args["study_kind"])
            sub = args["subtype"]
            loc = state.locations[idx]
            err = _apply_kind_and_subtype(loc, kind, sub)
            if err:
                return err
            return f"Set location[{idx}] to {kind.value} / {sub}."

        if name == "set_location_time_windows":
            idx = args["index"]
            if not (0 <= idx < len(state.locations)):
                return f"Error: location index {idx} out of range."
            try:
                windows = [TimeWindow(**w) for w in args["windows"]]
            except ValidationError as exc:
                return f"Error: {exc.errors()[0]['msg']}"
            state.locations[idx].time_windows = windows
            return f"Set {len(windows)} time window(s) on location[{idx}]."

        if name == "set_global_time_windows":
            try:
                windows = [TimeWindow(**w) for w in args["windows"]]
            except ValidationError as exc:
                return f"Error: {exc.errors()[0]['msg']}"
            for loc in state.locations:
                loc.time_windows = [TimeWindow(**w.model_dump()) for w in windows]
            return f"Applied {len(windows)} time window(s) to all {len(state.locations)} location(s)."

        if name == "bulk_set_subtype":
            kind = StudyKind(args["filter_kind"])
            sub = args["subtype"]
            count = 0
            for loc in state.locations:
                if loc.study_kind != kind:
                    continue
                if kind == StudyKind.TURNING_MOVEMENT:
                    try:
                        loc.tmc_subtype = TMCSubtype(sub)
                    except ValueError:
                        return f"Error: {sub!r} is not a valid TMC subtype."
                    loc.tube_subtype = None
                else:
                    try:
                        loc.tube_subtype = TubeSubtype(sub)
                    except ValueError:
                        return f"Error: {sub!r} is not a valid tube subtype."
                    loc.tmc_subtype = None
                count += 1
            return f"Set subtype={sub} on {count} {kind.value} location(s)."

        if name == "remove_locations":
            raw_indices = args.get("indices") or []
            unique = sorted({int(i) for i in raw_indices})
            n = len(state.locations)
            out_of_range = [i for i in unique if not (0 <= i < n)]
            if out_of_range:
                return (
                    f"Error: indices out of range: {out_of_range} "
                    f"(have {n} locations, valid range 0..{n - 1})."
                )
            if not unique:
                return "No-op: removed 0 location(s) (empty index list)."
            removed_names = [state.locations[i].site_name for i in unique]
            for i in reversed(unique):  # delete highest-first to preserve lower indices
                del state.locations[i]
            return (
                f"Removed {len(unique)} location(s): {removed_names}. "
                f"{len(state.locations)} location(s) remain."
            )

        if name == "add_locations":
            items = args.get("locations") or []
            if not items:
                return "No-op: empty locations list."
            added: list[dict] = []
            failed: list[dict] = []
            for item in items:
                site_name = item.get("site_name", "?")
                address = item.get("address_or_intersection", "")
                try:
                    kind = StudyKind(item["study_kind"])
                except ValueError as exc:
                    failed.append({"site_name": site_name, "reason": f"invalid study_kind: {exc}"})
                    continue
                try:
                    geo = geocoder.geocode(address)
                except geocoder.GeocoderUnavailable as exc:
                    failed.append({"site_name": site_name, "reason": str(exc)})
                    continue
                except geocoder.GeocodingError as exc:
                    failed.append({"site_name": site_name, "reason": f"geocoder error: {exc}"})
                    continue
                if geo is None:
                    failed.append({
                        "site_name": site_name,
                        "reason": f"geocoder returned no results for {address!r} after trying phrasing variants",
                    })
                    continue
                try:
                    tws = [TimeWindow(**w) for w in (item.get("time_windows") or [])]
                except ValidationError as exc:
                    failed.append({"site_name": site_name, "reason": str(exc.errors()[0]["msg"])})
                    continue
                loc = StudyLocation(
                    site_name=site_name,
                    raw_text=item.get("raw_text") or address,
                    address_or_intersection=address,
                    study_kind=kind,
                    time_windows=tws,
                    estimate=LocationEstimate(
                        latitude=geo.latitude,
                        longitude=geo.longitude,
                        confidence=geo.confidence,
                        source="geocoded",
                        notes=f"Geocoder matched: {geo.formatted_address}",
                    ),
                )
                err = _apply_kind_and_subtype(loc, kind, item["subtype"])
                if err:
                    failed.append({"site_name": site_name, "reason": err})
                    continue
                state.locations.append(loc)
                added.append({
                    "site_name": site_name,
                    "confidence": geo.confidence,
                    "lat_lon": [geo.latitude, geo.longitude],
                })
            summary: dict = {
                "added": len(added),
                "failed": len(failed),
                "total_locations_now": len(state.locations),
            }
            if added:
                summary["successes"] = added
            if failed:
                summary["failures"] = failed
            return json.dumps(summary, indent=2)

        if name == "set_location_kind_and_subtype_for_indices":
            raw_indices = args.get("indices") or []
            unique = sorted({int(i) for i in raw_indices})
            n = len(state.locations)
            out_of_range = [i for i in unique if not (0 <= i < n)]
            if out_of_range:
                return f"Error: indices out of range: {out_of_range} (have {n} locations)."
            if not unique:
                return "No-op: empty index list."
            kind = StudyKind(args["study_kind"])
            sub = args["subtype"]
            for i in unique:
                err = _apply_kind_and_subtype(state.locations[i], kind, sub)
                if err:
                    return err  # abort on first error; partial mutation possible (caller can re-call)
            return (
                f"Set {len(unique)} location(s) at indices {unique} "
                f"to {kind.value} / {sub}."
            )

        if name == "set_location_time_windows_for_indices":
            raw_indices = args.get("indices") or []
            unique = sorted({int(i) for i in raw_indices})
            n = len(state.locations)
            out_of_range = [i for i in unique if not (0 <= i < n)]
            if out_of_range:
                return f"Error: indices out of range: {out_of_range} (have {n} locations)."
            if not unique:
                return "No-op: empty index list."
            try:
                windows = [TimeWindow(**w) for w in args["windows"]]
            except ValidationError as exc:
                return f"Error: {exc.errors()[0]['msg']}"
            for i in unique:
                # Each location gets its own copy of the TimeWindow objects so
                # later per-location edits don't bleed across locations.
                state.locations[i].time_windows = [
                    TimeWindow(**w.model_dump()) for w in windows
                ]
            return (
                f"Set {len(windows)} time window(s) on {len(unique)} location(s) "
                f"at indices {unique}."
            )

        if name == "set_location_time_windows_by_kind":
            kind = StudyKind(args["study_kind"])
            try:
                windows = [TimeWindow(**w) for w in args["windows"]]
            except ValidationError as exc:
                return f"Error: {exc.errors()[0]['msg']}"
            matching_indices: list[int] = []
            for i, loc in enumerate(state.locations):
                if loc.study_kind == kind:
                    loc.time_windows = [TimeWindow(**w.model_dump()) for w in windows]
                    matching_indices.append(i)
            if not matching_indices:
                return (
                    f"No-op: no locations with study_kind={kind.value!r}. "
                    f"Total locations: {len(state.locations)}."
                )
            return (
                f"Set {len(windows)} time window(s) on {len(matching_indices)} "
                f"{kind.value} location(s) at indices {matching_indices}."
            )

        return f"Error: unknown tool {name!r}"
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"


# ---------- response serialization ----------

# Fields the SDK attaches to response blocks that the API rejects when those
# blocks are echoed back in `messages` history. As of anthropic 0.101.0 the
# main offender is `parsed_output` on text blocks (SDK-side parsed JSON);
# strip anything that doesn't belong in an input content block.
_BLOCK_FIELDS_TO_STRIP = {"parsed_output"}


def _serialize_block(block) -> dict:
    """Convert a response content block into a dict safe to send back to the
    API in messages history. Drops SDK-internal fields and None values.
    """
    d = block.model_dump()
    for f in _BLOCK_FIELDS_TO_STRIP:
        d.pop(f, None)
    return {k: v for k, v in d.items() if v is not None}


# ---------- chat turn (streaming, with tool loop) ----------

def run_chat_turn(
    user_message: str,
    history: list[dict],
    state: StudyRequest,
    *,
    on_text_delta: Callable[[str], None],
    on_tool_result: Callable[[str, str], None],
    on_action_request: Optional[Callable[[str, dict], None]] = None,
    artifacts: Optional[dict] = None,
    api_key: Optional[str] = None,
    model_chain: Optional[list[str]] = None,
    qchub_edit_session: Optional[Any] = None,
) -> list[dict]:
    """Send `user_message`, run the assistant→tools loop, stream text deltas via
    `on_text_delta`. Each tool execution reports through `on_tool_result(name, summary)`.

    `model_chain` is an ordered list of model IDs to try in succession on
    transient errors (overloaded / rate-limit / 5xx / connection). When one
    model exhausts its per-model retries, we fall back to the next. Default
    chain: 'auto' preference (Sonnet → Opus → Haiku). For a single-model
    deployment, pass `[<model_id>]` (no fallback).

    Action tools (create_mymaps_map etc.) invoke `on_action_request(name, args)`
    so the main thread can fire the existing worker flow.

    Returns the new history list (caller stores it to pass back next turn).
    """
    client = Anthropic(api_key=api_key or get_api_key())
    chain = model_chain or MODELS[DEFAULT_MODEL_PREFERENCE]
    multi_model = len(chain) > 1
    # In single-model mode, give the one model a full retry budget. In
    # multi-model mode, fail fast on each model so the whole chain stays
    # within reasonable wall time (worst case 2+4 = 6s per model × 3 = ~20s).
    per_model_attempts = RETRY_ATTEMPTS if not multi_model else 3

    history = history + [{"role": "user", "content": user_message}]

    base_kwargs = dict(
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=TOOLS,
    )

    for _round in range(MAX_TOOL_ROUNDS):
        # Try each model in the chain. For each model, retry on transient
        # errors with exponential backoff. On retry-exhaustion for the
        # current model, fall back to the next model in the chain. On a
        # non-retryable error (auth, validation, etc.) re-raise immediately.
        # Partial text emitted before a fail is already in the chat panel;
        # in practice transient errors fire BEFORE the first stream chunk
        # so duplication is rare, but it can happen.
        response: Optional[object] = None
        last_exc: Optional[BaseException] = None
        for model_idx, model in enumerate(chain):
            is_last_model = (model_idx == len(chain) - 1)
            for attempt in range(1, per_model_attempts + 1):
                try:
                    # Streaming: tokens stream as Claude generates them, so
                    # the user sees Ellen typing in real time via
                    # on_text_delta. The stream context manager returns the
                    # final Message via get_final_message() on exit.
                    import time as _time
                    from . import trace_log
                    _t_start = _time.monotonic()
                    _first_token_recorded = False
                    with client.messages.stream(
                        model=model,
                        messages=history,
                        timeout=PER_REQUEST_TIMEOUT,
                        **base_kwargs,
                    ) as stream:
                        for delta in stream.text_stream:
                            if delta:
                                # Trace first-token-in latency — quantifies the gap
                                # between API call start and Anthropic actually
                                # streaming, separate from total stream wall time.
                                if not _first_token_recorded:
                                    trace_log.event(
                                        "chat.first_token",
                                        duration_ms=int((_time.monotonic() - _t_start) * 1000),
                                        model=model, tool_round=_round, attempt=attempt,
                                    )
                                    _first_token_recorded = True
                                on_text_delta(delta)
                        response = stream.get_final_message()
                    trace_log.event(
                        "chat.stream_finalized",
                        duration_ms=int((_time.monotonic() - _t_start) * 1000),
                        model=model, tool_round=_round, attempt=attempt,
                        first_token_recorded=_first_token_recorded,
                    )
                    # Record usage to the global JSONL log (one record
                    # per chat turn / tool round). Best-effort — never
                    # break the chat flow on a record-write hiccup.
                    try:
                        from . import usage_tracker
                        usage_tracker.record(
                            phase="chat",
                            model=model,
                            usage_obj=getattr(response, "usage", None),
                            duration_ms=int((_time.monotonic() - _t_start) * 1000),
                            meta={"tool_round": _round, "attempt": attempt},
                        )
                    except Exception:
                        pass
                    break  # success on this model — exit retry loop
                except BaseException as exc:
                    last_exc = exc
                    if not _retryable_api_error(exc):
                        raise  # non-recoverable
                    # Timeout on this model = strong signal it's overloaded.
                    # Don't waste same-model retries; fall forward to the
                    # next model in the chain immediately. Same shape as
                    # extractor._stream_with_retry post 2026-05-18 PM.
                    is_timeout = isinstance(exc, anthropic.APITimeoutError)
                    is_last_attempt = (attempt == per_model_attempts)
                    if is_timeout or is_last_attempt:
                        try:
                            if is_timeout and not is_last_model:
                                on_text_delta(
                                    f"\n({_short_model_name(model)} timed out — "
                                    f"switching to {_short_model_name(chain[model_idx + 1])}…)\n"
                                )
                        except Exception:
                            pass
                        log.warning(
                            "Chat API call to %s failed (attempt %d/%d): %s — %s",
                            model, attempt, per_model_attempts, exc,
                            ("falling forward to next model" if is_timeout else "moving on"),
                        )
                        break
                    delay = RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                    log.warning(
                        "Chat API call to %s failed (attempt %d/%d): %s — retrying in %.1fs",
                        model, attempt, per_model_attempts, exc, delay,
                    )
                    try:
                        on_text_delta(
                            f"\n({_short_model_name(model)} busy — retrying in {delay:.0f}s…)\n"
                        )
                    except Exception:
                        pass
                    time.sleep(delay)
            if response is not None:
                break  # success — stop trying alternative models
            # All retries exhausted for this model; try the next if any
            if not is_last_model:
                next_model = chain[model_idx + 1]
                log.warning(
                    "Falling back from %s to %s after %d failed attempts",
                    model, next_model, per_model_attempts,
                )
                try:
                    on_text_delta(
                        f"\n({_short_model_name(model)} unavailable — switching to "
                        f"{_short_model_name(next_model)}…)\n"
                    )
                except Exception:
                    pass
        if response is None:
            # All models in chain exhausted retries.
            assert last_exc is not None
            raise last_exc

        assistant_blocks = [_serialize_block(b) for b in response.content]
        history.append({"role": "assistant", "content": assistant_blocks})

        tool_results: list[dict] = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(
                    block.name, block.input, state,
                    on_action_request=on_action_request,
                    artifacts=artifacts,
                    qchub_edit_session=qchub_edit_session,
                )
                on_tool_result(block.name, result)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

        if not tool_results:
            break
        history.append({"role": "user", "content": tool_results})
    else:
        on_tool_result("(system)", f"Tool loop exceeded {MAX_TOOL_ROUNDS} rounds — stopping.")

    return history
