# -*- coding: utf-8 -*-
"""Platform startup validation script for PowerShell launcher."""

from shared.platform.lifecycle import run_startup_validation

if __name__ == "__main__":
    raise SystemExit(run_startup_validation())
