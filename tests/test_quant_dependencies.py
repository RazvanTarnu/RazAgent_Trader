# -*- coding: utf-8 -*-
"""P2-8: quant-stack dependencies must be declared, not assumed."""

from pathlib import Path

REQUIRED = ("numpy", "pandas", "pyarrow", "duckdb", "hypothesis")
ROOT = Path(__file__).resolve().parents[1]


def test_quant_dependencies_are_declared_in_requirements():
    lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    declared = {line.split(">=")[0].split("==")[0].split("[")[0].strip().lower() for line in lines}
    missing = [name for name in REQUIRED if name not in declared]
    assert missing == [], f"missing from requirements.txt: {missing}"
