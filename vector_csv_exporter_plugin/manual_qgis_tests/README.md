# Manual QGIS integration tests

`dock_widget.py` (`VectorCsvExporterDockWidget`, `ExportTask`) depends on real `qgis.core`/`qgis.gui`
objects and can't be covered by the plain `pytest` suite in `../tests/` (that suite only covers the
QGIS-independent `export_utils.py`). These scripts fill that gap: they run against a real QGIS
installation, but headlessly -- no QGIS application window ever opens.

## How they work

Each script uses the Python interpreter QGIS ships with (not a standalone `pip install qgis` --
that isn't a thing on Windows; PyQGIS needs the GDAL/Qt bindings that only come from a real QGIS
install) to import `qgis.core`/`qgis.gui`, initialize a `QgsApplication`, and exercise the plugin's
real code directly:

- `dock_test_helpers.py` -- shared setup: starts a GUI-enabled-but-invisible `QgsApplication`
  (`GUIenabled=True` so `QDockWidget`/`uic.loadUi()` work, but `.show()`/`.exec_()` are never
  called, so nothing appears on screen), loads the `fixtures/*.csv` files as point-geometry
  delimited-text layers the same way "Add Delimited Text Layer" would, and provides
  `FakeTaskManager` (runs a `QgsTask` synchronously instead of scheduling it on a background
  thread, since there's no running Qt event loop to deliver the completion signal) and
  `DialogRecorder` (a monkeypatch target for `QMessageBox.question` that records what it was
  called with and returns a canned answer, so a modal dialog never blocks waiting for a real
  click).
- Each `test_*.py` imports that helper, then either calls specific `ExportTask`/
  `VectorCsvExporterDockWidget` methods directly, or drives the full
  `export_selected_layers()` flow with `QFileDialog`/`QInputDialog`/`QMessageBox` monkeypatched.

## Running them

Find your QGIS installation's bundled Python wrapper (on Windows this is normally
`<QGIS install dir>\bin\python-qgis-ltr.bat`, or `python-qgis.bat` for some release channels), then:

```powershell
& "C:\Program Files\QGIS 3.44.12\bin\python-qgis-ltr.bat" "path\to\vector_csv_exporter_plugin\manual_qgis_tests\test_29_csvt.py"
```

Adjust the QGIS path for your machine and installed version. Each script prints its own progress
and ends with `TEST N PASSED`, or raises an `AssertionError`/traceback on failure.

## What each one covers

| Script | TESTING.md item | Covers |
|---|---|---|
| `test_29_csvt.py` | #29 | The `.csvt` field-type sidecar has the right type per column, resolved from a real `QgsField`, for a layer with mixed Integer/String/Real fields (also incidentally re-confirms NULL values export as blank, not the literal text `NULL`). |
| `test_36_partial_failure.py` | #36 | A genuine mid-write I/O failure (simulated via a feature-source wrapper that raises `OSError` partway through) removes the failing group's partial output but leaves the earlier, successful group's files intact, and the manifest still gets written with `export_status=ERROR`. |
| `test_22_crs_precheck.py` | #22 | Part A unit-tests `_crs_transform_is_reachable()`'s exception handling directly with a controlled fake transform. Part B drives the real `export_selected_layers()` dialog logic (with reachability mocked to a known result, since an invalid/undefined CRS turned out not to reliably raise `QgsCsException` in practice) and confirms declining aborts with no task run and no files touched, while accepting proceeds. |
| `test_28_overwrite_warning.py` | #28 | Running the same export twice into the same directory triggers no dialog the first time, exactly one dialog naming every file that would be overwritten the second time, declining leaves the existing file's mtime untouched, and accepting actually rewrites it. |

## Regression note

`test_36_partial_failure.py` also asserts the cleanup log message doesn't say "after cancellation"
for a genuine write failure -- this is exactly how a real bug was found: an earlier fix added
`_remove_partial_output()` calls to the write-failure paths without noticing its success message
was hardcoded to describe cancellation specifically.
