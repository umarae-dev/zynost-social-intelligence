"""Reddit adapter. Mapping: docs/06. Credentials: CFG-03 Reddit set."""

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

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/search"
QUERY_MAX_CHARS = 512
RECENT_LOOKBACK_MAX_MINUTES = 7 * 24 * 60
MAX_RESULTS = 100
REDDIT_POST_PREFIX = "https://www.reddit.com"


@dataclass(frozen=True, slots=True)
class HttpResult:
    status_code: int
    body: bytes


class HttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> HttpResult: ...


class HttpxTransport:
    """Default transport. Outer timeout remains collect_isolated (SEC-05)."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> HttpResult:
        timeout = httpx.Timeout(10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=dict(headers),
                    params=dict(params) if params is not None else None,
                    data=dict(data) if data is not None else None,
                    auth=auth,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc
        except httpx.TransportError as exc:
            raise TransientProviderError from exc
        return HttpResult(status_code=response.status_code, body=response.content)


def _quote_phrase(value: str) -> str:
    cleaned = value.replace('"', " ").strip()
    if not cleaned:
        return ""
    if any(ch in cleaned for ch in " :()"):
        return f'"{cleaned}"'
    return cleaned


def _official_reddit_clause(raw: str) -> str:
    ident = raw.strip()
    if not ident:
        return ""
    lowered = ident.lower()
    if lowered.startswith(("u/", "/u/", "user/")):
        name = ident.rsplit("/", 1)[-1].strip().lstrip("@")
        return f"author:{name}" if name else ""
    if lowered.startswith(("r/", "/r/")):
        name = ident.rsplit("/", 1)[-1].strip()
        return f"subreddit:{name}" if name else ""
    name = ident.lstrip("/").strip()
    return f"subreddit:{name}" if name else ""


def build_reddit_query(asset: AssetIdentity) -> str:
    """Multi-signal query. Name is always included so this is never ticker-only (FR-ID-02)."""
    clauses: list[str] = []
    name = _quote_phrase(asset.name)
    if name:
        clauses.append(name)
    for identity in asset.official_reddit:
        clause = _official_reddit_clause(identity)
        if clause:
            clauses.append(clause)
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
        clauses.append(symbol.upper())
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


def _parse_created_utc(raw: object) -> datetime | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    stamp = float(raw)
    if not math.isfinite(stamp) or stamp <= 0:
        return None
    return datetime.fromtimestamp(stamp, tz=UTC)


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


def _decode_json(body: bytes) -> object:
    validate_body_size(body)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderError("malformed") from None


def _permalink_url(permalink: object) -> str | None:
    if not isinstance(permalink, str) or not permalink.strip():
        return None
    path = permalink.strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{REDDIT_POST_PREFIX}{path}"


def _post_to_mention(
    child: object,
    asset_id: str,
    cutoff: datetime,
) -> SocialMention | None:
    if not isinstance(child, dict):
        return None
    item = child.get("data", child)
    if not isinstance(item, dict):
        return None
    external_id = item.get("id")
    author_id = item.get("author_fullname")
    if not isinstance(external_id, str) or not external_id.strip():
        return None
    if not isinstance(author_id, str) or not author_id.strip():
        return None
    created_at = _parse_created_utc(item.get("created_utc"))
    if created_at is None or created_at < cutoff:
        return None
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    selftext = item.get("selftext")
    if selftext is None:
        body = ""
    elif isinstance(selftext, str):
        body = selftext
    else:
        return None
    combined = title.strip() if not body.strip() else f"{title.strip()}\n{body}"
    subreddit = item.get("subreddit")
    metadata: dict[str, str] = {}
    if isinstance(subreddit, str) and subreddit.strip():
        metadata["subreddit"] = subreddit.strip()
    return SocialMention(
        provider="reddit",
        external_id=external_id.strip(),
        asset_id=asset_id,
        text=sanitize_text(combined),
        author_id_hash=hash_identifier(author_id.strip()),
        created_at=created_at,
        engagement=_finite_nonneg(item.get("score")),
        url_hash=hash_url(_permalink_url(item.get("permalink"))),
        metadata=metadata,
    )


class RedditProvider(SocialProvider):
    def __init__(
        self,
        *,
        http: HttpTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._http: HttpTransport = http or HttpxTransport()
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def provider(self) -> ProviderId:
        return "reddit"

    @property
    def credential_names(self) -> tuple[str, ...]:
        return ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        client_id = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        user_agent = os.environ.get("REDDIT_USER_AGENT")
        if (
            client_id is None
            or not client_id.strip()
            or client_secret is None
            or not client_secret.strip()
            or user_agent is None
            or not user_agent.strip()
        ):
            raise ProviderError("not_configured")
        lookback = max(1, min(int(since_minutes), RECENT_LOOKBACK_MAX_MINUTES))
        cutoff = (self._now() - timedelta(minutes=lookback)).astimezone(UTC)
        ua = user_agent.strip()
        token_result = await self._http.request(
            "POST",
            TOKEN_URL,
            headers={"User-Agent": ua},
            data={"grant_type": "client_credentials"},
            auth=(client_id.strip(), client_secret.strip()),
        )
        _map_http_status(token_result.status_code)
        token_payload = _decode_json(token_result.body)
        if not isinstance(token_payload, dict):
            raise ProviderError("malformed")
        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ProviderError("malformed")
        search_result = await self._http.request(
            "GET",
            SEARCH_URL,
            headers={
                "Authorization": f"Bearer {access_token.strip()}",
                "User-Agent": ua,
            },
            params={
                "q": build_reddit_query(asset),
                "sort": "new",
                "limit": str(MAX_RESULTS),
                "type": "link",
                "raw_json": "1",
            },
        )
        _map_http_status(search_result.status_code)
        payload = _decode_json(search_result.body)
        if not isinstance(payload, dict):
            raise ProviderError("malformed")
        listing = payload.get("data", {})
        if listing is None:
            listing = {}
        if not isinstance(listing, dict):
            raise ProviderError("malformed")
        children = listing.get("children", [])
        if children is None:
            children = []
        if not isinstance(children, list):
            raise ProviderError("malformed")
        mentions: list[SocialMention] = []
        for child in children:
            mention = _post_to_mention(child, asset.asset_id, cutoff)
            if mention is not None:
                mentions.append(mention)
        return mentions
