# index_schema — Data Dictionary

## Purpose
Canonical registry of collection sites and their linkage to roadway anchors and source types. Feeds roadway_config seeding and ties raw files to sites.

## Tables
### index_schema.collection_sites
**Purpose:** Physical collection sites with coordinates and optional OSM-enriched attributes.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| collection_site_id | bigserial | yes |  | no |  | Identity of the site |
| location_name | varchar(255) |  |  | yes |  | Human-friendly name |
| location_type | varchar(50) |  |  | yes |  | Site type label |
| latitude | numeric |  |  | yes |  | Latitude |
| longitude | numeric |  |  | yes |  | Longitude |
| point_geometry | geometry(point,4326) |  |  | yes |  | Canonical site point |
| road_geometry | geometry(linestring,4326) |  |  | yes |  | Road segment geometry |
| other_geometry | geometry(polygon,4326) |  |  | yes |  | Optional polygon |
| place_id | varchar(50) |  |  | yes |  | External place id |
| osm_id | varchar(50) |  |  | yes |  | OSM way/node id |
| osm_type | varchar(50) |  |  | yes |  | OSM type |
| osm_attributes | jsonb |  |  | yes |  | OSM enrichment blob |
| createdAt | timestamptz |  |  | no | now() | Audit |
| updatedAt | timestamptz |  |  | no | now() | Audit |

**Constraints & Indexes**
- PK: collection_sites_pkey (collection_site_id)
- Indexes: idx_collection_sites_point_geometry (gist(point_geometry))

**Relationships**
- Referenced by: index_schema.collection_site_anchor_map(collection_site_id), index_schema.collection_site_sources(collection_site_id), raw_metadata_catalog.files(collection_site_id)

**Business Logic Notes**
- OSM enrichment fields may be inline or in aux tables; see docs/json_schemas.md.
- Sites originate from SmartSuite/QC‑Hub; created/upserted by Lambda `create_collection_sites`, then events fan out to roadway_config seeding. Refs: docs/readme.md, .ai/snapshots/ops-center/2025-10-28-qchub-order-integration.md

**Operational Notes**
- PostGIS geometry columns with spatial index for point lookups.
- Idempotent upserts keyed by upstream natural identifiers; downstream EventBridge → SQS pipeline for seeding anchors/configs. Refs: docs/readme.md

### index_schema.roadway_anchor
**Purpose:** Canonical roadway nodes to which sites map; shared across configurations.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| anchor_id | uuid | yes |  | no |  | Anchor id |
| kind | roadway_config.rc_site_kind |  |  | no |  | Site kind (intersection/midblock) |
| canonical_point | geometry(point,4326) |  |  | no |  | Canonical location |
| canonical_bbox | geometry(polygon,4326) |  |  | yes |  | Bounding box |
| primary_name | text |  |  | yes |  | Primary name |
| alt_names | text[] |  |  | yes |  | Alternate names |
| created_by | text |  |  | yes |  | User |
| created_at | timestamptz |  |  | yes | now() | Audit |
| notes | text |  |  | yes |  | Notes |
| is_provisional | bool |  |  | no | false | Provisional flag |
| provisional_seed_source | text |  |  | yes |  | Seed source |
| provisional_confidence | numeric |  |  | yes |  | Confidence |
| provisional_seeded_at | timestamptz |  |  | yes |  | Seeded time |
| provisional_seed_key | text |  |  | yes |  | Source key |

**Constraints & Indexes**
- PK: roadway_anchor_pkey (anchor_id)
- Indexes: ix_anchor_geom (gist(canonical_point)), ix_anchor_provisional (is_provisional)

**Relationships**
- Referenced by: index_schema.collection_site_anchor_map(anchor_id), delivery.qc_publishing_station(anchor_id), roadway_config.configuration(anchor_id)

### index_schema.source_types
**Purpose:** Catalog of source systems/types used for site metadata.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| source_type_id | serial4 | yes |  | no |  | Identity |
| type_name | varchar(50) |  |  | no |  | Unique type name |
| description | text |  |  | yes |  | Description |
| json_schema | jsonb |  |  | yes |  | Optional schema |

**Constraints & Indexes**
- PK: source_types_pkey
- Unique: source_types_type_name_key (type_name)

