# Vector CSV Exporter

Exports selected QGIS vector layers' attributes and geometries to CSV files, reprojecting geometries to WGS84 and grouping layers with matching headers.

[![CI](https://github.com/elwilkening/Python/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/elwilkening/Python/actions)

## Features

- Export multiple vector layers to CSV
- Reproject geometries to EPSG:4326 (WGS84)
- Group layers with identical attribute headers into a single CSV
- Include a `WKT` column containing WKT for each feature

## Requirements
- QGIS >= 3.44
- Python 3 (as provided by QGIS)

## Installation

1. Copy the `vector_csv_exporter_plugin` folder into your QGIS plugins directory.
2. Restart QGIS and enable the plugin in the Plugin Manager.

## Usage

1. Open the plugin from the Vector menu or the toolbar icon.
2. Select layers to export and choose an output directory.
3. Provide a filename prefix and click Export.

## Author

Eric Wilkening with OpenAI (<elwilkening@outlook.com>)

## License

MIT License — see LICENSE.
