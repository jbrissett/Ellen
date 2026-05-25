# raw_metadata_catalog — Data Dictionary

## Purpose
Raw file registration and capability flags for ingest, enrichment, and downstream summarization.

## Tables
### raw_metadata_catalog.data_types
**Purpose:** Catalog known data types and their detection/validation patterns.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| data_type_id | bigserial | yes |  | no |  | Identity |
| name | varchar(50) |  |  | no |  | Type name |
| description | varchar(250) |  |  | no |  | Description |
| regex | text |  |  | yes |  | Filename/format regex |
| format_type | text |  |  | yes |  | Format grouping |
| expected_columns | jsonb |  |  | yes |  | Expected header/fields |
| classification_rules | jsonb |  |  | yes |  | Classification rules |
| ai_signature_vector | bytea |  |  | yes |  | Optional embedding |

**Constraints & Indexes**
- PK: data_types_pkey (data_type_id)

**Business Logic Notes**
- Used by ingest to classify vendor/per‑vehicle files and validate column expectations. Refs: docs/readme.md

**Operational Notes**
- Extend cautiously; updating regex/expected_columns can impact auto‑classification.

### raw_metadata_catalog.format_registration_count_files
**Purpose:** Registration manifest for count file formats and their storage/index vectors.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| id | bigserial | yes |  | no |  | Identity |
| format_id | text |  |  | no |  | Unique format id |
| ftype | text |  |  | no |  | File type |
| sample_id | text |  |  | yes |  | Sample key |
| source_bucket | text |  |  | no |  | Source bucket |
| source_key | text |  |  | no |  | Source key |
| repo_bucket | text |  |  | no |  | Repo bucket |
| hash_manifest_key | text |  |  | no |  | Manifest key |
| vector_bucket | text |  |  | no |  | Vector bucket |
| vector_index | text |  |  | no |  | Vector index |
| vector_key | text |  |  | no |  | Vector key |
| props | jsonb |  |  | no | '{}' | Arbitrary properties |
| created_at | timestamptz |  |  | no | now() | Audit |
| missing_attributes | jsonb |  |  | yes |  | Missing props |
| step_function_arn | text |  |  | yes |  | Orchestration ARN |

**Constraints & Indexes**
- PK: format_registration_count_files_pkey (id)
- Unique: uq_format (format_id)

**Business Logic Notes**
- Tracks vectorization and manifests for format catalogs, enabling quick lookup and dedupe. Refs: docs/readme.md

**Operational Notes**
- Supports replayable pipelines; ARN stored when orchestrated via Step Functions.

### raw_metadata_catalog.pending_file_formats
**Purpose:** Pending file classification suggestions for manual triage.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| id | uuid | yes |  | no | gen_random_uuid() | Identity |
| order_no | text |  |  | no |  | Order number |
| sitecode | text |  |  | no |  | Sitecode |
| original_bucket | text |  |  | no |  | S3 bucket |
| original_key | text |  |  | no |  | S3 key |
| fallback_key | text |  |  | yes |  | Alternate key |
| file_preview | text |  |  | yes |  | Preview text/sample |
| file_type | text |  |  | yes |  | Detected type |
| ingestion_timestamp | timestamptz |  |  | yes | now() | Seen time |
| triage_status | text |  |  | yes | 'pending' | Status |
| triage_notes | text |  |  | yes |  | Notes |
| suggested_format | jsonb |  |  | yes |  | Suggested profile |

**Constraints & Indexes**
- PK: pending_file_formats_pkey (id)

**Business Logic Notes**
- Analysts review and confirm/override suggested formats before registration. Refs: docs/readme.md

**Operational Notes**
- Triage fields record status/notes; consider TTL/cleanup strategy.

### raw_metadata_catalog.stage_______files
**Purpose:** Unlogged staging table used during batch imports.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| order_no | int8 |  |  | yes |  | |
| sitecode | int8 |  |  | yes |  | |
| collection_site_id | int8 |  |  | yes |  | |
| data_type | text |  |  | yes |  | |
| source | text |  |  | yes |  | |
| original_bucket | text |  |  | yes |  | |
| original_key | text |  |  | yes |  | |
| target_bucket | text |  |  | yes |  | |
| target_key | text |  |  | yes |  | |
| target_parquet_key | text |  |  | yes |  | |
| creation_date | timestamp |  |  | yes |  | |
| data_start_date | timestamp |  |  | yes |  | |
| data_end_date | timestamp |  |  | yes |  | |
| data_interval | int4 |  |  | yes |  | |
| qc_office | text |  |  | yes |  | |
| customer | text |  |  | yes |  | |
| source_specific_metadata | text |  |  | yes |  | |
| count_parameters | text |  |  | yes |  | |
| ingestion_timestamp | timestamp |  |  | yes |  | |
| file_status | text |  |  | yes |  | |
| file_checksum | text |  |  | yes |  | |
| file_size | int8 |  |  | yes |  | |
| version | int4 |  |  | yes |  | |
| error_message | text |  |  | yes |  | |
| qa_notes | text |  |  | yes |  | |
| last_updated | timestamp |  |  | yes |  | |

