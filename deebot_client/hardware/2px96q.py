"""Compatibility alias for hardware class 2px96q."""

from __future__ import annotations

import importlib


def get_device_info():
    """Return device info from the canonical GOAT module."""
    module = importlib.import_module(f"{__package__}.5xu9h3")
    return module.get_device_info()

__all__ = ["get_device_info"]