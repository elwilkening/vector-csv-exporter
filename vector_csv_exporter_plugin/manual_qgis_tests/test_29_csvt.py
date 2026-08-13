"""
Manual QGIS integration test #29: CSVT field-type sidecar.
Runs the real ExportTask/_build_column_types production code (no GUI needed)
against a real QgsVectorLayer and checks the resulting .csvt content.
"""
import csv
import os
import tempfile

from dock_test_helpers import start_qgis_gui, load_csv_layer

start_qgis_gui()

from qgis.core import QgsVectorLayerFeatureSource  # noqa: E402

from vector_csv_exporter_plugin.dock_widget import ExportTask, VectorCsvExporterDockWidget  # noqa: E402
from vector_csv_exporter_plugin.export_utils import build_group_header, normalize_value  # noqa: E402

layer = load_csv_layer("mixed_types.csv", "mixed_types")

field_pairs = [(idx, f.name()) for idx, f in enumerate(layer.fields())]
print("Detected fields:", [(f.name(), f.typeName()) for f in layer.fields()])

header, index_maps, canonical_maps = build_group_header([(layer, field_pairs)])
print("Header:", header)

column_types = VectorCsvExporterDockWidget._build_column_types(None, header, [(layer, field_pairs)], index_maps)
print("Resolved column_types:", column_types)

out_dir = tempfile.mkdtemp(prefix="csvt_test_")
output_path = os.path.join(out_dir, "mixed_types.csv")
manifest_path = os.path.join(out_dir, "manifest.csv")

layer_spec = {
    "feature_source": QgsVectorLayerFeatureSource(layer),
    "layer_name": layer.name(),
    "crs_valid": layer.crs().isValid(),
    "feature_count": layer.featureCount(),
    "field_names": field_pairs,
    "transform": None,
    "header_index_map": index_maps[0],
    "canonical_map": canonical_maps[0],
    "expected_feature_ids": set(layer.allFeatureIds()),
}
group_spec = {
    "output_path": output_path,
    "header": header,
    "layers_with_fields": [layer_spec],
    "column_types": column_types,
}


class FakeDockWidget:
    def _normalize_value(self, value):
        from qgis.core import NULL
        if value == NULL:
            value = None
        return normalize_value(value)


task = ExportTask("test", [group_spec], ",", "utf-8", FakeDockWidget(), manifest_path)
result = task.run()

print("\n--- task.messages ---")
for text, level in task.messages:
    print(f"[{level}] {text}")
print("task result:", result, "task.error:", task.error)

csvt_path = os.path.splitext(output_path)[0] + ".csvt"
assert os.path.exists(csvt_path), f"csvt sidecar not written: {csvt_path}"
with open(csvt_path, newline="") as handle:
    csvt_row = next(csv.reader(handle))
print("\n.csvt row:", csvt_row)

# Header is id, name, score, X, Y, SOURCE_LAYER, WKT -- the delimited-text
# provider keeps X/Y as real attribute columns in addition to using them to
# build point geometry, and QGIS's type detection correctly reports them
# as Real (double) fields.
expected = ["Integer", "String", "Real", "Real", "Real", "String", "String"]
assert csvt_row == expected, f"MISMATCH: expected {expected}, got {csvt_row}"

with open(output_path, newline="") as handle:
    rows = list(csv.reader(handle))
print("\noutput CSV rows:")
for r in rows:
    print(r)

print("\nTEST 29 PASSED")
