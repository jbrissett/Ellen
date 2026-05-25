# delivery — Data Dictionary

## Purpose
Immutable published counts with deterministic lineage; station bindings for roadway configurations.

## Tables
### delivery.published_count
**Purpose:** Published count headers per assembly session version.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| published_count_id | uuid | yes |  | no | gen_random_uuid() | Identity |
| assembly_session_id | uuid |  | assembly.assembly_session | no |  | Source session |
| version | int4 |  |  | no |  | Monotonic version per session |
| order_no | int4 |  |  | no |  | Order |
| location_id | int8 |  |  | yes |  | Location id |
| sitecode_id | int8 |  |  | no |  | Sitecode id |
| qc_station_id | int8 |  |  | yes |  | Publishing station (resolved) |
| bucket_minutes | int2 |  |  | yes |  | Snapshot bucket |
| published_at | timestamptz |  |  | no | now() | Publish time |
| published_by | text |  |  | no |  | User |
| notes | text |  |  | yes |  | Notes |
| content_hash_sha256 | text |  |  | no |  | Deterministic hash |
| interval_start | timestamp |  |  | no |  | Window start |
| interval_end | timestamp |  |  | no |  | Window end |

**Constraints & Indexes**
- PK: published_count_pkey (published_count_id)
- Unique: published_count_assembly_session_id_version_key (assembly_session_id, version)
- FK: assembly_session_id → assembly.assembly_session
- Indexes: ix_pubcount_sitecode (sitecode_id, version desc)

**Business Logic Notes**
- Each publish increments a per‑session version and locks lineage; publishing can be retried idempotently because of the deterministic hash. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Treat as immutable; any correction requires a new version.

### delivery.published_lineage
**Purpose:** Lineage snapshot per published count and lineage key.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| published_count_id | uuid | yes | delivery.published_count | no |  | Published id |
| lineage_key | uuid | yes |  | no |  | Lineage grouping key |
| contributors | jsonb |  |  | no |  | Source contributors |
| applied_rules | jsonb |  |  | no |  | Rule ids applied |
| applied_overrides | jsonb |  |  | no |  | Override ids applied |
| qa_snapshot | jsonb |  |  | yes |  | Optional QA context |

**Constraints & Indexes**
- PK: published_lineage_pkey (published_count_id, lineage_key)
- FK: published_count_id → delivery.published_count ON DELETE CASCADE

### delivery.published_volume_by_movement
**Purpose:** Published volume rows per movement and 5-min interval.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| published_count_id | uuid |  | delivery.published_count | no |  | Header id |
| movement | text |  |  | no |  | Movement label |
| volume_count | int4 |  |  | no |  | Total volume |
| volume_count_by_class | jsonb |  |  | yes |  | Optional per-class |
| category_dimension | assembly.category_dimension |  |  | no | 'none' | Target dimension |
| bank_schema_id | int8 |  | ops_config.bin_schemes | yes |  | Bank scheme |
| category_breakdown | jsonb |  |  | yes |  | Class/bank breakdown |
| lineage_key | uuid |  |  | no |  | Lineage key |
| interval_start | timestamp |  |  | no |  | Interval start |

**Constraints & Indexes**
- FK: published_count_id → delivery.published_count ON DELETE CASCADE; bank_schema_id → ops_config.bin_schemes(id)

**Business Logic Notes**
- Category dimension captures final target (class or bank) for consumers; lineage_key ties back to contributors. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Consumers can group by interval_start/movement for time series queries.

### delivery.published_volume_by_speed
**Purpose:** Published speed distribution rows per mph (optionally per movement).

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| published_count_id | uuid |  | delivery.published_count | no |  | Header id |
| movement | text |  |  | no | '~' | Movement or '~' for overall |
| speed_mph | int4 |  |  | no |  | MPH |
| vehicle_count | int4 |  |  | no |  | Count at mph |
| vehicle_count_by_class | jsonb |  |  | yes |  | Optional per-class |
| category_dimension | assembly.category_dimension |  |  | no | 'none' | Target dimension |
| bank_schema_id | int8 |  | ops_config.bin_schemes | yes |  | Bank scheme |
| category_breakdown | jsonb |  |  | yes |  | Class/bank breakdown |
| lineage_key | uuid |  |  | no |  | Lineage key |
| interval_start | timestamp |  |  | no |  | Interval start |

**Constraints & Indexes**
- FK: published_count_id → delivery.published_count ON DELETE CASCADE; bank_schema_id → ops_config.bin_schemes(id)

**Business Logic Notes**
- Stores per‑mph counts; optionally includes per‑class/bank breakdown for analytics. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Exclude 0‑mph from percentile calculations but retain in counts (business rule). Refs: docs/readme.md

### delivery.qc_publishing_station
**Purpose:** QC station entities bound to roadway anchors and configurations.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| station_id | uuid | yes |  | no |  | Identity |
| anchor_id | uuid |  | index_schema.roadway_anchor | no |  | Anchor id |
| name | text |  |  | no |  | Station name |
| slug | text |  |  | no |  | Unique slug |
| status | text |  |  | no |  | 'draft'|'active'|'archived' |
| config_selection | text |  |  | no |  | 'latest_published'|'pinned_version' |
| pinned_configuration_id | uuid |  | roadway_config.configuration | yes |  | Pinned config |
| owner_office | text |  |  | yes |  | Office |
| created_by | text |  |  | yes |  | User |
| created_at | timestamptz |  |  | yes | now() | Audit |
| notes | text |  |  | yes |  | Notes |

**Constraints & Indexes**
- PK: qc_publishing_station_pkey (station_id)
- Unique: qc_publishing_station_anchor_id_key (anchor_id), qc_publishing_station_slug_key (slug)
- Check: status in ('draft','active','archived'); config_selection in ('latest_published','pinned_version')
- FKs: anchor_id → index_schema.roadway_anchor(anchor_id) ON DELETE RESTRICT; pinned_configuration_id → roadway_config.configuration(configuration_id)

**Business Logic Notes**
- Stations define where counts are published from (anchor + configuration selection). Refs: docs/readme.md

**Operational Notes**
- Use slug for stable URLs; pin configurations when strict reproducibility is required.

### delivery.station_config_binding
**Purpose:** Bindings from stations to specific roadway configurations.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| station_id | uuid | yes | delivery.qc_publishing_station | no |  | Station id |
| configuration_id | uuid | yes | roadway_config.configuration | no |  | Configuration id |
| bound_at | timestamptz |  |  | yes | now() | Bind time |
| bound_by | text |  |  | yes |  | User |

**Constraints & Indexes**
- PK: station_config_binding_pkey (station_id, configuration_id)
- FKs: station_id → delivery.qc_publishing_station(station_id) ON DELETE CASCADE; configuration_id → roadway_config.configuration(configuration_id) ON DELETE RESTRICT

## Enums
- Uses assembly.category_dimension in published rows

## Views
- None

## Functions
- delivery.publish_preview(session_id, published_by) — writes headers and rows with deterministic content hash

## Triggers
- None

## Sequences
- None

## Schema Relationships
- References: assembly.assembly_session (published_count.assembly_session_id); ops_config.bin_schemes (bank_schema_id in published rows); index_schema.roadway_anchor (qc_publishing_station.anchor_id); roadway_config.configuration (pinned/binding FKs)
- Referenced by: None (terminal delivery layer)

## Diagrams
- See ERD: `db/schemas/delivery/qc_datalake_rds_dev_db - delivery.png`

## References
- `docs/readme.md`
- `docs/count_assembly_readme.md`

## Open Questions
- None
