"""Manipulation / organic-quality assessment. Formulas: docs/10. No LLM, no I/O."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from zynost_social.models import ManipulationAssessment, SocialMention

# --- Closed constants (docs/10 MAN-*) ---

K_SHINGLE: Final = 5
THETA_DUP: Final = 0.60
DELTA_BURST_SECONDS: Final = 300.0

# MAN-10 — spam/bot weights (sum to 1)
W_SPAM_DUP: Final = 0.45
W_SPAM_URL: Final = 0.30
W_SPAM_AUTHOR: Final = 0.25

# MAN-12 — manipulation_risk weights (sum to 1)
W_RISK_DUP: Final = 0.30
W_RISK_AUTHOR_INV: Final = 0.20
W_RISK_ENGAGEMENT: Final = 0.15
W_RISK_BURST: Final = 0.15
W_RISK_SPAM: Final = 0.20

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _clip(value: float, lo: float, hi: float) -> float:
    if value != value or value in (math.inf, -math.inf):
        return 0.0
    return max(lo, min(hi, value))


def _normalize_text(text: str) -> str:
    lowered = text.lower()
    without_urls = _URL_RE.sub(" ", lowered)
    without_punct = _PUNCT_RE.sub(" ", without_urls)
    return _WS_RE.sub(" ", without_punct).strip()


def _fingerprint(normalized: str) -> frozenset[str]:
    tokens = normalized.split()
    if len(tokens) >= K_SHINGLE:
        return frozenset(
            " ".join(tokens[i : i + K_SHINGLE])
            for i in range(len(tokens) - K_SHINGLE + 1)
        )
    return frozenset((normalized,))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _are_content_duplicates(
    norm_i: str,
    fp_i: frozenset[str],
    norm_j: str,
    fp_j: frozenset[str],
) -> bool:
    if norm_i == norm_j:
        return True
    return _jaccard(fp_i, fp_j) >= THETA_DUP


def _gini(values: Sequence[float]) -> float:
    ordered = sorted(values)
    m = len(ordered)
    total = sum(ordered)
    if m <= 1 or total == 0.0:
        return 0.0
    weighted = sum((k + 1) * x for k, x in enumerate(ordered))
    return (2.0 * weighted) / (m * total) - (m + 1) / m


def _seconds_apart(left: datetime, right: datetime) -> float:
    return abs((left - right).total_seconds())


def _population_stdev(values: Sequence[float]) -> float:
    m = len(values)
    if m <= 1:
        return 0.0
    mean = sum(values) / m
    variance = sum((x - mean) ** 2 for x in values) / m
    return math.sqrt(variance)


def assess(mentions: Sequence[SocialMention]) -> ManipulationAssessment:
    n = len(mentions)
    if n == 0:
        return ManipulationAssessment(
            duplicate_content_ratio=0.0,
            unique_author_ratio=0.0,
            engagement_concentration=0.0,
            author_concentration=0.0,
            suspicious_burst_score=0.0,
            spam_bot_suspicion=0.0,
            manipulation_risk=0.0,
            organic_score=100.0,
            anomaly_score=0.0,
        )

    authors = [m.author_id_hash for m in mentions]
    unique_authors = len(set(authors))
    unique_author_ratio = unique_authors / n

    if n <= 1:
        author_concentration = 0.0
        engagement_concentration = 0.0
    elif unique_authors == 1:
        author_concentration = 1.0
        engagement_concentration = _gini([m.engagement for m in mentions])
    else:
        counts = Counter(authors)
        author_concentration = _gini([float(c) for c in counts.values()])
        engagement_concentration = _gini([m.engagement for m in mentions])

    fp_items: list[tuple[int, str, frozenset[str]]] = []
    for i, mention in enumerate(mentions):
        normalized = _normalize_text(mention.text)
        if normalized:
            fp_items.append((i, normalized, _fingerprint(normalized)))

    n_fp = len(fp_items)
    has_dup_partner = [False] * n
    in_burst = [False] * n
    for a, (i, norm_i, fp_i) in enumerate(fp_items):
        for j, norm_j, fp_j in fp_items[a + 1 :]:
            if not _are_content_duplicates(norm_i, fp_i, norm_j, fp_j):
                continue
            has_dup_partner[i] = True
            has_dup_partner[j] = True
            if _seconds_apart(mentions[i].created_at, mentions[j].created_at) <= (
                DELTA_BURST_SECONDS
            ):
                in_burst[i] = True
                in_burst[j] = True
    duplicate_content_ratio = (
        0.0
        if n_fp == 0
        else sum(1 for i, _, _ in fp_items if has_dup_partner[i]) / n_fp
    )

    url_indices = [i for i, m in enumerate(mentions) if m.url_hash is not None]
    n_url = len(url_indices)
    if n_url == 0:
        repeated_url_ratio = 0.0
    else:
        url_counts = Counter(mentions[i].url_hash for i in url_indices)
        repeated_url_ratio = (
            sum(1 for i in url_indices if url_counts[mentions[i].url_hash] >= 2) / n_url
        )

    suspicious_burst_score = sum(1 for flag in in_burst if flag) / n

    spam_bot_suspicion = _clip(
        W_SPAM_DUP * duplicate_content_ratio
        + W_SPAM_URL * repeated_url_ratio
        + W_SPAM_AUTHOR * author_concentration,
        0.0,
        1.0,
    )
    manipulation_risk = _clip(
        W_RISK_DUP * duplicate_content_ratio
        + W_RISK_AUTHOR_INV * (1.0 - unique_author_ratio)
        + W_RISK_ENGAGEMENT * engagement_concentration
        + W_RISK_BURST * suspicious_burst_score
        + W_RISK_SPAM * spam_bot_suspicion,
        0.0,
        1.0,
    )
    organic_score = _clip(100.0 * (1.0 - manipulation_risk), 0.0, 100.0)

    if n < 2:
        cv = 0.0
    else:
        ordered_times = sorted(m.created_at for m in mentions)
        gaps = [
            (ordered_times[k + 1] - ordered_times[k]).total_seconds()
            for k in range(n - 1)
        ]
        mean_gap = sum(gaps) / len(gaps)
        cv = 1.0 if mean_gap == 0.0 else _population_stdev(gaps) / mean_gap
    anomaly_intensity_time = _clip(cv / (1.0 + cv), 0.0, 1.0)
    anomaly_score = _clip(0.5 * anomaly_intensity_time + 0.5 * manipulation_risk, 0.0, 1.0)

    return ManipulationAssessment(
        duplicate_content_ratio=duplicate_content_ratio,
        unique_author_ratio=unique_author_ratio,
        engagement_concentration=engagement_concentration,
        author_concentration=author_concentration,
        suspicious_burst_score=suspicious_burst_score,
        spam_bot_suspicion=spam_bot_suspicion,
        manipulation_risk=manipulation_risk,
        organic_score=organic_score,
        anomaly_score=anomaly_score,
    )
