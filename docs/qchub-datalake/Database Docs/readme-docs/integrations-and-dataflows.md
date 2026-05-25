# Integrations And Dataflows

## Purpose

This document describes how `datalake-ops-center` interfaces with the adjacent systems that provide source data, metadata, background processing, and report compatibility.

## External Systems

### QC-Hub

Current role:

- source of order/site metadata
- source of legacy turn and tube reporting data
- compatibility target for report formats and terminology

Current integration points in this repo:

- reporting adapter in `apps/ops-center/server/reporting/qchub-report.adapter.ts`
- `qchub` schema SQL references under `db/schemas/qchub/**`
- historical and compatibility docs in `turn-count-report.md` and `tube-count-report.md`

### Datalens

Current role:

- source of counted file context
- source of segment metadata and trajectory TXT source material
- source of legacy near-miss detections used for import or parity context

Current integration points in this repo:

- QC Reels artifact and source lookup repos
- Near Miss source discovery, detection-set import, playback, and overlay prep

### Trajectory Encoder (`datalake_functions`)

Current role:

- produces QC Reels trajectory bundles
- produces Near Miss overlay bundles through the `near_miss_overlay_bundle_v1` profile

Reviewed design reference:

- `C:\Repos\datalake_functions\datalake-trajectory-encoder-v2\TRANSFORMATION_DESIGN_REVIEW.md`

### AWS runtime

Current role:

- S3 for source and generated artifacts
- Lambda for encoder and export/render workers
- presigned URLs for browser playback and artifact access

### SmartSuite

Current role in architecture:

- upstream order approval trigger in the intended collection-site creation flow

Current state:

- referenced in architecture docs
- not fully represented as implemented application logic inside this repo

### Seed Roadway Anchor Lambda

Current role:

- implemented roadway auto-seeder for collection sites
- matches existing anchors or seeds new provisional anchors
- creates or reuses taxonomy roads, corridor groups/segments, roadway configurations, and approaches
- writes detailed provenance into `collection_site_anchor_map.match_explanation`

Current implementation location:

- `C:\Repos\datalake_functions\seed-roadway-anchor\README.md`
- `C:\Repos\datalake_functions\seed-roadway-anchor\function-flow.md`

Current runtime shape from those docs:

- supports SQS-triggered collection-site workflows
- supports direct invocation/manual task routing
- attempts existing-anchor match before seeding
- supports intersection, midblock, and study fallback seeding paths
- persists corridor and approach derivations for downstream QA and editing

## Primary End-To-End Flows

### 1. Collection site to roadway configuration

Current implemented pieces:

- collection sites and roadway anchors exist in the schema model
- roadway auto-seeding Lambda is implemented and operational
- roadway configuration editor exists in Ops Center
- collection-site to anchor assignment and config editing are handled through the roadway-config tool

Implemented outside this repo:

- `seed-roadway-anchor` Lambda runtime
- SQS-triggered seeding workflow
- direct-invocation/manual seed task support

Still evolving or not fully represented in this repo:

- SmartSuite approval trigger
- `create_collection_sites`
- `get_osm_data`

Interpretation for current architecture:

- roadway configuration is operational in Ops Center
- roadway auto-seeding is operational, but its implementation lives in `datalake_functions`
- the upstream order-approval-to-site-creation story is still only partially captured in this repo

### 2. Registered source to count assembly to publish

Flow:

1. source files are registered in `raw_metadata_catalog`
2. summarized source data lands in `source_summary`
3. analysts create or reopen `assembly.assembly_session`
4. selection rules define which source rows contribute to each preview cell
5. `assembly.compute_preview_snapshot(...)` materializes preview tables and lineage
6. publish flow writes immutable `delivery.*` rows
7. report and downstream publishing surfaces consume delivery or adapter outputs

Relevant repo/code references:

- `docs/count_assembly_readme.md`
- `apps/ops-center/server/repos/count-assembly/**`
- `apps/ops-center/server/core/db_scripts/compute_preview_snapshot.sql`

### 3. QC Reels movement review and export

Flow:

1. operator selects a sitecode and camera
2. Ops Center resolves the target `all_tracks` source file
3. server checks encoded artifact readiness
4. movement rows and signed segment assets are returned
5. browser worker range-fetches `tracks.index.json` and `tracks.bin(.gz)`
6. operator builds a saved reel or starts an export job
7. export worker writes the output artifact and updates job status

Relevant repo/code references:

- `apps/ops-center/server/qc-reels/**`
- `apps/ops-center/app/tools/qc-reels/**`
- `db/migrations/2026-02-16-create-qc-reels-saved.sql`
- `db/migrations/2026-02-17-qc-reels-schema-and-export-jobs.sql`

### 4. Near Miss MVP project to QA to report review

Flow:

1. operator initializes a Near Miss project by order number
2. project locations, sitecodes, and cameras are created under `nearmiss.*`
3. source discovery identifies near-miss-capable source files
4. detection sets are imported for a location
5. events are curated in the QA surface
6. optional chart snapshots, camera backgrounds, and overlay bundles are created
7. reels and baseline review jobs help pre-curate video outputs
8. location report review assembles the curated summary before export/handoff

Relevant repo/code references:

- `apps/ops-center/app/tools/nearmiss/mvp/**`
- `apps/ops-center/server/nearmiss/**`
- `db/migrations/2026-03-25-nearmiss-mvp-phase1-bootstrap.sql`
- `db/migrations/2026-04-02-nearmiss-mvp-phase5-reels.sql`
- `db/migrations/2026-04-10-nearmiss-mvp-phase8-overlay-bundles.sql`
- `db/migrations/2026-04-21-nearmiss-mvp-phase11-baseline-reel-review.sql`

## Important Cross-System Contracts

### Shared contract approach

The repo increasingly uses `packages/schemas` as the stable contract layer between:

- UI and API routes
- API routes and services
- services and worker callback payloads

Important files:

- `packages/schemas/src/qc-reels.contract.ts`
- `packages/schemas/src/nearmiss.contract.ts`
- `packages/schemas/src/report.contract.ts`

### Callback-style async interfaces

Current async callback patterns exist for:

- Near Miss overlay-bundle jobs
- Near Miss reel render jobs
- Near Miss baseline review jobs
- QC Reels export jobs

These are important because the user-facing tools are not purely request/response. They depend on durable job rows plus worker callbacks.

## Current Architecture Boundaries

### What Ops Center owns

- operator-facing workflow state
- most domain APIs
- schema-backed workflow records in Aurora
- report-contract rendering logic inside this repo

### What external systems still own

- QC-Hub legacy source-of-truth reporting tables
- Datalens count-run and segment-source ecosystem
- trajectory encoding worker implementation
- some upstream order approval orchestration

## Known Tension Areas

### Legacy vs current Near Miss architecture

- older `nearmiss_ops` docs describe an earlier design
- current implemented direction is the `nearmiss` schema and `/tools/nearmiss/mvp/*` route family

### In-repo vs external roadway automation

- the roadway editor is implemented in Ops Center
- the roadway auto-seeder is implemented externally in `datalake_functions`
- the complete upstream approval/site-creation orchestration is still only partly codified in this repo

### Reporting parity vs datalake-native reporting

- current report implementation is strongest on QC-Hub turn-report compatibility
- older handoff docs define the mapping needed for datalake-native tube and turn parity
- the template set has not yet reached full parity across all report types
