"""Manipulation assessment (docs/10; TST-S-07–12, TST-S-14, TST-02)."""

from datetime import UTC, datetime, timedelta

from zynost_social.manipulation import assess
from zynost_social.models import BaselineBundle, SocialMention
from zynost_social.scoring import score

T0 = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
TRADE_TOKENS = ("BUY", "SELL", "LONG", "SHORT")

SHILL_TEXT = (
    "join this exclusive giveaway now claim your guaranteed allocation before close"
)


def _mention(
    text: str,
    *,
    ext: str,
    created: datetime = T0,
    engagement: float = 0.0,
    author: str = "author-a",
    url_hash: str | None = None,
) -> SocialMention:
    return SocialMention(
        provider="x",
        external_id=ext,
        asset_id="btc",
        text=text,
        author_id_hash=author,
        created_at=created,
        engagement=engagement,
        url_hash=url_hash,
        matched_terms=("bitcoin",),
        match_confidence=0.8,
        match_rationale="official name in text",
    )


def _finite_unit(value: float) -> None:
    assert 0.0 <= value <= 1.0
    assert value == value
    assert value not in (float("inf"), float("-inf"))


def _assert_ranges(result) -> None:
    _finite_unit(result.duplicate_content_ratio)
    _finite_unit(result.unique_author_ratio)
    _finite_unit(result.engagement_concentration)
    _finite_unit(result.author_concentration)
    _finite_unit(result.suspicious_burst_score)
    _finite_unit(result.spam_bot_suspicion)
    _finite_unit(result.manipulation_risk)
    _finite_unit(result.anomaly_score)
    assert 0.0 <= result.organic_score <= 100.0
    assert result.organic_score == result.organic_score


def test_empty_batch_is_no_evidence_not_organic_verdict() -> None:
    result = assess([])
    assert result.duplicate_content_ratio == 0.0
    assert result.unique_author_ratio == 0.0
    assert result.engagement_concentration == 0.0
    assert result.author_concentration == 0.0
    assert result.suspicious_burst_score == 0.0
    assert result.spam_bot_suspicion == 0.0
    assert result.manipulation_risk == 0.0
    assert result.organic_score == 100.0
    assert result.anomaly_score == 0.0
    _assert_ranges(result)


def test_duplicate_content_ratio_on_near_identical_copies() -> None:
    unique = [
        _mention(
            f"distinct organic wording about custody topic {i} and setup",
            ext=str(i),
            author=f"u{i}",
        )
        for i in range(4)
    ]
    copies = [
        _mention(SHILL_TEXT, ext=f"c{i}", author=f"c{i}", created=T0 + timedelta(hours=i))
        for i in range(4)
    ]
    unique_result = assess(unique)
    copy_result = assess(copies)
    assert unique_result.duplicate_content_ratio == 0.0
    assert copy_result.duplicate_content_ratio == 1.0
    assert copy_result.duplicate_content_ratio > unique_result.duplicate_content_ratio


def test_shingle_overlap_counts_as_duplicate() -> None:
    shared = "alpha bravo charlie delta echo foxtrot golf hotel india"
    a = _mention(shared + " juliet", ext="a", author="a")
    b = _mention(shared + " kilo", ext="b", author="b")
    result = assess([a, b])
    assert result.duplicate_content_ratio == 1.0


def test_empty_text_excluded_from_duplicate_ratio() -> None:
    mentions = [
        _mention("   ", ext="e1", author="e1"),
        _mention("", ext="e2", author="e2"),
        _mention(SHILL_TEXT, ext="s1", author="s1"),
    ]
    result = assess(mentions)
    assert result.duplicate_content_ratio == 0.0


def test_repeated_author_flood() -> None:
    mentions = [
        _mention(
            f"unique flood wording number {i} about fees and latency",
            ext=str(i),
            author="only",
        )
        for i in range(10)
    ]
    result = assess(mentions)
    assert result.unique_author_ratio == 0.1
    assert result.author_concentration == 1.0


def test_single_mention_has_zero_author_concentration() -> None:
    result = assess([_mention("one mention only about fees and latency today", ext="1")])
    assert result.author_concentration == 0.0
    assert result.unique_author_ratio == 1.0
    assert result.engagement_concentration == 0.0


