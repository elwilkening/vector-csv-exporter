import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction, QDockWidget
from qgis.core import QgsMapLayer

from .dock_widget import VectorCsvExporterDockWidget


class VectorCsvExporterPlugin(object):
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock_widget = None

    def initGui(self):
        self.action = QAction("Export vector layers to CSV", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu("&Vector", self.action)

        self.dock_widget = VectorCsvExporterDockWidget(self.iface.mainWindow())
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
        self.dock_widget.hide()

    def run(self):
        if self.dock_widget is None:
            self.dock_widget = VectorCsvExporterDockWidget(self.iface.mainWindow())
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
        self.dock_widget.populate_layers()
        self.dock_widget.show()
        self.dock_widget.raise_()
        self.dock_widget.activateWindow()

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu("&Vector", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.dock_widget is not None:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
            self.dock_widget = None
