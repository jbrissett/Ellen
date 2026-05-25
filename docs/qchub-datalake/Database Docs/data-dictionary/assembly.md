# assembly — Data Dictionary

## Purpose
Workspace and artifacts for Count Assembly: orders, locations, sessions, selection rules, preview outputs, QA flags, and publishing readiness.

## Tables
### assembly.order_assembly
**Purpose:** Order header synchronized from QC-Hub; tracks progress and bin-scheme defaults.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| order_no | text | yes |  | no |  | Order number |
| project_name | text |  |  | yes |  | Project name |
| company_id | int8 |  |  | yes |  | Company id |
| company_name | text |  |  | yes |  | Company name |
| office_id | int8 |  |  | yes |  | Office id |
| office_name | text |  |  | yes |  | Office name |
| order_date | timestamp |  |  | yes |  | Order date |
| desired_delivery_date | timestamp |  |  | yes |  | Desired delivery date |
| default_midblock_bin_scheme_id | int8 |  |  | yes |  | Default midblock bank scheme |
| default_tmc_bin_scheme_id | int8 |  |  | yes |  | Default TMC bank scheme |
| locations_total | int4 |  |  | yes | 0 | Count of locations |
| sitecodes_total | int4 |  |  | yes | 0 | Count of sitecodes |
| percent_complete | numeric(5,2) |  |  | yes | 0 | Progress percent |
| qchub_order_id | int8 |  |  | yes |  | Upstream id |
| qchub_last_sync_at | timestamptz |  |  | yes |  | Sync timestamp |
| created_at | timestamptz |  |  | no | now() | Audit |
| updated_at | timestamptz |  |  | no | now() | Audit |

**Constraints & Indexes**
- PK: order_assembly_pkey (order_no)

**Relationships**
- Referenced by: location_grid, order_corridor, order_location, view v_order_progress

**Business Logic Notes**
- Sourced from QC‑Hub post‑approval; progress computed via session statuses. Refs: docs/readme.md

**Operational Notes**
- Defaults for bin schemes guide session targets; sync timestamps enable idempotent updates.

### assembly.order_note
**Purpose:** Free-form notes at order or location scope.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| order_no | text | yes | order_assembly | no |  | Order number |
| scope | text | yes |  | no |  | Scope key |
| order_location_id | int8 |  |  | yes |  | Optional location id |
| note_text | text |  |  | no |  | Note |
| created_by | text |  |  | yes |  | Author |
| created_at | timestamptz | yes |  | no | now() | Created |

**Constraints & Indexes**
- PK: order_note_pkey (order_no, scope, created_at)

**Business Logic Notes**
- Used by analysts to coordinate assembly/QA activities per order/location scope. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Append‑only by created_at in PK; scope can segment by feature area (e.g., 'qa', 'notes').

### assembly.order_location
**Purpose:** Locations under an order; includes study types and defaults.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| order_no | text | yes | order_assembly | no |  | Order number |
| order_location_id | int8 | yes |  | no |  | Location id |
| location_status | text |  |  | yes |  | Status |
| location_name | text |  |  | no |  | Name |
| city | text |  |  | yes |  | City |
| state_code | text |  |  | yes |  | State |
| latitude | numeric |  |  | yes |  | Latitude |
| longitude | numeric |  |  | yes |  | Longitude |
| study_type | assembly.study_type |  |  | yes |  | Study type |
| default_midblock_bin_scheme_id | int8 |  |  | yes |  | Default midblock bank scheme |
| default_tmc_bin_scheme_id | int8 |  |  | yes |  | Default TMC bank scheme |
| has_any_files | bool |  |  | yes | false | Any files flag |
| sitecodes_count | int4 |  |  | yes | 0 | Sitecodes count |
| collection_site_id | int8 |  |  | yes |  | Link to index_schema.collection_sites |
| note | text |  |  | yes |  | Notes |
| created_at | timestamptz |  |  | no | now() | Audit |
| updated_at | timestamptz |  |  | no | now() | Audit |
| default_collection_date | date |  |  | yes |  | Default collection date |
| default_requested_bucket_minutes | int4 |  |  | yes |  | Default requested bucket |

