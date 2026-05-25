from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class StudyKind(str, Enum):
    TURNING_MOVEMENT = "turning_movement"
    TUBE = "tube"
    SURVEY = "survey"  # qchub's third Study Type, used for gap studies, occupancy, parking, etc.


class TMCSubtype(str, Enum):
    STANDARD = "standard"
    LARGE = "large"
    COMPLEX = "complex"


class TubeSubtype(str, Enum):
    """qchub Tube Counts > Study Type options (observed 2026-05-17 screenshot).

    These are the GROUP-LEVEL subtypes shown in the Add Study Group modal's
    Study Type dropdown when 'Tube Counts' is selected. Each becomes a
    separate Study Group in qchub.

    Distinct from the ESTIMATE-MODAL subtype (granular pricing variants
    like 'Volume Radar Count' vs 'Volume Video Count 4+ Lanes') — those
    are set per-line in the estimate modal post-submit, not at group-create
    time. See `project_qchub_data_model.md` for the two-layer model.
    """
    VIDEO_ATR_VOLUME = "video_atr_volume"     # 'Video ATR - Volume' (ATR video volume counts)
    VOLUME = "volume"                          # default tube
    VOLUME_CLASS = "volume_class"
    VOLUME_SPEED = "volume_speed"
    VOLUME_SPEED_CLASS = "volume_speed_class"


class SurveySubtype(str, Enum):
    """qchub Survey > Service Subtype options — full dropdown captured from
    screenshots 2026-05-17 PM. These are the GROUP-LEVEL Service Subtype
    values in the Add Study Group modal when 'Survey' is selected.

    IMPORTANT: Survey-type estimate rows can ONLY adjust price on the estimate
    modal — not subtype. The subtype is fixed at group-create time. (For Tube
    and TMC groups, subtype CAN be refined per-row on the estimate modal.)

    CUSTOM_VIDEO_SURVEY and CUSTOM_NON_VIDEO_SURVEY both surface a
    'Custom Survey' name input in the group modal — provide via
    `StudyLocation.survey_custom_name` (validated bidirectionally below).
    """
    BLUETOOTH_SURVEY = "bluetooth_survey"
    DATAPOINT_SUBSCRIPTION = "datapoint_subscription"
    DELAY_STUDY = "delay_study"
    EQUIPMENT_RENTAL = "equipment_rental"
    FLOATING_CAR_TRAVEL_TIME = "floating_car_travel_time"
    HANDHELD_RADAR_SURVEY = "handheld_radar_survey"
    HISTORICAL_DATA = "historical_data"
    HORIZONTAL_CURVE_ADVISORY_SPEED = "horizontal_curve_advisory_speed"
    INTERVIEW_SURVEY = "interview_survey"
    LICENSE_PLATE_OD = "license_plate_od"
    OCCUPANCY_SURVEY = "occupancy_survey"
    PARKING_STUDY = "parking_study"
    PEDESTRIAN_VOLUME = "pedestrian_volume"
    QUEUE_STUDY = "queue_study"
    ROAD_INVENTORY = "road_inventory"
    SATURATION_FLOW_RATE = "saturation_flow_rate"
    SUPPORT_SERVICES = "support_services"
    TRANSIT_SURVEY = "transit_survey"
    VEHICULAR_GAP_STUDY = "vehicular_gap_study"   # 'gap analysis' in emails
    VIDEO_SURVEILLANCE = "video_surveillance"     # second-most-common video survey type
    CUSTOM_NON_VIDEO_SURVEY = "custom_non_video_survey"  # requires custom name
    CUSTOM_VIDEO_SURVEY = "custom_video_survey"          # requires custom name


EstimateSource = Literal["kmz", "vision", "text_only", "geocoded", "manual", "unknown"]
EstimateConfidence = Literal["high", "medium", "low"]


