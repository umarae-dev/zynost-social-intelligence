"""Per-source combine and scope. Rules: docs/11. No I/O, no Redis, no LLM."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Final

from zynost_social.models import (
    AggregateResult,
    AnomalyRecord,
    Classification,
    ManipulationAssessment,
    MatchBatch,
    ProviderId,
    Scope,
    ScoreBundle,
    SourceBundle,
)
from zynost_social.scoring import N_MIN, S_STRONG, S_TONE, source_agreement_from_scores

THETA_FLAG: Final = 0.50
TRADE_TOKENS: Final = ("BUY", "SELL", "LONG", "SHORT")


def _clip(value: float, lo: float, hi: float) -> float:
    if value != value or value in (math.inf, -math.inf):
        return 0.0
    return max(lo, min(hi, value))


def _weighted_mean(weights_and_values: Sequence[tuple[float, float]]) -> float:
    total = sum(weight for weight, _ in weights_and_values)
    if total <= 0.0:
        return 0.0
    return sum(weight * value for weight, value in weights_and_values) / total


def _n_eff(match_batch: MatchBatch) -> int:
    return sum(1 for mention in match_batch.asset_mentions if mention.text.strip())


def _scope(n_a: int, n_w: int) -> Scope:
    if n_a >= 1:
        return "asset_specific"
    if n_w >= 1:
        return "market_wide"
    return "asset_specific"


def _classify_from_agg(s_agg: float, votes: Sequence[float]) -> Classification:
    has_pos = any(score >= S_TONE for score in votes)
    has_neg = any(score <= -S_TONE for score in votes)
    if has_pos and has_neg:
        return "mixed"
    if s_agg >= S_STRONG:
        return "strong_positive"
    if s_agg >= S_TONE:
        return "positive"
    if s_agg <= -S_STRONG:
        return "strong_negative"
    if s_agg <= -S_TONE:
        return "negative"
    return "neutral"


def _assert_no_trade_language(*parts: str) -> None:
    for part in parts:
        upper = part.upper()
        for token in TRADE_TOKENS:
            if token in upper.split():
                raise ValueError("aggregation must not emit trade language")


def aggregate(
    per_source: Sequence[SourceBundle],
    match_batch: MatchBatch,
    horizon: str,
    *,
    scores: Mapping[ProviderId, ScoreBundle] | None = None,
    assessments: Mapping[ProviderId, ManipulationAssessment] | None = None,
    union_score: ScoreBundle | None = None,
    union_assessment: ManipulationAssessment | None = None,
) -> AggregateResult:
    """Combine retained source rows. Horizon is identity only (AGG-03); mix weight is 1.0."""
    del horizon
    score_map = scores or {}
    assess_map = assessments or {}
    rows = tuple(per_source)
    p_reg = len(rows)
    evidence = [row for row in rows if row.status == "available"]
    available = len(evidence)
    n_a = len(match_batch.asset_mentions)
    n_w = len(match_batch.market_wide)
    n_eff = _n_eff(match_batch)
    scope = _scope(n_a, n_w)
    use_union = n_a >= 1 and union_score is not None

    votes: list[tuple[SourceBundle, ScoreBundle]] = []
    for row in evidence:
        bundle = score_map.get(row.provider)
        if bundle is None:
            continue
        if bundle.classification == "insufficient_data":
            continue
        votes.append((row, bundle))
    vote_scores = [bundle.sentiment_score for _, bundle in votes]
    agreement, disagreement = source_agreement_from_scores(vote_scores)

    if available == 0:
        sentiment: float | None = None
        social_heat = 0.0
        mention_velocity = 0.0
        organic_score = 0.0
        manipulation_risk = 0.0
        c_raw = 0.0
    elif scope == "market_wide":
        sentiment = 0.0
        social_heat = 0.0
        mention_velocity = 0.0
        organic_score = 0.0
        manipulation_risk = 0.0
        c_raw = 0.0
    elif use_union:
        assert union_score is not None
        sentiment = _clip(union_score.sentiment_score, -100.0, 100.0)
        social_heat = union_score.social_heat
        mention_velocity = union_score.mention_velocity
        c_raw = union_score.confidence
        if union_assessment is not None:
            organic_score = union_assessment.organic_score
            manipulation_risk = union_assessment.manipulation_risk
        else:
            organic_score = _fallback_activity(evidence, assess_map, "organic")
            manipulation_risk = _fallback_activity(evidence, assess_map, "risk")
    else:
        sentiment = _fallback_tone(votes, assess_map)
        social_heat = _fallback_heat(evidence, score_map)
        mention_velocity = _fallback_velocity(evidence, score_map)
        organic_score = _fallback_activity(evidence, assess_map, "organic")
        manipulation_risk = _fallback_activity(evidence, assess_map, "risk")
        c_raw = _fallback_confidence(evidence, score_map)

    coverage = (available / p_reg) if p_reg else 0.0
    confidence = _clip(c_raw * coverage, 0.0, 100.0)
    classification = _asset_classification(
        available=available,
        scope=scope,
        n_eff=n_eff,
        union_score=union_score if use_union else None,
        s_agg=0.0 if sentiment is None else sentiment,
        vote_scores=vote_scores,
    )
    anomalies = _anomalies(
        evidence=evidence,
        assess_map=assess_map,
        union_assessment=union_assessment if n_a >= 1 else None,
        n_w=n_w,
        vote_count=len(vote_scores),
        agreement=agreement,
        disagreement=disagreement,
    )
    return AggregateResult(
        scope=scope,
        classification=classification,
        confidence=confidence,
        social_heat=social_heat,
        mention_velocity=mention_velocity,
        organic_score=organic_score,
        manipulation_risk=manipulation_risk,
        source_agreement=agreement,
        source_disagreement=disagreement,
        mention_count=n_a,
        sentiment_score=sentiment,
        sources=rows,
        anomalies=anomalies,
    )


def _asset_classification(
    *,
    available: int,
    scope: Scope,
    n_eff: int,
    union_score: ScoreBundle | None,
    s_agg: float,
    vote_scores: Sequence[float],
) -> Classification:
    if available == 0:
        return "insufficient_data"
    if scope == "market_wide":
        return "insufficient_data"
    if n_eff < N_MIN:
        return "insufficient_data"
    if union_score is not None:
        return union_score.classification
    return _classify_from_agg(s_agg, vote_scores)


def _fallback_tone(
    votes: Sequence[tuple[SourceBundle, ScoreBundle]],
    assess_map: Mapping[ProviderId, ManipulationAssessment],
) -> float:
    pairs: list[tuple[float, float]] = []
    for row, bundle in votes:
        if row.mention_count <= 0:
            continue
        assessment = assess_map.get(row.provider)
        organic = 0.0 if assessment is None else assessment.organic_score
        weight = float(row.mention_count) * (organic / 100.0)
        pairs.append((weight, bundle.sentiment_score))
    return _clip(_weighted_mean(pairs), -100.0, 100.0)


def _fallback_heat(
    evidence: Sequence[SourceBundle],
    score_map: Mapping[ProviderId, ScoreBundle],
) -> float:
    pairs: list[tuple[float, float]] = []
    for row in evidence:
        if row.mention_count <= 0:
            continue
        bundle = score_map.get(row.provider)
        if bundle is None:
            continue
        pairs.append((float(row.mention_count), bundle.social_heat))
    return _weighted_mean(pairs)


def _fallback_velocity(
    evidence: Sequence[SourceBundle],
    score_map: Mapping[ProviderId, ScoreBundle],
) -> float:
    pairs: list[tuple[float, float]] = []
    for row in evidence:
        if row.mention_count <= 0:
            continue
        bundle = score_map.get(row.provider)
        if bundle is None:
            continue
        pairs.append((float(row.mention_count), bundle.mention_velocity))
    return _weighted_mean(pairs)


def _fallback_activity(
    evidence: Sequence[SourceBundle],
    assess_map: Mapping[ProviderId, ManipulationAssessment],
    kind: str,
) -> float:
    pairs: list[tuple[float, float]] = []
    for row in evidence:
        if row.mention_count <= 0:
            continue
        assessment = assess_map.get(row.provider)
        if assessment is None:
            continue
        value = assessment.organic_score if kind == "organic" else assessment.manipulation_risk
        pairs.append((float(row.mention_count), value))
    return _weighted_mean(pairs)


def _fallback_confidence(
    evidence: Sequence[SourceBundle],
    score_map: Mapping[ProviderId, ScoreBundle],
) -> float:
    pairs: list[tuple[float, float]] = []
    for row in evidence:
        if row.mention_count <= 0:
            continue
        bundle = score_map.get(row.provider)
        if bundle is None:
            continue
        pairs.append((float(row.mention_count), bundle.confidence))
    return _weighted_mean(pairs)


def _anomalies(
    *,
    evidence: Sequence[SourceBundle],
    assess_map: Mapping[ProviderId, ManipulationAssessment],
    union_assessment: ManipulationAssessment | None,
    n_w: int,
    vote_count: int,
    agreement: float,
    disagreement: float,
) -> tuple[AnomalyRecord, ...]:
    seen: set[tuple[str, ProviderId | None]] = set()
    records: list[AnomalyRecord] = []

    def add(kind: str, provider: ProviderId | None, score: float, detail: str) -> None:
        key = (kind, provider)
        if key in seen:
            return
        seen.add(key)
        _assert_no_trade_language(kind, detail)
        records.append(
            AnomalyRecord(type=kind, provider=provider, score=score, detail=detail),
        )

    if union_assessment is not None:
        if union_assessment.anomaly_score >= THETA_FLAG:
            add("anomaly", None, union_assessment.anomaly_score, "anomaly_score")
        if union_assessment.manipulation_risk >= THETA_FLAG:
            add(
                "manipulation_risk",
                None,
                union_assessment.manipulation_risk,
                "manipulation_risk",
            )
    for row in evidence:
        assessment = assess_map.get(row.provider)
        if assessment is None:
            continue
        if assessment.anomaly_score >= THETA_FLAG:
            add("anomaly", row.provider, assessment.anomaly_score, "anomaly_score")
        if assessment.manipulation_risk >= THETA_FLAG:
            add(
                "manipulation_risk",
                row.provider,
                assessment.manipulation_risk,
                "manipulation_risk",
            )
    if vote_count >= 2 and agreement < THETA_FLAG:
        add("source_disagreement", None, disagreement, "source_disagreement")
    if n_w >= 1:
        add("market_wide_leftovers", None, float(n_w), "market_wide_leftovers")
    return tuple(records)