**Constraints & Indexes**
- PK: order_location_pkey (order_no, order_location_id)
- FK: order_no → assembly.order_assembly(order_no) ON DELETE CASCADE
- Indexes: order_location_has_any_files_idx, order_location_location_status_idx, order_location_order_no_idx

**Business Logic Notes**
- May link to `index_schema.collection_sites` for map/anchor context; study_type influences movement set. Refs: docs/readme.md

**Operational Notes**
- Has_any_files/sitecodes_count accelerate UI; defaults propagate to sessions if unset.

### assembly.order_location_sitecode
**Purpose:** Sitecodes per location with scheduling and defaults; ties to session.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| order_no | text | yes | order_location | no |  | Order number |
| order_location_id | int8 | yes |  | no |  | Location id |
| sitecode | text | yes |  | no |  | Sitecode |
| start_time | time |  |  | yes |  | Start time |
| end_time | time |  |  | yes |  | End time |
| duration_hours | numeric(6,2) |  |  | yes |  | Duration hours |
| days_json | jsonb |  |  | yes |  | Days scheduling |
| assembly_session_id | uuid |  |  | yes |  | Linked session |
| has_files | bool |  |  | yes | false | Any files flag |
| created_at | timestamptz |  |  | no | now() | Audit |
| updated_at | timestamptz |  |  | no | now() | Audit |
| target_bin_scheme_id | int4 |  |  | yes |  | Target bank scheme |
| target_requested_minutes | int2 |  |  | yes |  | Target bucket |
| initialized_at | timestamptz |  |  | yes |  | Initialized at |
| initialized_by | text |  |  | yes |  | Who |

**Constraints & Indexes**
- PK: order_location_sitecode_pkey (order_no, order_location_id, sitecode)
- FK: (order_no, order_location_id) → assembly.order_location ON DELETE CASCADE
- Indexes: order_location_sitecode_has_files_idx, order_location_sitecode_sitecode_idx

**Business Logic Notes**
- Target bin scheme and requested minutes guide session defaults. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Assembly session linkage is optional until initialized; sitecode granularity aligns with publishing.

### assembly.assembly_session
**Purpose:** Working session (order/sitecode/time window) for building preview and publishing.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| assembly_session_id | uuid | yes |  | no | gen_random_uuid() | Identity |
| order_no | text |  |  | no |  | Order |
| location_id | int8 |  |  | yes |  | Optional location ref |
| sitecode_id | int8 |  |  | no |  | Sitecode id |
| status | assembly.assembly_status |  |  | no | 'draft' | Session status |
| requested_bucket_minutes | int2 |  |  | yes |  | Requested bucket |
| title | text |  |  | yes |  | Title |
| notes | text |  |  | yes |  | Notes |
| source_filter_hint | jsonb |  |  | yes |  | Hint for source picking |
| created_by | text |  |  | no |  | Author |
| created_at | timestamptz |  |  | no | now() | Audit |
| updated_at | timestamptz |  |  | no | now() | Audit |
| interval_start | timestamp |  |  | no |  | Start |
| interval_end | timestamp |  |  | no |  | End |
| order_location_id | int8 |  |  | yes |  | Location id (redundant) |
| sitecode | text |  |  | yes |  | Sitecode text |
| target_bin_scheme_id | int4 |  | ops_config.bin_schemes | yes |  | Target bank scheme |
| count_data_type | assembly.count_data_type |  |  | yes |  | Movement/Speed |
| study_type | assembly.study_type |  |  | yes |  | Study type |
| include_pedestrians | bool |  |  | no | false | Seed ped movements |
| include_rtor | bool |  |  | no | true | Include RTOR movements |
| direction_axis | text |  |  | yes |  | 'ns' or 'ew' for midblock |
| target_speed_bins | jsonb |  |  | yes |  | Target speed bins |
| directional_pedestrians | bool |  |  | no | false | Directional crosswalks |

