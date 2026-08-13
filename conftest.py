import sys
from pathlib import Path

# The tests import via the package name (vector_csv_exporter_plugin.export_utils),
# which requires the repo ROOT on sys.path -- not the package directory itself.
# Relying on pytest's default import mode to insert the rootdir breaks under
# --import-mode=importlib, so insert it explicitly.
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
