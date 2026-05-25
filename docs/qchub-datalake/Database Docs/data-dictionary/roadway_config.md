# roadway_config — Data Dictionary

## Purpose
Canonical roadway configuration (versioned) for approaches, lanes, and movements tied to roadway anchors.

## Tables
### roadway_config.configuration
**Purpose:** Root entity for a versioned roadway configuration at an anchor.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| configuration_id | uuid | yes |  | no |  | Identity |
| anchor_id | uuid |  | index_schema.roadway_anchor | no |  | Anchor id |
| site_kind | roadway_config.rc_site_kind |  |  | no |  | Site kind |
| junction_style | roadway_config.rc_junction_style |  |  | no | 'standard' | Junction style |
| version | int4 |  |  | no |  | Version per anchor |
| edit_revision | int4 |  |  | no | 0 | Edit revision counter |
| status | roadway_config.rc_status |  |  | no |  | Draft/Review/Published/Archived |
| effective_start_ts | timestamptz |  |  | no |  | Effective start |
| effective_end_ts | timestamptz |  |  | yes |  | Effective end |
| directionality | roadway_config.rc_directionality |  |  | yes |  | One/two-way |
| has_center_left_turn_lane | bool |  |  | yes |  | CLTL presence |
| has_median | bool |  |  | yes |  | Median presence |
| notes | text |  |  | yes |  | Notes |
| created_by | text |  |  | yes |  | Author |
| centerline_geom | geometry(linestring,4326) |  |  | yes |  | Centerline |
| period | tstzrange (generated) |  |  | yes |  | Generated period |

**Constraints & Indexes**
- PK: configuration_pkey (configuration_id)
- Unique: uq_config_anchor_version (anchor_id, version)
- Check: chk_config_time_order (effective_end_ts > effective_start_ts if not null)
- Indexes: ix_config_anchor_status_start (anchor_id, status, effective_start_ts desc)
- FKs: anchor_id → index_schema.roadway_anchor(anchor_id) ON DELETE RESTRICT
- Triggers: trg_block_update_published_config (blocks edits when published)

**Business Logic Notes**
- Draft→review→published lifecycle; version pinned for delivery reproducibility. Refs: docs/readme.md

**Operational Notes**
- Use effective period and status for point‑in‑time resolution of movement semantics.

### roadway_config.approach
**Purpose:** Approaches within a configuration (tied to taxonomy roads).

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| approach_id | uuid | yes |  | no |  | Identity |
| configuration_id | uuid |  | roadway_config.configuration | no |  | Configuration |
| road_id | int8 |  | taxonomy.road | no |  | Road id |
| dir_cardinal | roadway_config.rc_dir_cardinal |  |  | yes |  | Direction label |
| label | text |  |  | no |  | Approach label |
| bearing_deg | int4 |  |  | yes |  | Bearing degrees |
| speed_limit_mph | int2 |  |  | yes |  | Speed limit |
| control_type | roadway_config.rc_control |  |  | no |  | Control type |
| has_channelized_right | bool |  |  | no | false | Channelized right turn |

**Constraints & Indexes**
- PK: approach_pkey (approach_id)
- Indexes: ix_approach_config(configuration_id), ix_approach_road(road_id)
- FKs: configuration_id → roadway_config.configuration ON DELETE CASCADE; road_id → taxonomy.road ON DELETE RESTRICT
- Triggers: block inserts/updates/deletes on published configurations

**Business Logic Notes**
- Encodes control type and speed limit that inform movement naming/QA context. Refs: docs/readme.md

**Operational Notes**
- Keep approach bearings consistent with anchor orientation; ensure road_id matches taxonomy.road.

### roadway_config.lane
**Purpose:** Lanes within an approach in left-to-right order.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| lane_id | uuid | yes |  | no |  | Identity |
| approach_id | uuid |  | roadway_config.approach | no |  | Approach |
| idx_from_left | int4 |  |  | no |  | Index from left |
| lane_kind | roadway_config.rc_lane_kind |  |  | no |  | Lane kind |
| width_m | numeric |  |  | yes |  | Width meters |
| pocket_length_m | numeric |  |  | yes |  | Pocket length |

