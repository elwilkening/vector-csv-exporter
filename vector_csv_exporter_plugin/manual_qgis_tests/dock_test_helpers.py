"""Shared helpers for headless real-PyQGIS integration tests.

These tests run outside pytest, using the Python interpreter QGIS ships
with (see README.md in this folder for how to invoke them), because
they need real qgis.core/qgis.gui objects that don't exist without a
QGIS installation.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

from qgis.core import QgsApplication, QgsVectorLayer  # noqa: E402

_app = None


def start_qgis_gui():
    """Initialize a headless-but-GUI-enabled QgsApplication. GUI-enabled is
    required for QDockWidget/QComboBox/uic.loadUi() to work, but no window
    is ever shown (.show()/.exec_() are never called), so nothing appears
    on screen."""
    global _app
    if _app is None:
        _app = QgsApplication([], True)
        _app.initQgis()
    return _app


def fixture_path(name):
    return os.path.join(FIXTURES_DIR, name)


def load_csv_layer(csv_name, layer_name):
    """Load a fixtures/*.csv file as a point-geometry delimited-text layer,
    the same way "Add Delimited Text Layer" with Point coordinates would."""
    path = fixture_path(csv_name)
    uri = f"file:///{path.replace(os.sep, '/')}?delimiter=,&xField=X&yField=Y&crs=EPSG:4326&detectTypes=yes"
    layer = QgsVectorLayer(uri, layer_name, "delimitedtext")
    assert layer.isValid(), f"layer failed to load: {layer.error().summary()}"
    return layer


class FakeTaskManager:
    """Runs a QgsTask synchronously instead of scheduling it on a
    background thread through the real (event-loop-dependent) task
    manager, so export_selected_layers() can be exercised end-to-end in a
    script with no running Qt event loop."""

    def __init__(self):
        self.tasks_run = []

    def addTask(self, task):
        self.tasks_run.append(task)
        task.run()


class DialogRecorder:
    """Monkeypatch target for QMessageBox.question: records every call and
    returns a pre-set canned answer, so a modal dialog never actually
    blocks waiting for a real click."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, parent, title, text, buttons, default_button):
        self.calls.append((title, text))
        return self.answer
