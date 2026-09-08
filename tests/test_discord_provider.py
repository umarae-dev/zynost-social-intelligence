"""Discord adapter mapping (docs/06). Injected HTTP only — no live network (TST-01)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from zynost_social.models import AssetIdentity
from zynost_social.providers.base import (
    BODY_MAX_BYTES,
    collect_isolated,
    hash_identifier,
    hash_url,
)
from zynost_social.providers.discord import (
    API_ROOT,
    CHANNEL_URL_PREFIX,
    DiscordProvider,
    HttpGetResult,
    official_authorized_channels,
    parse_authorized_channel,
)

GUILD = "123456789012345678"
CHANNEL = "234567890123456789"
CHANNEL_B = "345678901234567890"
ASSET = AssetIdentity(
    asset_id="btc",
    symbol="BTC",
    name="Bitcoin",
    aliases=("bitcoin",),
    official_discord=(
        f"https://discord.com/channels/{GUILD}/{CHANNEL}",
        CHANNEL_B,
        "https://discord.gg/privateInvite",
        "https://discord.com/invite/nope",
        "https://discord.com/channels/@me/999999999999999999",
    ),
)
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
FAKE_TOKEN = "test-not-a-secret"


class FakeGetter:
    def __init__(self, responses: list[HttpGetResult]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict[str, str] | None]] = []

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
    ) -> HttpGetResult:
        self.calls.append((url, dict(headers), dict(params) if params is not None else None))
        if not self._responses:
            raise AssertionError("unexpected extra HTTP call")
        return self._responses.pop(0)


def _json_body(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _ok(payload: object) -> HttpGetResult:
    return HttpGetResult(200, _json_body(payload))


def _me() -> HttpGetResult:
    return _ok({"id": "111111111111111111", "username": "zynost_bot", "bot": True})


def _channel(
    *,
    channel_id: str = CHANNEL,
    guild_id: str = GUILD,
    channel_type: int = 0,
) -> HttpGetResult:
    return _ok({"id": channel_id, "guild_id": guild_id, "type": channel_type, "name": "announce"})


def _message(
    *,
    message_id: str = "456789012345678901",
    timestamp: str = "2026-09-08T11:30:00.000000+00:00",
    content: str = "Bitcoin hash rate",
    author_id: str = "987654321098765432",
    channel_id: str = CHANNEL,
    reaction_count: int | None = 5,
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": message_id,
        "channel_id": channel_id,
        "timestamp": timestamp,
        "content": content,
        "author": {"id": author_id},
    }
    if reaction_count is not None:
        item["reactions"] = [{"count": reaction_count, "emoji": {"name": "fire"}}]
    return item


def _provider(getter: FakeGetter) -> DiscordProvider:
    return DiscordProvider(http_get=getter, now=lambda: NOW)


@pytest.fixture
def discord_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)


def test_authorized_channels_skip_invites_dms_and_are_not_ticker_only() -> None:
    assert parse_authorized_channel("https://discord.gg/privateInvite") is None
    assert parse_authorized_channel("https://discord.com/invite/nope") is None
    assert parse_authorized_channel("https://discord.com/channels/@me/999999999999999999") is None
    assert parse_authorized_channel(f"https://discord.com/channels/{GUILD}/{CHANNEL}") == CHANNEL
    assert parse_authorized_channel(CHANNEL_B) == CHANNEL_B
    channels = official_authorized_channels(ASSET)
    assert channels == (CHANNEL, CHANNEL_B)
    assert "BTC" not in channels


async def test_success_maps_mentions_and_hashes(discord_env: None) -> None:
    getter = FakeGetter(
        [
            _me(),
            _channel(),
            _ok([_message(content="bitcoin hash rate\x00ok")]),
            _channel(channel_id=CHANNEL_B, guild_id="111111111111111111"),
            _ok([]),
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.status == "available"
    assert len(outcome.mentions) == 1
    mention = outcome.mentions[0]
    assert mention.provider == "discord"
    assert mention.external_id == "456789012345678901"
    assert mention.asset_id == "btc"
    assert mention.text == "bitcoin hash rateok"
    assert mention.author_id_hash == hash_identifier("987654321098765432")
    assert mention.url_hash == hash_url(
        f"{CHANNEL_URL_PREFIX}{GUILD}/{CHANNEL}/456789012345678901"
    )
    assert mention.engagement == 5.0
    assert mention.metadata["channel_id"] == CHANNEL
    assert mention.metadata["guild_id"] == GUILD
    assert mention.matched_terms == ()
    assert mention.match_confidence == 0.0
    assert mention.author_id_hash != "987654321098765432"
    urls = [call[0] for call in getter.calls]
    assert urls[0] == f"{API_ROOT}/users/@me"
    assert urls[1] == f"{API_ROOT}/channels/{CHANNEL}"
    assert urls[2] == f"{API_ROOT}/channels/{CHANNEL}/messages"
    assert getter.calls[2][2] == {"limit": "100"}
    assert getter.calls[0][1]["Authorization"] == f"Bot {FAKE_TOKEN}"
    assert "BTC" not in "".join(urls)


async def test_no_official_channels_is_available_zero(discord_env: None) -> None:
    asset = AssetIdentity(asset_id="btc", symbol="BTC", name="Bitcoin")
    getter = FakeGetter([_me()])
    outcome = await collect_isolated(_provider(getter), asset, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert len(getter.calls) == 1


async def test_empty_messages_is_available_zero(discord_env: None) -> None:
    getter = FakeGetter(
        [
            _me(),
            _channel(),
            _ok([]),
            _channel(channel_id=CHANNEL_B),
            _ok([]),
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert outcome.error_class is None


async def test_skips_incomplete_old_and_non_guild_text(discord_env: None) -> None:
    getter = FakeGetter(
        [
            _me(),
            _channel(),
            _ok(
                [
                    {"id": "1"},
                    _message(
                        message_id="222222222222222222",
                        timestamp="2026-09-08T10:00:00.000000+00:00",
                    ),
                    _message(
                        message_id="333333333333333333",
                        timestamp="2026-09-08T11:30:00.000000+00:00",
                        reaction_count=3,
                    ),
                ]
            ),
            HttpGetResult(200, _json_body({"id": CHANNEL_B, "type": 1, "guild_id": GUILD})),
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert [m.external_id for m in outcome.mentions] == ["333333333333333333"]
    assert outcome.mentions[0].engagement == 3.0


async def test_skips_inaccessible_channel_and_still_fetches(discord_env: None) -> None:
    getter = FakeGetter(
        [
            _me(),
            HttpGetResult(404, b'{"message":"Unknown Channel","code":10003}'),
            _channel(channel_id=CHANNEL_B, guild_id="111111111111111111"),
            _ok(
                [
                    _message(
                        channel_id=CHANNEL_B,
                        reaction_count=1,
                    )
                ]
            ),
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert [m.external_id for m in outcome.mentions] == ["456789012345678901"]
    assert outcome.mentions[0].url_hash == hash_url(
        f"{CHANNEL_URL_PREFIX}111111111111111111/{CHANNEL_B}/456789012345678901"
    )


async def test_unauthorized_on_401(discord_env: None) -> None:
    getter = FakeGetter([HttpGetResult(401, b'{"message":"401: Unauthorized"}')])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "unavailable"
    assert outcome.error_class == "unauthorized"
    assert outcome.mentions == ()
    assert len(getter.calls) == 1


async def test_rate_limited_on_429(discord_env: None) -> None:
    getter = FakeGetter([_me(), HttpGetResult(429, b"slow down")])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "rate_limited"


async def test_malformed_json(discord_env: None) -> None:
    getter = FakeGetter([HttpGetResult(200, b"not-json")])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_oversized_body_is_malformed(discord_env: None) -> None:
    getter = FakeGetter([HttpGetResult(200, b"x" * (BODY_MAX_BYTES + 1))])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_502_retries_then_timeout(discord_env: None) -> None:
    getter = FakeGetter(
        [
            HttpGetResult(502, b"bad gateway"),
            HttpGetResult(502, b"bad gateway"),
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=2.0)
    assert outcome.error_class == "timeout"
    assert len(getter.calls) == 2


async def test_502_then_success(discord_env: None) -> None:
    getter = FakeGetter(
        [
            HttpGetResult(502, b"bad gateway"),
            _me(),
            _channel(),
            _ok([]),
            _channel(channel_id=CHANNEL_B),
            _ok([]),
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=2.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert len(getter.calls) == 6
