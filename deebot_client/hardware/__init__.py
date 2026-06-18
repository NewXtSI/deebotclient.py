"""Hardware module."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from pathlib import Path
import re
from typing import TYPE_CHECKING, cast

from deebot_client.logging_filter import get_logger

if TYPE_CHECKING:
    from deebot_client.models import StaticDeviceInfo

__all__ = ["get_static_device_info"]

_LOGGER = get_logger(__name__)


_DEVICES: dict[str, StaticDeviceInfo] = {}
_NOT_FOUND: set[str] = set()
_ALIAS_PATTERN = re.compile(r"^([0-9a-z]{6})\.py$")


def _resolve_hardware_alias(class_: str) -> str:
    """Resolve one-line hardware alias files to their canonical module name."""
    base_dir = Path(__file__).resolve().parent
    resolved = class_
    visited: set[str] = set()

    while resolved not in visited:
        visited.add(resolved)
        module_path = base_dir / f"{resolved}.py"
        if not module_path.exists():
            return resolved

        first_line = module_path.read_text(encoding="utf-8").strip()
        match = _ALIAS_PATTERN.fullmatch(first_line)
        if match is None:
            return resolved

        resolved = match.group(1)

    return class_


async def get_static_device_info(class_: str) -> StaticDeviceInfo | None:
    """Get static device info for given class."""
    # Check if already loaded
    if device := _DEVICES.get(class_):
        _LOGGER.debug("Capabilities found for %s", class_)
        return device

    # Check if we already know it doesn't exist
    if class_ in _NOT_FOUND:
        return None

    # Try to load just this specific module
    try:
        resolved_class = _resolve_hardware_alias(class_)
        full_package_name = f"{__package__}.{resolved_class}"
        
        # Standard import fails for module names starting with digits
        # Use spec-based loading as a workaround
        try:
            module = await asyncio.to_thread(importlib.import_module, full_package_name)
        except (ModuleNotFoundError, SyntaxError):
            # Try loading directly from file for modules with invalid identifiers
            module_file = Path(__file__).resolve().parent / f"{resolved_class}.py"
            if not module_file.exists():
                raise ModuleNotFoundError(f"No module named {full_package_name}")
            
            spec = importlib.util.spec_from_file_location(
                full_package_name, module_file
            )
            if spec is None or spec.loader is None:
                raise ModuleNotFoundError(f"Cannot load {full_package_name}")
            
            module = importlib.util.module_from_spec(spec)
            await asyncio.to_thread(spec.loader.exec_module, module)
    except ModuleNotFoundError:
        _LOGGER.debug("No capabilities found for %s", class_)
        _NOT_FOUND.add(class_)
        return None

    # Get device info from the module's get_device_info function
    # This function is guaranteed to exist via a pytest test
    device = cast("StaticDeviceInfo", module.get_device_info())
    _DEVICES[class_] = device
    if resolved_class != class_:
        _DEVICES[resolved_class] = device
    _LOGGER.debug("Capabilities found for %s", class_)
    return device
