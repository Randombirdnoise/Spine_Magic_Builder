# Changelog

## Unreleased - atlas recovery

- Replaced one-atlas-only matching with a shared matcher for both builders. Every skeleton is independently evaluated against every atlas in its scan scope; all alternatives remain available without a candidate cap.
- Added **Atlas Matches** review with background report indexing, evidence details, on-demand pair building and SpineViewer launch. Deferred alternatives are the default; `--atlas-alternatives export` materializes all selected alternatives immediately. Source fingerprints protect against stale audits.
- Match JSON texture paths instead of bone/slot names; support old/new skin layouts, meshes, linked meshes and sequences. Read Spine 3.8 shared-string tables, validate binary string length prefixes for fallback matching, deduplicate atlas region names, and weight uncommon region evidence.
- Keep ambiguous binary matches provisional, prefer embedded wrapper atlases, and preserve weak substring candidates for review.
- Write a complete streaming JSONL audit and offer `--match-report-only`, `--atlas-candidates all`, and `--atlas-candidates best`.
- Default launchers and the GUI builder now scan the whole selected tree and no longer force unsupported atlas guesses. Explicit child-directory isolation remains available.
- Isolate output by run, exclude generated output from rescans, sanitize metadata-derived output names, and correct the reversed `--allow-reuse-textures` switch.
- Fix GUI post-build scanning to open the actual output run and use UTF-8 builder output on Windows.
- Add matching, export, source-preservation and scale regression tests.

## 1.1.1 - 2026-08-27

- Optimized GUI candidate navigation with cached blacklist, used-image, alias, atlas-page, and image-size lookups.
- Kept global hiding behavior intact while avoiding repeated full-state scans during next/previous candidate movement.
- Reduced duplicate preview refreshes when selecting candidates programmatically.

## 1.1.0 - 2026-08-16

- Added GUI **Finalize All** with confirmation, incomplete-set summary, and scan-root refresh after completion.
- Made accepted and blacklisted candidate images hide across the loaded tree by shared path, cleaned staged filename, and trailing long numeric suffix aliases.
- Updated candidate page counts to show the visible candidate total when hidden candidates are filtered.

## 1.0.0 - 2026-07-31

- Initial public release of the Spine set builder.
- Added dimension-based candidate staging and candidate materialization.
- Added the visual candidate picker and external SpineViewer launch workflow.
- Made all Windows launchers portable and removed machine-specific paths.
- Moved GUI state out of the repository and into the current user's local application-data folder.
