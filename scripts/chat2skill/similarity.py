"""Shared similarity primitives and merge thresholds.

Single source of truth for the tokenizer, lexical (Jaccard) and vector
(cosine) similarity used by the proposer, storage merge, replay sampling,
retrieval, and maintenance. The two thresholds are on different scales by
design: cosine compares dense embeddings, Jaccard compares token sets.
"""

from __future__ import annotations

import math
import re
from typing import List

MERGE_COSINE_THRESHOLD = 0.86
MERGE_LEXICAL_THRESHOLD = 0.62


def tokens(text: str) -> set[str]:
    """Word tokens plus CJK unigrams/bigrams, lowercased."""
    result = {
        token
        for token in re.split(r"[^a-zA-Z0-9_一-鿿]+", (text or "").lower())
        if len(token) > 1
    }
    cjk = re.findall(r"[一-鿿]", text or "")
    result.update(cjk)
    result.update("".join(pair) for pair in zip(cjk, cjk[1:]))
    return result


def jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def cosine(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
