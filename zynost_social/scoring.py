"""Deterministic polar + momentum scoring (docs/08, 09, 15). No LLM, no I/O."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, cast

from zynost_social.models import (
    AccelerationState,
    BaselineBundle,
    Classification,
    Horizon,
    ScoreBundle,
    SocialMention,
)

# --- Closed constants (docs/08 SCR-*, docs/09 MOM-*, docs/15 HOR-*) ---

TAU_POLAR: Final = 0.20
N_MIN: Final = 5
R_MIX: Final = 0.20
S_STRONG: Final = 60.0
S_TONE: Final = 20.0
N_FULL: Final = 40
TAU_ACC: Final = 0.25

HEAT_N_STAR: Final = 100.0
HEAT_A_STAR: Final = 50.0
HEAT_V_STAR: Final = 8.0
HEAT_E_STAR: Final = 500.0
HEAT_W_VOLUME: Final = 0.35
HEAT_W_AUTHORS: Final = 0.25
HEAT_W_VELOCITY: Final = 0.25
HEAT_W_ENGAGEMENT: Final = 0.15

LOOKBACK_MINUTES: Final[dict[Horizon, int]] = {
    "intraday": 360,
    "swing": 1440,
    "position": 4320,
}

# HOR-04 age buckets: <=15m, <=1h, <=6h, <=24h, >24h
_RECENCY: Final[dict[Horizon, tuple[float, float, float, float, float]]] = {
    "intraday": (1.00, 0.85, 0.50, 0.15, 0.05),
    "swing": (0.70, 0.80, 1.00, 0.90, 0.20),
    "position": (0.40, 0.50, 0.75, 1.00, 0.85),
}

# HOR-06 grains: 15m, 1h, 6h, 24h
_BLEND: Final[dict[Horizon, tuple[float, float, float, float]]] = {
    "intraday": (0.40, 0.35, 0.25, 0.00),
    "swing": (0.05, 0.15, 0.40, 0.40),
    "position": (0.00, 0.10, 0.30, 0.60),
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_SEC_15M = 15 * 60
_SEC_1H = 60 * 60
_SEC_6H = 6 * 60 * 60
_SEC_24H = 24 * 60 * 60

# Polar / directional / emotion families (SCR-01, SCR-05). Longest phrase first at match time.
# Directional phrases never increment polar hits.
_POLAR_POS: Final[tuple[str, ...]] = (
    "not a scam",
    "strong fundamentals",
    "quality project",
    "legit project",
    "well designed",
    "well built",
    "based team",
    "great work",
    "love this",
    "undervalued",
    "promising",
    "impressive",
    "excellent",
    "trustworthy",
    "innovative",
    "reliable",
    "shipping",
    "amazing",
    "wholesome",
    "solid",
    "useful",
    "legit",
    "great",
    "based",
    "love",
    "good",
)
_POLAR_NEG: Final[tuple[str, ...]] = (
    "exit scam",
    "dead project",
    "rug pull",
    "rugpull",
    "overhyped",
    "honeypot",
    "worthless",
    "abandoned",
    "disappointing",
    "incompetent",
    "scammed",
    "rugged",
    "useless",
    "garbage",
    "ponzi",
    "scam",
    "fraud",
    "trash",
    "broken",
    "dying",
    "buggy",
    "fake",
    "rug",
)
_BULLISH: Final[tuple[str, ...]] = (
    "to the moon",
    "skyrocket",
    "parabolic",
    "breakout",
    "mooning",
    "mooned",
    "pumping",
    "pumped",
    "moon",
    "pump",
    "ath",
)
_BEARISH: Final[tuple[str, ...]] = (
    "going to zero",
    "sell off",
    "selloff",
    "crashing",
    "crashed",
    "dumping",
    "dumped",
    "crash",
    "dump",
    "rekt",
)
_FEAR: Final[tuple[str, ...]] = (
    "terrified",
    "frightened",
    "panicking",
    "afraid",
    "worried",
    "panic",
    "scary",
    "fear",
)
_GREED: Final[tuple[str, ...]] = (
    "cant wait",
    "excitement",
    "excited",
    "greedy",
    "greed",
    "hype",
    "hyped",
    "fomo",
)
_UNCERTAINTY: Final[tuple[str, ...]] = (
    "mixed feelings",
    "not sure",
    "who knows",
    "uncertain",
    "confused",
    "unclear",
    "unsure",
    "maybe",
)
_EUPHORIA: Final[tuple[str, ...]] = (
    "insane gains",
    "unbelievable",
    "euphoric",
    "euphoria",
    "frenzy",
    "mania",
)


def _compile(phrases: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    compiled = tuple(tuple(_TOKEN_RE.findall(p.lower())) for p in phrases)
    return tuple(sorted(compiled, key=len, reverse=True))


_POLAR_LABELED: Final[tuple[tuple[tuple[str, ...], str], ...]] = tuple(
    sorted(
        [(p, "pos") for p in _compile(_POLAR_POS)] + [(p, "neg") for p in _compile(_POLAR_NEG)],
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_DIR_LABELED: Final[tuple[tuple[tuple[str, ...], str], ...]] = tuple(
    sorted(
        [(p, "bull") for p in _compile(_BULLISH)] + [(p, "bear") for p in _compile(_BEARISH)],
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_EMO_LABELED: Final[tuple[tuple[tuple[str, ...], str], ...]] = tuple(
    sorted(
        [(p, "fear") for p in _compile(_FEAR)]
        + [(p, "greed") for p in _compile(_GREED)]
        + [(p, "unc") for p in _compile(_UNCERTAINTY)]
        + [(p, "euph") for p in _compile(_EUPHORIA)],
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


def _applied_horizon(horizon: str) -> Horizon:
    if horizon in ("intraday", "swing", "position"):
        return cast(Horizon, horizon)
    return "swing"


def _clip(value: float, lo: float, hi: float) -> float:
    if value != value or value in (math.inf, -math.inf):
        return 0.0
    return max(lo, min(hi, value))


def _tokenize(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(text.lower()))


def _family_hits(
    tokens: Sequence[str],
    labeled: Sequence[tuple[tuple[str, ...], str]],
) -> dict[str, float]:
    used = [False] * len(tokens)
    counts: dict[str, float] = {}
    for phrase, label in labeled:
        n = len(phrase)
        if n == 0:
            continue
        i = 0
        while i <= len(tokens) - n:
            if not any(used[i : i + n]) and tuple(tokens[i : i + n]) == phrase:
                counts[label] = counts.get(label, 0.0) + 1.0
                for j in range(n):
                    used[i + j] = True
                i += n
            else:
                i += 1
    return counts


def recency_multiplier(age_seconds: float, horizon: str) -> float:
    """HOR-04. Skew (negative age) sits in the newest bucket."""
    applied = _applied_horizon(horizon)
    age = max(0.0, age_seconds)
    row = _RECENCY[applied]
    if age <= _SEC_15M:
        return row[0]
    if age <= _SEC_1H:
        return row[1]
    if age <= _SEC_6H:
        return row[2]
    if age <= _SEC_24H:
        return row[3]
    return row[4]


def blend_acceleration(
    alpha_15m: float,
    known_15m: bool,
    alpha_1h: float,
    known_1h: bool,
    alpha_6h: float,
    known_6h: bool,
    alpha_24h: float,
    known_24h: bool,
    horizon: str,
) -> float:
    """HOR-06: drop unknown / zero-weight grains, renormalize."""
    applied = _applied_horizon(horizon)
    weights = _BLEND[applied]
    grains = (
        (alpha_15m, known_15m, weights[0]),
        (alpha_1h, known_1h, weights[1]),
        (alpha_6h, known_6h, weights[2]),
        (alpha_24h, known_24h, weights[3]),
    )
    selected = [(alpha, weight) for alpha, known, weight in grains if known and weight > 0.0]
    if not selected:
        return 0.0
    total = sum(weight for _, weight in selected)
    return sum(alpha * (weight / total) for alpha, weight in selected)


def source_agreement_from_scores(scores: Sequence[float]) -> tuple[float, float]:
    """MOM-09 pairwise tone agreement. m < 2 → (0, 0). Missing sources omitted by caller."""
    m = len(scores)
    if m < 2:
        return 0.0, 0.0
    pair_sum = 0.0
    pairs = 0
    for i in range(m):
        for j in range(i + 1, m):
            pair_sum += 1.0 - abs(scores[i] - scores[j]) / 200.0
            pairs += 1
    agreement = _clip(pair_sum / pairs, 0.0, 1.0)
    return agreement, _clip(1.0 - agreement, 0.0, 1.0)


def _observation_anchor(
    mentions: Sequence[SocialMention],
    baselines: BaselineBundle,
) -> datetime | None:
    if baselines.observed_at is not None:
        return baselines.observed_at
    if mentions:
        return max(m.created_at for m in mentions)
    return None


def _in_hours(created: datetime, t0: datetime, hours: float) -> bool:
    if created > t0:
        return True
    return (t0 - timedelta(hours=hours)) < created <= t0


def _bin_15m(created: datetime, t0: datetime) -> int | None:
    """0 = oldest 15m of the current 1h, 3 = newest. None if outside the hour."""
    if created > t0:
        return 3
    age = (t0 - created).total_seconds()
    if age >= _SEC_1H:
        return None
    return 3 - int(age // _SEC_15M)


def _relative_change(current: float, prior: int | None) -> tuple[float, bool]:
    if prior is None:
        return 0.0, False
    return (current - prior) / max(prior, 1), True


def _classify(
    n_eff: int,
    r_pos: float,
    r_neg: float,
    sentiment_score: float,
) -> Classification:
    if n_eff < N_MIN:
        return "insufficient_data"
    if r_pos >= R_MIX and r_neg >= R_MIX:
        return "mixed"
    if sentiment_score >= S_STRONG:
        return "strong_positive"
    if sentiment_score >= S_TONE:
        return "positive"
    if sentiment_score <= -S_STRONG:
        return "strong_negative"
    if sentiment_score <= -S_TONE:
        return "negative"
    return "neutral"


def _acceleration_state(alpha: float) -> AccelerationState:
    if alpha > TAU_ACC:
        return "accelerating"
    if alpha < -TAU_ACC:
        return "fading"
    return "holding"


def score(
    mentions: Sequence[SocialMention],
    horizon: str,
    baselines: BaselineBundle,
) -> ScoreBundle:
    applied = _applied_horizon(horizon)
    n = len(mentions)
    t0 = _observation_anchor(mentions, baselines)

    n_pos = 0
    n_neu = 0
    n_neg = 0
    n_eff = 0
    n_empty = 0
    sum_r = 0.0
    sum_rs = 0.0
    sum_w = 0.0
    sum_ws = 0.0
    cap_bull = 0.0
    cap_bear = 0.0
    cap_fear = 0.0
    cap_greed = 0.0
    cap_unc = 0.0
    cap_euph = 0.0

    for mention in mentions:
        text = mention.text.strip()
        if not text:
            n_empty += 1
            continue
        n_eff += 1
        tokens = _tokenize(text)
        polar = _family_hits(tokens, _POLAR_LABELED)
        directional = _family_hits(tokens, _DIR_LABELED)
        emotion = _family_hits(tokens, _EMO_LABELED)
        pos_hits = polar.get("pos", 0.0)
        neg_hits = polar.get("neg", 0.0)
        denom = pos_hits + neg_hits
        p_raw = 0.0 if denom == 0.0 else (pos_hits - neg_hits) / denom
        if p_raw >= TAU_POLAR:
            s_i = 1
            n_pos += 1
        elif p_raw <= -TAU_POLAR:
            s_i = -1
            n_neg += 1
        else:
            s_i = 0
            n_neu += 1

        age = 0.0 if t0 is None else max(0.0, (t0 - mention.created_at).total_seconds())
        r_i = recency_multiplier(age, applied)
        w_i = (1.0 + math.log(1.0 + mention.engagement)) * r_i
        sum_r += r_i
        sum_rs += r_i * s_i
        sum_w += w_i
        sum_ws += w_i * s_i
        cap_bull += min(1.0, directional.get("bull", 0.0) / 2.0)
        cap_bear += min(1.0, directional.get("bear", 0.0) / 2.0)
        cap_fear += min(1.0, emotion.get("fear", 0.0) / 2.0)
        cap_greed += min(1.0, emotion.get("greed", 0.0) / 2.0)
        cap_unc += min(1.0, emotion.get("unc", 0.0) / 2.0)
        cap_euph += min(1.0, emotion.get("euph", 0.0) / 2.0)

    r_pos = (n_pos / n_eff) if n_eff else 0.0
    r_neu = (n_neu / n_eff) if n_eff else 0.0
    r_neg = (n_neg / n_eff) if n_eff else 0.0

    if n_eff < N_MIN or n_eff == 0:
        sentiment_score = 0.0
        engagement_weighted = 0.0
    else:
        sentiment_score = _clip(100.0 * (sum_rs / sum_r), -100.0, 100.0) if sum_r else 0.0
        engagement_weighted = _clip(100.0 * (sum_ws / sum_w), -100.0, 100.0) if sum_w else 0.0

    classification = _classify(n_eff, r_pos, r_neg, sentiment_score)

    empty_share = (n_empty / n) if n else 0.0
    coverage = _clip(n_eff / N_FULL, 0.0, 1.0)
    polar_mass = max(r_pos + r_neg, 1e-9)
    agreement = 1.0 - (2.0 * min(r_pos, r_neg) / polar_mass)
    confidence = _clip(
        100.0 * coverage * (0.40 + 0.60 * agreement) * (1.0 - 0.50 * empty_share),
        0.0,
        100.0,
    )

    def _intensity(capped: float) -> float:
        if n_eff == 0:
            return 0.0
        return _clip(100.0 * (capped / n_eff), 0.0, 100.0)

    unique_authors = len({m.author_id_hash for m in mentions}) if mentions else 0

    mentions_1h = 0
    mentions_6h = 0
    mentions_24h = 0
    bins = [0, 0, 0, 0]
    e_1h = 0.0
    e_bins = [0.0, 0.0, 0.0, 0.0]
    newest: datetime | None = None

    if t0 is not None:
        for mention in mentions:
            newest = mention.created_at if newest is None else max(newest, mention.created_at)
            if _in_hours(mention.created_at, t0, 1):
                mentions_1h += 1
                e_1h += mention.engagement
                slot = _bin_15m(mention.created_at, t0)
                if slot is not None:
                    bins[slot] += 1
                    e_bins[slot] += mention.engagement
            if _in_hours(mention.created_at, t0, 6):
                mentions_6h += 1
            if _in_hours(mention.created_at, t0, 24):
                mentions_24h += 1

    delta_6h, known_6h = _relative_change(mentions_6h, baselines.prior_mentions_6h)
    delta_24h, known_24h = _relative_change(mentions_24h, baselines.prior_mentions_24h)
    alpha_1h, known_1h = _relative_change(mentions_1h, baselines.prior_mentions_1h)
    known_15m = mentions_1h > 0
    if mentions_1h == 0:
        alpha_15m = 0.0
    else:
        alpha_15m = (bins[3] - bins[2]) / max(bins[2], 1)

    if n == 0:
        mention_velocity = 0.0
        engagement_velocity = 0.0
        social_heat = 0.0
        social_acceleration = 0.0
        acceleration_state: AccelerationState = "holding"
        activity_coverage = 0.0
        freshness_seconds: float | None = None
        mentions_1h_previous: int | None = baselines.prior_mentions_1h
    else:
        if baselines.prior_mentions_1h is None:
            rho = 1.0
        else:
            rho = mentions_1h / max(baselines.prior_mentions_1h, 1)
        kappa = 1.0 if mentions_1h == 0 else (4.0 * max(bins)) / mentions_1h
        mention_velocity = rho * kappa

        if baselines.prior_engagement_1h is None:
            eta = 1.0
        else:
            eta = (1.0 + e_1h) / (1.0 + baselines.prior_engagement_1h)
        kappa_e = 1.0 if e_1h == 0.0 else (4.0 * max(e_bins)) / e_1h
        engagement_velocity = eta * kappa_e

        vol = _clip(math.log(1.0 + n) / math.log(1.0 + HEAT_N_STAR), 0.0, 1.0)
        auth = _clip(unique_authors / HEAT_A_STAR, 0.0, 1.0)
        vel = _clip(
            math.log(1.0 + mention_velocity) / math.log(1.0 + HEAT_V_STAR),
            0.0,
            1.0,
        )
        eng = _clip(math.log(1.0 + e_1h) / math.log(1.0 + HEAT_E_STAR), 0.0, 1.0)
        social_heat = _clip(
            100.0
            * (
                HEAT_W_VOLUME * vol
                + HEAT_W_AUTHORS * auth
                + HEAT_W_VELOCITY * vel
                + HEAT_W_ENGAGEMENT * eng
            ),
            0.0,
            100.0,
        )
        social_acceleration = blend_acceleration(
            alpha_15m,
            known_15m,
            alpha_1h,
            known_1h,
            delta_6h,
            known_6h,
            delta_24h,
            known_24h,
            applied,
        )
        acceleration_state = _acceleration_state(social_acceleration)
        activity_coverage = (sum(1 for count in bins if count >= 1) / 4.0) if mentions_1h else 0.0
        if t0 is None or newest is None:
            freshness_seconds = None
        else:
            freshness_seconds = max(0.0, (t0 - newest).total_seconds())
        mentions_1h_previous = baselines.prior_mentions_1h

    sentiment_change: float | None = None
    sentiment_change_hours: float | None = None
    if (
        baselines.prior_sentiment_score is not None
        and baselines.prior_observed_at is not None
        and t0 is not None
    ):
        sentiment_change = sentiment_score - baselines.prior_sentiment_score
        sentiment_change_hours = (t0 - baselines.prior_observed_at).total_seconds() / 3600.0

    return ScoreBundle(
        sentiment_score=sentiment_score,
        classification=classification,
        confidence=confidence,
        social_heat=social_heat,
        mention_velocity=mention_velocity,
        social_acceleration=social_acceleration,
        mention_count=n,
        unique_authors=unique_authors,
        horizon=applied,
        positive_count=n_pos,
        neutral_count=n_neu,
        negative_count=n_neg,
        positive_ratio=r_pos,
        neutral_ratio=r_neu,
        negative_ratio=r_neg,
        engagement_weighted_sentiment=engagement_weighted,
        bullish_phrase_intensity=_intensity(cap_bull),
        bearish_phrase_intensity=_intensity(cap_bear),
        fear_intensity=_intensity(cap_fear),
        greed_excitement_intensity=_intensity(cap_greed),
        uncertainty_intensity=_intensity(cap_unc),
        euphoria_intensity=_intensity(cap_euph),
        mentions_1h=mentions_1h,
        mentions_1h_previous=mentions_1h_previous,
        mentions_6h=mentions_6h,
        mentions_24h=mentions_24h,
        mentions_6h_change=delta_6h,
        mentions_24h_change=delta_24h,
        engagement_velocity=engagement_velocity,
        acceleration_state=acceleration_state,
        source_agreement=0.0,
        source_disagreement=0.0,
        freshness_seconds=freshness_seconds,
        activity_coverage=activity_coverage,
        sentiment_change=sentiment_change,
        sentiment_change_hours=sentiment_change_hours,
    )
