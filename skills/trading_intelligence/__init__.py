# -*- coding: utf-8 -*-
"""Tombstone for the renamed legacy package (P2-1 / C3).

The former ``skills.trading_intelligence`` tree now lives at
``legacy.trading_intelligence_v1``. Keeping this module as a hard
ImportError prevents the old name from silently resolving after the
quant engine package ``trading_intelligence/`` lands at repo root.
"""

raise ImportError(
    "skills.trading_intelligence was renamed to "
    "legacy.trading_intelligence_v1 (P2-1 / C3). Update the import."
)
