from .plugin import VectorCsvExporterPlugin


def classFactory(iface):
    return VectorCsvExporterPlugin(iface)
