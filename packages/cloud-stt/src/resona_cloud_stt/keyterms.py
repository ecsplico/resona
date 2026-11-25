"""Keyterm normalization shared by every provider.

Keyterms bias a provider towards words it would otherwise mis-hear (names, drug
names, jargon). Callers pass them as-is; each provider sends
:func:`normalize_keyterms` of them in its own request field.
"""
import logging
from collections.abc import Sequence

log = logging.getLogger(__name__)

MAX_KEYTERMS = 100
MAX_KEYTERM_CHARS = 50


def normalize_keyterms(keyterms: Sequence[str] | None, *, provider: str) -> list[str]:
    """Stripped, non-empty, case-insensitively deduplicated terms in input order.

    Terms longer than ``MAX_KEYTERM_CHARS`` are dropped (a cut-off term would bias
    towards words nobody says); at most ``MAX_KEYTERMS`` are kept. Logs counts only,
    never the terms.
    """
    terms: list[str] = []
    seen: set[str] = set()
    dropped = 0
    for raw in keyterms or ():
        term = " ".join(str(raw).split())
        if not term:
            continue
        if len(term) > MAX_KEYTERM_CHARS:
            dropped += 1
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    if len(terms) > MAX_KEYTERMS:
        dropped += len(terms) - MAX_KEYTERMS
        terms = terms[:MAX_KEYTERMS]
    if terms or dropped:
        log.info("%s: sending %d keyterms (%d dropped)", provider, len(terms), dropped)
    return terms
