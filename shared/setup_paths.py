# -*- coding: utf-8 -*-
"""Centralized sys.path setup for RazAgent Enterprise.

Replaces 88+ scattered ``sys.path.insert()`` hacks with a single idempotent
call.  Entry points need one small bootstrap line to reach this module, then
delegate all path setup here.

Usage (entry points):
    # Bootstrap (one line to reach project root):
    import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    # Then delegate:
    from shared.setup_paths import setup_import_paths; setup_import_paths()
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # D:\RazAgent_Enterprise


def setup_import_paths() -> None:
    """Add PROJECT_ROOT to ``sys.path`` (once) so ``shared``, ``backend``, etc.
    can be imported with absolute paths from any entry point.

    Idempotent — safe to call multiple times.
    """
    root_str = str(PROJECT_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


# Backward-compat alias (existing consumers use ``activate``).
activate = setup_import_paths
