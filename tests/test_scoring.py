"""Polar + momentum scoring (docs/08, 09, 15; TST-S-01–06, TST-S-14, TST-I-05)."""

from datetime import UTC, datetime, timedelta

from zynost_social.models import BaselineBundle, SocialMention
from zynost_social.scoring import (
    LOOKBACK_MINUTES,
    recency_multiplier,
    score,
    source_agreement_from_scores,
)

T0 = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
TRADE_TOKENS = ("BUY", "SELL", "LONG", "SHORT")


def _mention(
    text: str,
    *,
    ext: str,
    created: datetime = T0,
    engagement: float = 0.0,
    author: str = "author-a",
) -> SocialMention:
    return SocialMention(
        provider="x",
        external_id=ext,
        asset_id="btc",
        text=text,
        author_id_hash=author,
        created_at=created,
        engagement=engagement,
        matched_terms=("bitcoin",),
        match_confidence=0.8,
        match_rationale="official name in text",
    )


def _board(texts: list[str], *, created: datetime = T0) -> list[SocialMention]:
    return [
        _mention(text, ext=str(i), created=created, author=f"a{i}")
        for i, text in enumerate(texts)
    ]


def test_empty_mentions_are_insufficient_data() -> None:
    bundle = score([], "swing", BaselineBundle.unknown())
    assert bundle.classification == "insufficient_data"
    assert bundle.sentiment_score == 0.0
    assert bundle.mention_count == 0
    assert bundle.mention_velocity == 0.0
    assert bundle.social_heat == 0.0
    assert bundle.social_acceleration == 0.0
    assert bundle.acceleration_state == "holding"
    assert bundle.confidence == 0.0
    assert bundle.freshness_seconds is None


def test_polar_positive_and_negative_bands() -> None:
    positive = _board(
        [
            "this is a great quality project",
            "solid and promising work",
            "amazing well built release",
            "love this legit project",
            "excellent trustworthy team",
        ]
    )
    negative = _board(
        [
            "this is a scam rug pull",
            "worthless dead project",
            "garbage honeypot fraud",
            "disappointing abandoned trash",
            "useless buggy broken mess",
        ]
    )
    pos = score(positive, "swing", BaselineBundle(observed_at=T0))
    neg = score(negative, "swing", BaselineBundle(observed_at=T0))
    assert pos.classification in {"positive", "strong_positive"}
    assert pos.sentiment_score >= 20.0
    assert -100.0 <= pos.sentiment_score <= 100.0
    assert neg.classification in {"negative", "strong_negative"}
    assert neg.sentiment_score <= -20.0


def test_insufficient_data_below_five_nonempty() -> None:
    mentions = _board(["great solid project", "love this", "amazing work", "scam rug"])
    bundle = score(mentions, "swing", BaselineBundle(observed_at=T0))
    assert bundle.classification == "insufficient_data"
    assert bundle.sentiment_score == 0.0
    assert bundle.engagement_weighted_sentiment == 0.0
    assert bundle.positive_count == 3
    assert bundle.negative_count == 1


def test_empty_text_excluded_from_n_eff() -> None:
    mentions = _board(["great solid project"] * 4) + [
        _mention("   ", ext="empty", author="empty-author"),
    ]
    bundle = score(mentions, "swing", BaselineBundle(observed_at=T0))
    assert bundle.mention_count == 5
    assert bundle.classification == "insufficient_data"


def test_engagement_weighted_does_not_relabel_classification() -> None:
    mentions = [
        _mention("great solid promising project", ext="1", engagement=10_000.0, author="loud"),
        *(
            _mention("scam rug pull trash", ext=str(i), engagement=0.0, author=f"n{i}")
            for i in range(2, 7)
        ),
    ]
    bundle = score(mentions, "swing", BaselineBundle(observed_at=T0))
    assert bundle.positive_ratio < 0.20
    assert bundle.classification in {"negative", "strong_negative"}
    assert bundle.engagement_weighted_sentiment > bundle.sentiment_score


def test_directional_phrases_do_not_move_polarity() -> None:
    mentions = _board(
        [
            "to the moon pump ath breakout",
            "dump crash rekt selloff",
            "moon pumping parabolic",
            "going to zero dump",
            "ath moon pump",
        ]
    )
    bundle = score(mentions, "swing", BaselineBundle(observed_at=T0))
    assert bundle.classification == "neutral"
    assert bundle.sentiment_score == 0.0
    assert bundle.bullish_phrase_intensity > 0.0
    assert bundle.bearish_phrase_intensity > 0.0


def test_burst_velocity_higher_than_even_spread() -> None:
    even = [
        _mention(
            "bitcoin note",
            ext=str(i),
            created=T0 - timedelta(minutes=10 + 15 * i),
            author=f"e{i}",
        )
        for i in range(4)
    ]
    burst = [
        _mention("bitcoin note", ext=str(i), created=T0 - timedelta(minutes=2 + i), author=f"b{i}")
        for i in range(4)
    ]
    priors = BaselineBundle(observed_at=T0, prior_mentions_1h=4)
    even_s = score(even, "swing", priors)
    burst_s = score(burst, "swing", priors)
    assert even_s.mentions_1h == burst_s.mentions_1h == 4
    assert burst_s.mention_velocity > even_s.mention_velocity
    assert even_s.classification == burst_s.classification == "insufficient_data"
    assert even_s.sentiment_score == 0.0
    assert burst_s.sentiment_score == 0.0


