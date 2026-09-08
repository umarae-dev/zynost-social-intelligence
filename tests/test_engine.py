"""Public engine contract (docs/14; TST-I-01–07, TST-P-08/10, FR-HOR-03)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from tests.test_providers import FakeProvider
from zynost_social.cache import CacheContext, open_cache_context
from zynost_social.engine import REQUIRED_KEYS, build_social_sentiment
from zynost_social.models import AssetIdentity, ProviderId, SocialMention
from zynost_social.providers.base import ProviderError, SocialProvider

T0 = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
ASSET = AssetIdentity(asset_id="btc", symbol="BTC", name="Bitcoin", aliases=("btc",))
TRADE_TOKENS = ("BUY", "SELL", "LONG", "SHORT")


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[bytes, int, float]] = {}
        self.now = 0.0

    async def get(self, key: str) -> bytes | None:
        return self._read(key)

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        return [self._read(key) for key in keys]

    async def set_ex(self, items: Sequence[tuple[str, bytes, int]]) -> None:
        for key, value, ttl in items:
            self.store[key] = (value, ttl, self.now + ttl)

    def _read(self, key: str) -> bytes | None:
        item = self.store.get(key)
        if item is None:
            return None
        value, _ttl, expires_at = item
        if expires_at <= self.now:
            del self.store[key]
            return None
        return value


class RecordingProvider(FakeProvider):
    def __init__(
        self,
        provider: ProviderId,
        *,
        mentions: list[SocialMention] | None = None,
        error: BaseException | None = None,
        delay_s: float = 0.0,
    ) -> None:
        super().__init__(provider, mentions=mentions, error=error, delay_s=delay_s)
        self.since_minutes: list[int] = []

    async def fetch_mentions(self, asset: AssetIdentity, since_minutes: int) -> list[SocialMention]:
        self.since_minutes.append(since_minutes)
        return await super().fetch_mentions(asset, since_minutes)


def _mention(
    *,
    provider: ProviderId = "x",
    ext: str = "1",
    author: str = "author-a",
    created: datetime | None = None,
) -> SocialMention:
    return SocialMention(
        provider=provider,
        external_id=ext,
        asset_id="btc",
        text="bitcoin discussion about the network",
        author_id_hash=author,
        created_at=created or T0,
        engagement=1.0,
    )


def _providers(
    *,
    x: Sequence[SocialMention] | None = None,
    reddit: Sequence[SocialMention] | None = None,
    x_status: str = "up",
    reddit_status: str = "up",
    telegram_status: str = "up",
    discord_status: str = "up",
) -> tuple[RecordingProvider, ...]:
    def one(
        provider: ProviderId,
        mentions: Sequence[SocialMention],
        status: str,
    ) -> RecordingProvider:
        if status == "timeout":
            return RecordingProvider(provider, error=ProviderError("timeout"))
        if status == "not_configured":
            return RecordingProvider(provider, error=ProviderError("not_configured"))
        return RecordingProvider(provider, mentions=list(mentions))

    return (
        one("x", x or (), x_status),
        one("reddit", reddit or (), reddit_status),
        one("telegram", (), telegram_status),
        one("discord", (), discord_status),
    )


def _cache(disabled: bool = True) -> CacheContext:
    if disabled:
        return CacheContext(transport=None, disabled=True)
    return open_cache_context(transport=MemoryRedis())


async def _run(
    providers: Sequence[SocialProvider],
    *,
    horizon: str = "swing",
    cache: CacheContext | None = None,
    observed_at: datetime = T0,
) -> dict[str, object]:
    return await build_social_sentiment(
        ASSET,
        horizon,
        providers=providers,
        cache_ctx=cache if cache is not None else _cache(),
        observed_at=observed_at,
    )


def _assert_identity(result: dict[str, object]) -> None:
    assert result["name"] == "social_sentiment"
    assert result["role"] == "context"
    assert result["source_class"] == "multi_source_social_intelligence"
    assert set(result) == set(REQUIRED_KEYS)
    blob = json.dumps(result)
    for token in TRADE_TOKENS:
        assert token not in blob


async def test_all_unavailable_nulls_scores_and_serializes() -> None:
    result = await _run(
        _providers(
            x_status="timeout",
            reddit_status="timeout",
            telegram_status="not_configured",
            discord_status="not_configured",
        )
    )
    _assert_identity(result)
    assert result["status"] == "unavailable"
    assert result["scope"] == "asset_specific"
    assert result["classification"] == "insufficient_data"
    assert result["sentiment_score"] is None
    assert result["social_heat"] is None
    assert result["mention_velocity"] is None
    assert result["organic_score"] is None
    assert result["source_agreement"] is None
    assert result["manipulation_risk"] is None
    assert result["confidence"] == 0.0
    assert result["anomalies"] == []
    assert result["metrics"] == {"horizon_requested": "swing", "horizon_applied": "swing"}
    quality = result["data_quality"]
    assert isinstance(quality, dict)
    assert quality["provider_coverage"] == 0.0
    assert quality["freshness_score"] is None
    assert quality["organic_data_ratio"] is None
    sources = result["sources"]
    assert isinstance(sources, dict)
    assert set(sources) == {"x", "reddit", "telegram", "discord"}
    assert sources["x"]["error_class"] == "timeout"
    assert "provider" not in sources["x"]
    json.dumps(result)


async def test_quiet_available_is_not_unavailable() -> None:
    result = await _run(_providers())
    _assert_identity(result)
    assert result["status"] == "available"
    assert result["classification"] == "insufficient_data"
    assert result["sentiment_score"] == 0.0
    quality = result["data_quality"]
    assert isinstance(quality, dict)
    assert quality["provider_coverage"] == 1.0
    assert quality["organic_data_ratio"] is None
    sources = result["sources"]
    assert isinstance(sources, dict)
    assert sources["x"]["status"] == "available"
    assert sources["x"]["error_class"] is None
    assert sources["x"]["mention_count"] == 0


async def test_unknown_horizon_applies_swing() -> None:
    providers = _providers()
    result = await _run(providers, horizon="not-a-horizon")
    assert result["metrics"]["horizon_requested"] == "not-a-horizon"
    assert result["metrics"]["horizon_applied"] == "swing"
    assert providers[0].since_minutes == [1440]


async def test_horizons_change_lookback() -> None:
    intra = _providers()
    pos = _providers()
    await _run(intra, horizon="intraday")
    await _run(pos, horizon="position")
    assert intra[0].since_minutes == [360]
    assert pos[0].since_minutes == [4320]


async def test_partial_coverage_shrinks_quality_not_dropped() -> None:
    mentions = tuple(_mention(ext=str(i), author=f"a{i}") for i in range(6))
    result = await _run(
        _providers(
            x=mentions,
            reddit_status="timeout",
            telegram_status="not_configured",
            discord_status="not_configured",
        )
    )
    assert result["status"] == "available"
    quality = result["data_quality"]
    assert isinstance(quality, dict)
    assert quality["provider_coverage"] == 0.25
    sources = result["sources"]
    assert isinstance(sources, dict)
    assert sources["reddit"]["status"] == "unavailable"
    assert sources["reddit"]["error_class"] == "timeout"
    assert "redis" not in sources
    assert result["confidence"] != 100.0


async def test_cache_miss_is_not_a_provider_row() -> None:
    result = await _run(_providers(), cache=_cache(disabled=True))
    sources = result["sources"]
    assert isinstance(sources, dict)
    assert "redis" not in sources
    quality = result["data_quality"]
    assert isinstance(quality, dict)
    assert quality["served_from_cache"] is False


async def test_aggregate_cache_hit_keeps_collection_time() -> None:
    redis = MemoryRedis()
    cache = open_cache_context(transport=redis)
    mentions = tuple(_mention(ext=str(i), author=f"a{i}") for i in range(6))
    first = _providers(
        x=mentions,
        reddit_status="timeout",
        telegram_status="not_configured",
        discord_status="not_configured",
    )
    result1 = await _run(first, cache=cache)
    assert result1["data_quality"]["served_from_cache"] is False
    second = _providers(
        x_status="timeout",
        reddit_status="timeout",
        telegram_status="not_configured",
        discord_status="not_configured",
    )
    result2 = await _run(second, cache=cache, observed_at=T0.replace(minute=5))
    assert second[0].fetch_calls == 0
    assert result2["observed_at"] == result1["observed_at"]
    assert result2["data_quality"]["served_from_cache"] is True
    assert result2["sentiment_score"] == result1["sentiment_score"]


async def test_provider_failure_does_not_raise() -> None:
    result = await _run(
        _providers(
            x_status="timeout",
            reddit_status="not_configured",
            telegram_status="not_configured",
            discord_status="not_configured",
        )
    )
    assert result["status"] == "unavailable"
    json.dumps(result)