class LocationEstimate(BaseModel):
    """A latitude/longitude guess for a study location, with provenance."""
    latitude: float
    longitude: float
    confidence: EstimateConfidence = Field(
        description=(
            "high = exact (KMZ, or vision-matched aerial); "
            "medium = well-known intersection in a named city; "
            "low = obscure, partial info — needs user review"
        )
    )
    source: EstimateSource = Field(
        description="Where the coordinates came from: kmz file, image vision, text knowledge, manual edit, or unknown."
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional note, e.g. 'matched KMZ placemark Site 1' or 'identified driveway south of signal in aerial'.",
    )


class TimeWindow(BaseModel):
    label: str = Field(description="Human label like 'AM peak' or 'PM peak' or '72-hour count'")
    start: str = Field(description="Start time, 24h HH:MM")
    end: str = Field(description="End time, 24h HH:MM")
    total_hours: Optional[int] = Field(
        default=None,
        description=(
            "For multi-day tube counts (e.g. 72-hour or 1-week), set this to the "
            "total duration in hours. Overrides the start/end computation when "
            "qchub's Tube form is being filled (which uses Duration + Days/Hours). "
            "Single-day windows (peak hours, full-day TMCs) leave this None."
        ),
    )
    raw_text: Optional[str] = None
    flag: Optional[str] = Field(
        default=None,
        description="Set if the literal email text looks contradictory or impossible.",
    )


class StudyLocation(BaseModel):
    site_name: str = Field(description="Concise label for the map pin")
    raw_text: str = Field(description="Exact text from the email describing this location")
    address_or_intersection: str = Field(
        description="Plain-English form suitable for MyMaps search, e.g. 'Street A and Street B, City, State'."
    )
    study_kind: StudyKind
    tmc_subtype: Optional[TMCSubtype] = None
    tube_subtype: Optional[TubeSubtype] = None
    survey_subtype: Optional[SurveySubtype] = None
    # When survey_subtype == CUSTOM_VIDEO_SURVEY, qchub's group modal
    # surfaces a 'Custom Survey' name input that becomes the line item's
    # description. Provide a short, human-readable name (e.g. 'Pedestrian
    # crossing count', 'Queue spillback observation'). None for the other
    # survey subtypes (they're self-named by qchub).
    survey_custom_name: Optional[str] = None
    time_windows: list[TimeWindow] = Field(default_factory=list)
    study_dates: Optional[str] = None

    estimate: Optional[LocationEstimate] = Field(
        default=None,
        description=(
            "Best-effort lat/lon for the UI preview map. None means we couldn't "
            "place it confidently — user must drop a pin manually in the UI."
        ),
    )

    # Survey subtypes that surface qchub's 'Custom Survey' NAME input
    # in the group modal. Both require + permit `survey_custom_name`;
    # all other survey subtypes forbid it.
    _CUSTOM_NAME_SUBTYPES = (
        SurveySubtype.CUSTOM_VIDEO_SURVEY,
        SurveySubtype.CUSTOM_NON_VIDEO_SURVEY,
    )

    @model_validator(mode="after")
    def _validate_survey_custom_name(self) -> "StudyLocation":
        # Whitespace-only names normalize to None so the layer-consistency
        # check below has one canonical "no name" state.
        if self.survey_custom_name is not None and not self.survey_custom_name.strip():
            self.survey_custom_name = None

        is_custom = self.survey_subtype in self._CUSTOM_NAME_SUBTYPES
        has_name = self.survey_custom_name is not None

        if is_custom and not has_name:
            raise ValueError(
                f"survey_custom_name is required when survey_subtype is "
                f"{self.survey_subtype.value!r} (qchub's group modal needs a line description)."
            )
        if not is_custom and has_name:
            subtype_label = self.survey_subtype.value if self.survey_subtype else None
            raise ValueError(
                f"survey_custom_name must be None when survey_subtype is "
                f"{subtype_label!r}; it is only valid for CUSTOM_VIDEO_SURVEY "
                "or CUSTOM_NON_VIDEO_SURVEY."
            )
        return self


