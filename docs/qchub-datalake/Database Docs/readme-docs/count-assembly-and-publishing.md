# Count Assembly And Publishing

## Purpose

Count Assembly is the workspace that turns heterogeneous source summaries into one reviewed preview for a sitecode and time window, with deterministic lineage into published delivery data.

## Current Implementation Status

Primary code and doc surfaces:

- UI: `apps/ops-center/app/tools/count-assembly/**`
- APIs: `apps/ops-center/app/api/assembly/**`
- repos: `apps/ops-center/server/repos/count-assembly/**`
- SQL reference: `apps/ops-center/server/core/db_scripts/compute_preview_snapshot.sql`
- existing deep-dive: `docs/count_assembly_readme.md`

This remains one of the most database-centered domains in the system.

## Main Data Model

### Source-side inputs

- `raw_metadata_catalog.files`
- `raw_metadata_catalog.file_summary_capability`
- `source_summary.summarized_data_interval`
- `source_summary.volume_by_movement`
- `source_summary.volume_by_speed`
- `source_summary.qa_metrics`

These provide the registered source, time grain, class/bank capabilities, and QA context.

### Assembly workspace

- `assembly.assembly_session`
- `assembly.selection_rule`
- `assembly.preview_snapshot`
- `assembly.preview_volume_by_movement`
- `assembly.preview_volume_by_speed`
- `assembly.preview_lineage`
- `assembly.manual_override`
- `assembly.qa_flag`

### Publish side

- `delivery.*`

The older docs reference `delivery.publish_preview(session_id, published_by)` as the publish boundary.

## Core Design Principle

The domain is built around mapping late:

- sources are registered in their native form
- preview computation aligns time buckets, categories, and movement labels at assembly time
- lineage is preserved so output can be audited back to the source rows and rules

## Current Assembly Flow

### Session and rule setup

An analyst creates or reopens an `assembly_session` scoped to:

- order
- sitecode
- study window
- requested bucket size
- optional target bin scheme

Rules then specify:

- time scope
- movement scope
- include or exclude behavior
- weighted merge behavior
- optional formula adjustments
- source bindings
- category include/exclude filters

### Preview computation

The existing assembly docs describe `assembly.compute_preview_snapshot(...)` as the preview engine.

That function:

- locks the session
- allocates a snapshot sequence
- creates baseline missing cells
- resolves effective rules
- collects source rows
- buckets and merges source contributions
- applies class/bank mapping helpers
- applies overrides
- writes preview rows and lineage

### Publish

Publish promotes preview output into immutable delivery rows with preserved lineage.

This is the handoff point for downstream published-data browsing and report rendering.

## Relationship To Roadway Configuration

Count assembly depends on roadway context in two ways:

- movement labels need a canonical roadway interpretation
- future corridor/balancing behavior depends on geometry and anchor structure

Older docs note that some corridor concepts should eventually live more cleanly with roadway configuration rather than inside assembly.

## Relationship To Reporting

Published count data is the long-term reporting source. The repo already contains:

- published-data browsing UI
- reporting render routes
- a QC-Hub compatibility adapter

That means count assembly and publishing are not isolated; they feed the reporting surface.

## Current State Notes

- The assembly domain is real and code-backed.
- Publishing manager as a distinct polished tool is still less mature than the assembly workspace itself.
- Documentation for assembly is already comparatively strong; the main v2 goal is consistency with the other domains.