**Constraints & Indexes**
- PK: assembly_session_pkey (assembly_session_id)
- Check: direction_axis in ('ns','ew') when set
- FK: (order_no, order_location_id, sitecode) → assembly.order_location_sitecode
- Index: ix_asm_session_status(status)

**Business Logic Notes**
- Controls assembler behavior (bucket, movement seeding, ped/RTOR flags) and mapping target. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Use consistent requested_bucket_minutes to reduce re‑bucketing; speed bins optional for speed path.

### assembly.manual_override
**Purpose:** Analyst overrides at a cell (movement/speed×interval) for a session.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| manual_override_id | uuid | yes |  | no | gen_random_uuid() | Identity |
| assembly_session_id | uuid |  | assembly.assembly_session | no |  | Session id |
| count_data_type | assembly.count_data_type |  |  | no |  | movement/speed |
| movement | text |  |  | yes |  | Movement |
| speed_mph | int4 |  |  | yes |  | Speed mph |
| volume_count | int4 |  |  | yes |  | Volume override |
| volume_count_by_class | jsonb |  |  | yes |  | Legacy per-class |
| vehicle_count | int4 |  |  | yes |  | Speed count |
| vehicle_count_by_class | jsonb |  |  | yes |  | Per-class speed |
| category_breakdown | jsonb |  |  | yes |  | Target breakdown |
| reason_code | assembly.override_reason |  |  | no |  | Reason |
| note | text |  |  | yes |  | Note |
| created_by | text |  |  | no |  | Author |
| created_at | timestamptz |  |  | no | now() | Audit |
| updated_at | timestamptz |  |  | no | now() | Audit |
| interval_start | timestamp |  |  | no |  | Interval start |

**Constraints & Indexes**
- PK: manual_override_pkey (manual_override_id)
- FK: assembly_session_id → assembly.assembly_session ON DELETE CASCADE
- Indexes: ix_override_json (gin category_breakdown), ix_override_movement (movement)

**Business Logic Notes**
- Overrides replace assembled values and are captured in lineage `applied_overrides`. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Keep overrides minimal and documented; prefer fixing upstream rules/sources.

### assembly.qa_flag
**Purpose:** QA flags within a session, scoped by movement or category key and time range.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| qa_flag_id | uuid | yes |  | no | gen_random_uuid() | Identity |
| assembly_session_id | uuid |  | assembly.assembly_session | no |  | Session id |
| flag_type | assembly.qa_flag_type |  |  | no |  | Flag type |
| movement | text |  |  | yes |  | Movement |
| class_or_bank_key | text |  |  | yes |  | Category key |
| status | assembly.qa_status |  |  | no | 'open' | Status |
| reason_tags | text[] |  |  | yes |  | Reason tags |
| action_tags | text[] |  |  | yes |  | Action tags |
| has_attachments | bool |  |  | no | false | Attachments |
| note | text |  |  | yes |  | Note |
| created_by | text |  |  | no |  | Author |
| created_at | timestamptz |  |  | no | now() | Created |
| updated_at | timestamptz |  |  | no | now() | Updated |
| resolved_by | text |  |  | yes |  | Resolver |
| resolved_at | timestamptz |  |  | yes |  | Resolved time |
| interval_range | tsrange |  |  | yes |  | Time range |

**Constraints & Indexes**
- PK: qa_flag_pkey (qa_flag_id)
- FK: assembly_session_id → assembly.assembly_session ON DELETE CASCADE
- Index: ix_qaflag_session_status(assembly_session_id, status)

**Business Logic Notes**
- Flags guide investigation and can drive rule excludes; link to source rows via qa_flag_link when possible. Refs: docs/readme.md

**Operational Notes**
- Use reason/action tagging for structured QA workflows; attachments noted via has_attachments.

