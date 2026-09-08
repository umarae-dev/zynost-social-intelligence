"""Public orchestrator (docs/14). Maps existing stages; does not recompute formulas."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from zynost_social.aggregation import aggregate
from zynost_social.cache import (
    CacheContext,
    apply_raw_window,
    get_aggregate,
    get_lookaside,
    open_cache_context,
    records_to_baseline_bundle,
    set_compute,
    set_raw_many,
)
from zynost_social.manipulation import assess
from zynost_social.matching import match_mentions
from zynost_social.models import (
    AggregateResult,
    AnomalyRecord,
    AssetIdentity,
    BaselineBundle,
    Classification,
    DataQuality,
    ErrorClass,
    Horizon,
    ManipulationAssessment,
    MatchBatch,
    ProvenanceRecord,
    ProviderFetchOutcome,
    ProviderId,
    Scope,
    ScoreBundle,
    SocialMention,
    SourceBundle,
)
from zynost_social.provenance import PROVIDER_ORDER, build_provenance
from zynost_social.providers.base import N_CONC, T_PROV_S, SocialProvider, collect_isolated
from zynost_social.providers.discord import DiscordProvider
from zynost_social.providers.reddit import RedditProvider
from zynost_social.providers.telegram import TelegramProvider
from zynost_social.providers.x import XProvider
from zynost_social.quality import compute_data_quality
from zynost_social.scoring import LOOKBACK_MINUTES, score

NAME: Final = "social_sentiment"
ROLE: Final = "context"
SOURCE_CLASS: Final = "multi_source_social_intelligence"
HORIZON_REQUEST_MAX: Final = 64
APPLIED_HORIZONS: Final[frozenset[str]] = frozenset(LOOKBACK_MINUTES)
REQUIRED_KEYS: Final[tuple[str, ...]] = (
    "name",
    "role",
    "status",
    "scope",
    "classification",
    "sentiment_score",
    "confidence",
    "social_heat",
    "mention_velocity",
    "organic_score",
    "source_agreement",
    "manipulation_risk",
    "metrics",
    "sources",
    "anomalies",
    "observed_at",
    "source_class",
    "data_quality",
)

_EMPTY_ASSESSMENT = ManipulationAssessment(
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


async def build_social_sentiment(
    asset: AssetIdentity,
    horizon: str = "swing",
    *,
    providers: Sequence[SocialProvider] | None = None,
    cache_ctx: CacheContext | None = None,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """Sole public async API (INT-01). Never raises because providers are down (FR-INT-05).

    ``providers``, ``cache_ctx``, and ``observed_at`` are test/orchestration hooks,
    not credentials (CFG-01).
    """
    requested = _cap_horizon(horizon)
    applied = _apply_horizon(horizon)
    since_minutes = LOOKBACK_MINUTES[applied]
    t0 = observed_at if observed_at is not None else datetime.now(UTC)
    ctx = cache_ctx if cache_ctx is not None else open_cache_context()
    cached = await get_aggregate(ctx, asset.asset_id, applied)
    served = _contract_from_cache(cached, requested=requested, applied=applied)
    if served is not None:
        return served

    registry = _registry(providers)
    raw_payloads, baseline_records = await get_lookaside(ctx, asset.asset_id)
    baselines = replace(records_to_baseline_bundle(baseline_records), observed_at=t0)
    outcomes = await _collect(
        registry,
        asset,
        since_minutes,
        t0,
        ctx,
        raw_payloads,
    )
    await _store_raw(ctx, asset.asset_id, since_minutes, t0, outcomes, raw_payloads)
    mentions: list[SocialMention] = []
    for outcome in outcomes:
        if outcome.status == "available":
            mentions.extend(outcome.mentions)
    match_batch = match_mentions(asset, mentions)
    scores, assessments, union_score, union_assessment = _score_stage(
        match_batch, applied, baselines
    )
    rows = _source_rows(outcomes, match_batch, scores, t0)
    result = aggregate(
        rows,
        match_batch,
        applied,
        scores=scores,
        assessments=assessments,
        union_score=union_score,
        union_assessment=union_assessment,
    )
    assessment = union_assessment if union_assessment is not None else _EMPTY_ASSESSMENT
    provenance = build_provenance(
        outcomes,
        match_batch,
        result,
        served_from_cache=False,
        observed_at=t0,
        duplicate_content_ratio=assessment.duplicate_content_ratio,
    )
    quality = compute_data_quality(provenance, match_batch, result, assessment)
    available = len(provenance.providers_available)
    extras = _metrics_extras(union_score, baselines, available)
    if available >= 1:
        await set_compute(
            ctx,
            asset.asset_id,
            applied,
            _envelope(result, provenance, extras, t0),
            _baseline_records(match_batch.asset_mentions, union_score, t0),
            available_providers=available,
        )
    return _contract(
        result,
        quality,
        extras,
        t0,
        requested=requested,
        applied=applied,
        available=available,
    )


def _cap_horizon(horizon: str) -> str:
    text = horizon if isinstance(horizon, str) else str(horizon)
    return text[:HORIZON_REQUEST_MAX]


def _apply_horizon(horizon: str) -> Horizon:
    if horizon in APPLIED_HORIZONS:
        return cast(Horizon, horizon)
    return "swing"


def _registry(providers: Sequence[SocialProvider] | None) -> tuple[SocialProvider, ...]:
    factories: dict[ProviderId, type[SocialProvider]] = {
        "x": XProvider,
        "reddit": RedditProvider,
        "telegram": TelegramProvider,
        "discord": DiscordProvider,
    }
    by_id: dict[ProviderId, SocialProvider] = {}
    if providers is not None:
        for provider in providers:
            by_id[provider.provider] = provider
    ordered: list[SocialProvider] = []
    for provider_id in PROVIDER_ORDER:
        existing = by_id.get(provider_id)
        ordered.append(existing if existing is not None else factories[provider_id]())
    return tuple(ordered)


async def _collect(
    registry: Sequence[SocialProvider],
    asset: AssetIdentity,
    since_minutes: int,
    t0: datetime,
    ctx: CacheContext,
    raw_payloads: Mapping[str, Mapping[str, object] | None],
) -> tuple[ProviderFetchOutcome, ...]:
    semaphore = asyncio.Semaphore(N_CONC)

    async def one(provider: SocialProvider) -> ProviderFetchOutcome:
        hit = apply_raw_window(
            ctx,
            raw_payloads.get(provider.provider),
            provider=provider.provider,
            since_minutes=since_minutes,
        )
        if hit is not None:
            lag = max(0.0, (t0 - hit.observed_at).total_seconds())
            return ProviderFetchOutcome(
                provider=hit.provider,
                status="available",
                mentions=hit.mentions,
                freshness_seconds=lag,
            )
        async with semaphore:
            return await collect_isolated(provider, asset, since_minutes, timeout_s=T_PROV_S)

    gathered = await asyncio.gather(*[one(provider) for provider in registry])
    by_id = {outcome.provider: outcome for outcome in gathered}
    return tuple(
        by_id.get(provider_id)
        or ProviderFetchOutcome(
            provider=provider_id, status="unavailable", error_class="not_configured"
        )
        for provider_id in PROVIDER_ORDER
    )


async def _store_raw(
    ctx: CacheContext,
    asset_id: str,
    since_minutes: int,
    t0: datetime,
    outcomes: Sequence[ProviderFetchOutcome],
    raw_payloads: Mapping[str, Mapping[str, object] | None],
) -> None:
    envelopes: list[tuple[str, int, datetime, Sequence[SocialMention]]] = []
    for outcome in outcomes:
        if outcome.status != "available":
            continue
        cached = apply_raw_window(
            ctx,
            raw_payloads.get(outcome.provider),
            provider=outcome.provider,
            since_minutes=since_minutes,
        )
        if cached is not None:
            continue
        envelopes.append((outcome.provider, since_minutes, t0, outcome.mentions))
    await set_raw_many(ctx, asset_id, envelopes)


def _score_stage(
    match_batch: MatchBatch,
    applied: Horizon,
    baselines: BaselineBundle,
) -> tuple[
    dict[ProviderId, ScoreBundle],
    dict[ProviderId, ManipulationAssessment],
    ScoreBundle | None,
    ManipulationAssessment | None,
]:
    scores: dict[ProviderId, ScoreBundle] = {}
    assessments: dict[ProviderId, ManipulationAssessment] = {}
    by_provider: dict[ProviderId, list[SocialMention]] = {pid: [] for pid in PROVIDER_ORDER}
    for mention in match_batch.asset_mentions:
        by_provider[mention.provider].append(mention)
    for provider_id, group in by_provider.items():
        if not group:
            continue
        scores[provider_id] = score(group, applied, baselines)
        assessments[provider_id] = assess(group)
    union_score: ScoreBundle | None = None
    union_assessment: ManipulationAssessment | None = None
    if match_batch.asset_mentions:
        union_score = score(match_batch.asset_mentions, applied, baselines)
        union_assessment = assess(match_batch.asset_mentions)
    return scores, assessments, union_score, union_assessment


def _source_rows(
    outcomes: Sequence[ProviderFetchOutcome],
    match_batch: MatchBatch,
    scores: Mapping[ProviderId, ScoreBundle],
    t0: datetime,
) -> tuple[SourceBundle, ...]:
    asset_by_provider: dict[ProviderId, list[SocialMention]] = {pid: [] for pid in PROVIDER_ORDER}
    for mention in match_batch.asset_mentions:
        asset_by_provider[mention.provider].append(mention)
    rows: list[SourceBundle] = []
    for outcome in outcomes:
        if outcome.status == "unavailable":
            rows.append(
                SourceBundle(
                    provider=outcome.provider,
                    status="unavailable",
                    error_class=outcome.error_class,
                )
            )
            continue
        group = asset_by_provider[outcome.provider]
        bundle = scores.get(outcome.provider)
        freshness = outcome.freshness_seconds
        if outcome.mentions:
            newest = max(mention.created_at for mention in outcome.mentions)
            freshness = max(0.0, (t0 - newest).total_seconds())
        rows.append(
            SourceBundle(
                provider=outcome.provider,
                status="available",
                mention_count=len(group),
                unique_authors=len({mention.author_id_hash for mention in group}),
                sentiment_score=None if bundle is None else bundle.sentiment_score,
                freshness_seconds=freshness,
            )
        )
    return tuple(rows)


def _metrics_extras(
    union_score: ScoreBundle | None,
    baselines: BaselineBundle,
    available: int,
) -> dict[str, object]:
    if available == 0:
        return {}
    extras: dict[str, object] = {}
    if union_score is None:
        extras["mention_count"] = 0
        extras["unique_authors"] = 0
        extras["source_disagreement"] = 0.0
        return extras
    extras["mention_count"] = union_score.mention_count
    extras["unique_authors"] = union_score.unique_authors
    extras["social_acceleration"] = union_score.social_acceleration
    extras["acceleration_state"] = union_score.acceleration_state
    extras["engagement_weighted_sentiment"] = union_score.engagement_weighted_sentiment
    extras["positive_count"] = union_score.positive_count
    extras["neutral_count"] = union_score.neutral_count
    extras["negative_count"] = union_score.negative_count
    extras["positive_ratio"] = union_score.positive_ratio
    extras["neutral_ratio"] = union_score.neutral_ratio
    extras["negative_ratio"] = union_score.negative_ratio
    extras["bullish_phrase_intensity"] = union_score.bullish_phrase_intensity
    extras["bearish_phrase_intensity"] = union_score.bearish_phrase_intensity
    extras["fear_intensity"] = union_score.fear_intensity
    extras["greed_excitement_intensity"] = union_score.greed_excitement_intensity
    extras["uncertainty_intensity"] = union_score.uncertainty_intensity
    extras["euphoria_intensity"] = union_score.euphoria_intensity
    extras["mentions_1h"] = union_score.mentions_1h
    if union_score.mentions_1h_previous is not None:
        extras["mentions_prev_1h"] = union_score.mentions_1h_previous
    if baselines.prior_mentions_6h is not None:
        extras["delta_6h"] = union_score.mentions_6h_change
    if baselines.prior_mentions_24h is not None:
        extras["delta_24h"] = union_score.mentions_24h_change
    extras["engagement_velocity"] = union_score.engagement_velocity
    extras["activity_coverage"] = union_score.activity_coverage
    extras["source_disagreement"] = union_score.source_disagreement
    if union_score.sentiment_change is not None:
        extras["sentiment_change"] = union_score.sentiment_change
    if union_score.sentiment_change_hours is not None:
        extras["sentiment_change_hours"] = union_score.sentiment_change_hours
    return extras


def _baseline_records(
    asset_mentions: Sequence[SocialMention],
    union_score: ScoreBundle | None,
    t0: datetime,
) -> dict[str, dict[str, object]]:
    iso = _iso(t0)
    sentiment = None if union_score is None else union_score.sentiment_score
    counts = {
        "1h": union_score.mentions_1h if union_score is not None else 0,
        "6h": union_score.mentions_6h if union_score is not None else 0,
        "24h": union_score.mentions_24h if union_score is not None else 0,
    }
    return {
        window: {
            "window": window,
            "observed_at": iso,
            "mention_count": counts[window],
            "engagement_sum": _engagement_in_hours(asset_mentions, t0, hours),
            "sentiment_score": sentiment if window == "1h" else None,
        }
        for window, hours in (("1h", 1.0), ("6h", 6.0), ("24h", 24.0))
    }


def _engagement_in_hours(mentions: Sequence[SocialMention], t0: datetime, hours: float) -> float:
    cutoff = t0 - timedelta(hours=hours)
    return sum(mention.engagement for mention in mentions if mention.created_at >= cutoff)


def _envelope(
    result: AggregateResult,
    provenance: ProvenanceRecord,
    extras: Mapping[str, object],
    t0: datetime,
) -> dict[str, object]:
    return {
        "observed_at": _iso(t0),
        "asset_match_score": provenance.asset_match_score,
        "duplicate_content_ratio": provenance.duplicate_content_ratio,
        "observation_count": provenance.observation_count,
        "unique_authors": provenance.unique_authors,
        "metrics_extra": dict(extras),
        "scope": result.scope,
        "classification": result.classification,
        "confidence": result.confidence,
        "social_heat": result.social_heat,
        "mention_velocity": result.mention_velocity,
        "organic_score": result.organic_score,
        "manipulation_risk": result.manipulation_risk,
        "source_agreement": result.source_agreement,
        "source_disagreement": result.source_disagreement,
        "mention_count": result.mention_count,
        "sentiment_score": result.sentiment_score,
        "sources": [_source_payload(row) for row in result.sources],
        "anomalies": [_anomaly_payload(item) for item in result.anomalies],
    }


def _contract_from_cache(
    payload: Mapping[str, object] | None,
    *,
    requested: str,
    applied: Horizon,
) -> dict[str, object] | None:
    if payload is None or "sources" not in payload:
        return None
    observed_raw = payload.get("observed_at")
    if not isinstance(observed_raw, str):
        return None
    try:
        t0 = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    sources_raw = payload.get("sources")
    if not isinstance(sources_raw, list):
        return None
    rows = tuple(_source_from_payload(item) for item in sources_raw)
    if any(row is None for row in rows):
        return None
    source_rows = tuple(row for row in rows if row is not None)
    anomalies_raw = payload.get("anomalies")
    anomalies: tuple[AnomalyRecord, ...] = ()
    if isinstance(anomalies_raw, list):
        parsed = tuple(_anomaly_from_payload(item) for item in anomalies_raw)
        if any(item is None for item in parsed):
            return None
        anomalies = tuple(item for item in parsed if item is not None)
    result = AggregateResult(
        scope=_scope(payload.get("scope")),
        classification=_classification(payload.get("classification")),
        confidence=_finite_or(payload.get("confidence"), 0.0),
        social_heat=_finite_or(payload.get("social_heat"), 0.0),
        mention_velocity=_finite_or(payload.get("mention_velocity"), 0.0),
        organic_score=_finite_or(payload.get("organic_score"), 0.0),
        manipulation_risk=_finite_or(payload.get("manipulation_risk"), 0.0),
        source_agreement=_finite_or(payload.get("source_agreement"), 0.0),
        source_disagreement=_finite_or(payload.get("source_disagreement"), 0.0),
        mention_count=_int_or(payload.get("mention_count"), 0),
        sentiment_score=_optional_float(payload.get("sentiment_score")),
        sources=source_rows,
        anomalies=anomalies,
    )
    n_a = _int_or(payload.get("observation_count"), result.mention_count)
    match_batch = MatchBatch(asset_match_score=_finite_or(payload.get("asset_match_score"), 0.0))
    dup = payload.get("duplicate_content_ratio")
    dup_ratio = (
        float(dup) if isinstance(dup, int | float) and math.isfinite(float(dup)) else None
    )
    provenance = replace(
        build_provenance(
            (),
            match_batch,
            result,
            served_from_cache=True,
            observed_at=t0,
            duplicate_content_ratio=dup_ratio,
        ),
        observation_count=n_a,
        unique_authors=_int_or(payload.get("unique_authors"), 0),
        duplicate_content_ratio=None if n_a == 0 else dup_ratio,
        asset_match_score=_finite_or(payload.get("asset_match_score"), 0.0),
    )
    quality = compute_data_quality(provenance, match_batch, result, _EMPTY_ASSESSMENT)
    extras_raw = payload.get("metrics_extra")
    extras = dict(extras_raw) if isinstance(extras_raw, dict) else {}
    available = len(provenance.providers_available)
    return _contract(
        result,
        quality,
        extras,
        t0,
        requested=requested,
        applied=applied,
        available=available,
    )


def _contract(
    result: AggregateResult,
    quality: DataQuality,
    extras: Mapping[str, object],
    t0: datetime,
    *,
    requested: str,
    applied: Horizon,
    available: int,
) -> dict[str, object]:
    down = available == 0
    metrics: dict[str, object] = {
        "horizon_requested": requested,
        "horizon_applied": applied,
    }
    if not down:
        metrics.update(extras)
        if "mention_count" not in metrics:
            metrics["mention_count"] = result.mention_count
        if "unique_authors" not in metrics:
            metrics["unique_authors"] = quality.unique_authors
        if "source_disagreement" not in metrics:
            metrics["source_disagreement"] = result.source_disagreement
    payload: dict[str, object] = {
        "name": NAME,
        "role": ROLE,
        "status": "unavailable" if down else "available",
        "scope": "asset_specific" if down else result.scope,
        "classification": result.classification,
        "sentiment_score": None if down else result.sentiment_score,
        "confidence": 0.0 if down else result.confidence,
        "social_heat": None if down else result.social_heat,
        "mention_velocity": None if down else result.mention_velocity,
        "organic_score": None if down else result.organic_score,
        "source_agreement": None if down else result.source_agreement,
        "manipulation_risk": None if down else result.manipulation_risk,
        "metrics": metrics,
        "sources": _sources_public(result.sources),
        "anomalies": [] if down else [_anomaly_payload(item) for item in result.anomalies],
        "observed_at": _iso(t0),
        "source_class": SOURCE_CLASS,
        "data_quality": asdict(quality),
    }
    return cast(dict[str, object], _json_safe(payload))


def _sources_public(rows: Sequence[SourceBundle]) -> dict[str, object]:
    by_id = {row.provider: row for row in rows}
    out: dict[str, object] = {}
    for provider_id in PROVIDER_ORDER:
        row = by_id.get(provider_id)
        if row is None:
            out[provider_id] = {
                "status": "unavailable",
                "mention_count": 0,
                "unique_authors": 0,
                "sentiment_score": None,
                "freshness_seconds": None,
                "error_class": "not_configured",
            }
            continue
        payload = _source_payload(row)
        payload.pop("provider", None)
        out[provider_id] = payload
    return out


def _source_payload(row: SourceBundle) -> dict[str, object]:
    return {
        "provider": row.provider,
        "status": row.status,
        "mention_count": row.mention_count,
        "unique_authors": row.unique_authors,
        "sentiment_score": row.sentiment_score,
        "freshness_seconds": row.freshness_seconds,
        "error_class": row.error_class,
    }


def _source_from_payload(item: object) -> SourceBundle | None:
    if not isinstance(item, dict):
        return None
    provider = item.get("provider")
    status = item.get("status")
    if provider not in PROVIDER_ORDER or status not in {"available", "unavailable"}:
        return None
    error = item.get("error_class")
    error_class: ErrorClass | None = error if error in {
        "not_configured",
        "unauthorized",
        "timeout",
        "malformed",
        "rate_limited",
    } else None
    try:
        return SourceBundle(
            provider=cast(ProviderId, provider),
            status="available" if status == "available" else "unavailable",
            mention_count=_int_or(item.get("mention_count"), 0),
            unique_authors=_int_or(item.get("unique_authors"), 0),
            sentiment_score=_optional_float(item.get("sentiment_score")),
            freshness_seconds=_optional_float(item.get("freshness_seconds")),
            error_class=error_class,
        )
    except ValueError:
        return None


def _anomaly_payload(item: AnomalyRecord) -> dict[str, object]:
    return {
        "type": item.type,
        "provider": item.provider,
        "score": item.score,
        "detail": item.detail,
    }


def _anomaly_from_payload(item: object) -> AnomalyRecord | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    detail = item.get("detail")
    if not isinstance(kind, str) or not isinstance(detail, str):
        return None
    provider = item.get("provider")
    if provider is not None and provider not in PROVIDER_ORDER:
        return None
    try:
        return AnomalyRecord(
            type=kind,
            provider=cast(ProviderId | None, provider),
            score=_finite_or(item.get("score"), 0.0),
            detail=detail,
        )
    except (TypeError, ValueError):
        return None


def _scope(value: object) -> Scope:
    if value == "market_wide":
        return "market_wide"
    return "asset_specific"


def _classification(value: object) -> Classification:
    allowed = {
        "strong_positive",
        "positive",
        "neutral",
        "mixed",
        "negative",
        "strong_negative",
        "insufficient_data",
    }
    if value in allowed:
        return cast(Classification, value)
    return "insufficient_data"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _finite_or(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    number = float(value)
    if not math.isfinite(number):
        return default
    return number


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _int_or(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return None
