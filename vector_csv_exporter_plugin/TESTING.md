# Testing

## Automated tests

`export_utils.py` is pure, QGIS-independent Python, and is fully covered by pytest:

```bash
pip install pytest
pytest -q
```

CI (`.github/workflows/ci.yml`) runs this on every push/PR to `main` across Python 3.10-3.12.

Everything in `dock_widget.py` (`VectorCsvExporterDockWidget`, `ExportTask`) depends on live PyQGIS
objects and cannot be covered by that pytest suite. Most of it still has to be checked manually via
the checklist below -- install the plugin into a QGIS 3.44+ profile (via Plugin Reloader or by
copying `vector_csv_exporter_plugin` into the profile's `python/plugins` folder) and work through it
before a release or after any change to `dock_widget.py`.

A handful of items (marked below with a script name) are instead covered by real, runnable
integration tests in `manual_qgis_tests/`, using the Python interpreter QGIS ships with -- see
`manual_qgis_tests/README.md` for how to run them. They exercise the actual plugin code headlessly
(no QGIS window ever opens) and were how the bug noted under item #36 was actually found.

## Setup / lifecycle

1. Install into QGIS, enable via Plugin Manager, confirm the toolbar icon and "Vector" menu entry
   appear (`plugin.py:initGui`).
2. Open the dock, close it, reopen via the action -- confirm it re-populates layers each time
   (`run()` calls `populate_layers()`).
3. Disable the plugin via Plugin Manager while the dock is open -- confirm no crash, and that the
   "Export vector layers to CSV" entry is actually removed from the Vector menu (regression check
   for the `removePluginVectorMenu` fix).

## Layer/field selection UI

4. Load a mix of vector and raster layers -- confirm rasters are skipped with a warning message in
   the log, not listed as checkable items.
5. Click a layer in the list -- confirm its field list populates, all fields checked by default.
6. Uncheck individual fields, switch to another layer, switch back -- confirm per-layer field
   selection persists (`_layer_field_selection`).
7. Toggle "Select All" -- confirm all layer checkboxes flip together.
8. Click "Refresh" after adding/removing a layer in the project -- confirm the list updates and
   selection state for still-present layers is preserved.

## Core export -- grouping logic

9. Export two layers with identical field names/types -- confirm they merge into one CSV
   (`data_header_signature` match).
10. Export two layers with different schemas -- confirm separate `_group1.csv`/`_group2.csv` files,
    correctly named via the prefix.
11. Export a single layer alone -- confirm output is `<prefix>.csv` with no `_group1` suffix.

## Duplicate/reserved field names

12. Export a layer with two fields sharing a name case-insensitively (e.g. `Name`/`name`) -- confirm
    one is renamed (`Name_ATTR`), a log message names the correct original field, and both columns
    carry distinct correct values.
13. Export a layer that has a real field literally named `WKT` or `Source_Layer` -- confirm it's
    renamed (e.g. `WKT_ATTR`) rather than clashing with the appended columns, and the warning log
    names the correct field/layer. Test with **two such layers in the same group with different
    original casing** and confirm each log line names its own layer's field correctly (regression
    test for the stale-variable log bug).

## Data correctness

14. Deselect some fields on a layer, export -- confirm only selected columns appear and values still
    line up correctly (validates true-index preservation through dedup/renaming).
15. Export a layer with zero features -- confirm header-only CSV plus a warning log entry.
16. Export a layer with zero attribute fields -- confirm geometry-only CSV (`SOURCE_LAYER`, `WKT`
    columns only).
17. Export a layer with a genuinely NULL attribute value -- confirm the cell is blank in the CSV, not
    the literal text `NULL` (regression test for the PyQGIS `NULL`-sentinel fix).

## Geometry / CRS

18. Export layers with different native CRSes together -- confirm all WKT geometries land in a single
    consistent CRS (default EPSG:4326) and coordinates are directly comparable.
19. Toggle "Keep original CRS" -- confirm geometries are *not* reprojected in that case.
20. Change the CRS selector to something other than EPSG:4326 -- confirm output WKT is in the chosen
    CRS.
21. **WKT precision**: open an exported CSV and inspect coordinate values -- confirm no long
    floating-point tails (e.g. `-97.74` not `-97.73999999999999488`).
