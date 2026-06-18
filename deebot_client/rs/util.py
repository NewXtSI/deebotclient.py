from __future__ import annotations

import base64
import lzma


def decompress_base64_data(value: str) -> bytes:
    """Decode base64 payload and try common compression formats."""
    raw = base64.b64decode(value)

    # Most map payloads are lzma-compressed.
    try:
        return lzma.decompress(raw)
    except lzma.LZMAError:
        pass

    # Python 3.13 has no stdlib zstd decoder; use optional dependency if present.
    try:
        import zstandard as zstd  # type: ignore[import-not-found]

        return zstd.ZstdDecompressor().decompress(raw)
    except Exception:
        return raw
