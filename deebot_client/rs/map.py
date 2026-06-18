from __future__ import annotations

import base64
from enum import Enum
import zlib

from deebot_client.events.map import Position


class BackgroundImage:
    """Python fallback for map background image handling."""

    def __init__(self) -> None:
        self._pieces: dict[int, bytes] = {}
        self._crc32: dict[int, int] = {}

    def update_map_piece(self, index: int, base64_data: str) -> bool:
        """Decode and store a map piece. Returns True when content changed."""
        raw = base64.b64decode(base64_data)
        changed = self._pieces.get(index) != raw
        self._pieces[index] = raw
        self._crc32[index] = zlib.crc32(raw) & 0xFFFFFFFF
        return changed

    def map_piece_crc32_indicates_update(self, index: int, crc32: int) -> bool:
        """Return True if the provided crc32 differs from cached data."""
        return self._crc32.get(index) != crc32


class TracePoints:
    """Python fallback for trace point handling."""

    def __init__(self) -> None:
        self._values: list[str] = []

    def add(self, value: str) -> None:
        self._values.append(value)

    def clear(self) -> None:
        self._values.clear()


class MapInfo:
    """Python fallback for map info payload storage."""

    def __init__(self) -> None:
        self.value: str | None = None

    def set(self, baset64_data: str) -> None:
        self.value = baset64_data


class MapData:
    """Python fallback for rust-backed map data."""

    def __init__(self) -> None:
        self._background_image = BackgroundImage()
        self._map_info = MapInfo()
        self._trace_points = TracePoints()

    @property
    def background_image(self) -> BackgroundImage:
        return self._background_image

    @property
    def map_info(self) -> MapInfo:
        return self._map_info

    @property
    def trace_points(self) -> TracePoints:
        return self._trace_points

    def generate_svg(
        self,
        subsets: list[object],
        position: list[Position],
        rotation: RotationAngle,
    ) -> str | None:
        """No SVG renderer in Python fallback; keep API compatible."""
        _ = (subsets, position, rotation)
        return None


class PositionType(Enum):
    """Position type enum."""

    DEEBOT = "deebot"
    CHARGER = "charger"

    @staticmethod
    def from_str(value: str) -> PositionType:
        normalized = value.lower()
        if normalized in {"deebot", "deebotpos"}:
            return PositionType.DEEBOT
        if normalized in {"charger", "chargepos", "chargerpos"}:
            return PositionType.CHARGER
        error_message = f"Unsupported position type: {value}"
        raise ValueError(error_message)


class RotationAngle(Enum):
    """Rotation angle enum."""

    DEG_0 = 0
    DEG_90 = 90
    DEG_180 = 180
    DEG_270 = 270

    @staticmethod
    def from_int(value: int) -> RotationAngle:
        mapping = {
            0: RotationAngle.DEG_0,
            90: RotationAngle.DEG_90,
            180: RotationAngle.DEG_180,
            270: RotationAngle.DEG_270,
        }
        return mapping.get(int(value), RotationAngle.DEG_0)
