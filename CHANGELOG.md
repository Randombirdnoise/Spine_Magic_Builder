# Changelog

## 1.1.2 - 2026-08-28

- Moved GUI folder scanning and candidate thumbnail loading off the Tk event loop to keep the picker responsive on large candidate sets.
- Added stale-result guards so delayed scans and thumbnail loads cannot overwrite the current selection.
- Prevented overlapping candidate activation jobs and duplicate automatic SpineViewer launches.
- Added a rolling GUI debug log for scan, preview, activation, blacklist, and finalize timings.
- Updated the GUI launcher to prefer `pythonw.exe` before `pyw.exe -3`, matching installs where Pillow is available on the direct Python executable.

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
