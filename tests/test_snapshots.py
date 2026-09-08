"""Snapshot projection (docs/13; TST-I-08, TST-I-09, SNP-04–05, SNP-11)."""

from datetime import UTC, datetime

from tests.test_engine import ASSET, T0, _cache, _mention, _providers, _run
from zynost_social.engine import _project_snapshot, project_snapshot
from zynost_social.models import AggregateResult, SocialMention, SourceBundle

TRADE_TOKENS = ("BUY", "SELL", "LONG", "SHORT")
SNAPSHOT_FIELDS = {
    "asset_id",
    "observed_at",
    "sentiment_score",
    "social_heat",
    "mention_count",
    "mention_velocity",
    "organic_score",
    "manipulation_risk",
    "source_coverage",
    "source_agreement",
    "classification",
}


def test_all_unavailable_projects_none() -> None:
    snapshot = _project_snapshot(
        AggregateResult(
            scope="asset_specific",
            classification="insufficient_data",
            confidence=0.0,
            social_heat=0.0,
            mention_velocity=0.0,
            organic_score=0.0,
            manipulation_risk=0.0,
            source_agreement=0.0,
            mention_count=0,
            sentiment_score=None,
            sources=(
                SourceBundle(provider="x", status="unavailable", error_class="timeout"),
                SourceBundle(provider="reddit", status="unavailable", error_class="timeout"),
                SourceBundle(
                    provider="telegram",
                    status="unavailable",
                    error_class="not_configured",
                ),
                SourceBundle(
                    provider="discord",
                    status="unavailable",
                    error_class="not_configured",
                ),
            ),
        ),
        "btc",
        T0,
    )
    assert snapshot is None


async def test_unavailable_contract_projects_none() -> None:
    result = await _run(
        _providers(
            x_status="timeout",
            reddit_status="timeout",
            telegram_status="not_configured",
            discord_status="not_configured",
        )
    )
    assert project_snapshot(result, asset_id="btc") is None


async def test_quiet_available_is_an_honest_row() -> None:
    result = await _run(_providers())
    snapshot = project_snapshot(result, asset_id="btc")
    assert snapshot is not None
    assert snapshot.asset_id == "btc"
    assert snapshot.observed_at == T0
    assert snapshot.mention_count == 0
    assert snapshot.classification == "insufficient_data"
    assert snapshot.sentiment_score == 0.0
    assert snapshot.source_coverage == 1.0
    assert set(snapshot.__dataclass_fields__) == SNAPSHOT_FIELDS
    for token in ("name", "metrics", "sources", "anomalies", "data_quality", "horizon"):
        assert token not in snapshot.__dataclass_fields__
    blob = repr(snapshot)
    for token in TRADE_TOKENS:
        assert token not in blob


async def test_leftovers_do_not_become_mention_count() -> None:
    chatter = SocialMention(
        provider="x",
        external_id="w",
        asset_id="btc",
        text="the crypto market dumped hard",
        author_id_hash="z",
        created_at=T0,
        engagement=1.0,
    )
    result = await _run(_providers(x=(chatter,)))
    snapshot = project_snapshot(result, asset_id="btc")
    assert snapshot is not None
    assert result["scope"] == "market_wide"
    assert snapshot.mention_count == 0
    assert snapshot.classification == "insufficient_data"


async def test_partial_coverage_is_not_normalized() -> None:
    mentions = tuple(_mention(ext=str(i), author=f"a{i}") for i in range(6))
    result = await _run(
        _providers(
            x=mentions,
            reddit_status="timeout",
            telegram_status="not_configured",
            discord_status="not_configured",
        )
    )
    snapshot = project_snapshot(result, asset_id="btc")
    assert snapshot is not None
    assert snapshot.source_coverage == 0.25
    assert snapshot.mention_count == 6


async def test_cache_served_is_the_same_observation() -> None:
    cache = _cache(disabled=False)
    mentions = tuple(_mention(ext=str(i), author=f"a{i}") for i in range(6))
    first = _providers(
        x=mentions,
        reddit_status="timeout",
        telegram_status="not_configured",
        discord_status="not_configured",
    )
    result1 = await _run(first, cache=cache)
    second = _providers(
        x_status="timeout",
        reddit_status="timeout",
        telegram_status="not_configured",
        discord_status="not_configured",
    )
    later = datetime(2026, 9, 8, 12, 5, tzinfo=UTC)
    result2 = await _run(second, cache=cache, observed_at=later)
    snap1 = project_snapshot(result1, asset_id=ASSET.asset_id)
    snap2 = project_snapshot(result2, asset_id=ASSET.asset_id)
    assert snap1 is not None and snap2 is not None
    assert snap1 == snap2
    assert snap1.observed_at == T0
