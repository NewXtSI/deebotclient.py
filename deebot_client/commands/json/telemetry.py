"""Telemetry command module for sensor and device metrics."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from deebot_client.commands.json.common import JsonCommand
from deebot_client.events import TelemetryEvent
from deebot_client.logging_filter import get_logger
from deebot_client.message import HandlingResult, HandlingState

if TYPE_CHECKING:
    from deebot_client.event_bus import EventBus

_LOGGER = get_logger(__name__)


class TelemetryCommand(JsonCommand):
    """Telemetry command handler for onFwBuryPoint-* and similar sensor data messages."""

    NAME: str = "TelemetryCommand"

    def __init__(self, sensor_type: str) -> None:
        """Initialize telemetry command.
        
        Args:
            sensor_type: The sensor type suffix from onFwBuryPoint-* messages (e.g., "bd_sysinfo")
        """
        self.sensor_type = sensor_type
        self.NAME = f"onFwBuryPoint-{sensor_type}"
        super().__init__()

    @staticmethod
    def parse_telemetry_payload(payload: bytes | str | bytearray) -> dict[str, Any]:
        """Parse telemetry payload from JSON or raw bytes.
        
        Args:
            payload: Raw message payload (bytes, str, or bytearray)
            
        Returns:
            Parsed JSON dictionary or empty dict on parse error
        """
        try:
            if isinstance(payload, (bytes, bytearray)):
                payload_str = payload.decode("utf-8")
            else:
                payload_str = payload

            data = json.loads(payload_str)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            _LOGGER.debug("Failed to parse telemetry payload: %s", e)
        
        return {}

    def handle_telemetry(
        self, event_bus: EventBus, payload: bytes | str | bytearray
    ) -> HandlingResult:
        """Handle incoming telemetry data and emit TelemetryEvent.
        
        Args:
            event_bus: Event bus for publishing events
            payload: Raw message payload
            
        Returns:
            HandlingResult indicating success or failure
        """
        data = self.parse_telemetry_payload(payload)
        
        if not data:
            _LOGGER.debug(
                "Received empty telemetry data for sensor type %s", self.sensor_type
            )
            return HandlingResult(HandlingState.FAILED)
        
        # Extract body data if present (typical structure: {"body": {...}})
        if "body" in data:
            body_data = data["body"]
        else:
            body_data = data

        _LOGGER.debug(
            "Received telemetry data for sensor %s: %s", self.sensor_type, body_data
        )
        
        event_bus.notify(TelemetryEvent(
            sensor_type=self.sensor_type,
            data=body_data
        ))
        
        return HandlingResult.success()

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        """Handle response from a telemetry command (not typically called).
        
        This method exists for completeness but telemetry is usually
        handled via handle_telemetry() directly.
        """
        return HandlingResult.success()

    def __eq__(self, obj: object) -> bool:
        if super().__eq__(obj) and isinstance(obj, TelemetryCommand):
            return self.sensor_type == obj.sensor_type
        return False

    def __hash__(self) -> int:
        return hash((self.__class__, self.sensor_type))
