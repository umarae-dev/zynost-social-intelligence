"""Redis cache adapter (docs/12; TST-C-01..07). Fake transport only — no live Redis."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from zynost_social.cache import (
    TTL_AGGREGATE_S,
    TTL_BASELINE_S,
    TTL_RAW_S,
    CacheContext,
    apply_raw_window,
    asset_key_segment,
    baselines_writable,
    cache_key,
    get_aggregate,
    get_baselines,
    get_lookaside,
    get_raw,
    open_cache_context,
    records_to_baseline_bundle,
    set_aggregate,
    set_baselines,
    set_compute,
    set_raw,
    set_raw_many,
)
from zynost_social.models import SocialMention

T0 = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
ASSET_ID = "btc"


class MemoryRedis:
    """In-test Redis stand-in (CON-05). Clock is injectable for TTL expiry."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[bytes, int, float]] = {}
        self.now = 0.0
        self.fail = False
        self.hang = False
        self.gets = 0
        self.mgets = 0
        self.writes = 0
        self.forbidden: list[str] = []

    async def get(self, key: str) -> bytes | None:
        self.gets += 1
        if self.hang:
            raise TimeoutError
        if self.fail:
            raise ConnectionError("unavailable")
        return self._read(key)

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        self.mgets += 1
        if self.hang:
            raise TimeoutError
        if self.fail:
            raise ConnectionError("unavailable")
        return [self._read(key) for key in keys]

    async def set_ex(self, items: Sequence[tuple[str, bytes, int]]) -> None:
        self.writes += 1
        if self.hang:
            raise TimeoutError
        if self.fail:
            raise ConnectionError("unavailable")
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

    async def delete(self, *_args: object, **_kwargs: object) -> None:
        self.forbidden.append("delete")

    async def keys(self, *_args: object, **_kwargs: object) -> None:
        self.forbidden.append("keys")

    async def scan(self, *_args: object, **_kwargs: object) -> None:
        self.forbidden.append("scan")

    async def flushdb(self, *_args: object, **_kwargs: object) -> None:
        self.forbidden.append("flushdb")


def _mention(
    text: str,
    *,
    ext: str,
    created_at: datetime = T0,
    match_confidence: float = 0.8,
) -> SocialMention:
    return SocialMention(
        provider="x",
        external_id=ext,
        asset_id=ASSET_ID,
        text=text,
        author_id_hash="author-a",
        created_at=created_at,
        engagement=1.0,
        matched_terms=("bitcoin",),
        match_confidence=match_confidence,
        match_rationale="official name in text",
    )


def _ctx(memory: MemoryRedis | None = None) -> tuple[CacheContext, MemoryRedis]:
    redis = memory or MemoryRedis()
    return CacheContext(transport=redis), redis


@pytest.mark.asyncio
async def test_raw_hit_round_trip_strips_match_evidence() -> None:
    ctx, _redis = _ctx()
    await set_raw(
        ctx,
        ASSET_ID,
        "x",
        360,
        [_mention("bitcoin rally", ext="1")],
        observed_at=T0,
    )
    hit = await get_raw(ctx, ASSET_ID, "x", 360)
    assert hit is not None
    assert hit.observed_at == T0
    assert hit.mentions[0].text == "bitcoin rally"
    assert hit.mentions[0].matched_terms == ()
    assert hit.mentions[0].match_confidence == 0.0
    assert hit.mentions[0].match_rationale == ""


@pytest.mark.asyncio
async def test_zero_mentions_are_cached_as_quiet_observation() -> None:
    ctx, _redis = _ctx()
    await set_raw(ctx, ASSET_ID, "reddit", 1440, (), observed_at=T0)
    hit = await get_raw(ctx, ASSET_ID, "reddit", 1440)
    assert hit is not None
    assert hit.mentions == ()


@pytest.mark.asyncio
async def test_aggregate_hit_keeps_cached_observed_at() -> None:
    ctx, _redis = _ctx()
    await set_aggregate(
        ctx,
        ASSET_ID,
        "swing",
        {"scope": "asset_specific", "observed_at": "2026-09-08T12:00:00Z", "mention_count": 12},
    )
    payload = await get_aggregate(ctx, ASSET_ID, "swing")
    assert payload is not None
    assert payload["observed_at"] == "2026-09-08T12:00:00Z"
    assert payload["mention_count"] == 12


