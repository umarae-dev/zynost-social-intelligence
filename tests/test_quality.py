"""data_quality scales (docs/14; QLY-01–06, TST-I-06, TST-S-12)."""

from datetime import UTC, datetime

from zynost_social.models import (
    AggregateResult,
    ManipulationAssessment,
    MatchBatch,
    SocialMention,
    SourceBundle,
)
from zynost_social.provenance import build_provenance
from zynost_social.quality import P_REG, T_SAT_SECONDS, compute_data_quality

T0 = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _mention(*, ext: str, author: str, confidence: float = 0.8) -> SocialMention:
    return SocialMention(
        provider="x",
        external_id=ext,
        asset_id="btc",
        text="bitcoin discussion",
        author_id_hash=author,
        created_at=T0,
        engagement=1.0,
        matched_terms=("bitcoin",),
        match_confidence=confidence,
        match_rationale="official name in text",
    )


def _aggregate(
    sources: tuple[SourceBundle, ...],
    *,
    mention_count: int = 0,
    organic_score: float = 0.0,
    confidence: float = 25.0,
) -> AggregateResult:
    available = sum(1 for row in sources if row.status == "available")
    return AggregateResult(
        scope="asset_specific",
        classification="insufficient_data",
        confidence=0.0 if available == 0 else confidence,
        social_heat=0.0,
        mention_velocity=0.0,
        organic_score=organic_score,
        manipulation_risk=0.0,
        source_agreement=0.0,
        mention_count=mention_count,
        sentiment_score=None if available == 0 else 0.0,
        sources=sources,
    )


def _all_down() -> tuple[SourceBundle, ...]:
    return (
        SourceBundle(provider="x", status="unavailable", error_class="timeout"),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )


def _empty_assessment(*, organic: float = 100.0) -> ManipulationAssessment:
    return ManipulationAssessment(
        duplicate_content_ratio=0.0,
        unique_author_ratio=0.0,
        engagement_concentration=0.0,
        author_concentration=0.0,
        suspicious_burst_score=0.0,
        spam_bot_suspicion=0.0,
        manipulation_risk=0.0,
        organic_score=organic,
        anomaly_score=0.0,
    )


def _quality(
    sources: tuple[SourceBundle, ...],
    match_batch: MatchBatch,
    *,
    served_from_cache: bool = False,
    duplicate_content_ratio: float | None = None,
    organic_score: float = 0.0,
    confidence: float = 25.0,
    assessment: ManipulationAssessment | None = None,
):
    aggregate = _aggregate(
        sources,
        mention_count=len(match_batch.asset_mentions),
        organic_score=organic_score,
        confidence=confidence,
    )
    provenance = build_provenance(
        (),
        match_batch,
        aggregate,
        served_from_cache=served_from_cache,
        observed_at=T0,
        duplicate_content_ratio=duplicate_content_ratio,
    )
    quality = compute_data_quality(
        provenance,
        match_batch,
        aggregate,
        assessment if assessment is not None else _empty_assessment(),
    )
    return quality, aggregate


def test_all_unavailable_nulls_freshness_and_organic() -> None:
    quality, _ = _quality(_all_down(), MatchBatch())
    assert P_REG == 4
    assert quality.provider_coverage == 0.0
    assert quality.freshness_score is None
    assert quality.organic_data_ratio is None
    assert quality.asset_match_score == 0.0
    assert quality.providers_available == ()
    assert quality.providers_unavailable == ("x", "reddit", "telegram", "discord")
    assert quality.observation_count == 0
    assert quality.duplicate_content_ratio is None
    assert quality.freshness_seconds is None
    assert quality.served_from_cache is False


def test_quiet_available_does_not_claim_organic() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=12.0),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    quality, _ = _quality(
        sources,
        MatchBatch(),
        organic_score=100.0,
        assessment=_empty_assessment(organic=100.0),
    )
    assert quality.provider_coverage == 0.25
    assert quality.organic_data_ratio is None
    assert quality.asset_match_score == 0.0
    assert quality.freshness_score == 100.0 * (1.0 - 12.0 / T_SAT_SECONDS)
    assert quality.freshness_seconds == 12.0


def test_freshness_score_is_linear_in_tau() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=1800.0),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    quality, _ = _quality(sources, MatchBatch())
    assert quality.freshness_score == 50.0


def test_stale_tau_clips_freshness_to_zero() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=7200.0),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    quality, _ = _quality(sources, MatchBatch())
    assert quality.freshness_score == 0.0


def test_unknown_tau_is_null_freshness_not_perfect() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=None),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    quality, _ = _quality(sources, MatchBatch())
    assert quality.freshness_seconds is None
    assert quality.freshness_score is None


def test_organic_ratio_from_aggregate_when_asset_evidence_exists() -> None:
    mentions = (_mention(ext="1", author="a"), _mention(ext="2", author="b"))
    sources = (
        SourceBundle(provider="x", status="available", mention_count=2, unique_authors=2),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    quality, _ = _quality(
        sources,
        MatchBatch(asset_mentions=mentions, asset_match_score=0.8),
        duplicate_content_ratio=0.25,
        organic_score=71.0,
        assessment=_empty_assessment(organic=100.0),
    )
    assert quality.organic_data_ratio == 0.71
    assert quality.asset_match_score == 0.8
    assert quality.observation_count == 2
    assert quality.unique_authors == 2
    assert quality.duplicate_content_ratio == 0.25


def test_does_not_rewrite_aggregate_confidence() -> None:
    mentions = (_mention(ext="1", author="a"),)
    sources = (
        SourceBundle(provider="x", status="available", mention_count=1, unique_authors=1),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    quality, aggregate = _quality(
        sources,
        MatchBatch(asset_mentions=mentions, asset_match_score=0.8),
        organic_score=50.0,
        confidence=20.0,
    )
    assert aggregate.confidence == 20.0
    assert quality.provider_coverage == 0.25
    assert quality.provider_coverage * aggregate.confidence == 5.0


def test_does_not_shrink_p_reg_to_the_live_set() -> None:
    sources = (SourceBundle(provider="telegram", status="available", freshness_seconds=1.0),)
    quality, _ = _quality(sources, MatchBatch())
    assert quality.provider_coverage == 0.25
    assert quality.providers_unavailable == ()


def test_cache_hit_keeps_stored_tau() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=55.0),
        SourceBundle(provider="reddit", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    quality, _ = _quality(sources, MatchBatch(), served_from_cache=True)
    assert quality.served_from_cache is True
    assert quality.freshness_seconds == 55.0
    assert quality.freshness_score == 100.0 * (1.0 - 55.0 / T_SAT_SECONDS)


def test_market_wide_leftovers_do_not_get_organic_ratio() -> None:
    leftover = _mention(ext="mw", author="z")
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=10.0),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    quality, _ = _quality(
        sources,
        MatchBatch(market_wide=(leftover,), asset_match_score=0.0),
        organic_score=100.0,
        assessment=_empty_assessment(organic=100.0),
    )
    assert quality.observation_count == 0
    assert quality.organic_data_ratio is None
    assert quality.asset_match_score == 0.0


def test_no_trade_language() -> None:
    quality, _ = _quality(_all_down(), MatchBatch())
    blob = repr(quality)
    for token in ("BUY", "SELL", "LONG", "SHORT"):
        assert token not in blob
