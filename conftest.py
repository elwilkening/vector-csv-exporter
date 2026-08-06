import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent / "vector_csv_exporter_plugin"
sys.path.insert(0, str(PLUGIN_DIR))
