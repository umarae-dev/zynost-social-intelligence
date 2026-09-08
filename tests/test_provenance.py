"""Provenance diary (docs/14; FR-PROV-02–03, CCH-13, CCH-17)."""

from datetime import UTC, datetime

from zynost_social.models import (
    AggregateResult,
    MatchBatch,
    ProviderFetchOutcome,
    SocialMention,
    SourceBundle,
)
from zynost_social.provenance import build_provenance

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


def _aggregate(sources: tuple[SourceBundle, ...], mention_count: int = 0) -> AggregateResult:
    available = sum(1 for row in sources if row.status == "available")
    return AggregateResult(
        scope="asset_specific",
        classification="insufficient_data",
        confidence=0.0 if available == 0 else 25.0,
        social_heat=0.0,
        mention_velocity=0.0,
        organic_score=0.0,
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


def test_all_unavailable_has_empty_available_and_null_filter_impact() -> None:
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(_all_down()),
        served_from_cache=False,
        observed_at=T0,
        duplicate_content_ratio=0.0,
    )
    assert record.providers_available == ()
    assert record.providers_unavailable == ("x", "reddit", "telegram", "discord")
    assert record.observation_count == 0
    assert record.unique_authors == 0
    assert record.duplicate_content_ratio is None
    assert record.freshness_seconds is None
    assert record.asset_match_score == 0.0
    assert record.served_from_cache is False


def test_quiet_available_is_not_unavailable() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=12.0),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(sources),
        served_from_cache=False,
        observed_at=T0,
    )
    assert record.providers_available == ("x",)
    assert "x" not in record.providers_unavailable
    assert record.observation_count == 0
    assert record.duplicate_content_ratio is None
    assert record.freshness_seconds == 12.0


def test_freshness_is_max_of_available_non_null_lags() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=40.0),
        SourceBundle(provider="reddit", status="available", freshness_seconds=None),
        SourceBundle(provider="telegram", status="available", freshness_seconds=90.0),
        SourceBundle(provider="discord", status="unavailable", error_class="timeout"),
    )
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(sources),
        served_from_cache=False,
        observed_at=T0,
    )
    assert record.freshness_seconds == 90.0


def test_unknown_freshness_on_every_available_row_is_null_not_perfect() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=None),
        SourceBundle(provider="reddit", status="available", freshness_seconds=None),
        SourceBundle(provider="telegram", status="unavailable", error_class="timeout"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(sources),
        served_from_cache=False,
        observed_at=T0,
    )
    assert record.freshness_seconds is None


def test_unavailable_lags_do_not_set_tau() -> None:
    sources = (
        SourceBundle(provider="x", status="unavailable", error_class="timeout"),
        SourceBundle(
            provider="reddit",
            status="unavailable",
            freshness_seconds=3.0,
            error_class="timeout",
        ),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(sources),
        served_from_cache=False,
        observed_at=T0,
    )
    assert record.freshness_seconds is None


def test_observation_count_and_unique_authors_come_from_asset_partition() -> None:
    mentions = (
        _mention(ext="1", author="a"),
        _mention(ext="2", author="a"),
        _mention(ext="3", author="b"),
    )
    sources = (
        SourceBundle(provider="x", status="available", mention_count=2, unique_authors=2),
        SourceBundle(provider="reddit", status="available", mention_count=1, unique_authors=1),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    record = build_provenance(
        (),
        MatchBatch(asset_mentions=mentions, asset_match_score=0.8),
        _aggregate(sources, mention_count=3),
        served_from_cache=False,
        observed_at=T0,
        duplicate_content_ratio=0.25,
    )
    assert record.observation_count == 3
    assert record.unique_authors == 2
    assert record.duplicate_content_ratio == 0.25
    assert record.asset_match_score == 0.8


def test_empty_asset_batch_nulls_duplicate_ratio_even_if_caller_passes_zero() -> None:
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(_all_down()),
        served_from_cache=False,
        observed_at=T0,
        duplicate_content_ratio=0.0,
    )
    assert record.duplicate_content_ratio is None


def test_cache_hit_flag_does_not_recompute_tau_from_read_time() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=55.0),
        SourceBundle(provider="reddit", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    later = datetime(2026, 9, 8, 12, 5, tzinfo=UTC)
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(sources),
        served_from_cache=True,
        observed_at=later,
    )
    assert record.served_from_cache is True
    assert record.freshness_seconds == 55.0


def test_redis_miss_is_not_a_provider_outcome() -> None:
    sources = (
        SourceBundle(provider="x", status="available"),
        SourceBundle(provider="reddit", status="available"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(sources),
        served_from_cache=False,
        observed_at=T0,
    )
    assert "redis" not in record.providers_available
    assert "redis" not in record.providers_unavailable
    assert record.served_from_cache is False


def test_does_not_invent_unregistered_providers_as_unavailable() -> None:
    sources = (SourceBundle(provider="telegram", status="available", freshness_seconds=1.0),)
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(sources),
        served_from_cache=False,
        observed_at=T0,
    )
    assert record.providers_available == ("telegram",)
    assert record.providers_unavailable == ()


def test_sources_win_over_stale_outcomes() -> None:
    sources = (
        SourceBundle(provider="x", status="available", freshness_seconds=8.0),
        SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
        SourceBundle(provider="telegram", status="unavailable", error_class="not_configured"),
        SourceBundle(provider="discord", status="unavailable", error_class="not_configured"),
    )
    outcomes = (
        ProviderFetchOutcome(provider="x", status="unavailable", error_class="timeout"),
        ProviderFetchOutcome(provider="reddit", status="available"),
    )
    record = build_provenance(
        outcomes,
        MatchBatch(),
        _aggregate(sources),
        served_from_cache=False,
        observed_at=T0,
    )
    assert record.providers_available == ("x",)
    assert record.providers_unavailable == ("reddit", "telegram", "discord")
    assert record.freshness_seconds == 8.0


def test_outcomes_used_only_when_aggregate_kept_no_source_rows() -> None:
    outcomes = (
        ProviderFetchOutcome(
            provider="x",
            status="available",
            freshness_seconds=4.0,
        ),
        ProviderFetchOutcome(provider="reddit", status="unavailable", error_class="unauthorized"),
    )
    empty = AggregateResult(
        scope="asset_specific",
        classification="insufficient_data",
        confidence=0.0,
        social_heat=0.0,
        mention_velocity=0.0,
        organic_score=0.0,
        manipulation_risk=0.0,
        source_agreement=0.0,
        mention_count=0,
        sources=(),
    )
    record = build_provenance(
        outcomes,
        MatchBatch(),
        empty,
        served_from_cache=False,
        observed_at=T0,
    )
    assert record.providers_available == ("x",)
    assert record.providers_unavailable == ("reddit",)
    assert record.freshness_seconds == 4.0


def test_diary_has_no_secrets_or_trade_language() -> None:
    record = build_provenance(
        (),
        MatchBatch(),
        _aggregate(_all_down()),
        served_from_cache=False,
        observed_at=T0,
    )
    blob = repr(record)
    assert "sk-" not in blob
    assert "Bearer" not in blob
    for token in ("BUY", "SELL", "LONG", "SHORT"):
        assert token not in blob
