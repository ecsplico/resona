"""Back-compat shim — registry now lives in resona-asr-core."""
from resona_asr_core.registry import (  # noqa: F401
    get_transcriber,
    reset,
    _load_from_entrypoint,
    ENTRY_POINT_GROUP,
)

__all__ = ["get_transcriber", "reset", "_load_from_entrypoint", "ENTRY_POINT_GROUP"]
