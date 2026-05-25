# <schema> — Data Dictionary

## Purpose
Short description of schema’s role in the platform; upstream/downstream flows.

## Tables
### <schema>.<table>
**Purpose:** Why this table exists / how it’s used (business terms).

**Columns**
| Column | Type | PK | FK | Nullable | Default | Description |
|-------|------|----|----|----------|---------|-------------|
| ...   | ...  |    |    |          |         | ...         |

**Constraints & Indexes**
- PK: ...
- FKs: ...
- Unique: ...
- Check: ...
- Indexes: ...

**Relationships**
- References: …
- Referenced by: …
(Optionally add a Mermaid ERD block.)

**Business Logic Notes**
- Bullet list translating DDL → domain: e.g., “`speed_85th_percentile_by_movement` stores movement-level JSONB…”

**Operational Notes**
- Partitioning, retention, expected write patterns, typical queries
- Links to snapshots: [.ai/snapshots/datalake-architecture/2025-04-25-qa-metrics-and-speed-percentiles.md](../../.ai/snapshots/datalake-architecture/2025-04-25-qa-metrics-and-speed-percentiles.md)