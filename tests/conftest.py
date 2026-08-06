"""Pytest bootstrap for Almighty AI tests.

Ensures the repository root is importable so tests can ``import linux_shim``,
``import ui``, ``import main``, etc., regardless of how pytest is launched.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
