# QC Reels

## Purpose

QC Reels is the movement-review and clip-export tool for `all_tracks` sources. It is optimized for fast operator review of individual tracks and small curated playlists, not for full-segment analytics.

## Current Implementation Status

Primary code surfaces:

- UI: `apps/ops-center/app/tools/qc-reels/**`
- worker: `apps/ops-center/app/tools/qc-reels/workers/qcReelsDecode.worker.ts`
- controllers/services: `apps/ops-center/server/qc-reels/**`
- repos: `apps/ops-center/server/repos/qc-reels/**`
- contracts: `packages/schemas/src/qc-reels.contract.ts`

Schema and migration surfaces:

- `db/migrations/2026-02-16-create-qc-reels-saved.sql`
- `db/migrations/2026-02-17-qc-reels-schema-and-export-jobs.sql`
- `db/migrations/2026-02-17-qc-reels-export-cancel-status.sql`

## Main Workflow

### 1. Context and file discovery

The tool starts with:

- sitecode lookup
- camera selection
- `all_tracks` file selection

The server exposes:

- context lookup
- per-sitecode file options
- optimization status

### 2. Movement loading

The movement load path returns:

- paged movement rows
- filter options
- time bounds
- signed video URLs
- signed bundle URLs per segment
- encoding status metrics

Relevant controller entry point:

- `loadMovements(...)` in `apps/ops-center/server/qc-reels/controllers/qc-reels.controller.ts`

### 3. Fast preview

The normal preview path is client-heavy:

- fetch `tracks.index.json`
- range-fetch the selected track bytes from `tracks.bin` or `tracks.bin.gz`
- decode rows in a Web Worker
- render video plus trajectory/bbox overlay in-browser

Fallback behavior still exists through the server preview path if the client decode flow cannot be used.

## Artifact Model

QC Reels depends on the trajectory encoder's `qc-reels` profile.

Expected core artifacts:

- `tracks.index.json`
- `tracks.bin` or `tracks.bin.gz`
- `meta.json`

Optional artifacts may also exist depending on runtime flags:

- binary index
- lite index

The key architectural point is that QC Reels uses a per-track random-access bundle, not a simplified path-analysis format.

## Saved Reels And Export Jobs

### Saved reels

Saved reel records are persisted in:

- `raw_metadata_catalog.qc_reels`

They store:

- name and description
- sitecode/camera/file scope
- tags
- playlist items

### Export jobs

Async export jobs are persisted in:

- `qc_reels.export_jobs`

The current controller/service layer supports:

- create job
- poll status
- cancel

Dispatch can be backed by:

- Lambda
- SQS

The docs and code both show SQS as a migration-ready option even if Lambda is the default.

## Contracts

`packages/schemas/src/qc-reels.contract.ts` is the best concise inventory of the current tool contract.

Important request/response families include:

- context
- files
- optimize
- load
- preview
- export jobs
- saved reels

## Relationship To Other Systems

### Datalens

QC Reels relies on Datalens-linked `all_tracks` lineage and segment artifacts.

### Trajectory encoder

QC Reels relies on the encoder's `qc-reels` bundle profile and on file-artifact readiness state.

### Near Miss

Near Miss MVP intentionally reuses QC Reels ideas for:

- fast playback
- overlay decoding
- clip-oriented review

The trajectory encoder design review makes that relationship explicit.

## Current State Notes

- QC Reels is implemented, not conceptual.
- The current architecture is optimized for operator latency and repeated row-level review.
- The saved-reel and export-job model makes it a durable workflow, not just a transient page tool.