### raw_metadata_catalog.file_summary_capability
**Purpose:** Per-file capabilities for summary/assembly (bucket, dimension, bank schema, etc.).

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| file_id | int8 | yes | files | no |  | File id |
| category_dimension | assembly.category_dimension |  |  | no |  | Target dimension |
| bin_scheme_id | int8 |  | ops_config.bin_schemes | yes |  | Target bank scheme |
| class_schema_tag | text |  |  | yes |  | Source class schema tag |
| bucket_minutes | int2 |  |  | no | 5 | Canonical bucket minutes |
| created_at | timestamptz |  |  | no | now() | Audit |

**Constraints & Indexes**
- PK: file_summary_capability_pkey (file_id)
- Check: file_cap_bucket_check (bucket_minutes in 1,5,10,15,30,60)
- FKs: file_id → raw_metadata_catalog.files(file_id) ON DELETE CASCADE; bin_scheme_id → ops_config.bin_schemes(id)
- Indexes: ix_filecap_bucket(bucket_minutes), ix_filecap_category(category_dimension, bin_scheme_id)

**Relationships**
- References: files, ops_config.bin_schemes

**Business Logic Notes**
- Canonical capability flags drive selection rules and mapping during preview. Refs: docs/json_schemas.md, docs/count_assembly_readme.md

**Operational Notes**
- Bucket minutes constrained to common values; bank schema optional unless targeting bank dimension.

### raw_metadata_catalog.files
**Purpose:** Authoritative registry of raw files and S3 keys with ingest status and metadata.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| file_id | bigserial | yes |  | no |  | Identity |
| order_no | varchar(50) |  |  | no |  | Order number |
| sitecode | varchar(50) |  |  | no |  | Sitecode |
| collection_site_id | int8 |  | index_schema.collection_sites | yes |  | Site id |
| data_type | varchar(50) |  |  | no |  | Data type |
| source | varchar(50) |  |  | no |  | Source system |
| original_bucket | varchar(255) |  |  | no |  | S3 bucket (raw) |
| original_key | varchar(1024) |  |  | no |  | S3 key (raw) |
| target_bucket | varchar(255) |  |  | no |  | S3 bucket (repo) |
| target_key | varchar(1024) |  |  | no |  | S3 key (repo) |
| target_parquet_key | varchar(1024) |  |  | yes |  | S3 key (parquet) |
| creation_date | timestamptz |  |  | no |  | File creation time |
| data_start_date | timestamptz |  |  | yes |  | Data start |
| data_end_date | timestamptz |  |  | yes |  | Data end |
| data_interval | varchar(50) |  |  | yes |  | Interval label |
| qc_office | varchar(100) |  |  | yes |  | Office |
| customer | varchar(100) |  |  | yes |  | Customer |
| source_specific_metadata | jsonb |  |  | yes |  | Vendor-specific metadata |
| count_parameters | jsonb |  |  | yes |  | Count params |
| ingestion_timestamp | timestamptz |  |  | no | now() | Ingest time |
| file_status | varchar(50) |  |  | no |  | Status |
| file_checksum | varchar(100) |  |  | yes |  | Checksum |
| file_size | int8 |  |  | yes |  | Size |
| version | int4 |  |  | yes | 1 | Version |
| error_message | text |  |  | yes |  | Error |
| qa_notes | text |  |  | yes |  | QA notes |
| last_updated | timestamptz |  |  | yes | now() | Updated |
| checksum_sha256 | text |  |  | yes |  | Deterministic checksum |
| s3_version_id | text |  |  | yes |  | Version id |
| uploader_user_id | text |  |  | yes |  | Uploader |
| upload_session_id | text |  |  | yes |  | Upload session |
| device_id | text |  |  | yes |  | Device id |
| original_version_id | text |  |  | yes |  | Original source version |

**Constraints & Indexes**
- PK: files_pkey (file_id)
- FKs: collection_site_id → index_schema.collection_sites(collection_site_id)

**Relationships**
- Referenced by: raw_metadata_catalog.file_summary_capability(file_id), source_summary tables via file linkage (indirect through summarized_data_interval)

**Business Logic Notes**
- Central source of truth for raw assets; used to kick off CSV→Parquet and summary pipelines. Refs: docs/readme.md

**Operational Notes**
- S3 keys are immutable pointers; maintain checksum and version ids for integrity and idempotency.

## Enums
- Uses assembly.category_dimension in capability table

## Views
- None

## Functions
- None

## Triggers
- None

## Sequences
- data_types_data_type_id_seq
- files_file_id_seq
- format_registration_count_files_id_seq

## Schema Relationships
- References: index_schema.collection_sites (files.collection_site_id); ops_config.bin_schemes (file_summary_capability.bin_scheme_id)
- Referenced by: source_summary tables consume files indirectly (not FKed in DDL); assembly/session reads via source_summary

## Diagrams
- See ERD: `db/schemas/raw_metadata_catalog/qc_datalake_rds_dev_db - raw_metadata_catalog.png`

## References
- `docs/readme.md`
- `docs/json_schemas.md`

## Open Questions
- Confirm long-term storage for pending_file_formats once triaged.
