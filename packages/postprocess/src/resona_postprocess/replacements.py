"""Static regex-based text replacements."""

import re
import logging

log = logging.getLogger(__name__)


def apply_replacements(text: str, replacements: list[dict[str, str]]) -> str:
    """Apply a list of regex replacements to text in order.

    Each replacement dict must have 'name' (regex pattern) and 'replacement' (text).
    Invalid patterns are logged and skipped.
    """
    for r in replacements:
        try:
            new_text, n = re.compile(r["name"], re.IGNORECASE).subn(r["replacement"], text)
            if n > 0:
                text = new_text
        except re.error as e:
            log.warning(f"Invalid replacement pattern '{r.get('name')}': {e}")
    return text
