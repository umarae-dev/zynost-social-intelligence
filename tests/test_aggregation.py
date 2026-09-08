"""Aggregation combine and scope (docs/11; TST-S-05/06/10/13, TST-P-09/10/14)."""

from datetime import UTC, datetime

from zynost_social.aggregation import aggregate
from zynost_social.models import (
    ManipulationAssessment,
    MatchBatch,
    ScoreBundle,
    SocialMention,
    SourceBundle,
)

T0 = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
TRADE_TOKENS = ("BUY", "SELL", "LONG", "SHORT")


def _mention(
    text: str,
    *,
    ext: str,
    provider: str = "x",
    author: str = "author-a",
) -> SocialMention:
    return SocialMention(
        provider=provider,  # type: ignore[arg-type]
        external_id=ext,
        asset_id="btc",
        text=text,
        author_id_hash=author,
        created_at=T0,
        engagement=1.0,
        matched_terms=("bitcoin",),
        match_confidence=0.8,
        match_rationale="official name in text",
    )


def _score(
    *,
    sentiment: float,
    classification: str,
    confidence: float = 80.0,
    heat: float = 40.0,
    velocity: float = 2.0,
    mention_count: int = 10,
) -> ScoreBundle:
    return ScoreBundle(
        sentiment_score=sentiment,
        classification=classification,  # type: ignore[arg-type]
        confidence=confidence,
        social_heat=heat,
        mention_velocity=velocity,
        social_acceleration=0.0,
        mention_count=mention_count,
        unique_authors=mention_count,
        horizon="swing",
    )


def _assess(
    *,
    organic: float = 80.0,
    risk: float = 0.2,
    anomaly: float = 0.1,
) -> ManipulationAssessment:
    return ManipulationAssessment(
        duplicate_content_ratio=0.0,
        unique_author_ratio=0.8,
        engagement_concentration=0.1,
        author_concentration=0.1,
        suspicious_burst_score=0.0,
        spam_bot_suspicion=0.0,
        manipulation_risk=risk,
        organic_score=organic,
        anomaly_score=anomaly,
    )


def _row(
    provider: str,
    *,
    status: str = "available",
    mention_count: int = 10,
    sentiment: float | None = 40.0,
    error_class: str | None = None,
) -> SourceBundle:
    return SourceBundle(
        provider=provider,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        mention_count=mention_count,
        unique_authors=mention_count,
        sentiment_score=sentiment,
        freshness_seconds=12.0 if status == "available" else None,
        error_class=error_class,  # type: ignore[arg-type]
    )


def _down(provider: str, error_class: str) -> SourceBundle:
    return _row(
        provider,
        status="unavailable",
        mention_count=0,
        sentiment=None,
        error_class=error_class,
    )


def _organic(*providers: str) -> dict[str, ManipulationAssessment]:
    return {provider: _assess(organic=100.0) for provider in providers}


def _registry(
    *,
    x: SourceBundle | None = None,
    reddit: SourceBundle | None = None,
    telegram: SourceBundle | None = None,
    discord: SourceBundle | None = None,
) -> tuple[SourceBundle, ...]:
    return (
        x or _row("x"),
        reddit or _row("reddit"),
        telegram or _row("telegram"),
        discord or _down("discord", "not_configured"),
    )


def _no_trade(result) -> None:
    blob = " ".join(
        [result.scope, result.classification]
        + [record.type + " " + record.detail for record in result.anomalies]
    )
    upper = blob.upper()
    for token in TRADE_TOKENS:
        assert token not in upper.split()


def test_all_unavailable_is_quiet_nulls_not_a_mix() -> None:
    rows = (
        _down("x", "timeout"),
        _down("reddit", "not_configured"),
        _down("telegram", "unauthorized"),
        _down("discord", "malformed"),
    )
    result = aggregate(rows, MatchBatch(), "swing")
    assert result.sentiment_score is None
    assert result.classification == "insufficient_data"
    assert result.confidence == 0.0
    assert result.source_agreement == 0.0
    assert result.mention_count == 0
    assert result.scope == "asset_specific"
    assert len(result.sources) == 4
    assert all(row.status == "unavailable" for row in result.sources)
    _no_trade(result)


def test_one_provider_shrinks_confidence_and_agreement_is_zero() -> None:
    rows = _registry(
        x=_down("x", "timeout"),
        reddit=_down("reddit", "not_configured"),
        telegram=_row("telegram", mention_count=12, sentiment=50.0),
    )
    scores = {"telegram": _score(sentiment=50.0, classification="positive", confidence=80.0)}
    assessments = {"telegram": _assess(organic=100.0)}
    mentions = tuple(
        _mention(f"bitcoin custody setup {i}", ext=str(i), provider="telegram")
        for i in range(6)
    )
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores=scores,
        assessments=assessments,
    )
    assert result.confidence == 20.0  # 80 * 1/4
    assert result.source_agreement == 0.0
    assert result.source_disagreement == 0.0
    assert result.sources[2].provider == "telegram"
    assert result.sources[0].error_class == "timeout"
    _no_trade(result)