### assembly.qa_flag_link
**Purpose:** Optional links from flags to specific source rows (file/sdi/movement/speed).

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| qa_flag_id | uuid |  | assembly.qa_flag | no |  | QA flag id |
| file_id | int8 |  |  | yes |  | File id |
| summarized_data_interval_id | int8 |  |  | yes |  | SDI id |
| interval_start | timestamptz |  |  | yes |  | Interval start |
| movement | text |  |  | yes |  | Movement |
| speed_mph | int4 |  |  | yes |  | Speed mph |

**Constraints & Indexes**
- FK: qa_flag_id → assembly.qa_flag(qa_flag_id) ON DELETE CASCADE

**Business Logic Notes**
- Strengthens traceability between QA findings and raw inputs. Refs: docs/readme.md

**Operational Notes**
- Partial links (some columns null) are allowed to capture best‑effort mapping.

### assembly.preview_snapshot
**Purpose:** Snapshot header for preview outputs for a session.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| assembly_session_id | uuid | yes | assembly.assembly_session | no |  | Session id |
| snapshot_seq | int4 | yes |  | no |  | Snapshot sequence |
| generated_at | timestamptz |  |  | no | now() | Generated time |
| derived_by | text |  |  | yes |  | Assembler tag |
| bucket_minutes | int2 |  |  | yes |  | Snapshot bucket |
| target_bin_scheme_id | int4 |  | ops_config.bin_schemes | yes |  | Target bank scheme |

**Constraints & Indexes**
- PK: preview_snapshot_pkey (assembly_session_id, snapshot_seq)
- FKs: assembly_session_id → assembly.assembly_session ON DELETE CASCADE; target_bin_scheme_id → ops_config.bin_schemes(id)
- Index: ix_prev_snap_session_seq

**Business Logic Notes**
- Snapshot isolates a coherent run of the assembler; target_bin_scheme_id echoes mapping context. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Use derived_by to indicate assembler version for debugging/repro.

### assembly.preview_lineage
**Purpose:** Contributors, rules, and overrides per lineage key within a snapshot.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| assembly_session_id | uuid | yes |  | no |  | Session id |
| snapshot_seq | int4 | yes |  | no |  | Snapshot seq |
| lineage_key | uuid | yes |  | no |  | Lineage key |
| contributors | jsonb |  |  | no |  | Source rows/weights |
| applied_rules | jsonb |  |  | no |  | Rule ids |
| applied_overrides | jsonb |  |  | no |  | Override ids |

**Constraints & Indexes**
- PK: preview_lineage_pkey (assembly_session_id, snapshot_seq, lineage_key)
- FK: (assembly_session_id, snapshot_seq) → assembly.preview_snapshot ON DELETE CASCADE
- Index: ix_prev_lineage_json (gin contributors jsonb_path_ops)

**Business Logic Notes**
- Enables deterministic hash for delivery and complete traceability to inputs and decisions. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Favor compact contributor objects (file_id, sdi_id, interval_start, movement, proportion).

### assembly.preview_volume_by_movement
**Purpose:** Cell-level movement preview values per interval and movement.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| assembly_session_id | uuid |  | assembly.preview_snapshot | no |  | Session id |
| snapshot_seq | int4 |  |  | no |  | Snapshot seq |
| movement | text |  |  | no |  | Movement |
| volume_count | int4 |  |  | no |  | Total volume |
| volume_count_by_class | jsonb |  |  | yes |  | Legacy per-class |
| category_dimension | assembly.category_dimension |  |  | no | 'bank' | Target dimension |
| bank_schema_id | int8 |  | ops_config.bin_schemes | yes |  | Bank scheme |
| category_breakdown | jsonb |  |  | yes |  | Class/bank breakdown |
| lineage_key | uuid |  |  | no | gen_random_uuid() | Lineage key |
| interval_start | timestamp |  |  | no |  | Interval start |
| is_missing | bool |  |  | no | true | Was baseline missing |