def test_coordinated_campaign_raises_burst_and_risk() -> None:
    staggered = [
        _mention(SHILL_TEXT, ext=str(i), author=f"a{i}", created=T0 + timedelta(hours=i))
        for i in range(6)
    ]
    clustered = [
        _mention(SHILL_TEXT, ext=str(i), author=f"a{i}", created=T0 + timedelta(seconds=i * 10))
        for i in range(6)
    ]
    far = assess(staggered)
    near = assess(clustered)
    assert far.duplicate_content_ratio == 1.0
    assert near.duplicate_content_ratio == 1.0
    assert far.suspicious_burst_score == 0.0
    assert near.suspicious_burst_score == 1.0
    assert near.manipulation_risk > far.manipulation_risk


def test_diverse_same_instant_is_timing_anomaly_not_scripted_copies() -> None:
    mentions = [
        _mention(
            f"independent topic {i} covering custody {i} fees {i} and latency {i}",
            ext=str(i),
            author=f"a{i}",
            created=T0,
        )
        for i in range(8)
    ]
    result = assess(mentions)
    polar = score(mentions, "swing", BaselineBundle(observed_at=T0))
    assert result.duplicate_content_ratio == 0.0
    assert result.suspicious_burst_score == 0.0
    assert result.anomaly_score > 0.0
    assert polar.classification != "strong_positive"
    blob = f"{polar.classification} {result.manipulation_risk}"
    for token in TRADE_TOKENS:
        assert token not in blob


def test_organic_scores_above_volume_shill() -> None:
    genuine = [
        _mention(
            f"independent topic {i} covering custody {i} fees {i} and latency {i}",
            ext=f"g{i}",
            author=f"g{i % 70}",
            created=T0 + timedelta(minutes=i * 3),
            engagement=float(i % 5),
        )
        for i in range(80)
    ]
    shill = [
        _mention(
            SHILL_TEXT,
            ext=f"s{i}",
            author=f"s{i % 20}",
            created=T0 + timedelta(seconds=i),
            engagement=100.0 if i < 5 else 0.0,
        )
        for i in range(500)
    ]
    organic = assess(genuine)
    pumped = assess(shill)
    assert organic.organic_score > pumped.organic_score
    assert pumped.organic_score < 30.0
    assert organic.organic_score > 85.0
    assert pumped.manipulation_risk > 0.5
    assert organic.manipulation_risk < 0.15
    assert pumped.duplicate_content_ratio == 1.0
    assert pumped.unique_author_ratio == 20 / 500


def test_manipulation_ranges_and_determinism() -> None:
    mentions = [
        _mention(SHILL_TEXT, ext="1", author="a", url_hash="link-a", engagement=10.0),
        _mention(SHILL_TEXT, ext="2", author="a", url_hash="link-a", engagement=0.0),
        _mention("different genuine custody writeup with unique tokens here", ext="3", author="b"),
    ]
    first = assess(mentions)
    second = assess(mentions)
    assert first == second
    _assert_ranges(first)


def test_repeated_url_hash_raises_spam_suspicion() -> None:
    shared = [
        _mention(
            f"link drop wording {i} about docs and status",
            ext=str(i),
            author=f"a{i}",
            url_hash="same",
        )
        for i in range(4)
    ]
    distinct = [
        _mention(
            f"link drop wording {i} about docs and status",
            ext=str(i),
            author=f"a{i}",
            url_hash=f"u{i}",
        )
        for i in range(4)
    ]
    assert assess(shared).spam_bot_suspicion > assess(distinct).spam_bot_suspicion


def test_engagement_concentration_when_one_post_takes_all() -> None:
    mentions = [
        _mention(
            f"organic wording {i} on fees latency and custody",
            ext=str(i),
            author=f"a{i}",
            engagement=eng,
        )
        for i, eng in enumerate((1000.0, 0.0, 0.0, 0.0, 0.0))
    ]
    even = [
        _mention(
            f"organic wording {i} on fees latency and custody",
            ext=str(i),
            author=f"a{i}",
            engagement=10.0,
        )
        for i in range(5)
    ]
    concentrated = assess(mentions)
    spread = assess(even)
    assert concentrated.engagement_concentration > spread.engagement_concentration
    assert spread.engagement_concentration == 0.0


def test_no_trade_language_on_assessment() -> None:
    result = assess([_mention(SHILL_TEXT, ext=str(i), author=f"a{i}") for i in range(6)])
    blob = " ".join(
        f"{name}={getattr(result, name)}"
        for name in (
            "duplicate_content_ratio",
            "unique_author_ratio",
            "engagement_concentration",
            "author_concentration",
            "suspicious_burst_score",
            "spam_bot_suspicion",
            "manipulation_risk",
            "organic_score",
            "anomaly_score",
        )
    )
    for token in TRADE_TOKENS:
        assert token not in blob
        assert token.lower() not in blob
