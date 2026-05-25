# taxonomy — Data Dictionary

## Purpose
Canonical vocabulary for roads and category/class mapping helpers used during assembly.

## Tables
### taxonomy.road
**Purpose:** Normalized road names with canonical and alternate representations.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| road_id | bigserial | yes |  | no |  | Identity |
| canonical_name | text |  |  | no |  | Canonical road name |
| prefix_dir | taxonomy.tx_dir |  |  | yes |  | Prefix dir |
| base_name | text |  |  | yes |  | Base name |
| suffix_type | text |  |  | yes |  | Suffix type |
| route_number | text |  |  | yes |  | Route number |
| alt_names | text[] |  |  | yes |  | Alternate names |

**Constraints & Indexes**
- PK: road_pkey (road_id)
- Unique: uq_taxonomy_road_canonical (canonical_name)

**Relationships**
- Referenced by: index_schema.anchor_road(road_id), roadway_config.approach(road_id)

**Business Logic Notes**
- Canonical road naming ensures consistent anchor/config linkage and matching. Refs: docs/readme.md

**Operational Notes**
- Keep canonical_name unique; suffix normalization helps matching and import.

### taxonomy.suffix_norm
**Purpose:** Mapping of raw suffix strings to canonical forms.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| raw | text | yes |  | no |  | Raw suffix |
| canonical | text |  |  | no |  | Canonical suffix |

**Constraints & Indexes**
- PK: suffix_norm_pkey (raw)

**Business Logic Notes**
- Used during OSM/road import to standardize names. Refs: docs/readme.md

**Operational Notes**
- Expand cautiously; changes affect string matching.

## Enums
- taxonomy.tx_dir — {'N','S','E','W','NE','NW','SE','SW'}

## Views
- taxonomy.category_member — Bank members derived from ops_config.bin_scheme_banks
- taxonomy.category_schema — Bank schemes derived from ops_config.bin_schemes
- taxonomy.class_member — Vehicle classes derived from ops_config.vehicle_classes
- taxonomy.class_to_bank_map — Mapping from class_key to bank_key per scheme

## Functions
- taxonomy.filter_category_json(jsonb, _text, _text)
- taxonomy.rollup_class_json_to_bank_json(jsonb, int8)

## Triggers
- None

## Sequences
- road_road_id_seq

## Schema Relationships
- Referenced by: index_schema.anchor_road(road_id); roadway_config.approach(road_id)
- References: ops_config.* via derived views (category_member/category_schema/class_member/class_to_bank_map)

## Diagrams
- See ERD: `db/schemas/taxonomy/qc_datalake_rds_dev_db - taxonomy.png`

## References
- `docs/readme.md`

## Open Questions
- None