**Constraints & Indexes**
- Unique: uq_prev_mv_cell (assembly_session_id, snapshot_seq, interval_start, movement)
- Check: preview_vbm_bank_guard (requires bank_schema_id when category_dimension='bank')
- FK: (assembly_session_id, snapshot_seq) → assembly.preview_snapshot ON DELETE CASCADE; bank_schema_id → ops_config.bin_schemes
- Index: ix_prev_mv_session_seq_t0 (session_id, snapshot_seq, interval_start)

**Business Logic Notes**
- category_breakdown holds final class/bank keys post‑mapping and filtering; is_missing indicates baseline vs filled cells. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Enforce consistent bank schema per cell; mixed schemas must be split by rules.

### assembly.preview_volume_by_speed
**Purpose:** Cell-level speed preview values per mph (and optional movement).

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| assembly_session_id | uuid |  | assembly.preview_snapshot | no |  | Session id |
| snapshot_seq | int4 |  |  | no |  | Snapshot seq |
| movement | text |  |  | no | '~' | Movement or '~' overall |
| speed_mph | int4 |  |  | no |  | MPH |
| vehicle_count | int4 |  |  | no |  | Vehicle count |
| vehicle_count_by_class | jsonb |  |  | yes |  | Per-class speed |
| category_dimension | assembly.category_dimension |  |  | no | 'bank' | Target dimension |
| bank_schema_id | int8 |  | ops_config.bin_schemes | yes |  | Bank scheme |
| category_breakdown | jsonb |  |  | yes |  | Class/bank breakdown |
| lineage_key | uuid |  |  | no | gen_random_uuid() | Lineage key |
| interval_start | timestamp |  |  | no |  | Interval start |
| is_missing | bool |  |  | no | true | Was baseline missing |
| speed_bin | jsonb |  |  | yes |  | Target speed bin object |

**Constraints & Indexes**
- Unique: uq_prev_sp_cell (assembly_session_id, snapshot_seq, interval_start, movement, speed_mph)
- Check: preview_vbs_bank_guard (requires bank_schema_id when category_dimension='bank')
- FK: (assembly_session_id, snapshot_seq) → assembly.preview_snapshot ON DELETE CASCADE; bank_schema_id → ops_config.bin_schemes
- Index: ix_prev_sp_session_seq_t0

**Business Logic Notes**
- speed_bin persists the target bin definition for reproducibility; '~' movement denotes overall. Refs: docs/count_assembly_readme.md

**Operational Notes**
- 0‑mph policy mirrors source_summary rules; document in assembler notes if overridden.

### assembly.selection_rule
**Purpose:** Rule definitions for selecting and combining sources into preview cells.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| selection_rule_id | uuid | yes |  | no | gen_random_uuid() | Identity |
| assembly_session_id | uuid |  | assembly.assembly_session | no |  | Session id |
| count_data_type | assembly.count_data_type |  |  | no |  | movement/speed |
| movements | text[] |  |  | yes |  | Filter movements |
| interval_mode | assembly.interval_mode |  |  | no | 'range' | session/range/specific |
| interval_predicate | jsonb |  |  | yes |  | Predicate for selection |
| category_dimension | assembly.category_dimension |  |  | no | 'none' | Target dim hint |
| bank_schema_id | int8 |  | ops_config.bin_schemes | yes |  | Target bank scheme |
| category_include | text[] |  |  | yes |  | Include keys |
| category_exclude | text[] |  |  | yes |  | Exclude keys |
| rule_action | assembly.rule_action |  |  | no | 'include' | include/merge/formula/exclude |
| source_bindings | jsonb |  |  | no |  | File weights array |
| formula | jsonb |  |  | yes |  | Formula e.g. {"scale":0.98} |
| priority | int4 |  |  | no | 100 | Priority (higher wins) |
| note | text |  |  | yes |  | Note |
| created_by | text |  |  | no |  | Author |
| created_at | timestamptz |  |  | no | now() | Created |
| updated_at | timestamptz |  |  | no | now() | Updated |
| interval_range | tsrange |  |  | yes |  | Range |
| interval_list | tsrange[] |  |  | yes |  | Discrete ranges |

