# Near Miss MVP

## Purpose

Near Miss MVP is the active Near Miss workflow in Ops Center. It replaces the older idea of a separate `nearmiss_ops` product surface with a vertically integrated `nearmiss` schema and `/tools/nearmiss/mvp/*` route family.

## Current Source Of Truth

Use these as the current truth:

- `apps/ops-center/app/tools/nearmiss/mvp/**`
- `apps/ops-center/app/api/nearmiss/mvp/**`
- `apps/ops-center/server/nearmiss/**`
- `apps/ops-center/server/repos/nearmiss/**`
- `packages/schemas/src/nearmiss.contract.ts`
- `C:\codex-projects\near-miss-refactor\readme.md`

Important rule:

- older `nearmiss_ops` design notes are historical context
- the active implementation direction is the `nearmiss` schema and MVP route family

## Current User-Facing Route Family

- `/tools/nearmiss/mvp`
- `/tools/nearmiss/mvp/[orderNo]`
- `/tools/nearmiss/mvp/[orderNo]/[projectLocationId]`
- `/tools/nearmiss/mvp/[orderNo]/[projectLocationId]/setup`
- `/tools/nearmiss/mvp/[orderNo]/[projectLocationId]/report`

## Core Schema Model

### Base MVP workflow tables

From `db/migrations/2026-03-25-nearmiss-mvp-phase1-bootstrap.sql`:

- `nearmiss.projects`
- `nearmiss.project_locations`
- `nearmiss.project_sitecodes`
- `nearmiss.project_location_cameras`
- `nearmiss.detection_sets`
- `nearmiss.detections`
- `nearmiss.events`
- `nearmiss.event_reviews`

This is the base project, source, event, and review model.

### Additional implemented slices

Later migrations add:

- dedupe groups
- reels and reel snapshots
- render jobs
- chart snapshots
- camera overlay backgrounds
- overlay bundles and bundle jobs
- baseline reel review jobs and items

These are not speculative. They are already represented in schema and service/controller code.

## Current Workflow

### 1. Project initialization

An operator initializes a project from an order number. The system creates:

- one `nearmiss.projects` row per order
- location rows
- linked sitecodes
- camera context

### 2. Source discovery and detection-set import

The setup flow discovers which source files are near-miss-capable and imports detection sets into the `nearmiss` schema.

This supports the current direction of moving the workable Near Miss state into the datalake instead of leaving it only in Datalens tables.

### 3. QA workspace

The QA surface includes:

- filterable/virtualized event review
- event-level keep/reject decisions
- `C`, `V`, and `H`-style workflow flags through review state
- on-demand playback
- shared location header/context

### 4. Analysis and overlays

The MVP now includes:

- chart review
- saved chart snapshots
- camera overlay backgrounds
- overlay readiness tracking
- overlay bundle job orchestration and callbacks

This is important because Near Miss is no longer just a table-review tool.

### 5. Reels and baseline review

The MVP also includes:

- location reels
- reel snapshots
- reel render jobs
- baseline review jobs for standard PET buckets
- acceptance flow that materializes reviewed reel state

This is the part of the architecture that replaces ad hoc database-flag editing and manual clip management.

### 6. Report review and export

The current route set includes a dedicated report view and service layer for report-review data assembly.

There is also a project Excel export path in the controller/service layer.

## Relationship To Datalens

The current Near Miss architecture still depends on Datalens-originated source material:

- conflict detections
- trajectory TXT source
- segment and camera context

But the workflow state that operators need is now being re-homed into `nearmiss.*` tables so that:

- reviews survive reruns
- versions can be reasoned about more cleanly
- reporting and reels are first-class workflows

## Relationship To The Trajectory Encoder

Near Miss relies on the trajectory encoder for overlay bundles.

The encoder design review confirms a dedicated profile:

- `near_miss_overlay_bundle_v1`

This profile is distinct from:

- `datalens`
- `qc-reels`

That distinction matters because Near Miss overlays package selected event tracks, not whole-segment full-fidelity replay or whole-segment path analysis.

## Current State Notes

- Near Miss MVP is implemented and active in Ops Center.
- The architecture is now much broader than "import detections and review a table."
- Reel review, overlay prep, camera overlay, chart snapshots, and report review are part of the current system, not just planning artifacts.
- The main documentation risk before this v2 set was that the code moved faster than the README narrative. This page should now be treated as the high-level baseline.