def test_partial_mix_retains_failed_rows() -> None:
    rows = _registry(
        x=_row("x", mention_count=8, sentiment=40.0),
        reddit=_down("reddit", "rate_limited"),
    )
    scores = {
        "x": _score(sentiment=40.0, classification="positive", confidence=70.0),
        "telegram": _score(sentiment=42.0, classification="positive", confidence=70.0),
    }
    assessments = {"x": _assess(), "telegram": _assess()}
    mentions = tuple(_mention(f"bitcoin hash rate {i}", ext=str(i)) for i in range(6))
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores=scores,
        assessments=assessments,
    )
    assert [row.provider for row in result.sources] == ["x", "reddit", "telegram", "discord"]
    assert result.sources[1].error_class == "rate_limited"
    assert result.sources[3].error_class == "not_configured"
    assert result.confidence == 70.0 * 2 / 4
    assert result.source_agreement > 0.9


def test_quiet_available_is_not_unavailable() -> None:
    rows = _registry(
        x=_row("x", mention_count=0, sentiment=0.0),
        reddit=_row("reddit", mention_count=0, sentiment=0.0),
        telegram=_row("telegram", mention_count=0, sentiment=0.0),
        discord=_row("discord", mention_count=0, sentiment=0.0),
    )
    result = aggregate(rows, MatchBatch(), "swing")
    assert all(row.status == "available" for row in result.sources)
    assert result.classification == "insufficient_data"
    assert result.scope == "asset_specific"
    assert result.sentiment_score == 0.0
    assert result.confidence == 0.0
    assert result.mention_count == 0


def test_leftovers_do_not_fill_asset_tone() -> None:
    leftovers = tuple(_mention("crypto market is wild today", ext=str(i)) for i in range(8))
    rows = _registry()
    scores = {
        "x": _score(sentiment=90.0, classification="strong_positive", confidence=99.0),
        "reddit": _score(sentiment=90.0, classification="strong_positive", confidence=99.0),
        "telegram": _score(sentiment=90.0, classification="strong_positive", confidence=99.0),
    }
    result = aggregate(
        rows,
        MatchBatch(market_wide=leftovers),
        "swing",
        scores=scores,
        union_score=_score(sentiment=90.0, classification="strong_positive", confidence=99.0),
    )
    assert result.scope == "market_wide"
    assert result.classification == "insufficient_data"
    assert result.sentiment_score == 0.0
    leftover = [row for row in result.anomalies if row.type == "market_wide_leftovers"]
    assert len(leftover) == 1
    assert leftover[0].score == 8.0
    assert leftover[0].provider is None
    _no_trade(result)


def test_thin_asset_evidence_stays_insufficient_data() -> None:
    mentions = tuple(_mention("bitcoin looks strong", ext=str(i)) for i in range(4))
    rows = _registry(reddit=_row("reddit", mention_count=4, sentiment=80.0))
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores={"x": _score(sentiment=80.0, classification="strong_positive", confidence=90.0)},
        assessments={"x": _assess(organic=100.0)},
        union_score=_score(sentiment=80.0, classification="strong_positive", confidence=90.0),
    )
    assert result.classification == "insufficient_data"
    assert result.mention_count == 4


def test_union_clears_n_min_and_overrides_per_source_insufficient() -> None:
    mentions = tuple(_mention(f"bitcoin custody {i} is solid", ext=str(i)) for i in range(6))
    rows = _registry(
        x=_row("x", mention_count=3, sentiment=0.0),
        reddit=_row("reddit", mention_count=3, sentiment=0.0),
        telegram=_row("telegram", mention_count=0, sentiment=0.0),
    )
    per_source_thin = _score(
        sentiment=0.0,
        classification="insufficient_data",
        confidence=10.0,
        mention_count=3,
    )
    union = _score(
        sentiment=45.0,
        classification="positive",
        confidence=60.0,
        heat=55.0,
        velocity=3.0,
    )
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores={"x": per_source_thin, "reddit": per_source_thin},
        assessments={"x": _assess(), "reddit": _assess()},
        union_score=union,
        union_assessment=_assess(organic=90.0, risk=0.1),
    )
    assert result.classification == "positive"
    assert result.sentiment_score == 45.0
    assert result.social_heat == 55.0
    assert result.organic_score == 90.0
    assert result.source_agreement == 0.0  # both rows insufficient → no votes
    assert result.confidence == 60.0 * 3 / 4


def test_aligned_sources_agree_opposed_sources_disagree() -> None:
    mentions = tuple(_mention(f"bitcoin note {i}", ext=str(i)) for i in range(6))
    batch = MatchBatch(asset_mentions=mentions)
    aligned_rows = _registry()
    aligned = aggregate(
        aligned_rows,
        batch,
        "swing",
        scores={
            "x": _score(sentiment=60.0, classification="positive"),
            "reddit": _score(sentiment=62.0, classification="positive"),
            "telegram": _score(sentiment=61.0, classification="positive"),
        },
        assessments=_organic("x", "reddit", "telegram"),
    )
    opposed = aggregate(
        aligned_rows,
        batch,
        "swing",
        scores={
            "x": _score(sentiment=100.0, classification="strong_positive"),
            "reddit": _score(sentiment=-100.0, classification="strong_negative"),
            "telegram": _score(sentiment=100.0, classification="strong_positive"),
        },
        assessments=_organic("x", "reddit", "telegram"),
    )
    assert aligned.source_agreement > opposed.source_agreement
    assert aligned.source_agreement > 0.9
    assert any(row.type == "source_disagreement" for row in opposed.anomalies)
    assert opposed.classification == "mixed"


