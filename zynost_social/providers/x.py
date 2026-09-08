"""X/Twitter adapter. Mapping: docs/06. Credentials: X_API_KEY + X_BEARER_TOKEN."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from zynost_social.models import AssetIdentity, ProviderId, SocialMention
from zynost_social.providers.base import (
    ProviderError,
    SocialProvider,
    TransientProviderError,
    hash_identifier,
    hash_url,
    sanitize_text,
    validate_body_size,
)

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
QUERY_MAX_CHARS = 512
RECENT_LOOKBACK_MAX_MINUTES = 7 * 24 * 60
MAX_RESULTS = 100
TWEET_FIELDS = "author_id,created_at,lang,public_metrics"
STATUS_URL_PREFIX = "https://x.com/i/web/status/"


@dataclass(frozen=True, slots=True)
class HttpGetResult:
    status_code: int
    body: bytes


class HttpGetter(Protocol):
    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
    ) -> HttpGetResult: ...


class HttpxGetter:
    """Default transport. Outer timeout remains collect_isolated (SEC-05)."""

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
    ) -> HttpGetResult:
        timeout = httpx.Timeout(10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers=dict(headers), params=dict(params))
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc
        except httpx.TransportError as exc:
            raise TransientProviderError from exc
        return HttpGetResult(status_code=response.status_code, body=response.content)


def _quote_phrase(value: str) -> str:
    cleaned = value.replace('"', " ").strip()
    if not cleaned:
        return ""
    if any(ch in cleaned for ch in " :()"):
        return f'"{cleaned}"'
    return cleaned


def build_x_query(asset: AssetIdentity) -> str:
    """Multi-signal query. Name is always included so this is never ticker-only (FR-ID-02)."""
    clauses: list[str] = []
    name = _quote_phrase(asset.name)
    if name:
        clauses.append(name)
    for handle in asset.official_x_accounts:
        ident = handle.strip().lstrip("@")
        if ident:
            clauses.append(f"from:{ident}")
    if asset.contract_address:
        quoted = _quote_phrase(asset.contract_address)
        if quoted:
            clauses.append(quoted)
    for alias in asset.aliases:
        quoted = _quote_phrase(alias)
        if quoted:
            clauses.append(quoted)
    symbol = asset.symbol.strip()
    if symbol:
        clauses.append(f"${symbol.upper()}")
    while len(clauses) > 1 and len(" OR ".join(clauses)) > QUERY_MAX_CHARS:
        clauses.pop()
    query = " OR ".join(clauses)
    if not query or len(query) > QUERY_MAX_CHARS:
        raise ProviderError("malformed")
    return query


def _finite_nonneg(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number


def _engagement(metrics: object) -> float:
    """likes + retweets (PRV-X-02). Missing/non-finite counts → 0, never invented virality."""
    if not isinstance(metrics, dict):
        return 0.0
    likes = _finite_nonneg(metrics.get("like_count"))
    retweets = _finite_nonneg(metrics.get("retweet_count"))
    return likes + retweets


def _parse_created_at(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _map_http_status(status_code: int) -> None:
    if status_code == 200:
        return
    if status_code in (401, 403):
        raise ProviderError("unauthorized")
    if status_code == 429:
        raise ProviderError("rate_limited")
    if status_code in (502, 503, 504):
        raise TransientProviderError
    if 400 <= status_code < 500:
        raise ProviderError("malformed")
    raise TransientProviderError


def _tweet_to_mention(item: object, asset_id: str) -> SocialMention | None:
    if not isinstance(item, dict):
        return None
    external_id = item.get("id")
    author_id = item.get("author_id")
    if not isinstance(external_id, str) or not external_id.strip():
        return None
    if not isinstance(author_id, str) or not author_id.strip():
        return None
    created_at = _parse_created_at(item.get("created_at"))
    if created_at is None:
        return None
    raw_text = item.get("text")
    if raw_text is None:
        text = ""
    elif isinstance(raw_text, str):
        text = sanitize_text(raw_text)
    else:
        return None
    language = item.get("lang")
    if isinstance(language, str) and language.strip() and language not in {"und", "zxx"}:
        lang: str | None = language.strip()[:16]
    else:
        lang = None
    tweet_id = external_id.strip()
    return SocialMention(
        provider="x",
        external_id=tweet_id,
        asset_id=asset_id,
        text=text,
        author_id_hash=hash_identifier(author_id.strip()),
        created_at=created_at,
        engagement=_engagement(item.get("public_metrics")),
        url_hash=hash_url(f"{STATUS_URL_PREFIX}{tweet_id}"),
        language=lang,
    )


class XProvider(SocialProvider):
    def __init__(
        self,
        *,
        http_get: HttpGetter | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._http_get: HttpGetter = http_get or HttpxGetter()
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def provider(self) -> ProviderId:
        return "x"

    @property
    def credential_names(self) -> tuple[str, ...]:
        return ("X_API_KEY", "X_BEARER_TOKEN")

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        api_key = os.environ.get("X_API_KEY")
        bearer = os.environ.get("X_BEARER_TOKEN")
        if api_key is None or not api_key.strip() or bearer is None or not bearer.strip():
            raise ProviderError("not_configured")
        lookback = max(1, min(int(since_minutes), RECENT_LOOKBACK_MAX_MINUTES))
        start_time = (self._now() - timedelta(minutes=lookback)).astimezone(UTC)
        start_stamp = start_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        result = await self._http_get.get(
            SEARCH_URL,
            headers={
                "Authorization": f"Bearer {bearer.strip()}",
                "User-Agent": "zynost-social-intelligence",
            },
            params={
                "query": build_x_query(asset),
                "start_time": start_stamp,
                "max_results": str(MAX_RESULTS),
                "tweet.fields": TWEET_FIELDS,
            },
        )
        _map_http_status(result.status_code)
        validate_body_size(result.body)
        try:
            payload: object = json.loads(result.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("malformed") from None
        if not isinstance(payload, dict):
            raise ProviderError("malformed")
        raw_data = payload.get("data", [])
        if raw_data is None:
            raw_data = []
        if not isinstance(raw_data, list):
            raise ProviderError("malformed")
        mentions: list[SocialMention] = []
        for item in raw_data:
            mention = _tweet_to_mention(item, asset.asset_id)
            if mention is not None:
                mentions.append(mention)
        return mentions