def test_unknown_prior_is_not_a_fake_spike() -> None:
    mentions = [
        _mention("bitcoin note", ext=str(i), created=T0 - timedelta(minutes=i), author=f"u{i}")
        for i in range(8)
    ]
    unknown = score(mentions, "swing", BaselineBundle(observed_at=T0))
    known_quiet = score(
        mentions,
        "swing",
        BaselineBundle(observed_at=T0, prior_mentions_1h=0),
    )
    assert unknown.mentions_1h_previous is None
    assert known_quiet.mention_velocity > unknown.mention_velocity
    assert known_quiet.acceleration_state == "accelerating"


def test_source_agreement_aligned_opposed_and_singleton() -> None:
    aligned, _ = source_agreement_from_scores((60.0, 62.0))
    opposed, opposed_d = source_agreement_from_scores((100.0, -100.0))
    singleton, singleton_d = source_agreement_from_scores((40.0,))
    missing, missing_d = source_agreement_from_scores(())
    assert aligned > opposed
    assert opposed == 0.0
    assert opposed_d == 1.0
    assert singleton == 0.0 and singleton_d == 0.0
    assert missing == 0.0 and missing_d == 0.0


def test_score_leaves_agreement_zero_for_one_source_call() -> None:
    mentions = _board(["great solid project"] * 5)
    bundle = score(mentions, "swing", BaselineBundle(observed_at=T0))
    assert bundle.source_agreement == 0.0
    assert bundle.source_disagreement == 0.0


def test_horizons_change_recency_and_acceleration() -> None:
    burst = [
        _mention(
            "great solid project",
            ext=str(i),
            created=T0 - timedelta(minutes=i),
            author=f"n{i}",
        )
        for i in range(5)
    ]
    older = [
        _mention(
            "scam rug pull trash",
            ext=f"old{i}",
            created=T0 - timedelta(hours=20),
            author=f"o{i}",
        )
        for i in range(5)
    ]
    mentions = burst + older
    priors = BaselineBundle(
        observed_at=T0,
        prior_mentions_1h=2,
        prior_mentions_6h=20,
        prior_mentions_24h=40,
    )
    intra = score(mentions, "intraday", priors)
    pos = score(mentions, "position", priors)
    assert intra.horizon == "intraday"
    assert pos.horizon == "position"
    assert intra.sentiment_score != pos.sentiment_score
    assert intra.social_acceleration != pos.social_acceleration
    assert LOOKBACK_MINUTES["intraday"] == 360
    assert LOOKBACK_MINUTES["position"] == 4320
    assert recency_multiplier(60.0, "intraday") > recency_multiplier(60.0, "position")


def test_mixed_before_strong() -> None:
    mentions = _board(
        ["great solid promising project"] * 3 + ["scam rug pull trash"] * 3
    )
    bundle = score(mentions, "swing", BaselineBundle(observed_at=T0))
    assert bundle.positive_ratio >= 0.20
    assert bundle.negative_ratio >= 0.20
    assert bundle.classification == "mixed"


def test_stale_mentions_excluded_from_1h_window() -> None:
    stale = [
        _mention(
            "great solid project",
            ext=str(i),
            created=T0 - timedelta(hours=30),
            author=f"s{i}",
        )
        for i in range(5)
    ]
    bundle = score(stale, "swing", BaselineBundle(observed_at=T0, prior_mentions_1h=5))
    assert bundle.mention_count == 5
    assert bundle.mentions_1h == 0
    assert bundle.mentions_24h == 0
    assert bundle.activity_coverage == 0.0


def test_determinism_same_fixture_twice() -> None:
    mentions = _board(
        ["great solid project", "scam rug", "maybe unclear", "love this", "trash scam"]
    )
    baselines = BaselineBundle(observed_at=T0, prior_mentions_1h=3)
    a = score(mentions, "swing", baselines)
    b = score(mentions, "swing", baselines)
    assert a == b


def test_no_trade_language_in_scoring_outputs() -> None:
    mentions = _board(["great solid project to the moon"] * 5)
    bundle = score(mentions, "swing", BaselineBundle(observed_at=T0))
    blob = f"{bundle.classification} {bundle.acceleration_state} {bundle.horizon}"
    for token in TRADE_TOKENS:
        assert token not in blob
        assert token.lower() not in blob
    assert bundle.classification != "bullish"


def test_sentiment_delta_requires_real_prior() -> None:
    mentions = _board(["great solid project"] * 5)
    unknown = score(mentions, "swing", BaselineBundle(observed_at=T0))
    known = score(
        mentions,
        "swing",
        BaselineBundle(
            observed_at=T0,
            prior_sentiment_score=18.0,
            prior_observed_at=T0 - timedelta(hours=3),
        ),
    )
    assert unknown.sentiment_change is None
    assert unknown.sentiment_change_hours is None
    assert known.sentiment_change == known.sentiment_score - 18.0
    assert known.sentiment_change_hours == 3.0
