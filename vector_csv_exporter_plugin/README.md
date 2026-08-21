# Vector CSV Exporter

Exports selected QGIS vector layers' attributes and geometries to CSV files, reprojecting geometries to WGS84 (or a CRS of your choice) and grouping layers with matching headers into as few output files as possible.

[![CI](https://github.com/elwilkening/Python/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/elwilkening/Python/actions)

## Features

- Export one or more selected vector layers to CSV, with per-layer field selection.
- Reproject geometries to EPSG:4326 (WGS84) by default, choose a different target CRS, or keep each layer's original CRS.
- Group layers with matching (deduplicated, case-insensitive) attribute headers into a single CSV, minimizing the number of output files.
- Duplicate field names within a layer, and real fields that collide with the plugin's own `SOURCE_LAYER`/`WKT` columns, are automatically renamed rather than dropped.
- Choose the output delimiter (`,`, `;`, Tab) and encoding (utf-8, latin-1, cp1252).
- CSV-injection protection (values starting with `=`, `+`, `-`, `@` are escaped) and correct handling of NULL attribute values.
- A `.csvt` sidecar file is written in a `csvt` subfolder so the output directory stays uncluttered; the manifest records each metadata path. Re-importing the CSV into QGIS (or any GDAL/OGR-based tool) can use the sidecar to restore the original field types instead of treating everything as text.
- Background export with a progress bar and cancel support; partial output is cleaned up on cancellation or failure.
- Warns before overwriting existing output files, and before exporting a layer whose CRS can't be reprojected to the target CRS.
- After every export, a `<prefix>_manifest.csv` documents exactly what happened: per-layer feature counts (attempted/exported/skipped, with reasons), a reconciled total, and row-count and feature-ID verification against what was actually written -- durable evidence that nothing was silently dropped, even after the plugin's own log is gone.

## Requirements
- QGIS >= 3.44
- Python 3 (as provided by QGIS)

## Installation

1. Copy the `vector_csv_exporter_plugin` folder into your QGIS plugins directory.
2. Restart QGIS and enable the plugin in the Plugin Manager.

## Usage

1. Open the plugin from the Vector menu or the toolbar icon.
2. Check the layers to export; click a layer to choose which of its fields to include (all are included by default).
3. Set the delimiter, encoding, and target CRS (or check "Keep original CRS").
4. Choose an output directory and a filename prefix, then click Export.
5. Review the status log, the `<prefix>_manifest.csv`, and the `csvt` subfolder to confirm the export was complete and metadata was created.

## Author

Eric Wilkening

## License

MIT License — see LICENSE.
