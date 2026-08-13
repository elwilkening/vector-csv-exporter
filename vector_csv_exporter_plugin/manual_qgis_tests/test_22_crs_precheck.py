"""
Manual QGIS integration test #22: CRS reprojection pre-check warning.

Two parts:
  A. Unit-test _crs_transform_is_reachable()'s own exception handling with a
     controlled fake transform (real PROJ behavior turned out too lenient to
     reliably trigger QgsCsException from an invalid/undefined CRS alone --
     it silently no-ops rather than raising, which is itself a useful thing
     to have learned).
  B. Integration-test export_selected_layers()'s dialog-triggering logic by
     mocking the reachability check to a known per-layer result, exercising
     the real dialog title/text/Yes-No handling without depending on
     reproducing a genuine PROJ failure.
"""
import os
import tempfile

from dock_test_helpers import start_qgis_gui, load_csv_layer, FakeTaskManager, DialogRecorder

start_qgis_gui()

from qgis.PyQt import QtWidgets  # noqa: E402
from qgis.core import QgsCsException, QgsProject  # noqa: E402
import vector_csv_exporter_plugin.dock_widget as dw  # noqa: E402

# ---------------------------------------------------------------------------
# Part A: _crs_transform_is_reachable() unit-level checks
# ---------------------------------------------------------------------------
layer_a = load_csv_layer("group_a.csv", "layer_a")


class RaisingTransform:
    def transform(self, point):
        raise QgsCsException("simulated unreachable transform")


class SucceedingTransform:
    def transform(self, point):
        return point


assert dw.VectorCsvExporterDockWidget._crs_transform_is_reachable(None, layer_a, None) is True, \
    "no transform (None) should be treated as reachable"
assert dw.VectorCsvExporterDockWidget._crs_transform_is_reachable(None, layer_a, RaisingTransform()) is False, \
    "a transform whose .transform() raises QgsCsException should be reported unreachable"
assert dw.VectorCsvExporterDockWidget._crs_transform_is_reachable(None, layer_a, SucceedingTransform()) is True, \
    "a transform whose .transform() succeeds should be reported reachable"
print("Part A (_crs_transform_is_reachable unit checks) PASSED")

# ---------------------------------------------------------------------------
# Part B: export_selected_layers() dialog integration, with reachability
# mocked so the test doesn't depend on organically reproducing a PROJ error.
# ---------------------------------------------------------------------------
layer_bad = load_csv_layer("group_a.csv", "bad_crs_layer")
layer_good = load_csv_layer("group_b.csv", "good_crs_layer")

project = QgsProject.instance()
project.removeAllMapLayers()
project.addMapLayers([layer_bad, layer_good])

fake_task_manager = FakeTaskManager()


class FakeQgsApplication:
    @staticmethod
    def taskManager():
        return fake_task_manager


dw.QgsApplication = FakeQgsApplication


# Mock the reachability check itself: only "bad_crs_layer" is unreachable.
def fake_reachable(self, layer, transform):
    return layer.name() != "bad_crs_layer"


dw.VectorCsvExporterDockWidget._crs_transform_is_reachable = fake_reachable

out_dir = tempfile.mkdtemp(prefix="crs_precheck_test_")
dw.QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: out_dir)
dw.QtWidgets.QInputDialog.getText = staticmethod(lambda *a, **k: ("crs_test", True))

dock = dw.VectorCsvExporterDockWidget()
dock.populate_layers()
assert dock.layer_list_widget.count() == 2, "expected both layers listed"

# --- Scenario 1: decline the warning ---
recorder_no = DialogRecorder(QtWidgets.QMessageBox.No)
dw.QtWidgets.QMessageBox.question = staticmethod(recorder_no)

dock.export_selected_layers()

print("Scenario 1 (decline) -- QMessageBox.question calls:", len(recorder_no.calls))
assert len(recorder_no.calls) == 1, f"expected exactly one warning dialog, got {len(recorder_no.calls)}"
title, text = recorder_no.calls[0]
print(f"  title={title!r}")
print(f"  text={text!r}")
assert title == "Some layers may not reproject"
assert "bad_crs_layer" in text
assert "good_crs_layer" not in text, "only the affected layer should be named"
assert len(fake_task_manager.tasks_run) == 0, "declining should prevent the export task from ever running"
assert os.listdir(out_dir) == [], "declining should leave the output directory untouched"

# --- Scenario 2: accept and continue anyway ---
recorder_yes = DialogRecorder(QtWidgets.QMessageBox.Yes)
dw.QtWidgets.QMessageBox.question = staticmethod(recorder_yes)
dock._active_task = None  # reset, as _on_export_task_completed would normally do

dock.export_selected_layers()

print("\nScenario 2 (accept) -- QMessageBox.question calls:", len(recorder_yes.calls))
assert len(recorder_yes.calls) == 1
assert len(fake_task_manager.tasks_run) == 1, "accepting should let the export task run"

task = fake_task_manager.tasks_run[0]
print("\n--- task.messages ---")
for msg, level in task.messages:
    print(f"[{level}] {msg}")

# Both layers actually have valid, transformable CRSes in reality (only the
# mocked reachability check called "bad_crs_layer" unreachable to drive the
# dialog), so both should export cleanly once the user says "continue anyway".
assert task.error is None
assert any("bad_crs_layer" in m and "2 of 2 feature(s) exported" in m for m, _l in task.messages)
assert any("good_crs_layer" in m and "3 of 3 feature(s) exported" in m for m, _l in task.messages)

print("\nTEST 22 PASSED")
