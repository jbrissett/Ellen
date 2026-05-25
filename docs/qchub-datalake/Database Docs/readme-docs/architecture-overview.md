# Architecture Overview

## Purpose

This document is the top-level architecture reference for `datalake-ops-center` as it exists today.

It covers:

- the business purpose of the datalake
- the end-to-end operational flow from order to delivery
- the runtime shape of the monorepo
- the major tools exposed through Ops Center
- the core datalake schemas used by those tools
- the difference between implemented surfaces and planned extensions

This is the best starting point for business managers, operations managers, and technical leads who need a shared picture of what the datalake is for, how it is used, and where Ops Center fits.

## TL;DR

The QC datalake exists to turn field-collected traffic study inputs into structured, quality-controlled, traceable outputs that can be reviewed, assembled, published, and reported consistently across many study types and source formats.

At a high level, the intended operational flow is:

1. an order is created and approved
2. study locations and sitecodes are established
3. collection sites are linked to roadway anchors and configurations
4. raw source files are registered and summarized
5. analysts assemble final count outputs and review quality
6. approved outputs are published with lineage
7. reports and downstream client-facing deliveries are produced

The system is designed to support that flow while preserving:

- operational repeatability
- source-to-output lineage
- support for multiple vendor/source formats
- stable roadway and location identity
- reviewable quality-control workflows

## Business Intent

The datalake is not just a storage layer. It is the operational backbone for moving traffic-study work from collection through final delivery.

From a business and operations perspective, it is intended to provide:

- one place to register and understand raw study inputs
- one canonical model for where data was collected and how the roadway is configured
- one repeatable way to transform mixed source data into final published counts
- one review surface for operational QA tasks such as count assembly, QC Reels, and Near Miss review
- one durable lineage trail from delivered outputs back to source files, rules, and operator decisions

## Goals And Tenets

- Single source of operational truth from file registration through publish and reporting.
- Schema-first, mapping-late handling of heterogeneous source formats.
- Stable physical-site identity through collection sites, anchors, and configuration versions.
- Deterministic lineage so published outputs can be traced back to source data and assembly decisions.
- Human-in-the-loop operations, not just automation: operators can review, correct, curate, and approve important workflow stages.
- Idempotent orchestration where upstream events and jobs can be retried safely.

## End-To-End Business Flow

The business flow below reflects the intended overall operating model, while later sections in this document distinguish which parts are already implemented in this repo and which parts are implemented externally or still evolving.

1. A customer order is created in QC-Hub, with one or more locations and time windows. Those location/time windows become sitecodes.
2. The order moves through approval workflow.
3. Approved locations become collection sites in the datalake operating model.
4. Collection sites are linked to roadway anchors and roadway configurations so the physical context of the study is modeled explicitly.
5. Operations deploy, retrieve, and process field equipment or source media.
6. Source files are registered in the datalake. These may be summarized vendor outputs, per-vehicle files, tube counts, video-derived tracks, or other supported source types.
7. Summarization produces canonical time-bucketed rows and QA metrics.
8. Analysts use Count Assembly to select, normalize, merge, and review the source data that should make up the final count output.
9. Final reviewed results are published into immutable delivery structures with deterministic lineage.
10. Reports and downstream client-facing outputs are generated from that published layer.
11. Other operational tools, such as QC Reels and Near Miss MVP, provide adjacent QA, review, and analysis workflows tied to the same datalake context.

## Usage Context By Audience

### Business and operations managers

This architecture matters because it defines:

- how orders become operational work
- where quality-control steps happen
- how final outputs can be trusted and reproduced
- which tools staff use at different stages of the workflow

### Product and technical leads

This architecture matters because it defines:

- domain ownership boundaries
- core schemas and system interfaces
- where automation exists versus where manual review remains essential
- how new workflows should attach to the existing datalake model

## Current System Shape

`datalake-ops-center` is a Next.js 14 monorepo that acts as the operator-facing application for several datalake workflows:

- roadway configuration
- count assembly
- QC Reels
- Near Miss MVP
- reporting over published data

The repo contains both the UI routes and the server/API layer that sits in front of Aurora Postgres, QC-Hub, Datalens-linked metadata, and AWS-backed async jobs.

Ops Center should be thought of as the operator workbench for the datalake, not as the whole datalake by itself. Some critical runtime components live in adjacent repositories or managed AWS services.

## Runtime Stack

### Frontend

- `apps/ops-center`
  - Next.js 14 App Router
  - TanStack Query for server state
  - `@qc/ui` and `@qc/tokens`
  - MapLibre for map-based tools
  - Cognito OIDC auth

### Backend

- `apps/ops-center/app/api/**`
  - Next.js API routes
- `apps/ops-center/server/**`
  - controllers, services, and SQL repos by domain
- `packages/schemas`
  - shared Zod contracts for client/server boundaries

### Data and external runtime dependencies

