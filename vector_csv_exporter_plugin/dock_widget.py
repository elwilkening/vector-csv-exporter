import csv
import os
from datetime import datetime

from qgis.PyQt import QtCore, QtWidgets, uic
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    NULL,
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsGeometry,
    QgsMapLayer,
    QgsMessageLog,
    QgsProject,
    QgsTask,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)
from qgis.gui import QgsProjectionSelectionWidget

from .export_utils import (
    build_output_name,
    data_header_signature,
    normalize_value,
    sanitize_prefix,
    build_group_header,
    dedupe_field_names,
)

# Decimal degrees of precision for exported WKT coordinates. 8 decimal
# degrees is well below sub-millimeter precision at the equator, so this
# trims floating-point noise from CRS reprojection without losing accuracy.
WKT_COORDINATE_PRECISION = 8

# Maps QGIS field types to the type names GDAL/OGR's CSV driver recognizes in
# a .csvt sidecar file, so a re-imported CSV regains its original field types
# instead of every column becoming plain text.
_CSVT_TYPE_MAP = {
    QVariant.Int: "Integer",
    QVariant.UInt: "Integer",
    QVariant.LongLong: "Integer64",
    QVariant.ULongLong: "Integer64",
    QVariant.Double: "Real",
    QVariant.Bool: "Integer",
    QVariant.Date: "Date",
    QVariant.Time: "Time",
    QVariant.DateTime: "DateTime",
    QVariant.String: "String",
}


def _csvt_type_for_field(field):
    return _CSVT_TYPE_MAP.get(field.type(), "String")


