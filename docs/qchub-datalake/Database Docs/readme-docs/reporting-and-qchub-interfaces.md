# Reporting And QC-Hub Interfaces

## Purpose

This document captures the current reporting implementation in Ops Center and explains how it interfaces with QC-Hub-compatible report expectations.

## Current Implementation Status

Primary code surfaces:

- route: `apps/ops-center/app/api/reporting/render/[publishingIndexId]/route.ts`
- controller: `apps/ops-center/server/core/controllers/reporting.controller.ts`
- adapter: `apps/ops-center/server/reporting/qchub-report.adapter.ts`
- templates: `apps/ops-center/server/reporting/templates/**`
- UI entry point: `apps/ops-center/app/tools/count-data-explorer/**`

Existing handoff docs still relevant for requirements:

- `turn-count-report.md`
- `tube-count-report.md`
- `docs/reporting/qchub-adapter-spec.md`

## Current Reporting Shape

The current reporting system does not render directly from arbitrary tables per template. Instead, it uses a normalized report contract and then applies a renderer.

Current template set:

- `qchub.turn.csv`
- `qchub.turn.html`

Those templates are registered in:

- `apps/ops-center/server/reporting/templates/index.ts`

## Current Adapter Boundary

The active adapter is:

- `buildQchubReportContract(...)`

in:

- `apps/ops-center/server/reporting/qchub-report.adapter.ts`

This adapter currently builds report contracts from QC-Hub-backed data keyed by the publishing index context.

It already has separate branches for:

- turn/intersection data
- tube/midblock data

Even though the currently registered render templates are centered on turn reports, the adapter code already carries both concepts.

## Current Interfaces To QC-Hub

### Turn data

The adapter reads QC-Hub tables for:

- location and street metadata
- comments
- lane configuration
- interval movement counts

It then builds a stable report contract with:

- identity
- metadata
- coverage
- bin scheme
- movement volumes
- lane configuration

### Tube data

The adapter also contains a tube branch that reads QC-Hub:

- volume data
- vehicle class data
- speed-bin data
- comments and location metadata

This means the repo already contains most of the contract-shaping logic needed for tube reporting, even if the active template registration has not yet been expanded to full parity.

## Relationship To Published Datalake Data

The reporting architecture is moving toward a model where:

- published datalake data provides the long-term source
- QC-Hub-compatible templates remain the output expectation where needed

The existing handoff docs define the mapping needed from:

- `source_summary.*`
- `delivery.*`
- `roadway_config.*`
- `ops_config.*`

to QC-Hub-style report structures.

## Current Gaps And Truths

### True today

- reporting code exists in this repo
- turn-report rendering is implemented
- a normalized report contract exists
- count-data explorer can launch report rendering

### Not yet complete

- full template parity for all legacy report variants
- complete production-ready tube-report template set in the current renderer registry
- fully datalake-native reporting flow without QC-Hub-shaped compatibility assumptions

## Why The Older Tube And Turn Docs Still Matter

The two handoff docs remain valuable because they document:

- exact QC-Hub query shapes
- peak-hour/peak-15 calculations
- CSV/PDF output semantics
- legacy edge cases
- target mappings into datalake summary structures

In other words:

- the current code is the implementation baseline
- the older tube/turn docs are still the best parity-spec references

## Recommended Maintenance Model

When reporting changes:

1. Update this page.
2. Update the specific domain parity doc if the change affects turn or tube semantics.
3. Keep `qchub-report.adapter.ts` and the registered template list aligned with the documented supported outputs.
