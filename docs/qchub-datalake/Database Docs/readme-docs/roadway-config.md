# Roadway Configuration

## Purpose

The roadway configuration domain provides the canonical anchor and lane-configuration model used to tie collection sites to stable roadway entities and to interpret movements consistently across other tools.

## Current Implementation Status

This domain is implemented as a live Ops Center tool, not just a future concept.

Primary code surfaces:

- UI: `apps/ops-center/app/tools/roadway-config/**`
- controller: `apps/ops-center/server/core/controllers/roadway-config.controller.ts`
- repos: `apps/ops-center/server/repos/roadway-config/**`
- schema: `db/schemas/roadway_config/roadway_config_ddl.sql`

Current UI capabilities represented in code include:

- map-based corridor and anchor browsing
- collection-site listing and assignment
- provisional anchor seeding through API
- anchor detail editing
- configuration version editing
- approach and lane editing
- corridor assignment and review views

Current auto-seeding capability represented outside this repo:

- the `seed-roadway-anchor` Lambda in `C:\Repos\datalake_functions\seed-roadway-anchor`

That Lambda is operational and part of the deployed roadway workflow even though its implementation does not live inside `datalake-ops-center`.

## Data Model

### Anchor and collection-site linkage

The anchor-side identity is split across `index_schema` and `roadway_config`.

Key records:

- `index_schema.collection_sites`
- `index_schema.collection_site_sources`
- `index_schema.roadway_anchor`
- `index_schema.collection_site_anchor_map`

This supports:

- one physical collection site
- mapping to one canonical roadway anchor
- versioned configurations over time for that anchor

### Roadway configuration core tables

From `db/schemas/roadway_config/roadway_config_ddl.sql`:

- `roadway_config.configuration`
- `roadway_config.approach`
- `roadway_config.lane`
- `roadway_config.capture`
- `roadway_config.audit_log`

Important helper view/functions:

- `roadway_config.vw_qchub_legs_flat`
- `roadway_config.effective_config(anchor_id, at_ts)`
- `roadway_config.effective_config_by_collection_site(site_id, at_ts)`

## Configuration Lifecycle

### Configuration versions

Each configuration:

- belongs to one anchor
- has a version number
- has `draft`, `review`, `published`, or `archived` status
- is scoped by effective time range

This is the core mechanism that lets reporting and publishing resolve the correct roadway interpretation for a study date.

### Approaches and lanes

An approach captures:

- road identity
- direction/cardinal labeling
- bearing
- control type
- turning permissions
- corridor linkage

A lane captures:

- left-to-right order
- lane kind
- optional width and pocket length

### Field capture

`roadway_config.capture` exists for field-observed or office-submitted payloads and is guarded by the `json_contract` schema registry trigger.

## Current Tool Behavior

The controller and repo layer show a tool that already supports:

- listing anchors, approaches, corridors, OSM ways, and OSM intersections
- assigning/unassigning collection sites to anchors
- assigning/unassigning corridors
- editing anchor metadata
- creating new configuration versions
- updating approaches and replacing lanes
- marking anchors non-provisional after confirmation

There is also an in-app seed trigger flow:

- `seedAnchorController(...)`

This calls an external API gateway endpoint:

- `${AWS_DATALAKE_API_URL}/anchor/seed-collection-site`

The deployed auto-seeder behind that flow is the `seed-roadway-anchor` Lambda. Its current docs show that it:

- accepts SQS and direct-invocation inputs
- loads collection-site context and helper roads
- tries to match an existing anchor first
- seeds provisional anchors when matching is weak
- creates taxonomy roads, corridor groups/segments, anchor-road mappings, configurations, and approaches
- records rich provenance in `collection_site_anchor_map.match_explanation`

That means the current architecture is hybrid:

- the editing and review experience lives in Ops Center
- the seeding runtime lives outside this repo in `datalake_functions`
- both parts are implemented and operational

## Relationship To Other Domains

### Count assembly

Roadway config gives count assembly a canonical movement interpretation and the anchor/config context needed for consistent preview and publishing.

### Delivery and publishing

Published stations are bound to anchors and configurations, so roadway config is part of the immutable delivery story.

### Reporting

The QC-Hub-compatible reporting path already depends on roadway semantics for lane/control rendering and for long-term replacement of legacy lane-config sources.

### Near Miss

Near Miss project locations already reference collection sites. As the system matures, roadway anchors/configs can help align location identity and future spatial analysis.

## What Is Still Partial Or Evolving

- SmartSuite-driven collection-site creation is still documented more fully than it is represented in this repo.
- The roadway seeder is implemented, but its runtime code and some orchestration details live in `datalake_functions` rather than here.
- The seeder docs themselves still note some follow-up items and enum/control-type gaps, so "implemented" does not mean "finished."
- The older docs refer to roadway draft tables and flows that do not perfectly match the current `roadway_config` DDL, tool code, and deployed seeder behavior. The v2 docs should be treated as the current baseline.
