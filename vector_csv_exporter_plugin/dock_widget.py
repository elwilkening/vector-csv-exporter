import csv
import os

from qgis.PyQt import QtCore, QtWidgets, uic
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
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
    source_layer_column_name,
    build_group_header,
)


class ExportTask(QgsTask):
    def __init__(self, description, group_specs, delimiter, encoding, dock_widget):
        super().__init__(description, QgsTask.CanCancel)
        self.group_specs = group_specs
        self.delimiter = delimiter
        self.encoding = encoding
        self.dock_widget = dock_widget
        self.messages = []
        self.error = None
        self.total_features = sum(
            sum(layer_spec["feature_count"] for layer_spec in spec["layers_with_fields"]) for spec in group_specs
        )
        self.features_written = 0

    def run(self):
        for spec in self.group_specs:
            if self.isCanceled():
                self.messages.append("Export cancelled by the user.")
                return False
            try:
                self._write_group(spec)
            except RuntimeError as exc:
                if str(exc) == "cancelled":
                    self.messages.append("Export cancelled by the user.")
                    return False
                self.error = str(exc)
                self.messages.append(f"Export failed: {exc}")
                return False
            except OSError as exc:
                self.error = str(exc)
                self.messages.append(f"Failed to write file '{spec['output_path']}': {exc}")
                return False

        self.messages.append("Export completed successfully.")
        return True

    def _remove_partial_output(self, output_path):
        try:
            os.remove(output_path)
        except FileNotFoundError:
            return
        except OSError as exc:
            self.messages.append(f"Unable to remove partial file '{output_path}': {exc}")
            return
        self.messages.append(f"Removed partial file '{output_path}' after cancellation.")

    def _write_group(self, spec):
        output_path = spec["output_path"]
        header = spec["header"]
        layers_with_fields = spec["layers_with_fields"]

        try:
            with open(output_path, "w", encoding=self.encoding, errors="replace", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n", delimiter=self.delimiter)
                writer.writerow(header)

                for layer_spec in layers_with_fields:
                    if self.isCanceled():
                        self._remove_partial_output(output_path)
                        raise RuntimeError("cancelled")

                    if not layer_spec["crs_valid"]:
                        self.messages.append(
                            f"Skipping layer '{layer_spec['layer_name']}': invalid or undefined source CRS.",
                        )
                        continue

                    # header_index_map maps header_name.lower() -> attribute index (or None)
                    field_lookup = layer_spec.get("header_index_map", {})

                    if layer_spec["feature_count"] == 0:
                        continue

                    for feature in layer_spec["feature_source"].getFeatures():
                        if self.isCanceled():
                            self._remove_partial_output(output_path)
                            raise RuntimeError("cancelled")

                        geometry = QgsGeometry(feature.geometry())
                        if layer_spec["transform"] is not None:
                            try:
                                geometry.transform(layer_spec["transform"])
                            except Exception as exc:
                                self.messages.append(
                                    f"Reprojection failed for '{layer_spec['layer_name']}': {exc}",
                                )
                                continue

                        row = []
                        for header_name in header[:-2]:
                            index = field_lookup.get(header_name.lower())
                            if index is None:
                                row.append("")
                            else:
                                value = feature.attributes()[index] if index < len(feature.attributes()) else None
                                row.append(self.dock_widget._normalize_value(value))
                        row.append(layer_spec["layer_name"])
                        row.append(geometry.asWkt())
                        writer.writerow(row)
                        self.features_written += 1
                        if self.total_features:
                            progress = int(100 * self.features_written / self.total_features)
                        else:
                            progress = 100
                        self.setProgress(progress)
        except RuntimeError:
            raise

        self.messages.append(f"Exported {output_path}")


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
        self._cancel_requested = False
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
            field_names = [name for name in all_field_names if name in selected_fields]
            if not field_names and all_field_names:
                self._log_message(
                    f"Layer '{layer.name()}' has no selected fields; exporting geometry-only CSV.",
                    "info",
                )
            if not all_field_names:
                self._log_message(
                    f"Layer '{layer.name()}' has zero attribute fields; exporting geometry-only CSV.",
                    "info",
                )
            normalized_names = [name.lower() for name in field_names]
            if len(set(normalized_names)) != len(field_names):
                self._log_message(f"Skipping layer '{layer.name()}': duplicate field names detected.", "warning")
                continue
            if layer.featureCount() == 0:
                self._log_message(f"Layer '{layer.name()}' has zero features; exporting header only.", "warning")
            group_key = self._data_header_signature(field_names)
            grouped_layers.setdefault(group_key, []).append((layer, field_names))

        if not grouped_layers:
            self._show_message("No valid layers remained after validation.", "warning")
            return

        group_count = len(grouped_layers)
        group_specs = []
        for index, (_, layers_with_fields) in enumerate(grouped_layers.items(), start=1):
            # Build a collision-safe header and per-layer index maps
            header, per_layer_index_maps, per_layer_canonical_maps = build_group_header(layers_with_fields)
            output_name = self._build_output_name(prefix, group_count, index)
            output_path = os.path.join(output_dir, output_name)
            prepared_layers = []
            for (layer, field_names), index_map, canonical_map in zip(layers_with_fields, per_layer_index_maps, per_layer_canonical_maps):
                transform = None
                if not self.keep_original_crs_checkbox.isChecked():
                    transform = self._build_transform(layer.crs(), self._get_target_crs())
                layer_spec = {
                    "feature_source": QgsVectorLayerFeatureSource(layer),
                    "layer_name": layer.name(),
                    "crs_valid": layer.crs().isValid(),
                    "feature_count": layer.featureCount(),
                    "field_names": field_names,
                    "transform": transform,
                    "header_index_map": index_map,
                    "canonical_map": canonical_map,
                }
                prepared_layers.append(layer_spec)
                # Log any renamed fields for this layer
                for orig_lower, canonical in canonical_map.items():
                    if canonical.lower() != orig_lower:
                        orig_name = next((n for n in field_names if n.strip().lower() == orig_lower), orig_lower)
                        self._log_message(f"Field '{orig_name}' from layer '{layer.name()}' was renamed to '{canonical}' to avoid colliding with reserved column names.", "warning")

            group_specs.append({
                "output_path": output_path,
                "header": header,
                "layers_with_fields": prepared_layers,
            })

        self._cancel_requested = False
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
        )
        self._active_task.taskCompleted.connect(self._on_export_task_completed)
        self._active_task.progressChanged.connect(self._on_export_task_progress)
        QgsApplication.taskManager().addTask(self._active_task)

    def _cancel_export(self):
        if self._active_task is not None:
            self._cancel_requested = True
            self._active_task.cancel()
            self._log_message("Cancellation requested; finishing the current feature and stopping soon.", "warning")

    def _on_export_task_progress(self, progress):
        self.progress_bar.setValue(int(progress))

    def _on_export_task_completed(self, result=True):
        task = self.sender()
        if task is None:
            return

        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.export_button.setEnabled(True)
        self._active_task = None

        for message in task.messages:
            self._log_message(message, "info" if message != "Export cancelled by the user." else "warning")

        if result:
            self._show_message("Export finished successfully.", "info")
        else:
            if task.error:
                self._show_message(task.error, "error")
            else:
                self._show_message("Export was cancelled or failed.", "warning")

    # _build_layer_export_spec was removed as its logic is constructed inline
    # in `export_selected_layers`. Keeping this comment to avoid accidentally
    # reintroducing dead code.

    def _normalize_value(self, value):
        return normalize_value(value)

    def _get_selected_delimiter(self):
        selected = self.delimiter_combo.currentText()
        return "\t" if selected == "Tab" else selected

    def _source_layer_column_name(self, field_names):
        return source_layer_column_name(field_names)

    def _data_header_signature(self, field_names):
        return data_header_signature(field_names)
    # group header construction is handled by export_utils.build_group_header

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
