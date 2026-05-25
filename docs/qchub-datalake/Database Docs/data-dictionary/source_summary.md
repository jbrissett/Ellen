# source_summary — Data Dictionary

## Purpose
Canonical 5-minute summaries of source data (movement and speed), QA metrics, and Area-of-Concern intervals.

## Tables
### source_summary.summarized_data_interval
**Purpose:** Time windows per file (partitioned by interval_start) anchoring summary rows.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| summarized_data_interval_id | serial4 | yes |  | no |  | Identity |
| file_id | int4 |  |  | no |  | Source file id |
| interval_start | timestamp | yes |  | no |  | Interval start |
| interval_end | timestamp |  |  | no |  | Interval end |
| created_at | timestamp |  |  | yes | now() | Audit |

**Constraints & Indexes**
- PK: summarized_data_interval_pkey (summarized_data_interval_id, interval_start)
- Indexes (ONLY): summarized_data_interval_file_id_idx(file_id), t_sdi_brin_interval(brin interval_start), t_sdi_file_start(file_id, interval_start)
- Partitioned by RANGE(interval_start)

**Business Logic Notes**
- Canonical 5‑minute windows for all summaries; upstream sources rebucketed as needed. Refs: docs/readme.md

**Operational Notes**
- BRIN index supports fast scans by time; consider pg_partman for rolling partition maintenance.

### source_summary.volume_by_movement
**Purpose:** Per-interval movement volumes with optional class/bank breakdowns.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| volume_by_movement_id | serial4 | yes |  | no |  | Identity |
| summarized_data_interval_id | int4 |  | summarized_data_interval | no |  | SDI id |
| interval_start | timestamp |  |  | no |  | Interval start |
| in_gate | varchar(10) |  |  | yes |  | Optional in gate |
| out_gate | varchar(10) |  |  | yes |  | Optional out gate |
| movement | varchar(25) |  |  | yes |  | Movement label |
| volume_count | int4 |  |  | yes |  | Total volume |
| volume_count_by_class | jsonb |  |  | yes |  | Per-class |
| volume_count_by_bank | jsonb |  |  | yes |  | Per-bank |

**Constraints & Indexes**
- PK: volume_by_movement_pkey (volume_by_movement_id)
- FK: (summarized_data_interval_id, interval_start) → source_summary.summarized_data_interval ON DELETE CASCADE
- Indexes: vbm_sdi_interval_movement (sdi, interval_start, movement), volume_by_movement_summarized_data_interval_id_idx (sdi, interval_start)

**Business Logic Notes**
- Movement label reflects roadway config conventions when available; per‑class JSON uses datalens_alias keys. Refs: docs/json_schemas.md, docs/readme.md

**Operational Notes**
- Typical queries aggregate by interval_start and movement; use GIN on JSONB if adding filters later.

### source_summary.volume_by_speed
**Purpose:** Per-interval per-mph counts with optional movement and class/bank breakdowns.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| id | serial4 | yes |  | no |  | Identity |
| summarized_data_interval_id | int4 |  | summarized_data_interval | no |  | SDI id |
| interval_start | timestamp |  |  | no |  | Interval start |
| speed_mph | int4 |  |  | no |  | MPH |
| in_gate | varchar(10) |  |  | yes |  | Optional in gate |
| out_gate | varchar(10) |  |  | yes |  | Optional out gate |
| movement | varchar(25) |  |  | yes |  | Optional movement |
| vehicle_count | int4 |  |  | no |  | Vehicles at mph |
| vehicle_count_by_class | jsonb |  |  | yes |  | Per-class |
| vehicle_count_by_bank | jsonb |  |  | yes |  | Per-bank |

**Constraints & Indexes**
- PK: volume_by_speed_pkey (id)
- FK: (summarized_data_interval_id, interval_start) → source_summary.summarized_data_interval ON DELETE CASCADE
- Indexes: vbs_sdi_interval_movement (sdi, interval_start, movement, speed_mph), volume_by_speed_summarized_data_interval_id_idx (sdi, interval_start)

**Business Logic Notes**
- 0‑mph excluded from percentile metrics but included in counts; movement may be null for midblock overall. Refs: docs/readme.md

**Operational Notes**
- Typical queries pull banded histograms per interval or compute percentiles per movement.

