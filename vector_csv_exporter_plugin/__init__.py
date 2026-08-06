def classFactory(iface):
    from .plugin import VectorCsvExporterPlugin

    return VectorCsvExporterPlugin(iface)
