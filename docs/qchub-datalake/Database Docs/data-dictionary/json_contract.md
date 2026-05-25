# json_contract — Data Dictionary

## Purpose
Register and validate JSON payload schemas used across services and events.

## Tables
### json_contract.schema_registry
**Purpose:** Stores JSON Schemas by id; referenced at write-time by triggers or application code.

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| schema_id | text | yes |  | no |  | Schema identifier |
| json_schema | jsonb |  |  | no |  | JSON Schema document |
| created_at | timestamptz |  |  | yes | now() | Audit |
| description | text |  |  | yes |  | Human-readable description |

**Constraints & Indexes**
- PK: schema_registry_pkey (schema_id)

**Business Logic Notes**
- Centralizes payload schema validation across events and APIs. Refs: docs/json_schemas.md

**Operational Notes**
- Treat schema updates as breaking changes unless backwards compatibility is guaranteed.

## Enums
- None

## Views
- None

## Functions
- json_contract.assert_known_payload_schema() — trigger helper to validate payload schema ids

## Triggers
- None in repo DDL; function intended for use by tables storing JSON payloads

## Sequences
- None

## Schema Relationships
- Referenced by: intended for tables that store `payload_schema_id` (none in this repo's DDL)
- References: None

## Diagrams
- None

## References
- `docs/json_schemas.md`

## Open Questions
- None
