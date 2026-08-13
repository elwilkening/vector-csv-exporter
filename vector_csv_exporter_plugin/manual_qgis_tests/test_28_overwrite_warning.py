"""
Manual QGIS integration test #28: warn before overwriting existing output
files. Runs the same export twice into the same directory with the same
prefix and confirms the second run asks for confirmation, that declining
leaves the existing files untouched, and that accepting overwrites them.

All monkeypatches are restored in a finally block so several of these
scripts can run sequentially in one QGIS Python process without
cross-contaminating each other.
"""
import os
import tempfile
import time

from dock_test_helpers import start_qgis_gui, load_csv_layer, FakeTaskManager, DialogRecorder

start_qgis_gui()

from qgis.PyQt import QtWidgets  # noqa: E402
from qgis.core import QgsProject  # noqa: E402
import vector_csv_exporter_plugin.dock_widget as dw  # noqa: E402

_originals = {
    "QgsApplication": dw.QgsApplication,
    "getExistingDirectory": dw.QtWidgets.QFileDialog.getExistingDirectory,
    "getText": dw.QtWidgets.QInputDialog.getText,
    "question": dw.QtWidgets.QMessageBox.question,
}

try:
    layer = load_csv_layer("group_a.csv", "overwrite_test_layer")

    project = QgsProject.instance()
    project.removeAllMapLayers()
    project.addMapLayers([layer])

    fake_task_manager = FakeTaskManager()

    class FakeQgsApplication:
        @staticmethod
        def taskManager():
            return fake_task_manager

    dw.QgsApplication = FakeQgsApplication

    out_dir = tempfile.mkdtemp(prefix="overwrite_test_")
    dw.QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: out_dir)
    dw.QtWidgets.QInputDialog.getText = staticmethod(lambda *a, **k: ("overwrite_test", True))

    dock = dw.VectorCsvExporterDockWidget()
    dock.populate_layers()
    assert dock.layer_list_widget.count() == 1

    # --- Run 1: nothing exists yet, no warning expected ---
    recorder_run1 = DialogRecorder(QtWidgets.QMessageBox.Yes)
    dw.QtWidgets.QMessageBox.question = staticmethod(recorder_run1)

    dock.export_selected_layers()

    output_csv = os.path.join(out_dir, "overwrite_test.csv")
    output_csvt = os.path.join(out_dir, "overwrite_test.csvt")
    manifest_path = os.path.join(out_dir, "overwrite_test_manifest.csv")

    assert len(recorder_run1.calls) == 0, "first run into an empty directory should show no overwrite dialog"
    assert len(fake_task_manager.tasks_run) == 1
    assert os.path.exists(output_csv) and os.path.exists(output_csvt) and os.path.exists(manifest_path)
    print("Run 1 (empty directory): no dialog shown, files created as expected.")

    first_run_mtime = os.path.getmtime(output_csv)
    time.sleep(1.1)  # ensure a re-write would produce a detectably different mtime

    # --- Run 2: same dir/prefix, decline the overwrite ---
    dock._active_task = None
    recorder_run2_decline = DialogRecorder(QtWidgets.QMessageBox.No)
    dw.QtWidgets.QMessageBox.question = staticmethod(recorder_run2_decline)

    dock.export_selected_layers()

    print("\nRun 2 (decline overwrite) -- QMessageBox.question calls:", len(recorder_run2_decline.calls))
    assert len(recorder_run2_decline.calls) == 1, "second run should trigger exactly one overwrite-confirmation dialog"
    title, text = recorder_run2_decline.calls[0]
    print(f"  title={title!r}")
    print(f"  text={text!r}")
    assert title == "Overwrite existing files?"
    assert "overwrite_test.csv" in text
    assert "overwrite_test.csvt" in text
    assert "overwrite_test_manifest.csv" in text
    assert len(fake_task_manager.tasks_run) == 1, "declining should NOT run a second export task"
    assert os.path.getmtime(output_csv) == first_run_mtime, "declining should leave the existing file untouched"

    # --- Run 3: same dir/prefix, accept the overwrite ---
    dock._active_task = None
    recorder_run3_accept = DialogRecorder(QtWidgets.QMessageBox.Yes)
    dw.QtWidgets.QMessageBox.question = staticmethod(recorder_run3_accept)

    dock.export_selected_layers()

    print("\nRun 3 (accept overwrite) -- QMessageBox.question calls:", len(recorder_run3_accept.calls))
    assert len(recorder_run3_accept.calls) == 1
    assert len(fake_task_manager.tasks_run) == 2, "accepting should run a second export task"
    assert os.path.getmtime(output_csv) > first_run_mtime, "accepting should actually rewrite the file"

    print("\nTEST 28 PASSED")
finally:
    dw.QgsApplication = _originals["QgsApplication"]
    dw.QtWidgets.QFileDialog.getExistingDirectory = _originals["getExistingDirectory"]
    dw.QtWidgets.QInputDialog.getText = _originals["getText"]
    dw.QtWidgets.QMessageBox.question = _originals["question"]