- Aurora PostgreSQL
- S3
- Lambda-backed jobs and callbacks
- QC-Hub database access
- Datalens-linked source metadata and artifacts
- trajectory encoder output from `datalake_functions`

## Major Tool Surfaces

### Roadway Configuration

Implemented in:

- `apps/ops-center/app/tools/roadway-config/**`
- `apps/ops-center/server/core/controllers/roadway-config.controller.ts`
- `apps/ops-center/server/repos/roadway-config/**`

This is a live map/editor workflow for anchors, corridors, collection-site assignment, configuration versions, approaches, and lanes.

### Count Assembly

Implemented in:

- `apps/ops-center/app/tools/count-assembly/**`
- `apps/ops-center/app/api/assembly/**`
- `apps/ops-center/server/repos/count-assembly/**`

This is the count-preview workspace over `assembly.*`, `source_summary.*`, and `delivery.*`.

### QC Reels

Implemented in:

- `apps/ops-center/app/tools/qc-reels/**`
- `apps/ops-center/server/qc-reels/**`
- `apps/ops-center/server/repos/qc-reels/**`

This is a movement review and clip export workflow that uses encoded trajectory bundles for fast client-side preview.

### Near Miss MVP

Implemented in:

- `apps/ops-center/app/tools/nearmiss/mvp/**`
- `apps/ops-center/app/api/nearmiss/mvp/**`
- `apps/ops-center/server/nearmiss/**`
- `apps/ops-center/server/repos/nearmiss/**`

This is now the active Near Miss product surface. It includes project initialization, detection-set import, QA, chart review, camera overlays, overlay bundles, reels, baseline reel review, and report-review flows.

### Reporting / Count Data Explorer

Implemented in:

- `apps/ops-center/app/tools/count-data-explorer/**`
- `apps/ops-center/app/api/reporting/**`
- `apps/ops-center/server/reporting/**`

Current implemented report rendering is centered on QC-Hub-style turn reports, with an adapter that builds a stable report contract from QC-Hub source data.

## System Overview

At the highest level, the current architecture spans:

- QC-Hub for order/location context and legacy reporting semantics
- Ops Center as the operator-facing workflow application
- Aurora Postgres as the transactional and workflow system of record
- S3 and related jobs for file and artifact storage
- external Lambda-based workers for encoding, seeding, rendering, and other async tasks
- published delivery structures as the handoff point for reports and downstream consumption

## Core Schema Responsibilities

### `index_schema`

- collection sites
- site source records
- roadway anchors
- collection-site to anchor mappings

### `roadway_config`

- roadway configuration versions
- approaches
- lanes
- captures
- configuration audit history

### `raw_metadata_catalog`

- registered source files
- file artifact tracking
- QC Reels saved reels

### `source_summary`

- canonical summarized intervals
- movement totals
- speed totals
- QA metrics

### `assembly`

- sessions
- selection rules
- preview grids
- lineage
- overrides and QA flags

### `delivery`

- immutable published results
- publishing stations
- publishing bindings

### `nearmiss`

- projects and locations
- sitecodes and cameras
- detection sets, detections, events, reviews
- chart snapshots and backgrounds
- overlay bundles and jobs
- reels, snapshots, render jobs, review jobs

## Architecture Status By Area

### Implemented now

- roadway configuration UI and server layer
- roadway auto-seeding runtime through the external `seed-roadway-anchor` Lambda
- QC Reels load/preview/save/export workflow
- Near Miss MVP route family and core data model
- reporting adapter and turn-report templates
- count assembly preview and publishing-related schema model

### Present but not fully owned by this repo

- end-to-end SmartSuite-driven collection-site creation flow
- count publishing manager as a first-class tool surface
- full datalake-native tube report rendering parity in the current report template set
- finalization of all reporting paths around published datalake data instead of QC-Hub inputs

Important distinction:

- the roadway auto-seeder is implemented and operational
- its Lambda code and runtime flow live outside this repo in `C:\Repos\datalake_functions\seed-roadway-anchor`
- this repo contains the operator-facing roadway tool and the downstream schema/API surfaces that interact with seeded anchors and configurations

## Repo Navigation Pointers

- app shell and tool nav: `apps/ops-center/app/shell/nav-config.ts`
- shared architecture notes: `.ai/02-architecture.md`
- current shared contracts: `packages/schemas/src/*.contract.ts`
- schema and migration baseline: `db/schemas/**`, `db/migrations/**`

## Recommended Reading Order

1. [integrations-and-dataflows.md](./integrations-and-dataflows.md)
2. [roadway-config.md](./roadway-config.md)
3. [count-assembly-and-publishing.md](./count-assembly-and-publishing.md)
4. [qc-reels.md](./qc-reels.md)
5. [near-miss-mvp.md](./near-miss-mvp.md)
6. [reporting-and-qchub-interfaces.md](./reporting-and-qchub-interfaces.md)