def test_unavailable_source_is_not_an_agreement_vote() -> None:
    mentions = tuple(_mention(f"bitcoin note {i}", ext=str(i)) for i in range(6))
    rows = _registry(
        reddit=_down("reddit", "timeout"),
        telegram=_down("telegram", "timeout"),
        discord=_down("discord", "not_configured"),
    )
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores={"x": _score(sentiment=40.0, classification="positive")},
        assessments={"x": _assess(organic=100.0)},
    )
    assert result.source_agreement == 0.0
    assert not any(row.type == "source_disagreement" for row in result.anomalies)


def test_organic_downweights_manipulated_source_tone() -> None:
    mentions = tuple(_mention(f"bitcoin note {i}", ext=str(i)) for i in range(6))
    rows = _registry(
        x=_row("x", mention_count=10, sentiment=100.0),
        reddit=_row("reddit", mention_count=10, sentiment=-100.0),
        telegram=_row("telegram", mention_count=0, sentiment=0.0),
        discord=_down("discord", "not_configured"),
    )
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores={
            "x": _score(sentiment=100.0, classification="strong_positive", heat=10.0),
            "reddit": _score(sentiment=-100.0, classification="strong_negative", heat=90.0),
        },
        assessments={
            "x": _assess(organic=100.0, risk=0.0),
            "reddit": _assess(organic=0.0, risk=1.0),
        },
    )
    assert result.sentiment_score == 100.0
    assert result.social_heat == 50.0  # activity still sees the spike


def test_anomaly_does_not_flip_classification_bullish() -> None:
    mentions = tuple(_mention(f"bitcoin dump narrative {i}", ext=str(i)) for i in range(6))
    rows = _registry()
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores={
            "x": _score(sentiment=-40.0, classification="negative", heat=90.0, velocity=8.0),
            "reddit": _score(sentiment=-38.0, classification="negative", heat=88.0, velocity=7.0),
            "telegram": _score(sentiment=-42.0, classification="negative", heat=85.0, velocity=7.5),
        },
        assessments={
            "x": _assess(organic=20.0, risk=0.8, anomaly=0.7),
            "reddit": _assess(organic=25.0, risk=0.6, anomaly=0.55),
            "telegram": _assess(organic=30.0, risk=0.4, anomaly=0.2),
        },
        union_score=_score(sentiment=-40.0, classification="negative", confidence=50.0, heat=90.0),
        union_assessment=_assess(organic=22.0, risk=0.75, anomaly=0.8),
    )
    assert result.classification == "negative"
    types = {row.type for row in result.anomalies}
    assert "anomaly" in types
    assert "manipulation_risk" in types
    _no_trade(result)


def test_mention_count_is_partition_not_source_sum() -> None:
    mentions = tuple(_mention(f"bitcoin note {i}", ext=str(i), author="same") for i in range(6))
    rows = _registry(
        x=_row("x", mention_count=6, sentiment=20.0),
        reddit=_row("reddit", mention_count=6, sentiment=20.0),
        telegram=_row("telegram", mention_count=6, sentiment=20.0),
    )
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores={
            "x": _score(sentiment=20.0, classification="positive"),
            "reddit": _score(sentiment=20.0, classification="positive"),
            "telegram": _score(sentiment=20.0, classification="positive"),
        },
        assessments=_organic("x", "reddit", "telegram"),
    )
    assert result.mention_count == 6


def test_aggregate_is_deterministic() -> None:
    mentions = tuple(_mention(f"bitcoin note {i}", ext=str(i)) for i in range(6))
    rows = _registry()
    kwargs = {
        "scores": {
            "x": _score(sentiment=30.0, classification="positive"),
            "reddit": _score(sentiment=28.0, classification="positive"),
            "telegram": _score(sentiment=32.0, classification="positive"),
        },
        "assessments": {
            "x": _assess(),
            "reddit": _assess(),
            "telegram": _assess(),
        },
    }
    batch = MatchBatch(asset_mentions=mentions)
    first = aggregate(rows, batch, "swing", **kwargs)
    second = aggregate(rows, batch, "swing", **kwargs)
    assert first == second


def test_empty_text_does_not_count_toward_n_eff() -> None:
    mentions = tuple(_mention("   ", ext=str(i)) for i in range(6))
    rows = _registry()
    result = aggregate(
        rows,
        MatchBatch(asset_mentions=mentions),
        "swing",
        scores={"x": _score(sentiment=80.0, classification="strong_positive")},
        assessments={"x": _assess(organic=100.0)},
    )
    assert result.classification == "insufficient_data"
    assert result.mention_count == 6