class StudyRequest(BaseModel):
    email_subject: str
    email_from: str
    email_to: str
    email_cc: Optional[str] = None
    email_date: Optional[datetime] = None

    # Full cleaned body text of the client's email. Preserved so the chat
    # assistant (Ellen) can re-read it when the extractor missed details —
    # e.g., when the email body says "all 4 approaches at each intersection"
    # but the extractor only synthesized intersection-level pins. Excluded
    # from the routine `get_request` dump to keep chat context tight; Ellen
    # reads it via the dedicated `read_email_body` tool.
    email_body: Optional[str] = None

    client_company: Optional[str] = None
    client_contact_name: Optional[str] = None
    client_contact_email: Optional[str] = None
    client_contact_phone: Optional[str] = None
    client_project_number: Optional[str] = None
    jurisdiction: Optional[str] = None

    # Sender's company mailing address — pulled from the signature block at
    # extraction time. Used by qchub auto-create when the client isn't yet
    # in qchub. All fields optional; if any are missing, auto-create falls
    # back to a regex parse of the email_body, then to the legacy
    # "manual add" modal.
    client_company_address_1: Optional[str] = None
    client_company_address_2: Optional[str] = None
    client_company_city: Optional[str] = None
    client_company_state: Optional[str] = None
    client_company_zip: Optional[str] = None

    locations: list[StudyLocation]

    notes: Optional[str] = None
    has_kmz_attachment: bool = False
    has_aerial_image: bool = False

    @property
    def total_locations(self) -> int:
        return len(self.locations)


class EstimateLine(BaseModel):
    """One row from a qchub order's estimate. Field set follows the data-
    model memory (see `project_qchub_data_model.md` EstimateLine table):
    each line is one (location × time period × study-type subtype) and
    carries the priced quantity + total. Some fields may be missing — the
    raw text fallback lets the caller surface the line verbatim when
    parsing is incomplete.
    """
    description: Optional[str] = Field(
        default=None,
        description="Combined human-readable label: study type + subtype + when. "
                    "Example: 'Turn Count -- Standard — Midweek 07:00AM-9:00AM (2hrs)'.",
    )
    unit_price: Optional[float] = None
    quantity: Optional[float] = None
    line_total: Optional[float] = None
    raw_text: Optional[str] = Field(
        default=None,
        description="Verbatim line text scraped from qchub's estimate table. "
                    "Kept as a fallback when structured fields couldn't be parsed.",
    )


class Estimate(BaseModel):
    """qchub-generated estimate for a submitted order.

    Captured by clicking PREVIEW on the order detail page after SUBMIT
    REQUEST. We keep the full rendered HTML as a diagnostic fallback
    (always trustworthy) AND a best-effort structured parse into
    EstimateLines (useful for in-chat presentation). When the parser
    can't decompose the HTML cleanly, the lines list may be empty but
    the html_path still points to the saved rendered estimate.
    """
    order_id: str = Field(description="qchub order ID this estimate is for.")
    order_url: Optional[str] = Field(
        default=None,
        description="Direct link to the order on qchub (Admin/Orders/{id}).",
    )
    lines: list[EstimateLine] = Field(
        default_factory=list,
        description="Parsed line items from the priced estimate, in display order.",
    )
    total: Optional[float] = Field(
        default=None,
        description="Grand total across all lines, if rendered by qchub.",
    )
    html_path: Optional[str] = Field(
        default=None,
        description="Local path to the saved estimate HTML snapshot. "
                    "Always populated even if parsing failed.",
    )
    screenshot_path: Optional[str] = Field(
        default=None,
        description="Local path to the saved estimate screenshot.",
    )
    pdf_path: Optional[str] = Field(
        default=None,
        description="Local path to the estimate PDF downloaded from qchub's "
                    "PREVIEW button. None if the download didn't fire within "
                    "the timeout — the HTML/screenshot fallbacks still work.",
    )
    parse_note: Optional[str] = Field(
        default=None,
        description="If parsing was imperfect, a short explanation of what got "
                    "captured vs not. Empty/None on clean parse.",
    )
