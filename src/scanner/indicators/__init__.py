"""
Indicator registry — auto-discovers every module in this package.

Usage
-----
from scanner.indicators import REGISTRY

for name, mod in REGISTRY.items():
    result = mod.compute(df)

Adding an indicator
-------------------
Drop a file into src/scanner/indicators/.
It will appear in REGISTRY automatically on the next import.
The module must expose a top-level ``NAME`` string and a ``compute`` callable.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType


def _discover() -> dict[str, ModuleType]:
    """Import every non-private module in this package and return a name→module map.

    "Non-private" means the filename does not start with ``_``.
    The registry key is the module's ``NAME`` attribute if present, otherwise the
    module's bare filename stem (e.g. ``rsi`` from ``rsi.py``).

    Safe on an empty directory — returns {} with no error.
    """
    registry: dict[str, ModuleType] = {}
    pkg_dir = Path(__file__).parent

    for module_info in pkgutil.iter_modules([str(pkg_dir)]):
        if module_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"scanner.indicators.{module_info.name}")
        except Exception as exc:  # noqa: BLE001
            import warnings
            warnings.warn(
                f"scanner.indicators: skipping {module_info.name!r} — import failed: {exc}",
                stacklevel=2,
            )
            continue
        key = getattr(mod, "NAME", module_info.name)
        registry[key] = mod

    return registry


REGISTRY: dict[str, ModuleType] = _discover()

__all__ = ["REGISTRY"]