**Constraints & Indexes**
- PK: lane_pkey (lane_id)
- Unique: uq_lane_order_per_approach (approach_id, idx_from_left)
- Indexes: ix_lane_approach(approach_id)
- FK: approach_id → roadway_config.approach
- Triggers: block inserts/updates/deletes on published configurations

**Business Logic Notes**
- Lane kinds translate to allowable movements and impact ped counting logic. Refs: docs/readme.md

**Operational Notes**
- Maintain unique ordering per approach; widths help with future visualization.

### roadway_config.movement
**Purpose:** Movements between approaches with protected/permitted flags.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| movement_id | uuid | yes |  | no |  | Identity |
| configuration_id | uuid |  | roadway_config.configuration | no |  | Config id |
| from_approach_id | uuid |  | roadway_config.approach | no |  | From approach |
| to_approach_id | uuid |  | roadway_config.approach | yes |  | To approach (null for midblock) |
| movement_type | roadway_config.rc_move |  |  | no |  | left/thru/right/uturn |
| is_protected | bool |  |  | no | false | Protected phase |
| is_permitted | bool |  |  | no | false | Permitted phase |
| on_red_allowed | bool |  |  | yes |  | Right-on-red etc. |

**Constraints & Indexes**
- PK: movement_pkey (movement_id)
- Unique: uq_mv_from_to_type (configuration_id, from_approach_id, to_approach_id, movement_type)
- Partial Unique: uq_mv_from_type_approach_only (configuration_id, from_approach_id, movement_type) WHERE to_approach_id IS NULL
- Indexes: ix_movement_config(configuration_id), ix_movement_from_approach(from_approach_id)
- FKs: configuration_id → roadway_config.configuration; from_approach_id/to_approach_id → roadway_config.approach
- Triggers: trg_movement_same_config, trg_validate_movement_flags, block edits on published configs

**Business Logic Notes**
- Movement set underpins movement labels used in summaries and assembly grid. Refs: docs/readme.md

**Operational Notes**
- Validate that from/to approach belong to same configuration; leverage triggers to keep flags consistent.

### roadway_config.audit_log
**Purpose:** Audit trail of changes per configuration version and edit revision.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| configuration_id | uuid | yes | roadway_config.configuration | no |  | Config id |
| version | int4 | yes |  | no |  | Version |
| edit_revision | int4 | yes |  | no |  | Revision |
| changed_at | timestamptz | yes |  | no | now() | When changed |
| changed_by | text |  |  | yes |  | Who |
| reason | text |  |  | yes |  | Why |
| diff_schema_id | text |  |  | no | 'audit.diff.v1' | Diff schema id |
| diff | jsonb |  |  | no |  | Diff payload |

**Constraints & Indexes**
- PK: audit_log_pkey (configuration_id, version, edit_revision, changed_at)
- FK: configuration_id → roadway_config.configuration

**Business Logic Notes**
- Captures human and automated changes; diff schema allows forward compatibility. Refs: docs/readme.md

**Operational Notes**
- Use for change review and rollback planning; consider pruning strategies by status.

## Enums
- rc_control: signal, stop_all, stop_minor, yield, uncontrolled
- rc_dir_cardinal: NB, SB, EB, WB, NEB, NWB, SEB, SWB, FWD, REV
- rc_directionality: oneway, twoway
- rc_junction_style: standard, roundabout
- rc_lane_kind: general, left, thru, right, left_thru, thru_right, left_thru_right, uturn, bike, bus, hov, shoulder, left_right
- rc_move: left, thru, right, uturn
- rc_site_kind: intersection, midblock
- rc_status: draft, review, published, archived

## Views
- None

## Functions
- Roadway-config triggers and validators (see DDL)

## Triggers
- Block edits on published configurations; validate movement flags and same-config constraints

## Sequences
- None

## Schema Relationships
- References: index_schema.roadway_anchor(anchor_id); taxonomy.road(road_id)
- Referenced by: delivery.qc_publishing_station(pinned_configuration_id), delivery.station_config_binding(configuration_id)

## Diagrams
- See ERD: `db/schemas/roadway_config/qc_datalake_rds_dev_db - roadway_config.png`

## References
- `docs/readme.md`

## Open Questions
- None
