import csv
import os

from qgis.PyQt import QtCore, QtWidgets, uic
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsMapLayer,
    QgsMessageLog,
    QgsProject,
    QgsVectorLayer,
)


class VectorCsvExporterDockWidget(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        ui_path = os.path.join(os.path.dirname(__file__), "dock_widget.ui")
        uic.loadUi(ui_path, self)
        self.setWindowTitle("Vector CSV Exporter")
        self.select_all_checkbox.stateChanged.connect(self._set_all_checked)
        self.export_button.clicked.connect(self.export_selected_layers)
        self.populate_layers()

    def populate_layers(self):
        self.layer_list_widget.clear()
        project = QgsProject.instance()
        warning_messages = []

        for layer in project.mapLayers().values():
            if layer.type() == QgsMapLayer.VectorLayer:
                item = QtWidgets.QListWidgetItem(layer.name())
                item.setData(QtCore.Qt.UserRole, layer.id())
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Checked)
                self.layer_list_widget.addItem(item)
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

    def export_selected_layers(self):
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

        output_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose output directory",
            os.path.expanduser("~"),
        )
        if not output_dir:
            self._log_message("Export cancelled by the user.", "info")
            return

        if not os.access(output_dir, os.W_OK):
            self._show_message(f"Cannot write to the selected directory: {output_dir}", "error")
            return

        prefix, ok = QtWidgets.QInputDialog.getText(
            self,
            "Export prefix",
            "Enter a shared prefix for the output files:",
            text="export",
        )
        if not ok:
            self._log_message("Export cancelled by the user.", "info")
            return

        prefix = self._sanitize_prefix(prefix)
        grouped_layers = {}

        for layer in selected_layers:
            field_names = [field.name() for field in layer.fields()]
            if not field_names:
                self._log_message(f"Skipping layer '{layer.name()}': zero fields.", "warning")
                continue
            if len({name.lower() for name in field_names}) != len(field_names):
                self._log_message(f"Skipping layer '{layer.name()}': duplicate field names detected.", "warning")
                continue
            if layer.featureCount() == 0:
                self._log_message(f"Layer '{layer.name()}' has zero features; exporting header only.", "warning")
            group_key = tuple(name.lower() for name in field_names)
            grouped_layers.setdefault(group_key, []).append((layer, field_names))

        if not grouped_layers:
            self._show_message("No valid layers remained after validation.", "warning")
            return

        group_count = len(grouped_layers)
        for index, (_, layers_with_fields) in enumerate(grouped_layers.items(), start=1):
            header = layers_with_fields[0][1] + ["GEOMETRY"]
            output_name = self._build_output_name(prefix, group_count, index)
            output_path = os.path.join(output_dir, output_name)
            try:
                self._write_csv(output_path, header, layers_with_fields)
                self._log_message(f"Exported {output_path}", "info")
            except OSError as exc:
                self._show_message(f"Failed to write file '{output_path}': {exc}", "error")

    def _write_csv(self, output_path, header, layers_with_fields):
        with open(output_path, "w", encoding="utf-8", errors="replace", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)

            for layer, field_names in layers_with_fields:
                if not layer.crs().isValid():
                    self._log_message(
                        f"Skipping layer '{layer.name()}': invalid or undefined source CRS.",
                        "warning",
                    )
                    continue

                transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
                field_lookup = {name.lower(): idx for idx, name in enumerate(field_names)}

                if layer.featureCount() == 0:
                    continue

                for feature in layer.getFeatures():
                    geometry = QgsGeometry(feature.geometry())
                    try:
                        geometry.transform(transform)
                    except Exception as exc:
                        self._log_message(
                            f"Reprojection failed for '{layer.name()}': {exc}",
                            "warning",
                        )
                        continue

                    row = []
                    for header_name in header[:-1]:
                        index = field_lookup.get(header_name.lower())
                        if index is None:
                            row.append("")
                        else:
                            value = feature.attributes()[index] if index < len(feature.attributes()) else None
                            row.append(self._normalize_value(value))
                    row.append(geometry.asWkt())
                    writer.writerow(row)

    def _normalize_value(self, value):
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            return value.encode("utf-8", errors="replace").decode("utf-8")
        return str(value)

    def _sanitize_prefix(self, prefix):
        import re
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", prefix.strip())
        return sanitized or "export"

    def _build_output_name(self, prefix, group_count, index):
        if group_count == 1:
            return f"{prefix}.csv" if not prefix.lower().endswith(".csv") else prefix
        if prefix.lower().endswith(".csv"):
            prefix = prefix[:-4]
        return f"{prefix}_group{index}.csv"

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