@pytest.mark.asyncio
async def test_miss_is_none_not_zero_filled() -> None:
    ctx, _redis = _ctx()
    assert await get_aggregate(ctx, ASSET_ID, "swing") is None
    assert await get_raw(ctx, ASSET_ID, "x", 360) is None
    records = await get_baselines(ctx, ASSET_ID)
    assert records == {}
    bundle = records_to_baseline_bundle(records)
    assert bundle.prior_mentions_1h is None
    assert bundle.prior_mentions_6h is None
    assert bundle.prior_mentions_24h is None
    assert bundle.prior_engagement_1h is None
    assert bundle.prior_sentiment_score is None


@pytest.mark.asyncio
async def test_quiet_baseline_zero_is_not_unknown() -> None:
    ctx, _redis = _ctx()
    await set_baselines(
        ctx,
        ASSET_ID,
        {
            "1h": {
                "window": "1h",
                "observed_at": "2026-09-08T12:00:00Z",
                "mention_count": 0,
                "engagement_sum": 0.0,
                "sentiment_score": None,
            }
        },
    )
    records = await get_baselines(ctx, ASSET_ID)
    bundle = records_to_baseline_bundle(records)
    assert bundle.prior_mentions_1h == 0
    assert bundle.prior_engagement_1h == 0.0
    assert bundle.prior_mentions_6h is None
    assert bundle.prior_sentiment_score is None


@pytest.mark.asyncio
async def test_expiry_turns_hit_into_miss() -> None:
    ctx, redis = _ctx()
    await set_aggregate(ctx, ASSET_ID, "intraday", {"observed_at": "2026-09-08T12:00:00Z"})
    assert await get_aggregate(ctx, ASSET_ID, "intraday") is not None
    redis.now += TTL_AGGREGATE_S + 1
    assert await get_aggregate(ctx, ASSET_ID, "intraday") is None


@pytest.mark.asyncio
async def test_raw_ttl_is_longer_than_aggregate() -> None:
    assert TTL_RAW_S == 180
    assert TTL_AGGREGATE_S == 120
    assert TTL_RAW_S > TTL_AGGREGATE_S
    assert TTL_BASELINE_S["1h"] == 10_800
    assert TTL_BASELINE_S["6h"] == 43_200
    assert TTL_BASELINE_S["24h"] == 172_800


@pytest.mark.asyncio
async def test_redis_unavailable_is_miss_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    redis = MemoryRedis()
    redis.fail = True
    ctx, _ = _ctx(redis)
    caplog.set_level(logging.WARNING, logger="zynost_social.cache")
    assert await get_aggregate(ctx, ASSET_ID, "swing") is None
    await set_raw(ctx, ASSET_ID, "x", 360, (), observed_at=T0)
    assert await get_raw(ctx, ASSET_ID, "x", 360) is None
    assert ctx.disabled is True
    lines = [rec.getMessage() for rec in caplog.records]
    assert len(lines) == 1
    assert "purpose=aggregate" in lines[0]
    assert "kind=connection" in lines[0]
    assert "redis://" not in lines[0].lower()
    assert "password" not in lines[0].lower()


@pytest.mark.asyncio
async def test_timeout_disables_cache_for_the_rest_of_the_run() -> None:
    redis = MemoryRedis()
    redis.hang = True
    ctx, _ = _ctx(redis)
    assert await get_baselines(ctx, ASSET_ID) == {}
    redis.hang = False
    await set_aggregate(ctx, ASSET_ID, "swing", {"observed_at": "2026-09-08T12:00:00Z"})
    assert await get_aggregate(ctx, ASSET_ID, "swing") is None
    assert redis.writes == 0


@pytest.mark.asyncio
async def test_narrow_cached_window_is_miss_wide_is_filtered() -> None:
    ctx, _redis = _ctx()
    old = T0 - timedelta(minutes=1000)
    recent = T0 - timedelta(minutes=10)
    await set_raw(
        ctx,
        ASSET_ID,
        "x",
        360,
        [
            _mention("bitcoin old", ext="old", created_at=old),
            _mention("bitcoin now", ext="new", created_at=recent),
        ],
        observed_at=T0,
    )
    assert await get_raw(ctx, ASSET_ID, "x", 1440) is None
    hit = await get_raw(ctx, ASSET_ID, "x", 60)
    assert hit is not None
    assert [m.external_id for m in hit.mentions] == ["new"]
    assert hit.observed_at == T0


@pytest.mark.asyncio
async def test_lookaside_is_one_mget_round_trip() -> None:
    ctx, redis = _ctx()
    await set_raw(ctx, ASSET_ID, "x", 360, [_mention("bitcoin", ext="1")], observed_at=T0)
    await set_baselines(
        ctx,
        ASSET_ID,
        {
            "1h": {
                "window": "1h",
                "observed_at": "2026-09-08T12:00:00Z",
                "mention_count": 4,
                "engagement_sum": 8.0,
                "sentiment_score": 12.0,
            }
        },
    )
    redis.mgets = 0
    raw, baselines = await get_lookaside(ctx, ASSET_ID)
    assert redis.mgets == 1
    hit = apply_raw_window(ctx, raw["x"], provider="x", since_minutes=360)
    assert hit is not None
    assert hit.mentions[0].external_id == "1"
    assert raw["reddit"] is None
    assert baselines["1h"]["mention_count"] == 4