### index_schema.collection_site_anchor_map
**Purpose:** Mapping from collection sites to roadway anchors with confidence and source.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| collection_site_id | int8 | yes | collection_sites | no |  | Site id |
| anchor_id | uuid |  | roadway_anchor | no |  | Anchor id |
| source | text |  |  | no |  | Source of mapping (auto/import/manual) |
| confidence | numeric |  |  | yes |  | 0..1 confidence |
| is_locked | bool |  |  | yes | false | Lock mapping |
| linked_by | text |  |  | yes |  | User |
| linked_at | timestamptz |  |  | yes | now() | Timestamp |
| match_confidence | numeric |  |  | yes |  | Computed confidence |
| match_method | text |  |  | yes |  | Method name |

**Constraints & Indexes**
- PK: collection_site_anchor_map_pkey (collection_site_id)
- Check: confidence between 0 and 1; source in ('auto','import','manual')
- FKs: collection_site_id → index_schema.collection_sites(collection_site_id) ON DELETE CASCADE; anchor_id → index_schema.roadway_anchor(anchor_id) ON DELETE RESTRICT
- Indexes: ix_csam_anchor(anchor_id)

**Relationships**
- References: index_schema.collection_sites, index_schema.roadway_anchor

**Business Logic Notes**
- Auto matching uses proximity + street-set similarity; users can lock/adjust matches in Ops‑Center. Refs: docs/readme.md

**Operational Notes**
- Confidence score bounded 0–1 with check constraint; manual vs auto source tracked for audit.

### index_schema.collection_site_sources
**Purpose:** Sources (QC-Hub, uploader, etc.) associated with a collection site.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| collection_site_source_id | serial4 | yes |  | no |  | Identity |
| collection_site_id | int8 |  | collection_sites | no |  | Site id |
| source_type_id | int4 |  | source_types | no |  | Source type |
| source_attributes | jsonb |  |  | yes |  | Arbitrary attributes |
| createdAt | timestamptz |  |  | no | now() | Audit |
| updatedAt | timestamptz |  |  | no | now() | Audit |

**Constraints & Indexes**
- PK: collection_site_sources_pkey
- FKs: collection_site_id → index_schema.collection_sites ON DELETE CASCADE; source_type_id → index_schema.source_types ON DELETE CASCADE
- Indexes: idx_source_attributes_gin (gin jsonb_path_ops), idx_source_attributes_order_no (btree on (source_attributes->>'order_no'))

**Relationships**
- References: index_schema.collection_sites, index_schema.source_types

**Business Logic Notes**
- Carries upstream pointers (e.g., QC‑Hub order/site keys) and attributes to support idempotency and lineage. Refs: docs/readme.md

**Operational Notes**
- JSONB attributes indexed via GIN (jsonb_path_ops) and a dedicated btree for common keys like order_no.

### index_schema.anchor_road
**Purpose:** Join table linking anchors to roads with a role.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| anchor_id | uuid | yes | roadway_anchor | no |  | Anchor id |
| road_id | int8 | yes | taxonomy.road | no |  | Road id |
| role | text | yes |  | no |  | Role: through/crossing/adjacent |

**Constraints & Indexes**
- PK: anchor_road_pkey (anchor_id, road_id, role)
- Check: role in ('through','crossing','adjacent')
- FKs: anchor_id → index_schema.roadway_anchor(anchor_id) ON DELETE CASCADE; road_id → taxonomy.road(road_id) ON DELETE RESTRICT

**Relationships**
- References: index_schema.roadway_anchor, taxonomy.road

## Enums
- Uses enums from roadway_config (rc_site_kind)

## Views
- None

## Functions
- None

## Triggers
- None

## Sequences
- collection_sites_collection_site_id_seq
- collection_site_sources_collection_site_source_id_seq
- source_types_source_type_id_seq

## Schema Relationships
- References: taxonomy.road (via anchor_road); uses roadway_config.rc_site_kind enum in roadway_anchor.kind
- Referenced by: raw_metadata_catalog.files(collection_site_id); delivery.qc_publishing_station(anchor_id); roadway_config.configuration(anchor_id)

## Diagrams
- See ERD: `db/schemas/index_schema/qc_datalake_rds_dev_db - index_schema.png`

## References
- `docs/readme.md`

## Open Questions
- Confirm inline vs side-table OSM enrichment approach.
