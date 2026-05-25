# ops_config — Data Dictionary

## Purpose
Operational configuration for bin schemes and vehicle classes used for category mapping and UI.

## Tables
### ops_config.bin_schemes
**Purpose:** Defines bank/bin schemes used to aggregate classes.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| id | bigserial | yes |  | no |  | Identity |
| name | text |  |  | no |  | Scheme name |
| code | text |  |  | yes |  | Short code |
| description | text |  |  | yes |  | Description |
| is_locked | bool |  |  | no | false | Prevent edits |
| created_at | timestamptz |  |  | no | now() | Audit |
| updated_at | timestamptz |  |  | no | now() | Audit |

**Constraints & Indexes**
- PK: bin_schemes_pkey (id)
- Unique: bin_schemes_name_key (name), bin_schemes_code_key (code)
- Trigger: trg_bin_schemes_touch → ops_config.touch_updated_at()

**Business Logic Notes**
- Bank schemes define UI layout and aggregation for class rollups; used across preview and delivery. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Lock schemes that are in production to prevent edits impacting reproducibility.

### ops_config.format_profiles
**Purpose:** Declarative ingest format profiles with validators and processing plans.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| profile_id | text | yes |  | no |  | Identity |
| source_group | text |  |  | yes |  | Group |
| granularity | text |  |  | yes |  | 'per_vehicle' or 'summarized' |
| container | text |  |  | yes |  | Container type |
| detector | jsonb |  |  | yes |  | Detector config |
| schema | jsonb |  |  | yes |  | Schema hints |
| validators | text[] |  |  | yes | '{}' | Validators |
| processing_plan | jsonb |  |  | yes |  | Processing plan |
| is_active | bool |  |  | yes | true | Active flag |
| created_at | timestamptz |  |  | yes | now() | Audit |
| updated_at | timestamptz |  |  | yes | now() | Audit |

**Constraints & Indexes**
- PK: format_profiles_pkey (profile_id)
- Check: granularity in ('per_vehicle','summarized')

**Business Logic Notes**
- Encodes how to recognize and parse vendor files into canonical structures. Refs: docs/readme.md

**Operational Notes**
- Keep validators lightweight in hot path; push heavy checks to offline validation.

### ops_config.vehicle_classes
**Purpose:** Master list of vehicle classes; can mirror FHWA numbering and custom classes.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| id | bigserial | yes |  | no |  | Identity |
| fhwa_class_no | int2 |  |  | yes |  | 1..13 class number |
| vehicle_type | text |  |  | no |  | Name |
| fhwa_class_label | text (generated) |  |  | yes |  | 'Class N' if fhwa_class_no set |
| fhwa_description | text |  |  | yes |  | Description |
| datalens_alias | text |  |  | no |  | Unique alias |
| is_fhwa | bool |  |  | no | false | FHWA-aligned |
| code | text |  |  | yes |  | Short code |
| sort_order | int4 |  |  | no | 0 | UI/order |
| notes | text |  |  | yes |  | Notes |
| created_at | timestamptz |  |  | no | now() | Audit |
| updated_at | timestamptz |  |  | no | now() | Audit |

**Constraints & Indexes**
- PK: vehicle_classes_pkey (id)
- Unique: vehicle_classes_datalens_alias_key (datalens_alias)
- Check: fhwa_class_no between 1 and 13
- Indexes: idx_vehicle_classes_fhwa_no, idx_vehicle_classes_sort
- Trigger: trg_vehicle_classes_touch → ops_config.touch_updated_at()

**Business Logic Notes**
- Classes map to vendor aliases and FHWA classes; serve as source keys for taxonomy rollups. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Maintain stable ids and sort ordering; avoid churn to preserve diffable snapshots.

### ops_config.bin_scheme_banks
**Purpose:** Banks within a scheme, including labels and ped semantics.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| id | bigserial | yes |  | no |  | Identity |
| scheme_id | int8 |  | ops_config.bin_schemes | no |  | Scheme id |
| bank_index | int2 |  |  | no |  | Bank index |
| label | text |  |  | yes |  | Label |
| is_zero_filled | bool |  |  | no | false | Zero-fill bank |
| ped_column_semantics | text |  |  | no | 'none' | Pedestrian semantics |
| ped_uturn_bank | int2 |  |  | yes |  | Target for ped U-turns |

**Constraints & Indexes**
- PK: bin_scheme_banks_pkey (id)
- Unique: bin_scheme_banks_scheme_id_bank_index_key (scheme_id, bank_index)
- FK: scheme_id → ops_config.bin_schemes(id) ON DELETE CASCADE

**Business Logic Notes**
- Ped column semantics and u‑turn handling affect assembly seeding and ped totals. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Changing bank membership affects mapping; coordinate with taxonomy/class_to_bank_map.

### ops_config.bin_scheme_uturn_rules
**Purpose:** Mapping for U-turn reassignments by bank.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| scheme_id | int8 | yes | ops_config.bin_schemes | no |  | Scheme id |
| source_bank_index | int2 | yes |  | no |  | Source bank index |
| target_bank_index | int2 |  |  | no |  | Target bank index |
| ped_column_target | bool |  |  | no | true | Affects ped column |

**Constraints & Indexes**
- PK: bin_scheme_uturn_rules_pkey (scheme_id, source_bank_index)
- FK: scheme_id → ops_config.bin_schemes(id) ON DELETE CASCADE

**Business Logic Notes**
- Used during assembly to reallocate pedestrian u‑turn columns appropriately. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Keep rules consistent with ped_column_semantics on banks.

### ops_config.bin_scheme_bank_classes
**Purpose:** Join between banks and vehicle classes.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| bank_id | int8 | yes | ops_config.bin_scheme_banks | no |  | Bank id |
| vehicle_class_id | int8 | yes | ops_config.vehicle_classes | no |  | Class id |

**Constraints & Indexes**
- PK: bin_scheme_bank_classes_pkey (bank_id, vehicle_class_id)
- FKs: bank_id → ops_config.bin_scheme_banks(id) ON DELETE CASCADE; vehicle_class_id → ops_config.vehicle_classes(id) ON DELETE RESTRICT

**Business Logic Notes**
- Drives taxonomy.class_to_bank_map view used by preview mapping. Refs: docs/count_assembly_readme.md

**Operational Notes**
- Maintain one‑to‑many class assignments per bank; avoid overlapping memberships.

## Enums
- None

## Views
- ops_config.vw_vehicle_classes_display — Convenience view for UI ordering

## Functions
- ops_config.touch_updated_at() — trigger function to bump updated_at

## Triggers
- trg_bin_schemes_touch on bin_schemes
- trg_vehicle_classes_touch on vehicle_classes

## Sequences
- bin_schemes_id_seq
- bin_scheme_banks_id_seq
- vehicle_classes_id_seq

## Schema Relationships
- Referenced by: assembly.preview_* (bank_schema_id), assembly.selection_rule(bank_schema_id), raw_metadata_catalog.file_summary_capability(bin_scheme_id), delivery.published_* (bank_schema_id), taxonomy.* views
- References: None

## Diagrams
- See ERD: `db/schemas/ops_config/qc_datalake_rds_dev_db - ops_config.png`

## References
- `docs/readme.md`

## Open Questions
- None
