"""Accuracy metrics for the transcription benchmark.

Word- and character-error-rate against a reference, after light text
normalization (lowercase, strip punctuation, collapse whitespace). Umlauts and
other unicode word characters are preserved so German is scored fairly.
"""

import re

import jiwer

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def score(reference: str, hypothesis: str) -> dict:
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    return {
        "wer": jiwer.wer(ref, hyp),
        "cer": jiwer.cer(ref, hyp),
        "ref_words": len(ref.split()),
        "hyp_words": len(hyp.split()),
    }
