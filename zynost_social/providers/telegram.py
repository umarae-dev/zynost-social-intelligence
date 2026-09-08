"""Telegram public-channel adapter. Mapping: docs/06. Credentials: CFG-03 Telegram set."""

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

API_ROOT = "https://api.telegram.org"
RECENT_LOOKBACK_MAX_MINUTES = 7 * 24 * 60
MAX_RESULTS = 100
MAX_CHANNELS = 4
CHANNEL_URL_PREFIX = "https://t.me/"


@dataclass(frozen=True, slots=True)
class HttpResult:
    status_code: int
    body: bytes


class HttpTransport(Protocol):
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> HttpResult: ...


class HttpxTransport:
    """Default transport. Outer timeout remains collect_isolated (SEC-05)."""

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> HttpResult:
        timeout = httpx.Timeout(10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    url,
                    params=dict(params) if params is not None else None,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc
        except httpx.TransportError as exc:
            raise TransientProviderError from exc
        return HttpResult(status_code=response.status_code, body=response.content)


def parse_public_channel(raw: str) -> str | None:
    """Public channel username only. Invite links and private /c/ ids are dropped (PRV-T-02)."""
    text = raw.strip()
    if not text:
        return None
    lowered = text.lower()
    if "joinchat" in lowered or "t.me/+" in lowered or lowered.startswith("+"):
        return None
    text = text.split("?")[0].split("#")[0].rstrip("/")
    lowered = text.lower()
    if (
        lowered.startswith("https://")
        or lowered.startswith("http://")
        or lowered.startswith("t.me/")
    ):
        path = urlsplit(text if "://" in text else f"https://{text}").path.strip("/")
        if path.lower().startswith("s/"):
            path = path[2:]
        if not path or path.lower().startswith("c/") or "/" in path:
            return None
        text = path
    text = text.lstrip("@").strip()
    if not text or "/" in text or text.startswith("+"):
        return None
    if text.isdigit() or text.lower().startswith("c/"):
        return None
    return text


def official_public_channels(asset: AssetIdentity) -> tuple[str, ...]:
    seen: set[str] = set()
    channels: list[str] = []
    for raw in asset.official_telegram:
        username = parse_public_channel(raw)
        if username is None:
            continue
        key = username.lower()
        if key in seen:
            continue
        seen.add(key)
        channels.append(username)
        if len(channels) >= MAX_CHANNELS:
            break
    return tuple(channels)


def _api_url(token: str, method: str) -> str:
    return f"{API_ROOT}/bot{token}/{method}"


def _finite_nonneg(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number


def _parse_api_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed <= 0:
        return None
    return parsed


def _parse_unix(raw: object) -> datetime | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    stamp = float(raw)
    if not math.isfinite(stamp) or stamp <= 0:
        return None
    return datetime.fromtimestamp(stamp, tz=UTC)


def _engagement(reactions: object) -> float:
    """Sum of native reaction counts (PRV-T-02). Missing/non-finite → 0."""
    if not isinstance(reactions, dict):
        return 0.0
    results = reactions.get("results")
    if not isinstance(results, list):
        return 0.0
    total = 0.0
    for item in results:
        if not isinstance(item, dict):
            continue
        total += _finite_nonneg(item.get("count"))
    return total


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


def _raise_from_error_code(error_code: object) -> None:
    if error_code == 401 or error_code == 403:
        raise ProviderError("unauthorized")
    if error_code == 429:
        raise ProviderError("rate_limited")
    if error_code in (502, 503, 504):
        raise TransientProviderError
    if isinstance(error_code, int) and 400 <= error_code < 500:
        raise ProviderError("malformed")
    raise TransientProviderError


def _ok_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ProviderError("malformed")
    ok = payload.get("ok")
    if ok is True:
        return payload
    if ok is False:
        _raise_from_error_code(payload.get("error_code"))
    raise ProviderError("malformed")


def _chat_username(chat: object) -> str | None:
    if not isinstance(chat, dict):
        return None
    if chat.get("type") != "channel":
        return None
    username = chat.get("username")
    if not isinstance(username, str) or not username.strip():
        return None
    return username.strip()


def _message_text(item: dict[str, object]) -> str | None:
    text = item.get("text")
    caption = item.get("caption")
    if text is None and caption is None:
        return ""
    if text is not None and not isinstance(text, str):
        return None
    if caption is not None and not isinstance(caption, str):
        return None
    parts: list[str] = []
    if isinstance(text, str) and text.strip():
        parts.append(text)
    if isinstance(caption, str) and caption.strip():
        parts.append(caption)
    return "\n".join(parts)


def _author_preimage(item: dict[str, object], chat: dict[str, object]) -> str | None:
    sender = item.get("from")
    if isinstance(sender, dict):
        sender_id = sender.get("id")
        if isinstance(sender_id, int) and not isinstance(sender_id, bool):
            return str(sender_id)
    chat_id = chat.get("id")
    if isinstance(chat_id, int) and not isinstance(chat_id, bool):
        return str(chat_id)
    return None


def _channel_post_to_mention(
    item: object,
    asset_id: str,
    verified: set[str],
    cutoff: datetime,
) -> SocialMention | None:
    if not isinstance(item, dict):
        return None
    chat = item.get("chat")
    if not isinstance(chat, dict):
        return None
    username = _chat_username(chat)
    if username is None or username.lower() not in verified:
        return None
    message_id = item.get("message_id")
    if isinstance(message_id, bool) or not isinstance(message_id, int):
        return None
    created_at = _parse_unix(item.get("date"))
    if created_at is None or created_at < cutoff:
        return None
    text = _message_text(item)
    if text is None:
        return None
    author = _author_preimage(item, chat)
    if author is None:
        return None
    metadata: dict[str, str] = {"channel": username[:64]}
    return SocialMention(
        provider="telegram",
        external_id=f"{chat.get('id')}:{message_id}",
        asset_id=asset_id,
        text=sanitize_text(text),
        author_id_hash=hash_identifier(author),
        created_at=created_at,
        engagement=_engagement(item.get("reactions")),
        url_hash=hash_url(f"{CHANNEL_URL_PREFIX}{username}/{message_id}"),
        metadata=metadata,
    )


class TelegramProvider(SocialProvider):
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
        return "telegram"

    @property
    def credential_names(self) -> tuple[str, ...]:
        return ("TELEGRAM_API_ID", "TELEGRAM_API_HASH")

    async def _get(
        self,
        token: str,
        method: str,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        result = await self._http.get(_api_url(token, method), params=params)
        _map_http_status(result.status_code)
        return _ok_payload(_decode_json(result.body))

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        api_id = _parse_api_id(os.environ.get("TELEGRAM_API_ID"))
        token = os.environ.get("TELEGRAM_API_HASH")
        if api_id is None or token is None or not token.strip():
            raise ProviderError("not_configured")
        bot_token = token.strip()
        lookback = max(1, min(int(since_minutes), RECENT_LOOKBACK_MAX_MINUTES))
        cutoff = (self._now() - timedelta(minutes=lookback)).astimezone(UTC)
        await self._get(bot_token, "getMe")
        channels = official_public_channels(asset)
        if not channels:
            return []
        verified: set[str] = set()
        for username in channels:
            try:
                payload = await self._get(bot_token, "getChat", params={"chat_id": f"@{username}"})
            except ProviderError as exc:
                if exc.error_class == "malformed":
                    continue
                raise
            result = payload.get("result")
            if isinstance(result, dict):
                chat_name = _chat_username(result)
            else:
                chat_name = _chat_username(payload)
            if chat_name is None:
                continue
            verified.add(chat_name.lower())
        if not verified:
            return []
        updates_payload = await self._get(
            bot_token,
            "getUpdates",
            params={
                "timeout": "0",
                "limit": str(MAX_RESULTS),
                "allowed_updates": json.dumps(["channel_post"]),
            },
        )
        raw_updates = updates_payload.get("result", [])
        if raw_updates is None:
            raw_updates = []
        if not isinstance(raw_updates, list):
            raise ProviderError("malformed")
        mentions: list[SocialMention] = []
        for update in raw_updates:
            if not isinstance(update, dict):
                continue
            mention = _channel_post_to_mention(
                update.get("channel_post"),
                asset.asset_id,
                verified,
                cutoff,
            )
            if mention is not None:
                mentions.append(mention)
        return mentions
