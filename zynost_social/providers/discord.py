"""Discord authorized-API adapter. Mapping: docs/06. Credentials: CFG-03 Discord set."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

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

API_ROOT = "https://discord.com/api/v10"
CHANNEL_URL_PREFIX = "https://discord.com/channels/"
RECENT_LOOKBACK_MAX_MINUTES = 7 * 24 * 60
MAX_RESULTS = 100
MAX_CHANNELS = 4
GUILD_TEXT = 0
GUILD_ANNOUNCEMENT = 5
ALLOWED_CHANNEL_TYPES = frozenset({GUILD_TEXT, GUILD_ANNOUNCEMENT})
SNOWFLAKE_MIN_LEN = 17
SNOWFLAKE_MAX_LEN = 20


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
        params: Mapping[str, str] | None = None,
    ) -> HttpGetResult: ...


class HttpxGetter:
    """Default transport. Outer timeout remains collect_isolated (SEC-05)."""

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
    ) -> HttpGetResult:
        timeout = httpx.Timeout(10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    url,
                    headers=dict(headers),
                    params=dict(params) if params is not None else None,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc
        except httpx.TransportError as exc:
            raise TransientProviderError from exc
        return HttpGetResult(status_code=response.status_code, body=response.content)


def _is_snowflake(value: str) -> bool:
    if not value.isdigit():
        return False
    return SNOWFLAKE_MIN_LEN <= len(value) <= SNOWFLAKE_MAX_LEN


def parse_authorized_channel(raw: str) -> str | None:
    """Channel snowflake or /channels/{guild}/{channel} URL. Invites and DMs dropped (PRV-D-02)."""
    text = raw.strip()
    if not text:
        return None
    lowered = text.lower()
    if "discord.gg/" in lowered or "/invite/" in lowered or lowered.startswith("discord.gg/"):
        return None
    text = text.split("?")[0].split("#")[0].rstrip("/")
    lowered = text.lower()
    if (
        lowered.startswith("https://")
        or lowered.startswith("http://")
        or lowered.startswith("discord.com/")
        or lowered.startswith("discordapp.com/")
    ):
        parsed = urlsplit(text if "://" in text else f"https://{text}")
        host = parsed.netloc.lower()
        if host not in ("discord.com", "www.discord.com", "discordapp.com", "www.discordapp.com"):
            return None
        parts = [p for p in parsed.path.split("/") if p]
        lowered_parts = [p.lower() for p in parts]
        if "invite" in lowered_parts or not parts:
            return None
        if lowered_parts[0] != "channels" or len(parts) < 3:
            return None
        guild, channel = parts[1], parts[2]
        if guild.lower() == "@me":
            return None
        if not _is_snowflake(guild) or not _is_snowflake(channel):
            return None
        return channel
    if _is_snowflake(text):
        return text
    return None


def official_authorized_channels(asset: AssetIdentity) -> tuple[str, ...]:
    seen: set[str] = set()
    channels: list[str] = []
    for raw in asset.official_discord:
        channel_id = parse_authorized_channel(raw)
        if channel_id is None:
            continue
        if channel_id in seen:
            continue
        seen.add(channel_id)
        channels.append(channel_id)
        if len(channels) >= MAX_CHANNELS:
            break
    return tuple(channels)


def _finite_nonneg(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number


def _engagement(reactions: object) -> float:
    """Sum of native reaction counts (PRV-D-02). Missing/non-finite → 0."""
    if not isinstance(reactions, list):
        return 0.0
    total = 0.0
    for item in reactions:
        if not isinstance(item, dict):
            continue
        total += _finite_nonneg(item.get("count"))
    return total


def _parse_iso(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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


def _skip_channel_http(status_code: int) -> bool:
    """Missing access / unknown channel: skip identity, do not fail the fetch (PRV-D-02)."""
    if status_code in (401, 429) or status_code < 400 or status_code >= 500:
        return False
    return 400 <= status_code < 500


def _verified_channel(payload: object) -> tuple[str, str] | None:
    if not isinstance(payload, dict):
        return None
    channel_type = payload.get("type")
    if channel_type not in ALLOWED_CHANNEL_TYPES:
        return None
    channel_id = payload.get("id")
    guild_id = payload.get("guild_id")
    if not isinstance(channel_id, str) or not _is_snowflake(channel_id):
        return None
    if not isinstance(guild_id, str) or not _is_snowflake(guild_id):
        return None
    return guild_id, channel_id


def _author_id(item: dict[str, object]) -> str | None:
    author = item.get("author")
    if not isinstance(author, dict):
        return None
    author_id = author.get("id")
    if not isinstance(author_id, str) or not _is_snowflake(author_id):
        return None
    return author_id


def _message_text(item: dict[str, object]) -> str | None:
    content = item.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        return None
    return content


def _message_to_mention(
    item: object,
    asset_id: str,
    guild_id: str,
    channel_id: str,
    cutoff: datetime,
) -> SocialMention | None:
    if not isinstance(item, dict):
        return None
    message_id = item.get("id")
    if not isinstance(message_id, str) or not _is_snowflake(message_id):
        return None
    msg_channel = item.get("channel_id")
    if isinstance(msg_channel, str) and msg_channel != channel_id:
        return None
    created_at = _parse_iso(item.get("timestamp"))
    if created_at is None or created_at < cutoff:
        return None
    text = _message_text(item)
    if text is None:
        return None
    author = _author_id(item)
    if author is None:
        return None
    metadata: dict[str, str] = {"channel_id": channel_id[:64], "guild_id": guild_id[:64]}
    return SocialMention(
        provider="discord",
        external_id=message_id,
        asset_id=asset_id,
        text=sanitize_text(text),
        author_id_hash=hash_identifier(author),
        created_at=created_at,
        engagement=_engagement(item.get("reactions")),
        url_hash=hash_url(f"{CHANNEL_URL_PREFIX}{guild_id}/{channel_id}/{message_id}"),
        metadata=metadata,
    )


class DiscordProvider(SocialProvider):
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
        return "discord"

    @property
    def credential_names(self) -> tuple[str, ...]:
        return ("DISCORD_BOT_TOKEN",)

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bot {token}",
            "User-Agent": "zynost-social-intelligence",
        }

    async def _get(
        self,
        token: str,
        path: str,
        params: Mapping[str, str] | None = None,
    ) -> HttpGetResult:
        return await self._http_get.get(
            f"{API_ROOT}{path}",
            headers=self._headers(token),
            params=params,
        )

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        token = os.environ.get("DISCORD_BOT_TOKEN")
        if token is None or not token.strip():
            raise ProviderError("not_configured")
        bot_token = token.strip()
        lookback = max(1, min(int(since_minutes), RECENT_LOOKBACK_MAX_MINUTES))
        cutoff = (self._now() - timedelta(minutes=lookback)).astimezone(UTC)
        me = await self._get(bot_token, "/users/@me")
        _map_http_status(me.status_code)
        me_payload = _decode_json(me.body)
        if not isinstance(me_payload, dict) or not isinstance(me_payload.get("id"), str):
            raise ProviderError("malformed")
        channels = official_authorized_channels(asset)
        if not channels:
            return []
        mentions: list[SocialMention] = []
        for channel_id in channels:
            channel_result = await self._get(bot_token, f"/channels/{channel_id}")
            if _skip_channel_http(channel_result.status_code):
                continue
            _map_http_status(channel_result.status_code)
            verified = _verified_channel(_decode_json(channel_result.body))
            if verified is None:
                continue
            guild_id, verified_id = verified
            messages_result = await self._get(
                bot_token,
                f"/channels/{verified_id}/messages",
                params={"limit": str(MAX_RESULTS)},
            )
            if _skip_channel_http(messages_result.status_code):
                continue
            _map_http_status(messages_result.status_code)
            raw_messages = _decode_json(messages_result.body)
            if not isinstance(raw_messages, list):
                raise ProviderError("malformed")
            for item in raw_messages:
                mention = _message_to_mention(
                    item,
                    asset.asset_id,
                    guild_id,
                    verified_id,
                    cutoff,
                )
                if mention is not None:
                    mentions.append(mention)
        return mentions