@pytest.mark.asyncio
async def test_all_down_skips_baseline_write_but_may_write_aggregate() -> None:
    ctx, redis = _ctx()
    await set_compute(
        ctx,
        ASSET_ID,
        "swing",
        {"observed_at": "2026-09-08T12:00:00Z", "status": "unavailable"},
        {
            "1h": {
                "window": "1h",
                "observed_at": "2026-09-08T12:00:00Z",
                "mention_count": 0,
                "engagement_sum": 0.0,
                "sentiment_score": None,
            }
        },
        available_providers=0,
    )
    assert await get_aggregate(ctx, ASSET_ID, "swing") is not None
    assert await get_baselines(ctx, ASSET_ID) == {}
    assert baselines_writable(available_providers=0) is False
    assert baselines_writable(available_providers=1) is True
    assert redis.forbidden == []


@pytest.mark.asyncio
async def test_keys_use_asset_id_never_symbol() -> None:
    assert cache_key("btc", "raw", "x") == "zynost:social:v1:btc:raw:x"
    assert "BTC" not in cache_key("eth-mainnet", "aggregate", "swing")
    hostile = "btc:raw:x"
    segment = asset_key_segment(hostile)
    assert ":" not in segment
    assert cache_key(hostile, "raw", "x") == f"zynost:social:v1:{segment}:raw:x"
    assert cache_key("asset-one", "raw", "x") != cache_key("asset-in", "raw", "x")


@pytest.mark.asyncio
async def test_oversize_write_is_skipped() -> None:
    ctx, redis = _ctx()
    huge = {"observed_at": "2026-09-08T12:00:00Z", "blob": "x" * (300 * 1024)}
    await set_aggregate(ctx, ASSET_ID, "swing", huge)
    assert redis.store == {}
    assert await get_aggregate(ctx, ASSET_ID, "swing") is None


@pytest.mark.asyncio
async def test_malformed_payload_is_miss_and_disables() -> None:
    ctx, redis = _ctx()
    redis.store[cache_key(ASSET_ID, "aggregate", "swing")] = (b"not-json", 120, 10_000)
    assert await get_aggregate(ctx, ASSET_ID, "swing") is None
    assert ctx.disabled is True


@pytest.mark.asyncio
async def test_nan_is_not_written() -> None:
    ctx, redis = _ctx()
    await set_aggregate(ctx, ASSET_ID, "swing", {"sentiment_score": float("nan")})
    assert redis.store == {}


@pytest.mark.asyncio
async def test_unset_redis_url_disables_without_raising(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="zynost_social.cache")
    ctx = open_cache_context(environ={})
    assert ctx.disabled is True
    assert await get_raw(ctx, ASSET_ID, "x", 360) is None
    await set_raw_many(ctx, ASSET_ID, [("x", 360, T0, ())])
    messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "not_configured" in messages
    assert "redis://" not in messages


@pytest.mark.asyncio
async def test_unparseable_redis_url_disables() -> None:
    ctx = open_cache_context(environ={"REDIS_URL": "http://example.invalid"})
    assert ctx.disabled is True


@pytest.mark.asyncio
async def test_injected_transport_ignores_missing_url() -> None:
    redis = MemoryRedis()
    ctx = open_cache_context(transport=redis, environ={})
    await set_aggregate(ctx, ASSET_ID, "position", {"observed_at": "2026-09-08T12:00:00Z"})
    assert await get_aggregate(ctx, ASSET_ID, "position") is not None


def test_records_to_bundle_does_not_invent_current_t0() -> None:
    bundle = records_to_baseline_bundle(
        {
            "24h": {
                "window": "24h",
                "observed_at": "2026-09-08T12:00:00Z",
                "mention_count": 9,
                "engagement_sum": 3.0,
                "sentiment_score": 40.0,
            }
        }
    )
    assert bundle.observed_at is None
    assert bundle.prior_mentions_24h == 9
    assert bundle.prior_mentions_1h is None
    assert bundle.prior_sentiment_score is None


def test_cache_module_has_no_trade_language() -> None:
    from pathlib import Path

    text = Path("zynost_social/cache.py").read_text(encoding="utf-8")
    for token in ("BUY", "SELL", "LONG", "SHORT"):
        assert token not in text
