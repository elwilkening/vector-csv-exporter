"""
Manual QGIS integration test #36: on a genuine mid-export write failure,
confirm the partial output file is cleaned up AND the manifest is still
written (documenting what succeeded before the failure).
"""
import os
import tempfile

from dock_test_helpers import start_qgis_gui, load_csv_layer

start_qgis_gui()

from qgis.core import QgsVectorLayerFeatureSource  # noqa: E402

from vector_csv_exporter_plugin.dock_widget import ExportTask  # noqa: E402
from vector_csv_exporter_plugin.export_utils import build_group_header  # noqa: E402


class FailingFeatureSource:
    """Wraps a real feature source but raises OSError after yielding
    `fail_after` features, simulating a genuine mid-write I/O failure
    (e.g. disk full) partway through a group's export."""

    def __init__(self, real_source, fail_after):
        self._real = real_source
        self._fail_after = fail_after

    def getFeatures(self):
        count = 0
        for feature in self._real.getFeatures():
            if count >= self._fail_after:
                raise OSError("Simulated mid-export I/O failure")
            yield feature
            count += 1


def build_group_spec(layer, output_path, feature_source=None):
    field_pairs = [(idx, f.name()) for idx, f in enumerate(layer.fields())]
    header, index_maps, canonical_maps = build_group_header([(layer, field_pairs)])
    layer_spec = {
        "feature_source": feature_source or QgsVectorLayerFeatureSource(layer),
        "layer_name": layer.name(),
        "crs_valid": layer.crs().isValid(),
        "feature_count": layer.featureCount(),
        "field_names": field_pairs,
        "transform": None,
        "header_index_map": index_maps[0],
        "canonical_map": canonical_maps[0],
        "expected_feature_ids": set(layer.allFeatureIds()),
    }
    return {
        "output_path": output_path,
        "header": header,
        "layers_with_fields": [layer_spec],
        "column_types": ["Integer", "String", "Real", "Real", "String", "String"],
    }


out_dir = tempfile.mkdtemp(prefix="partial_failure_test_")
manifest_path = os.path.join(out_dir, "manifest.csv")

layer_a = load_csv_layer("group_a.csv", "group_a")
layer_b = load_csv_layer("group_b.csv", "group_b")

group_a_path = os.path.join(out_dir, "group_a.csv")
group_b_path = os.path.join(out_dir, "group_b.csv")

group_specs = [
    build_group_spec(layer_a, group_a_path),
    build_group_spec(
        layer_b,
        group_b_path,
        feature_source=FailingFeatureSource(QgsVectorLayerFeatureSource(layer_b), fail_after=1),
    ),
]

task = ExportTask("test", group_specs, ",", "utf-8", manifest_path)
result = task.run()

print("--- task.messages ---")
for text, level in task.messages:
    print(f"[{level}] {text}")
print("task result:", result)
print("task.error:", task.error)

# --- Assertions ---
assert result is False, "expected the task to report failure"
assert task.error, "expected task.error to be set"

assert os.path.exists(group_a_path), "group A (which succeeded) should still exist"
assert os.path.exists(os.path.join(out_dir, "csvt", "group_a.csvt")), "group A's .csvt sidecar should exist"

assert not os.path.exists(group_b_path), (
    f"group B's PARTIAL output should have been removed after the mid-write failure, "
    f"but it still exists: {group_b_path}"
)

assert os.path.exists(manifest_path), "manifest should still be written even though the run failed"
with open(manifest_path) as handle:
    manifest_content = handle.read()
print("\n--- manifest.csv ---")
print(manifest_content)

assert "group_a" in manifest_content, "manifest should document the successful group_a layer"
assert "ERROR" in manifest_content, "manifest export_status should reflect the failure"

removed_msg = next((m for m, _l in task.messages if m.startswith("Removed partial file")), None)
assert removed_msg is not None and "after cancellation" not in removed_msg, (
    "the cleanup log message should describe the write failure, not misreport it as a cancellation"
)

print("\nTEST 36 PASSED")