class ExportTask(QgsTask):
    def __init__(self, description, group_specs, delimiter, encoding, dock_widget, manifest_path):
        super().__init__(description, QgsTask.CanCancel)
        self.group_specs = group_specs
        self.delimiter = delimiter
        self.encoding = encoding
        self.dock_widget = dock_widget
        self.manifest_path = manifest_path
        self.messages = []
        self.error = None
        self.total_features = sum(
            sum(layer_spec["feature_count"] for layer_spec in spec["layers_with_fields"]) for spec in group_specs
        )
        self.features_written = 0
        self.features_skipped = 0
        # Per-layer accounting (attempted/written/skipped) so the final
        # summary can reconcile every feature instead of just reporting a
        # single running total. Also the basis for a future export manifest.
        self.layer_stats = []
        self.verification_failures = []
        # Stronger than count-based reconciliation: records layers where the
        # set of feature IDs actually processed doesn't match the layer's
        # feature IDs at export start, catching cases where counts could
        # coincidentally match despite the wrong features being processed.
        self.id_verification_failures = []

    def _log(self, text, level="info"):
        self.messages.append((text, level))

    def run(self):
        for spec in self.group_specs:
            if self.isCanceled():
                self._log("Export cancelled by the user.", "warning")
                return False
            try:
                self._write_group(spec)
            except RuntimeError as exc:
                if str(exc) == "cancelled":
                    self._log("Export cancelled by the user.", "warning")
                    return False
                self._remove_partial_output(spec["output_path"], reason="an export failure")
                self.error = str(exc)
                self._log(f"Export failed: {exc}", "error")
                self._write_manifest("ERROR")
                return False
            except OSError as exc:
                self._remove_partial_output(spec["output_path"], reason="a write failure")
                self.error = str(exc)
                self._log(f"Failed to write file '{spec['output_path']}': {exc}", "error")
                self._write_manifest("ERROR")
                return False

        # Reconciliation: total_features was computed up front from each
        # layer's feature count, independent of what actually got written or
        # skipped. On a non-cancelled run, features_written + features_skipped
        # must equal total_features -- if it doesn't, something was silently
        # dropped and this line would expose it.
        self._log(
            f"Summary: {self.features_written} of {self.total_features} feature(s) exported "
            f"across {len(self.layer_stats)} layer(s) into {len(self.group_specs)} file(s).",
        )
        if self.features_skipped:
            self._log(
                f"{self.features_skipped} feature(s) were skipped during export -- review the warnings above.",
                "warning",
            )

        status_parts = []
        if self.verification_failures:
            status_parts.append("ROW_COUNT_MISMATCH")
        if self.id_verification_failures:
            status_parts.append("FEATURE_ID_MISMATCH")
        self._write_manifest("+".join(status_parts) or "SUCCESS")

        if self.verification_failures or self.id_verification_failures:
            error_parts = []
            if self.verification_failures:
                error_parts.append(
                    "row count verification failed for "
                    + ", ".join(os.path.basename(path) for path, _expected, _actual in self.verification_failures)
                )
            if self.id_verification_failures:
                error_parts.append(
                    "feature ID verification failed for "
                    + ", ".join(
                        f"'{name}' ({missing} feature(s) never processed)"
                        for name, missing in self.id_verification_failures
                    )
                )
            self.error = "; ".join(error_parts) + "."
            self._log(
                "Export finished writing, but verification found problems -- see errors above.",
                "error",
            )
            return False

        self._log("Export completed successfully.")
        return True

    def _remove_partial_output(self, output_path, reason="cancellation"):
        try:
            os.remove(output_path)
        except FileNotFoundError:
            return
        except OSError as exc:
            self._log(f"Unable to remove partial file '{output_path}': {exc}", "warning")
            return
        self._log(f"Removed partial file '{output_path}' after {reason}.")

    def _write_group(self, spec):
        output_path = spec["output_path"]
        header = spec["header"]
        layers_with_fields = spec["layers_with_fields"]
        rows_written_for_group = 0

        try:
            with open(output_path, "w", encoding=self.encoding, errors="replace", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n", delimiter=self.delimiter)
                writer.writerow(header)

                for layer_spec in layers_with_fields:
                    if self.isCanceled():
                        self._remove_partial_output(output_path)
                        raise RuntimeError("cancelled")

                    layer_name = layer_spec["layer_name"]
                    attempted = layer_spec["feature_count"]
                    layer_stat = {
                        "layer_name": layer_name,
                        "output_path": output_path,
                        "attempted": attempted,
                        "written": 0,
                        "skipped": 0,
                        "skip_reasons": {},
                        # None means feature-ID verification wasn't performed for this
                        # layer (e.g. it was skipped entirely, or ID snapshotting failed).
                        "missing_feature_ids": None,
                        "unexpected_feature_ids": None,
                    }
                    self.layer_stats.append(layer_stat)

                    if not layer_spec["crs_valid"]:
                        if attempted:
                            layer_stat["skipped"] = attempted
                            layer_stat["skip_reasons"]["invalid or undefined source CRS"] = attempted
                            self.features_skipped += attempted
                        self._log(
                            f"Layer '{layer_name}': 0 of {attempted} feature(s) exported "
                            f"(skipped entirely: invalid or undefined source CRS).",
                            "warning",
                        )
                        continue

                    if attempted == 0:
                        # Already flagged as an empty layer before the task
                        # was started; nothing to reconcile here.
                        continue

                    # header_index_map maps header_name.lower() -> attribute index (or None)
                    field_lookup = layer_spec.get("header_index_map", {})
                    written_ids = set()
                    skipped_ids = set()

                    for feature in layer_spec["feature_source"].getFeatures():
                        if self.isCanceled():
                            self._remove_partial_output(output_path)
                            raise RuntimeError("cancelled")

                        feature_id = feature.id()

                        geometry = QgsGeometry(feature.geometry())
                        if layer_spec["transform"] is not None:
                            try:
                                geometry.transform(layer_spec["transform"])
                            except QgsCsException as exc:
                                self._log(f"Reprojection failed for '{layer_name}': {exc}", "warning")
                                layer_stat["skipped"] += 1
                                layer_stat["skip_reasons"]["reprojection failed"] = (
                                    layer_stat["skip_reasons"].get("reprojection failed", 0) + 1
                                )
                                self.features_skipped += 1
                                skipped_ids.add(feature_id)
                                continue

                        row = []
                        for header_name in header[:-2]:
                            index = field_lookup.get(header_name.lower())
                            if index is None:
                                row.append("")
                            else:
                                value = feature.attributes()[index] if index < len(feature.attributes()) else None
                                row.append(self.dock_widget._normalize_value(value))
                        row.append(layer_name)
                        row.append(geometry.asWkt(WKT_COORDINATE_PRECISION))
                        writer.writerow(row)
                        rows_written_for_group += 1
                        self.features_written += 1
                        layer_stat["written"] += 1
                        written_ids.add(feature_id)
                        # Periodically flush to ensure large exports leave
                        # more data on-disk in case of a crash. Flush every 1000 rows.
                        if self.features_written % 1000 == 0:
                            try:
                                handle.flush()
                            except Exception:
                                # Ignore flush errors; file will be removed on cancel/failure
                                pass
                        if self.total_features:
                            progress = int(100 * self.features_written / self.total_features)
                        else:
                            progress = 100
                        self.setProgress(progress)

                    reasons = ", ".join(f"{count} {reason}" for reason, count in layer_stat["skip_reasons"].items())
                    summary = f"Layer '{layer_name}': {layer_stat['written']} of {attempted} feature(s) exported"
                    if layer_stat["skipped"]:
                        summary += f" ({layer_stat['skipped']} skipped: {reasons})"
                    summary += "."
                    self._log(summary, "warning" if layer_stat["skipped"] else "info")

                    self._verify_feature_ids(layer_spec, layer_stat, written_ids, skipped_ids)
        except RuntimeError:
            raise

        self._log(f"Exported {output_path}")
        self._verify_output_row_count(output_path, rows_written_for_group + 1)
        self._write_csvt(output_path, spec.get("column_types"))

    def _verify_output_row_count(self, output_path, expected_rows):
        # Re-read the file we just wrote and count its actual rows (via
        # csv.reader so quoted embedded newlines aren't miscounted) rather
        # than trusting the in-memory write counters alone -- this catches
        # disk/encoding-layer problems the counters wouldn't see.
        try:
            with open(output_path, "r", encoding=self.encoding, errors="replace", newline="") as handle:
                actual_rows = sum(1 for _ in csv.reader(handle, delimiter=self.delimiter))
        except OSError as exc:
            self._log(f"Could not verify row count for '{output_path}': {exc}", "warning")
            return

        if actual_rows != expected_rows:
            self.verification_failures.append((output_path, expected_rows, actual_rows))
            self._log(
                f"'{output_path}' contains {actual_rows} row(s) but {expected_rows} were expected "
                f"(1 header + {expected_rows - 1} feature row(s)). The file may be incomplete.",
                "error",
            )
        else:
            self._log(f"Verified '{output_path}': row count matches ({actual_rows} rows).")

    def _write_csvt(self, output_path, column_types):
        # A .csvt sidecar (GDAL/OGR's CSV-driver convention: same basename,
        # always comma-separated regardless of the main file's delimiter)
        # lets a re-imported CSV regain its original field types instead of
        # every column becoming plain text. Best-effort: failure here
        # shouldn't fail an otherwise-successful export.
        if not column_types:
            return
        csvt_path = os.path.splitext(output_path)[0] + ".csvt"
        try:
            with open(csvt_path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n", quoting=csv.QUOTE_ALL)
                writer.writerow(column_types)
        except OSError as exc:
            self._log(f"Could not write field-type sidecar '{csvt_path}': {exc}", "warning")
            return
        self._log(f"Wrote field-type sidecar '{csvt_path}'.")

    def _verify_feature_ids(self, layer_spec, layer_stat, written_ids, skipped_ids):
        # Counts can coincidentally match even if the wrong features were
        # processed (e.g. one feature double-counted while another was
        # missed). Comparing actual feature-ID sets against the layer's IDs
        # at export start catches that a bare count comparison would not.
        expected_ids = layer_spec.get("expected_feature_ids")
        if expected_ids is None:
            return

        layer_name = layer_stat["layer_name"]
        processed_ids = written_ids | skipped_ids
        missing_ids = expected_ids - processed_ids
        unexpected_ids = processed_ids - expected_ids
        layer_stat["missing_feature_ids"] = len(missing_ids)
        layer_stat["unexpected_feature_ids"] = len(unexpected_ids)

        if missing_ids:
            sample = ", ".join(str(fid) for fid in sorted(missing_ids)[:5])
            self._log(
                f"Layer '{layer_name}': {len(missing_ids)} feature ID(s) from the source layer were "
                f"never processed during export (e.g. {sample}). Count-based tracking alone would not "
                f"have caught this.",
                "error",
            )
            self.id_verification_failures.append((layer_name, len(missing_ids)))

        if unexpected_ids:
            sample = ", ".join(str(fid) for fid in sorted(unexpected_ids)[:5])
            self._log(
                f"Layer '{layer_name}': {len(unexpected_ids)} feature ID(s) were processed that weren't "
                f"in the layer's feature-ID snapshot taken before export (e.g. {sample}) -- the layer may "
                f"have been edited while the export was running.",
                "warning",
            )

    def _write_manifest(self, status):
        # A durable, on-disk record of exactly what was processed -- unlike
        # the status log, this survives after the dock is closed and can be
        # handed to someone else as evidence nothing was silently dropped.
        try:
            with open(self.manifest_path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow([
                    "source_layer",
                    "output_file",
                    "features_in_layer",
                    "features_exported",
                    "features_skipped",
                    "skip_reasons",
                    "missing_feature_ids",
                    "unexpected_feature_ids",
                ])
                for stat in self.layer_stats:
                    reasons = "; ".join(
                        f"{reason}: {count}" for reason, count in stat["skip_reasons"].items()
                    )
                    writer.writerow([
                        stat["layer_name"],
                        os.path.basename(stat["output_path"]),
                        stat["attempted"],
                        stat["written"],
                        stat["skipped"],
                        reasons,
                        stat["missing_feature_ids"] if stat["missing_feature_ids"] is not None else "not checked",
                        stat["unexpected_feature_ids"] if stat["unexpected_feature_ids"] is not None else "not checked",
                    ])
                writer.writerow([])
                writer.writerow([
                    "TOTAL",
                    f"{len(self.group_specs)} file(s)",
                    self.total_features,
                    self.features_written,
                    self.features_skipped,
                    "",
                    sum(s["missing_feature_ids"] or 0 for s in self.layer_stats),
                    sum(s["unexpected_feature_ids"] or 0 for s in self.layer_stats),
                ])
                writer.writerow([])
                writer.writerow(["export_status", status])
                writer.writerow(["generated_at", datetime.now().isoformat(timespec="seconds")])
                writer.writerow(["delimiter", "Tab" if self.delimiter == "\t" else self.delimiter])
                writer.writerow(["encoding", self.encoding])
        except OSError as exc:
            self._log(f"Could not write export manifest '{self.manifest_path}': {exc}", "warning")
            return

        self._log(f"Wrote export manifest '{self.manifest_path}'.")


class VectorCsvExporterDockWidget(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        ui_path = os.path.join(os.path.dirname(__file__), "dock_widget.ui")
        uic.loadUi(ui_path, self)
        self.setWindowTitle("Vector CSV Exporter")
        self.select_all_checkbox.stateChanged.connect(self._set_all_checked)
        self.export_button.clicked.connect(self.export_selected_layers)
        self.refresh_button.clicked.connect(self.populate_layers)
        self.cancel_button.clicked.connect(self._cancel_export)
        self.layer_list_widget.itemClicked.connect(self._show_selected_layer_fields)
        self.layer_list_widget.itemChanged.connect(self._on_layer_item_changed)
        self.field_list_widget.itemChanged.connect(self._on_field_item_changed)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self._active_task = None
        self._settings = QtCore.QSettings("QGIS", "VectorCsvExporter")
        self._layer_field_selection = {}
        self._active_layer_id = None
        self._crs_selector = QgsProjectionSelectionWidget(self)
        self._crs_selector.setCrs(self._get_wgs84_crs())
        self.settings_layout.addWidget(self._crs_selector)
        self.keep_original_crs_checkbox = QtWidgets.QCheckBox("Keep original CRS")
        self.settings_layout.addWidget(self.keep_original_crs_checkbox)
        self.keep_original_crs_checkbox.toggled.connect(self._update_crs_selector_state)
        self._update_crs_selector_state(False)
        self.populate_layers()

    def populate_layers(self):
        self.layer_list_widget.clear()
        self.field_list_widget.clear()
        self._layer_field_selection = {}
        project = QgsProject.instance()
        warning_messages = []

        for layer in project.mapLayers().values():
            if layer.type() == QgsMapLayer.VectorLayer:
                item = QtWidgets.QListWidgetItem(layer.name())
                item.setData(QtCore.Qt.UserRole, layer.id())
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Checked)
                self.layer_list_widget.addItem(item)
                field_names = [field.name().strip() for field in layer.fields()]
                self._layer_field_selection[layer.id()] = set(field_names)
            else:
                warning_messages.append(f"Skipped non-vector layer: {layer.name()}")

        if warning_messages:
            self._log_message("\n".join(warning_messages), "warning")

        if self.layer_list_widget.count() == 0:
            self._log_message("No vector layers are available in the current project.", "warning")

    def _set_all_checked(self, state):
        checked_state = QtCore.Qt.Checked if state else QtCore.Qt.Unchecked
        for index in range(self.layer_list_widget.count()):
            item = self.layer_list_widget.item(index)
            item.setCheckState(checked_state)

    def _on_layer_item_changed(self, item):
        if item is None:
            return
        layer_id = item.data(QtCore.Qt.UserRole)
        if not layer_id:
            return
        self._show_selected_layer_fields(item)

    def _on_field_item_changed(self, item):
        if item is None:
            return
        self._save_selected_fields(self._active_layer_id)

    def _show_selected_layer_fields(self, item):
        self.field_list_widget.clear()
        if item is None:
            return
        layer_id = item.data(QtCore.Qt.UserRole)
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            return
        self._active_layer_id = layer.id()
        field_names = [field.name().strip() for field in layer.fields()]
        checked_fields = self._layer_field_selection.get(layer.id(), set(field_names))
        for field_name in field_names:
            field_item = QtWidgets.QListWidgetItem(field_name)
            field_item.setData(QtCore.Qt.UserRole, field_name)
            field_item.setFlags(field_item.flags() | QtCore.Qt.ItemIsUserCheckable)
            field_item.setCheckState(QtCore.Qt.Checked if field_name in checked_fields else QtCore.Qt.Unchecked)
            self.field_list_widget.addItem(field_item)
        self.field_list_widget.setVisible(True)

    def _save_selected_fields(self, layer_id):
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            return
        selected_fields = set()
        for index in range(self.field_list_widget.count()):
            item = self.field_list_widget.item(index)
            if item.checkState() == QtCore.Qt.Checked:
                selected_fields.add(item.text())
        self._layer_field_selection[layer.id()] = selected_fields

    def _update_crs_selector_state(self, checked):
        self._crs_selector.setEnabled(not checked)

    def export_selected_layers(self):
        if self._active_task is not None:
            self._show_message("An export is already in progress.", "warning")
            return

        selected_layers = []
        for index in range(self.layer_list_widget.count()):
            item = self.layer_list_widget.item(index)
            if item.checkState() == QtCore.Qt.Checked:
                layer_id = item.data(QtCore.Qt.UserRole)
                layer = QgsProject.instance().mapLayer(layer_id)
                if isinstance(layer, QgsVectorLayer):
                    selected_layers.append(layer)

        if not selected_layers:
            self._show_message("No layers selected for export.", "warning")
            return

        default_dir = self._settings.value("output_dir", os.path.expanduser("~"), str)
        output_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose output directory",
            default_dir,
        )
        if not output_dir:
            self._log_message("Export cancelled by the user.", "info")
            return

        self._settings.setValue("output_dir", output_dir)

        if not os.access(output_dir, os.W_OK):
            self._show_message(f"Cannot write to the selected directory: {output_dir}", "error")
            return

        stored_prefix = self._settings.value("prefix", "export", str)
        prefix, ok = QtWidgets.QInputDialog.getText(
            self,
            "Export prefix",
            "Enter a shared prefix for the output files:",
            text=stored_prefix,
        )
        if not ok:
            self._log_message("Export cancelled by the user.", "info")
            return

        self._settings.setValue("prefix", prefix)
        prefix = self._sanitize_prefix(prefix)
        grouped_layers = {}

        for layer in selected_layers:
            all_field_names = [field.name().strip() for field in layer.fields()]
            selected_fields = self._layer_field_selection.get(layer.id(), set(all_field_names))
            # Preserve true original indices by building (orig_index, name) pairs
            field_pairs = [(idx, name) for idx, name in enumerate(all_field_names) if name in selected_fields]
            if not field_pairs and all_field_names:
                self._log_message(
                    f"Layer '{layer.name()}' has no selected fields; exporting geometry-only CSV.",
                    "info",
                )
            if not all_field_names:
                self._log_message(
                    f"Layer '{layer.name()}' has zero attribute fields; exporting geometry-only CSV.",
                    "info",
                )
            # Deduplicate duplicate field names within the same layer instead
            # of skipping the layer entirely. This preserves attribute values
            # by renaming later occurrences to unique names. Pass (index,name)
            # pairs so deduplication preserves true original indices.
            deduped_field_pairs, renames = dedupe_field_names(field_pairs)
            if renames:
                for orig_lower, canonical, true_index in renames:
                    orig_name = all_field_names[true_index] if true_index is not None and 0 <= true_index < len(all_field_names) else orig_lower
                    self._log_message(
                        f"Layer '{layer.name()}': duplicate field '{orig_name}' renamed to '{canonical}' to keep it in the export.",
                        "info",
                    )
            field_names = deduped_field_pairs
            if layer.featureCount() == 0:
                self._log_message(f"Layer '{layer.name()}' has zero features; exporting header only.", "warning")
            # Build signature based on the deduplicated field names (not indices)
            signature_names = [name for (_idx, name) in field_names]
            group_key = self._data_header_signature(signature_names)
            grouped_layers.setdefault(group_key, []).append((layer, field_names))

        if not grouped_layers:
            self._show_message("No valid layers remained after validation.", "warning")
            return

        group_count = len(grouped_layers)
        group_specs = []
        unreachable_transform_layers = []
        for index, (_, layers_with_fields) in enumerate(grouped_layers.items(), start=1):
            # Build a collision-safe header and per-layer index maps
            header, per_layer_index_maps, per_layer_canonical_maps = build_group_header(layers_with_fields)
            output_name = self._build_output_name(prefix, group_count, index)
            output_path = os.path.join(output_dir, output_name)
            column_types = self._build_column_types(header, layers_with_fields, per_layer_index_maps)
            prepared_layers = []
            for (layer, field_names), index_map, canonical_map in zip(layers_with_fields, per_layer_index_maps, per_layer_canonical_maps):
                transform = None
                if not self.keep_original_crs_checkbox.isChecked():
                    transform = self._build_transform(layer.crs(), self._get_target_crs())
                    if not self._crs_transform_is_reachable(layer, transform):
                        unreachable_transform_layers.append(layer.name())
                # Snapshot the layer's feature IDs here on the main thread
                # (QgsVectorLayer isn't safe to query from the background
                # task's thread) so the export can later verify it actually
                # processed every one of them, not just the right count.
                try:
                    expected_feature_ids = set(layer.allFeatureIds())
                except Exception as exc:
                    expected_feature_ids = None
                    self._log_message(
                        f"Could not read feature IDs for layer '{layer.name()}' ahead of export: {exc}",
                        "warning",
                    )
                layer_spec = {
                    "feature_source": QgsVectorLayerFeatureSource(layer),
                    "layer_name": layer.name(),
                    "crs_valid": layer.crs().isValid(),
                    "feature_count": layer.featureCount(),
                    "field_names": field_names,
                    "transform": transform,
                    "header_index_map": index_map,
                    "canonical_map": canonical_map,
                    "expected_feature_ids": expected_feature_ids,
                }
                prepared_layers.append(layer_spec)
                # Log any renamed fields for this layer
                for orig_lower, canonical in canonical_map.items():
                    if canonical.lower() != orig_lower:
                        # field_names is this layer's list of (orig_index, name) pairs; find the
                        # original-cased name from it rather than the stale, last-iteration
                        # all_field_names from the earlier per-layer loop.
                        orig_name = next((name for (_idx, name) in field_names if name and name.strip().lower() == orig_lower), orig_lower)
                        self._log_message(f"Field '{orig_name}' from layer '{layer.name()}' was renamed to '{canonical}' to avoid colliding with reserved column names.", "warning")

            group_specs.append({
                "output_path": output_path,
                "header": header,
                "layers_with_fields": prepared_layers,
                "column_types": column_types,
            })

        if unreachable_transform_layers:
            names = "\n".join(sorted(set(unreachable_transform_layers)))
            reply = QtWidgets.QMessageBox.question(
                self,
                "Some layers may not reproject",
                "A test reprojection to the target CRS failed for the following layer(s):\n\n"
                f"{names}\n\n"
                "Affected features will be skipped during export (reported per-feature in "
                "the log and manifest, not silently dropped). Continue anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                self._log_message("Export cancelled by the user due to CRS reprojection concerns.", "info")
                return

        manifest_prefix = prefix[:-4] if prefix.lower().endswith(".csv") else prefix
        existing_outputs = {spec["output_path"] for spec in group_specs}
        manifest_path = os.path.join(output_dir, f"{manifest_prefix}_manifest.csv")
        suffix = 2
        while manifest_path in existing_outputs:
            manifest_path = os.path.join(output_dir, f"{manifest_prefix}_manifest_{suffix}.csv")
            suffix += 1

        prospective_outputs = list(existing_outputs) + [manifest_path]
        prospective_outputs += [os.path.splitext(p)[0] + ".csvt" for p in existing_outputs]
        already_existing = sorted(os.path.basename(p) for p in prospective_outputs if os.path.exists(p))
        if already_existing:
            reply = QtWidgets.QMessageBox.question(
                self,
                "Overwrite existing files?",
                "The following file(s) already exist in the output directory and will be "
                "overwritten:\n\n" + "\n".join(already_existing) + "\n\nContinue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                self._log_message("Export cancelled by the user (existing files not overwritten).", "info")
                return

        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.cancel_button.setVisible(True)
        self.export_button.setEnabled(False)
        delimiter = self._get_selected_delimiter()
        encoding = self.encoding_combo.currentText()
        self._active_task = ExportTask(
            "Exporting vector layers to CSV",
            group_specs,
            delimiter,
            encoding,
            self,
            manifest_path,
        )
        # QgsTask emits taskCompleted only when run() returns True; every
        # cancellation or failure path emits taskTerminated instead. Both must
        # be connected or an unsuccessful export leaves the progress bar up,
        # the export button disabled, and the task's log messages unshown.
        self._active_task.taskCompleted.connect(self._on_export_task_completed)
        self._active_task.taskTerminated.connect(self._on_export_task_terminated)
        self._active_task.progressChanged.connect(self._on_export_task_progress)
        QgsApplication.taskManager().addTask(self._active_task)

    def _cancel_export(self):
        if self._active_task is not None:
            self._active_task.cancel()
            self._log_message("Cancellation requested; finishing the current feature and stopping soon.", "warning")

    def _on_export_task_progress(self, progress):
        self.progress_bar.setValue(int(progress))

    def _on_export_task_terminated(self):
        self._on_export_task_completed(result=False)

    def _on_export_task_completed(self, result=True):
        task = self.sender()
        if task is None:
            return

        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.export_button.setEnabled(True)
        self._active_task = None

        for message, level in task.messages:
            self._log_message(message, level)

        if result:
            self._show_message("Export finished successfully.", "info")
        else:
            if task.error:
                self._show_message(task.error, "error")
            else:
                self._show_message("Export was cancelled or failed.", "warning")

    def _normalize_value(self, value):
        # PyQGIS represents a null attribute with its own NULL sentinel, which
        # is not Python's None, so it must be converted here before reaching
        # the QGIS-independent normalize_value() helper.
        if value == NULL:
            value = None
        return normalize_value(value)

    def _get_selected_delimiter(self):
        selected = self.delimiter_combo.currentText()
        return "\t" if selected == "Tab" else selected

    def _data_header_signature(self, field_names):
        return data_header_signature(field_names)

    def _get_target_crs(self):
        crs = self._crs_selector.crs()
        if crs.isValid():
            return crs
        return self._get_wgs84_crs()

    def _get_wgs84_crs(self):
        try:
            return QgsCoordinateReferenceSystem("EPSG:4326")
        except TypeError:
            return QgsCoordinateReferenceSystem.fromEpsgId(4326)

    def _build_transform(self, source_crs, destination_crs):
        # QgsCoordinateTransform's constructor signature has changed across
        # QGIS versions: newer APIs want an explicit QgsCoordinateTransformContext
        # (project.transformContext()), older ones take the QgsProject directly,
        # and the oldest take neither. Try each in turn so this plugin keeps
        # working across the QGIS versions it might be installed on.
        project = QgsProject.instance()
        if hasattr(project, "transformContext"):
            try:
                return QgsCoordinateTransform(source_crs, destination_crs, project.transformContext())
            except TypeError:
                pass

        try:
            return QgsCoordinateTransform(source_crs, destination_crs, project)
        except TypeError:
            return QgsCoordinateTransform(source_crs, destination_crs)

    def _crs_transform_is_reachable(self, layer, transform):
        # Test-transform the layer's extent center up front rather than only
        # discovering a bad CRS pairing feature-by-feature deep into a
        # potentially long export. A layer with no features has no extent to
        # test, so it's treated as reachable (nothing to reproject anyway).
        if transform is None:
            return True
        extent = layer.extent()
        if extent.isEmpty():
            return True
        try:
            transform.transform(extent.center())
        except QgsCsException:
            return False
        return True

    def _build_column_types(self, header, layers_with_fields, per_layer_index_maps):
        # Determine a CSVT field type for each real attribute column by
        # looking up the QgsField type from whichever layer's field first
        # established that canonical header column -- same precedence
        # build_group_header() already uses for the column name itself.
        column_types = []
        for column in header[:-2]:
            column_lower = column.lower()
            field_type = "String"
            for (layer, _field_names), index_map in zip(layers_with_fields, per_layer_index_maps):
                true_index = index_map.get(column_lower)
                if true_index is not None:
                    field_type = _csvt_type_for_field(layer.fields().at(true_index))
                    break
            column_types.append(field_type)
        column_types.extend(["String", "String"])  # SOURCE_LAYER, WKT
        return column_types

    def _sanitize_prefix(self, prefix):
        return sanitize_prefix(prefix)

    def _build_output_name(self, prefix, group_count, index):
        return build_output_name(prefix, group_count, index)

    def _log_message(self, message, level="info"):
        prefixes = {"info": "INFO", "warning": "WARNING", "error": "ERROR"}
        label = prefixes.get(level, "INFO")
        output = f"[{label}] {message}"
        self.status_text_edit.append(output)
        QgsMessageLog.logMessage(message, "Vector CSV Exporter", Qgis.Info if level == "info" else Qgis.Warning if level == "warning" else Qgis.Critical)

    def _show_message(self, message, level="warning"):
        self._log_message(message, level)
        if level == "error":
            QtWidgets.QMessageBox.critical(self, "Export Error", message)
        elif level == "warning":
            QtWidgets.QMessageBox.warning(self, "Export Warning", message)
