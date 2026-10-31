"""Local file input for the stdio server: only regular files under ``RESONA_MCP_ROOT``."""
import re
from pathlib import Path
from typing import Optional

_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class PathRejected(ValueError):
    """The requested path is not an allowed local input; the message is safe to show."""


def looks_like_url(value: str) -> bool:
    """True for anything carrying a URI scheme (``https:``, ``file:``, ``s3:`` …)."""
    return bool(_URL.match(value)) and not re.match(r"^[A-Za-z]:[\\/]", value)


def resolve_input_path(raw: str, root: Optional[Path]) -> Path:
    """Return the real path of ``raw`` if it is a regular file inside ``root``.

    ``raw`` may be absolute or relative to ``root``. Symlinks are resolved before the
    containment check, so a link pointing outside the root is rejected, as are ``..``
    escapes. Without a root, path input is disabled.
    """
    if root is None:
        raise PathRejected("path input is disabled: RESONA_MCP_ROOT is not set; use upload_content or upload_id")
    if not raw or looks_like_url(raw):
        raise PathRejected("path must be a local file path under RESONA_MCP_ROOT, not a URL")
    real_root = root.resolve(strict=True)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = real_root / candidate
    try:
        real = candidate.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError):
        raise PathRejected("path does not exist under RESONA_MCP_ROOT") from None
    if not real.is_relative_to(real_root):
        raise PathRejected("path resolves outside RESONA_MCP_ROOT")
    if not real.is_file():
        raise PathRejected("path is not a regular file")
    return real
