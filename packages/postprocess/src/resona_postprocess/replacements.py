"""Static regex-based text replacements."""

import re
import logging

log = logging.getLogger(__name__)


def apply_replacements(text: str, replacements: list[dict[str, str]]) -> str:
    """Apply a list of regex replacements to text in order.

    Each rule provides the regex under ``pattern`` (preferred) or the legacy
    ``name`` key, plus ``replacement``. Invalid patterns are logged and skipped.
    """
    for r in replacements:
        pattern = r.get("pattern", r.get("name"))
        if pattern is None:
            log.warning("Replacement rule missing 'pattern'/'name': %r", r)
            continue
        try:
            new_text, n = re.compile(pattern, re.IGNORECASE).subn(
                r.get("replacement", ""), text
            )
            if n > 0:
                text = new_text
        except re.error as e:
            log.warning(f"Invalid replacement pattern '{pattern}': {e}")
    return text
