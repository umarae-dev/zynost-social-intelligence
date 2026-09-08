"""Shared Redis adapter. Keys/TTLs: docs/12. No process-memory truth (LLD-08)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlparse

from zynost_social.models import BaselineBundle, ProviderId, SocialMention

CACHE_GENERATION = "v1"
KEY_PREFIX = "zynost:social"
ROUND_TRIP_TIMEOUT_S = 0.250
VALUE_MAX_BYTES = 256 * 1024
TTL_AGGREGATE_S = 120
TTL_RAW_S = 180
TTL_BASELINE_S: Mapping[str, int] = {"1h": 10_800, "6h": 43_200, "24h": 172_800}
PROVIDERS: tuple[ProviderId, ...] = ("x", "reddit", "telegram", "discord")
HORIZONS: frozenset[str] = frozenset({"intraday", "swing", "position"})
WINDOWS: tuple[str, ...] = ("1h", "6h", "24h")
_ASSET_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REDIS_SCHEMES = frozenset({"redis", "rediss"})

_logger = logging.getLogger("zynost_social.cache")


class RedisTransport(Protocol):
    """One Redis round trip. Tests inject a fake; production uses redis.asyncio."""

    async def get(self, key: str) -> bytes | None: ...

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]: ...

    async def set_ex(self, items: Sequence[tuple[str, bytes, int]]) -> None: ...


@dataclass
class CacheContext:
    """Per-run client + disable flag (CCH-01, CCH-12). Never a module global."""

    transport: RedisTransport | None
    disabled: bool = False
    failure_logged: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class RawCacheHit:
    """Usable raw envelope after CCH-07 window check. Mentions are pre-match."""

    mentions: tuple[SocialMention, ...]
    since_minutes: int
    observed_at: datetime
    provider: ProviderId


def asset_key_segment(asset_id: str) -> str:
    """CCH-04: verbatim charset-safe id, else sha256 prefix. Never `symbol`."""
    if _ASSET_SEGMENT.fullmatch(asset_id):
        return asset_id
    return hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:32]


def cache_key(asset_id: str, purpose: str, qualifier: str | None = None) -> str:
    """CCH-03 grammar: zynost:social:v1:<asset_id>:<purpose>[:<qualifier>]."""
    parts = [KEY_PREFIX, CACHE_GENERATION, asset_key_segment(asset_id), purpose]
    if qualifier is not None:
        parts.append(qualifier)
    return ":".join(parts)


def baselines_writable(*, available_providers: int) -> bool:
    """CCH-15: never persist window counts from an all-down run."""
    return available_providers >= 1


def open_cache_context(
    *,
    transport: RedisTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> CacheContext:
    """Build a per-run context. Unset/unparseable REDIS_URL disables cache (CCH-20)."""
    if transport is not None:
        return CacheContext(transport=transport, disabled=False)
    env = os.environ if environ is None else environ
    raw_url = env.get("REDIS_URL")
    if raw_url is None or not raw_url.strip() or not _redis_url_parseable(raw_url.strip()):
        ctx = CacheContext(transport=None, disabled=True)
        _log_failure(ctx, purpose="config", kind="not_configured")
        return ctx
    try:
        live = _RedisClientTransport(raw_url.strip())
    except Exception:
        ctx = CacheContext(transport=None, disabled=True)
        _log_failure(ctx, purpose="config", kind="connection")
        return ctx
    return CacheContext(transport=live, disabled=False)


def records_to_baseline_bundle(records: Mapping[str, Mapping[str, object]]) -> BaselineBundle:
    """Map present window records to priors. Absent key stays unknown (CCH-08, CCH-14)."""
    rec_1h = records.get("1h")
    rec_6h = records.get("6h")
    rec_24h = records.get("24h")
    prior_sentiment: float | None = None
    prior_observed: datetime | None = None
    if rec_1h is not None:
        prior_sentiment = _optional_float(rec_1h.get("sentiment_score"))
        prior_observed = _parse_observed(rec_1h.get("observed_at"))
    return BaselineBundle(
        observed_at=None,
        prior_mentions_1h=_optional_int(rec_1h.get("mention_count") if rec_1h else None),
        prior_mentions_6h=_optional_int(rec_6h.get("mention_count") if rec_6h else None),
        prior_mentions_24h=_optional_int(rec_24h.get("mention_count") if rec_24h else None),
        prior_engagement_1h=_optional_float(rec_1h.get("engagement_sum") if rec_1h else None),
        prior_sentiment_score=prior_sentiment,
        prior_observed_at=prior_observed,
    )


async def get_aggregate(
    ctx: CacheContext,
    asset_id: str,
    horizon: str,
) -> dict[str, object] | None:
    if horizon not in HORIZONS:
        return None
    raw = await _get(ctx, cache_key(asset_id, "aggregate", horizon), purpose="aggregate")
    if raw is None:
        return None
    payload = _decode_object(ctx, raw, purpose="aggregate")
    return payload


async def set_aggregate(
    ctx: CacheContext,
    asset_id: str,
    horizon: str,
    aggregate: Mapping[str, object],
) -> None:
    if horizon not in HORIZONS or not isinstance(aggregate, Mapping):
        return
    await _set(
        ctx,
        cache_key(asset_id, "aggregate", horizon),
        dict(aggregate),
        TTL_AGGREGATE_S,
        purpose="aggregate",
    )


async def get_baselines(ctx: CacheContext, asset_id: str) -> dict[str, dict[str, object]]:
    keys = [cache_key(asset_id, "baseline", window) for window in WINDOWS]
    blobs = await _mget(ctx, keys, purpose="baseline")
    records: dict[str, dict[str, object]] = {}
    if blobs is None:
        return records
    for window, blob in zip(WINDOWS, blobs, strict=True):
        if blob is None:
            continue
        payload = _decode_object(ctx, blob, purpose="baseline")
        if payload is None:
            return {}
        records[window] = payload
    return records


async def set_baselines(
    ctx: CacheContext,
    asset_id: str,
    records: Mapping[str, Mapping[str, object]],
) -> None:
    items: list[tuple[str, bytes, int]] = []
    for window, record in records.items():
        ttl = TTL_BASELINE_S.get(window)
        if ttl is None or not isinstance(record, Mapping):
            continue
        encoded = _encode(dict(record), purpose="baseline")
        if encoded is None:
            continue
        items.append((cache_key(asset_id, "baseline", window), encoded, ttl))
    await _set_ex(ctx, items, purpose="baseline")


async def get_raw(
    ctx: CacheContext,
    asset_id: str,
    provider: str,
    since_minutes: int,
) -> RawCacheHit | None:
    if provider not in PROVIDERS or since_minutes < 0:
        return None
    raw = await _get(ctx, cache_key(asset_id, "raw", provider), purpose="raw")
    if raw is None:
        return None
    payload = _decode_object(ctx, raw, purpose="raw")
    if payload is None:
        return None
    return _raw_hit_from_payload(ctx, payload, provider=provider, since_minutes=since_minutes)


async def set_raw(
    ctx: CacheContext,
    asset_id: str,
    provider: str,
    since_minutes: int,
    mentions: Sequence[SocialMention],
    *,
    observed_at: datetime,
) -> None:
    if provider not in PROVIDERS or since_minutes < 0:
        return
    envelope = {
        "since_minutes": since_minutes,
        "observed_at": _iso(observed_at),
        "provider": provider,
        "mentions": [_mention_to_json(m) for m in mentions],
    }
    await _set(
        ctx,
        cache_key(asset_id, "raw", provider),
        envelope,
        TTL_RAW_S,
        purpose="raw",
    )


async def get_lookaside(
    ctx: CacheContext,
    asset_id: str,
) -> tuple[dict[str, dict[str, object] | None], dict[str, dict[str, object]]]:
    """One MGET of four raw keys plus three baseline keys (CCH-11)."""
    raw_keys = [cache_key(asset_id, "raw", provider) for provider in PROVIDERS]
    base_keys = [cache_key(asset_id, "baseline", window) for window in WINDOWS]
    blobs = await _mget(ctx, [*raw_keys, *base_keys], purpose="lookaside")
    raw_out: dict[str, dict[str, object] | None] = {p: None for p in PROVIDERS}
    baselines: dict[str, dict[str, object]] = {}
    if blobs is None:
        return raw_out, baselines
    for provider, blob in zip(PROVIDERS, blobs[:4], strict=True):
        if blob is None:
            continue
        payload = _decode_object(ctx, blob, purpose="raw")
        raw_out[provider] = payload
    for window, blob in zip(WINDOWS, blobs[4:], strict=True):
        if blob is None:
            continue
        payload = _decode_object(ctx, blob, purpose="baseline")
        if payload is None:
            return {p: None for p in PROVIDERS}, {}
        baselines[window] = payload
    return raw_out, baselines


async def set_raw_many(
    ctx: CacheContext,
    asset_id: str,
    envelopes: Sequence[tuple[str, int, datetime, Sequence[SocialMention]]],
) -> None:
    """One pipeline write of successful raw fetches (CCH-11, CCH-16)."""
    items: list[tuple[str, bytes, int]] = []
    for provider, since_minutes, observed_at, mentions in envelopes:
        if provider not in PROVIDERS or since_minutes < 0:
            continue
        encoded = _encode(
            {
                "since_minutes": since_minutes,
                "observed_at": _iso(observed_at),
                "provider": provider,
                "mentions": [_mention_to_json(m) for m in mentions],
            },
            purpose="raw",
        )
        if encoded is None:
            continue
        items.append((cache_key(asset_id, "raw", provider), encoded, TTL_RAW_S))
    await _set_ex(ctx, items, purpose="raw")


async def set_compute(
    ctx: CacheContext,
    asset_id: str,
    horizon: str,
    aggregate: Mapping[str, object],
    records: Mapping[str, Mapping[str, object]],
    *,
    available_providers: int,
) -> None:
    """One pipeline write of aggregate plus baselines when A >= 1 (CCH-11, CCH-15)."""
    items: list[tuple[str, bytes, int]] = []
    if horizon in HORIZONS and isinstance(aggregate, Mapping):
        encoded = _encode(dict(aggregate), purpose="aggregate")
        if encoded is not None:
            items.append((cache_key(asset_id, "aggregate", horizon), encoded, TTL_AGGREGATE_S))
    if baselines_writable(available_providers=available_providers):
        for window, record in records.items():
            ttl = TTL_BASELINE_S.get(window)
            if ttl is None or not isinstance(record, Mapping):
                continue
            encoded = _encode(dict(record), purpose="baseline")
            if encoded is None:
                continue
            items.append((cache_key(asset_id, "baseline", window), encoded, ttl))
    await _set_ex(ctx, items, purpose="compute")


def apply_raw_window(
    ctx: CacheContext,
    payload: Mapping[str, object] | None,
    *,
    provider: str,
    since_minutes: int,
) -> RawCacheHit | None:
    """CCH-07: miss if cached window is narrower; filter created_at if wider."""
    if payload is None or provider not in PROVIDERS or since_minutes < 0:
        return None
    return _raw_hit_from_payload(
        ctx, dict(payload), provider=provider, since_minutes=since_minutes
    )


def _redis_url_parseable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in _REDIS_SCHEMES:
        return False
    return bool(parsed.hostname or parsed.path)


def _log_failure(ctx: CacheContext, *, purpose: str, kind: str) -> None:
    if ctx.failure_logged:
        return
    ctx.failure_logged = True
    ctx.disabled = True
    _logger.warning("cache_unavailable purpose=%s kind=%s", purpose, kind)


async def _timed[T](ctx: CacheContext, purpose: str, awaitable: Awaitable[T]) -> T | None:
    if ctx.disabled or ctx.transport is None:
        return None
    try:
        async with asyncio.timeout(ROUND_TRIP_TIMEOUT_S):
            return await awaitable
    except TimeoutError:
        _log_failure(ctx, purpose=purpose, kind="timeout")
        return None
    except Exception:
        _log_failure(ctx, purpose=purpose, kind="connection")
        return None


async def _get(ctx: CacheContext, key: str, *, purpose: str) -> bytes | None:
    if ctx.disabled or ctx.transport is None:
        return None
    result = await _timed(ctx, purpose, ctx.transport.get(key))
    if result is None:
        return None
    if not isinstance(result, (bytes, bytearray)):
        _log_failure(ctx, purpose=purpose, kind="malformed")
        return None
    return bytes(result)


async def _mget(
    ctx: CacheContext,
    keys: Sequence[str],
    *,
    purpose: str,
) -> list[bytes | None] | None:
    if ctx.disabled or ctx.transport is None:
        return None
    result = await _timed(ctx, purpose, ctx.transport.mget(keys))
    if result is None:
        return None
    if not isinstance(result, list) or len(result) != len(keys):
        _log_failure(ctx, purpose=purpose, kind="malformed")
        return None
    out: list[bytes | None] = []
    for item in result:
        if item is None:
            out.append(None)
        elif isinstance(item, (bytes, bytearray)):
            out.append(bytes(item))
        else:
            _log_failure(ctx, purpose=purpose, kind="malformed")
            return None
    return out


async def _set(
    ctx: CacheContext,
    key: str,
    payload: Mapping[str, object],
    ttl: int,
    *,
    purpose: str,
) -> None:
    encoded = _encode(payload, purpose=purpose)
    if encoded is None:
        return
    await _set_ex(ctx, [(key, encoded, ttl)], purpose=purpose)


async def _set_ex(
    ctx: CacheContext,
    items: Sequence[tuple[str, bytes, int]],
    *,
    purpose: str,
) -> None:
    if ctx.disabled or ctx.transport is None or not items:
        return
    await _timed(ctx, purpose, ctx.transport.set_ex(items))


def _encode(payload: Mapping[str, object], *, purpose: str) -> bytes | None:
    try:
        dumped = json.dumps(
            payload, allow_nan=False, ensure_ascii=False, default=_json_default
        )
        blob = dumped.encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(blob) > VALUE_MAX_BYTES:
        _logger.warning("cache_write_skipped purpose=%s kind=oversize", purpose)
        return None
    return blob


def _json_default(value: object) -> object:
    raise TypeError("non-json value")


def _decode_object(ctx: CacheContext, raw: bytes, *, purpose: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _log_failure(ctx, purpose=purpose, kind="malformed")
        return None
    if not isinstance(parsed, dict):
        _log_failure(ctx, purpose=purpose, kind="malformed")
        return None
    return parsed


def _raw_hit_from_payload(
    ctx: CacheContext,
    payload: dict[str, object],
    *,
    provider: str,
    since_minutes: int,
) -> RawCacheHit | None:
    cached_since = payload.get("since_minutes")
    observed = _parse_observed(payload.get("observed_at"))
    cached_provider = payload.get("provider")
    mentions_raw = payload.get("mentions")
    if (
        not isinstance(cached_since, int)
        or cached_since < 0
        or observed is None
        or cached_provider != provider
        or not isinstance(mentions_raw, list)
    ):
        _log_failure(ctx, purpose="raw", kind="malformed")
        return None
    if cached_since < since_minutes:
        return None
    mentions: list[SocialMention] = []
    cutoff = observed - timedelta(minutes=since_minutes)
    for item in mentions_raw:
        mention = _mention_from_json(item)
        if mention is None or mention.provider != provider:
            _log_failure(ctx, purpose="raw", kind="malformed")
            return None
        if mention.created_at >= cutoff:
            mentions.append(mention)
    return RawCacheHit(
        mentions=tuple(mentions),
        since_minutes=cached_since,
        observed_at=observed,
        provider=provider,  # type: ignore[arg-type]
    )


def _mention_to_json(mention: SocialMention) -> dict[str, object]:
    metadata = {key: _meta_json(value) for key, value in mention.metadata.items()}
    return {
        "provider": mention.provider,
        "external_id": mention.external_id,
        "asset_id": mention.asset_id,
        "text": mention.text,
        "author_id_hash": mention.author_id_hash,
        "created_at": _iso(mention.created_at),
        "engagement": mention.engagement,
        "url_hash": mention.url_hash,
        "matched_terms": [],
        "match_confidence": 0.0,
        "match_rationale": "",
        "language": mention.language,
        "metadata": metadata,
    }


def _meta_json(value: object) -> object:
    if isinstance(value, tuple):
        return list(value)
    return value


def _mention_from_json(item: object) -> SocialMention | None:
    if not isinstance(item, dict):
        return None
    try:
        created = _parse_observed(item.get("created_at"))
        if created is None:
            return None
        engagement = item.get("engagement")
        if not isinstance(engagement, (int, float)) or isinstance(engagement, bool):
            return None
        if not math.isfinite(float(engagement)):
            return None
        metadata_raw = item.get("metadata") or {}
        if not isinstance(metadata_raw, dict):
            return None
        provider = item.get("provider")
        if provider not in PROVIDERS:
            return None
        return SocialMention(
            provider=provider,
            external_id=str(item["external_id"]),
            asset_id=str(item["asset_id"]),
            text=str(item.get("text") or ""),
            author_id_hash=str(item["author_id_hash"]),
            created_at=created,
            engagement=float(engagement),
            url_hash=item.get("url_hash") if isinstance(item.get("url_hash"), str) else None,
            matched_terms=(),
            match_confidence=0.0,
            match_rationale="",
            language=item.get("language") if isinstance(item.get("language"), str) else None,
            metadata=metadata_raw,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_observed(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_bytes(value: object) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


class _RedisClientTransport:
    """redis.asyncio wrapper. URL is not stored on CacheContext (CCH-20)."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._client = redis.from_url(
            url,
            socket_timeout=ROUND_TRIP_TIMEOUT_S,
            socket_connect_timeout=ROUND_TRIP_TIMEOUT_S,
            decode_responses=False,
        )

    async def get(self, key: str) -> bytes | None:
        value = await self._client.get(key)
        return _as_bytes(value)

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        values = await self._client.mget(list(keys))
        return [_as_bytes(value) for value in values]

    async def set_ex(self, items: Sequence[tuple[str, bytes, int]]) -> None:
        pipe = self._client.pipeline(transaction=False)
        for key, value, ttl in items:
            pipe.set(key, value, ex=ttl)
        await pipe.execute()