### source_summary.qa_metrics
**Purpose:** QA metrics per interval, including confidences and percentiles (overall and per movement).

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| qc_metrics_id | serial4 | yes |  | no |  | Identity |
| summarized_data_interval_id | int4 |  |  | no |  | SDI id |
| interval_start | timestamp |  |  | no |  | Interval start |
| avg_detection_confidence | numeric |  |  | yes |  | Overall detection confidence |
| avg_classification_confidence | numeric |  |  | yes |  | Overall classification confidence |
| avg_detection_confidence_by_class | jsonb |  |  | yes |  | Per-class detection |
| avg_classification_confidence_by_class | jsonb |  |  | yes |  | Per-class classification |
| in_extrapolations | int4 |  |  | yes |  | In extrapolations |
| out_extrapolations | int4 |  |  | yes |  | Out extrapolations |
| avg_between_gate_confidence | numeric |  |  | yes |  | Between-gate confidence |
| speed_15th_percentile | numeric |  |  | yes |  | P15 speed |
| speed_50th_percentile | numeric |  |  | yes |  | Median speed |
| speed_85th_percentile | numeric |  |  | yes |  | P85 speed |
| avg_detection_confidence_by_movement | jsonb |  |  | yes |  | Per-movement detection |
| avg_classification_confidence_by_movement | jsonb |  |  | yes |  | Per-movement classification |
| in_extrapolations_by_movement | jsonb |  |  | yes |  | Per-movement in extrapolations |
| out_extrapolations_by_movement | jsonb |  |  | yes |  | Per-movement out extrapolations |
| between_gate_conf_by_movement | jsonb |  |  | yes |  | Per-movement between-gate confidence |
| speed_15th_percentile_by_movement | jsonb |  |  | yes |  | Per-movement P15 |
| speed_50th_percentile_by_movement | jsonb |  |  | yes |  | Per-movement median |
| speed_85th_percentile_by_movement | jsonb |  |  | yes |  | Per-movement P85 |
| num_extrapolations | int4 |  |  | yes |  | Extrapolations count |
| num_extrapolations_by_movement | jsonb |  |  | yes |  | Per-movement extrapolations count |

**Constraints & Indexes**
- PK: qa_metrics_pkey (qc_metrics_id)
- Indexes: qa_sdi_interval (summarized_data_interval_id, interval_start)

**Business Logic Notes**
- Contains movement‑level JSON metrics for charts in Ops‑Center (confidence, extrapolations, percentiles). Refs: docs/readme.md

**Operational Notes**
- Consider retention policy aligned with preview history; heavy reads are chart‑driven per session window.

### source_summary.aoc
**Purpose:** Area-of-Concern: flags a problematic time range across intervals for a given file/version.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| aoc_id | serial4 | yes |  | no |  | Identity |
| file_id | int4 |  |  | no |  | File id |
| version | int4 |  |  | no |  | AOC version |
| start_summarized_data_interval_id | int4 |  |  | no |  | Start SDI id |
| start_interval_start | timestamp |  |  | no |  | Start interval start |
| end_summarized_data_interval_id | int4 |  |  | no |  | End SDI id |
| end_interval_start | timestamp |  |  | no |  | End interval start |
| created_at | timestamptz |  |  | yes | now() | Created |
| created_by | varchar(255) |  |  | no |  | Author |
| reason_tags | jsonb |  |  | yes | '[]' | Reasons |
| action_tags | jsonb |  |  | yes | '[]' | Actions |
| qa_status | varchar(50) |  |  | yes | 'Open' | Status |
| has_attachments | bool |  |  | yes | false | Attachments flag |
| is_active | bool |  |  | yes | true | Active flag |

**Constraints & Indexes**
- PK: aoc_pkey (aoc_id)
- Indexes: aoc_file_id_idx (file_id, version)

**Business Logic Notes**
- QA marks AOCs to guide analysts in excluding or investigating ranges prior to assembly. Refs: docs/readme.md

**Operational Notes**
- AOCs may be used to auto‑suggest rule excludes; maintain clear tagging and notes.

### source_summary.aoc_notes
**Purpose:** Notes attached to an AOC.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| aoc_note_id | serial4 | yes |  | no |  | Identity |
| aoc_id | int4 |  | source_summary.aoc | no |  | AOC id |
| note_time | timestamptz |  |  | yes | now() | Timestamp |
| note_by | varchar(255) |  |  | no |  | Author |
| note_text | text |  |  | no |  | Note |

**Constraints & Indexes**
- PK: aoc_notes_pkey (aoc_note_id)
- FK: aoc_id → source_summary.aoc(aoc_id)

**Business Logic Notes**
- Records collaborative QA context over an AOC’s lifecycle. Refs: docs/readme.md

**Operational Notes**
- Keep notes concise and actionable; timestamps assist in audit trails.

## Enums
- None

## Views
- None

## Functions
- None

## Triggers
- None

## Sequences
- summarized_data_interval_summarized_data_interval_id_seq
- volume_by_movement_volume_by_movement_id_seq
- volume_by_speed_id_seq
- aoc_aoc_id_seq
- aoc_notes_aoc_note_id_seq
- qa_metrics_qc_metrics_id_seq

## Schema Relationships
- References: Internal FK to summarized_data_interval from volume tables; aoc_notes → aoc
- Referenced by: assembly (reads for preview computation); aoc used by QA workflows and can inform rule scoping

## Diagrams
- See ERD: `db/schemas/source_summary/qc_datalake_rds_dev_db - source_summary.png`

## References
- `docs/readme.md`
- `docs/json_schemas.md`

## Open Questions
- Confirm whether movement is always scalar in volume_by_speed for all sources.