22. **CRS pre-check** (`manual_qgis_tests/test_22_crs_precheck.py`): select a layer whose CRS cannot
    be reprojected to the target CRS -- confirm a warning dialog appears *before* the export starts,
    naming the affected layer, rather than only discovering the problem feature-by-feature deep into
    the run. (Note: an invalid/undefined CRS alone doesn't reliably trigger this in practice -- see
    the script for why it mocks the reachability check instead.)

## CSV formatting

23. Try each delimiter option (`,`, `;`, Tab) -- confirm the output file actually uses it.
24. Try each encoding option (utf-8, latin-1, cp1252) against a layer with accented/non-ASCII
    attribute values -- confirm correct decoding when reopened.
25. Add attribute values starting with `=`, `+`, `-`, `@` (e.g. `=1+1`) -- confirm they're prefixed
    with `'` in the CSV (CSV-injection guard).

## Output handling

26. Enter a prefix with spaces/special characters (e.g. `My Export #1`) -- confirm it's sanitized
    (e.g. `My_Export_1`) in the filename.
27. Point the output directory at a read-only location -- confirm the plugin reports the
    write-permission error rather than failing silently or crashing.
28. **Overwrite warning** (`manual_qgis_tests/test_28_overwrite_warning.py`): run the same export
    twice into the same directory with the same prefix -- confirm the second run shows a
    confirmation dialog listing the files that will be overwritten (group CSV(s), `.csvt`
    sidecar(s), and manifest), and that declining aborts without touching any files.
29. **CSVT sidecar** (`manual_qgis_tests/test_29_csvt.py`): after export, confirm a `<output>.csvt`
    file exists next to each group CSV, with one quoted type per column matching the header (e.g.
    `"Integer","String","Real",...,"String","String"` -- the last two always `String` for
    `SOURCE_LAYER`/`WKT`). Re-import the CSV into QGIS via "Add Delimited Text Layer" and confirm
    numeric/date fields come back typed instead of as text.

## Progress / cancellation

30. Export a large layer (tens of thousands of features) -- confirm the progress bar advances
    smoothly and the UI stays responsive (background `QgsTask`).
31. Click "Cancel" mid-export -- confirm the task stops promptly, the partial output CSV is deleted,
    and the status log shows the cancellation message.
32. Try clicking "Export" again while an export is already running -- confirm it's blocked with the
    "already in progress" warning instead of starting a second task.

## Audit trail: summary, row-count and feature-ID verification, manifest

33. Run a normal export and check the log for a `Layer '<name>': N of N feature(s) exported.` line
    per layer, and a final `Summary: N of N feature(s) exported across L layer(s) into F file(s).`
    line -- confirm the numbers reconcile (written + skipped == attempted).
34. Confirm a `Verified '<file>': row count matches (N rows).` log line appears for each output file.
35. Confirm a `<prefix>_manifest.csv` is written alongside the output, with one row per source layer
    (`source_layer, output_file, features_in_layer, features_exported, features_skipped,
    skip_reasons, missing_feature_ids, unexpected_feature_ids`), a `TOTAL` row, and
    `export_status`/`generated_at`/`delimiter`/`encoding` metadata rows -- confirm the `TOTAL` row
    matches the `Summary` log line.
36. **Partial failure** (`manual_qgis_tests/test_36_partial_failure.py`): force a partial failure
    (e.g. point the output directory at a location that becomes unwritable partway through a
    multi-group export, or revoke write permission mid-run) -- confirm the manifest is still written
    (with `export_status` reflecting the error) and that the partially-written output CSV for the
    failing group is cleaned up, not left corrupt on disk.
37. If feasible, edit a layer's features while an export of it is running -- confirm the feature-ID
    verification logs an `unexpected feature ID(s)` warning rather than silently ignoring the
    mismatch.

## Regression checklist for previously-fixed bugs

Re-run these specifically whenever `dock_widget.py` or `export_utils.py` change, since each guards
a bug that has actually shipped before:

- #12/#13 above (stale-variable rename-log bug)
- #21 above (WKT floating-point precision)
- #17 above (PyQGIS `NULL` rendering as literal text)
- #3 above (plugin unload menu removal)
- #36 above (partial-file cleanup and manifest on hard failure; also asserts the cleanup log message
  correctly describes the write failure instead of always saying "after cancellation")