**Constraints & Indexes**
- PK: selection_rule_pkey (selection_rule_id)
- Check: selection_rule_category_guard (bank_schema_id required when category_dimension='bank' with filters)
- FKs: assembly_session_id → assembly.assembly_session ON DELETE CASCADE; bank_schema_id → ops_config.bin_schemes(id)
- Indexes: ix_rule_bindings_gin (jsonb_path_ops), ix_rule_cat_dim, ix_rule_movements_gin, ix_rule_session_pri

**Business Logic Notes**
- Last‑rule‑wins by priority; category filters apply after mapping; supports include, weighted merge, and formula scaling. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Normalize weights to 1.0 within each cell; avoid overlapping excludes/includes with same priority.

### assembly.selection_rule_diff
**Purpose:** Audit diffs for rule changes.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| selection_rule_id | uuid |  | assembly.selection_rule | no |  | Rule id |
| changed_at | timestamptz |  |  | no | now() | When changed |
| changed_by | text |  |  | no |  | Who |
| diff | jsonb |  |  | no |  | JSON diff |

**Constraints & Indexes**
- FK: selection_rule_id → assembly.selection_rule(selection_rule_id) ON DELETE CASCADE

**Business Logic Notes**
- Captures JSON patch‑like changes for review and rollback. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Consider pruning low‑value diffs once published.

### Other tables
- location_grid, location_grid_edge, location_grid_flag — grid and linkage metadata across locations (FKs to location_grid, order_location)
- order_corridor — named corridors per order (axis NS/EW)
- order_location_corridor — links locations to corridors (axis override)
- order_location_street — ordered street names per location
- corridor_segment — geometric segments between locations for corridors
- location_grid_node — grid nodes per location within a grid

Business Logic Notes
- TODO: Refactor grid/corridor tables into roadway_config; keep minimal maintenance until migration.

Operational Notes
- Avoid adding new dependencies to grid/corridor tables pending refactor.

## Enums
- assembly.assembly_status — draft, qa_in_progress, ready_to_publish, published, archived
- assembly.category_dimension — none, class, bank
- assembly.count_data_type — movement, speed, both
- assembly.flag_severity — info, warn, error
- assembly.interval_mode — specific, range, predicate
- assembly.leg — N, S, E, W
- assembly.link_dir — NB, SB, EB, WB
- assembly.link_origin — auto, manual
- assembly.override_reason — fix_noise, backfill, balance_adj, other
- assembly.qa_flag_type — no_data, empty_interval, low_interval, high_interval, impossible_movement, suspicious_movement, suspicious_classification, other
- assembly.qa_status — open, investigating, resolved, wont_fix
- assembly.rule_action — include, exclude, weighted_merge, formula_adjusted
- assembly.source_type — single, weighted_merge, exclude
- assembly.study_type — tmc, midblock, survey, other

## Views
- assembly.v_order_progress — summarizes percent complete by order based on session statuses

## Functions
- assembly.compute_preview_snapshot(session_id, assembler_tag) — materializes preview snapshot rows

## Triggers
- See checks and guards on preview and selection_rule tables; none explicitly declared as triggers beyond DDL checks

## Sequences
- assembly.order_corridor_corridor_id_seq — default for order_corridor.corridor_id

## Schema Relationships
- References: ops_config.bin_schemes (target_bin_scheme_id, bank_schema_id); internal FKs across order/location/sitecode/session; selection rules and preview reference session; QA flags link to session
- Referenced by: delivery.published_count(assembly_session_id)

## Diagrams
- See ERD: `db/schemas/assembly/qc_datalake_rds_dev_db - assembly.png`

## References
- `docs/readme.md`
- `docs/count_assembly_readme.md`
- `docs/json_schemas.md`

## Open Questions
- None for listed tables; grid subsystem documentation can be expanded as needed.
