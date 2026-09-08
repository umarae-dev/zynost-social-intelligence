"""Telegram adapter mapping (docs/06). Injected HTTP only — no live network (TST-01)."""

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
from zynost_social.providers.telegram import (
    API_ROOT,
    CHANNEL_URL_PREFIX,
    HttpResult,
    TelegramProvider,
    official_public_channels,
    parse_public_channel,
)

ASSET = AssetIdentity(
    asset_id="btc",
    symbol="BTC",
    name="Bitcoin",
    aliases=("bitcoin",),
    official_telegram=("@bitcoin", "https://t.me/BitcoinNews", "https://t.me/+privateInvite"),
)
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
FAKE_ID = "123456"
FAKE_HASH = "test-not-a-secret"


class FakeTransport:
    def __init__(self, responses: list[HttpResult]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> HttpResult:
        self.calls.append((url, dict(params) if params is not None else None))
        if not self._responses:
            raise AssertionError("unexpected extra HTTP call")
        return self._responses.pop(0)


def _json_body(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _ok(result: object) -> HttpResult:
    return HttpResult(200, _json_body({"ok": True, "result": result}))


def _get_me() -> HttpResult:
    return _ok({"id": 1, "is_bot": True, "username": "zynost_bot"})


def _chat(
    *,
    username: str = "bitcoin",
    chat_id: int = -1001,
    chat_type: str = "channel",
) -> HttpResult:
    return _ok({"id": chat_id, "type": chat_type, "username": username})


def _channel_post(
    *,
    message_id: int = 12,
    date: float = 1757331000.0,
    text: str = "Bitcoin hash rate",
    caption: str | None = None,
    username: str = "bitcoin",
    chat_id: int = -1001,
    from_id: int | None = 42,
    reaction_count: int | None = 5,
) -> dict[str, object]:
    chat: dict[str, object] = {"id": chat_id, "type": "channel", "username": username}
    post: dict[str, object] = {
        "message_id": message_id,
        "date": date,
        "chat": chat,
        "text": text,
    }
    if caption is not None:
        post["caption"] = caption
    if from_id is not None:
        post["from"] = {"id": from_id}
    if reaction_count is not None:
        post["reactions"] = {"results": [{"type": {"type": "emoji"}, "count": reaction_count}]}
    return post


def _updates(posts: list[dict[str, object]]) -> HttpResult:
    return _ok([{"update_id": i, "channel_post": post} for i, post in enumerate(posts, start=1)])


def _provider(transport: FakeTransport) -> TelegramProvider:
    return TelegramProvider(http=transport, now=lambda: NOW)


@pytest.fixture
def telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", FAKE_ID)
    monkeypatch.setenv("TELEGRAM_API_HASH", FAKE_HASH)


def test_public_channels_skip_invites_and_are_not_ticker_only() -> None:
    assert parse_public_channel("https://t.me/+privateInvite") is None
    assert parse_public_channel("https://t.me/joinchat/abc") is None
    assert parse_public_channel("https://t.me/c/123456") is None
    assert parse_public_channel("@bitcoin") == "bitcoin"
    channels = official_public_channels(ASSET)
    assert channels == ("bitcoin", "BitcoinNews")
    assert "BTC" not in channels


async def test_success_maps_mentions_and_hashes(telegram_env: None) -> None:
    created = datetime(2026, 9, 8, 11, 30, tzinfo=UTC).timestamp()
    transport = FakeTransport(
        [
            _get_me(),
            _chat(),
            _chat(username="BitcoinNews", chat_id=-1002),
            _updates(
                [
                    _channel_post(
                        text="bitcoin hash rate\x00ok",
                        date=created,
                        reaction_count=5,
                    )
                ]
            ),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.status == "available"
    assert len(outcome.mentions) == 1
    mention = outcome.mentions[0]
    assert mention.provider == "telegram"
    assert mention.external_id == "-1001:12"
    assert mention.asset_id == "btc"
    assert mention.text == "bitcoin hash rateok"
    assert mention.author_id_hash == hash_identifier("42")
    assert mention.url_hash == hash_url(f"{CHANNEL_URL_PREFIX}bitcoin/12")
    assert mention.engagement == 5.0
    assert mention.metadata["channel"] == "bitcoin"
    assert mention.matched_terms == ()
    assert mention.match_confidence == 0.0
    assert mention.author_id_hash != "42"
    urls = [call[0] for call in transport.calls]
    assert urls[0] == f"{API_ROOT}/bot{FAKE_HASH}/getMe"
    assert urls[1] == f"{API_ROOT}/bot{FAKE_HASH}/getChat"
    assert transport.calls[1][1] == {"chat_id": "@bitcoin"}
    assert urls[-1] == f"{API_ROOT}/bot{FAKE_HASH}/getUpdates"
    assert (transport.calls[-1][1] or {})["timeout"] == "0"


async def test_no_official_channels_is_available_zero(telegram_env: None) -> None:
    asset = AssetIdentity(asset_id="btc", symbol="BTC", name="Bitcoin")
    transport = FakeTransport([_get_me()])
    outcome = await collect_isolated(_provider(transport), asset, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert len(transport.calls) == 1


async def test_empty_updates_is_available_zero(telegram_env: None) -> None:
    transport = FakeTransport(
        [
            _get_me(),
            _chat(),
            _chat(username="BitcoinNews", chat_id=-1002),
            _updates([]),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert outcome.error_class is None


async def test_skips_incomplete_old_and_non_channel_posts(telegram_env: None) -> None:
    in_window = datetime(2026, 9, 8, 11, 30, tzinfo=UTC).timestamp()
    too_old = datetime(2026, 9, 8, 10, 0, tzinfo=UTC).timestamp()
    transport = FakeTransport(
        [
            _get_me(),
            _chat(),
            _chat(username="BitcoinNews", chat_id=-1002),
            HttpResult(
                200,
                _json_body(
                    {
                        "ok": True,
                        "result": [
                            {"update_id": 1, "channel_post": {"message_id": 1, "text": "no chat"}},
                            {
                                "update_id": 2,
                                "channel_post": _channel_post(message_id=2, date=too_old),
                            },
                            {
                                "update_id": 3,
                                "message": {
                                    "message_id": 3,
                                    "date": in_window,
                                    "chat": {"id": 9, "type": "private"},
                                    "text": "ignore private",
                                },
                            },
                            {
                                "update_id": 4,
                                "channel_post": _channel_post(
                                    message_id=4,
                                    date=in_window,
                                    reaction_count=3,
                                ),
                            },
                        ],
                    }
                ),
            ),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert [m.external_id for m in outcome.mentions] == ["-1001:4"]
    assert outcome.mentions[0].engagement == 3.0


async def test_skips_getchat_not_found_and_still_fetches(telegram_env: None) -> None:
    created = datetime(2026, 9, 8, 11, 30, tzinfo=UTC).timestamp()
    transport = FakeTransport(
        [
            _get_me(),
            HttpResult(
                200,
                _json_body({"ok": False, "error_code": 400, "description": "chat not found"}),
            ),
            _chat(username="BitcoinNews", chat_id=-1002),
            _updates(
                [
                    _channel_post(
                        username="BitcoinNews",
                        chat_id=-1002,
                        date=created,
                        reaction_count=1,
                    )
                ]
            ),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert [m.external_id for m in outcome.mentions] == ["-1002:12"]


async def test_unparseable_api_id_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "not-a-number")
    monkeypatch.setenv("TELEGRAM_API_HASH", FAKE_HASH)
    transport = FakeTransport([])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "not_configured"
    assert transport.calls == []


async def test_unauthorized_on_401(telegram_env: None) -> None:
    transport = FakeTransport([HttpResult(401, b'{"ok":false}')])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "unavailable"
    assert outcome.error_class == "unauthorized"
    assert outcome.mentions == ()
    assert len(transport.calls) == 1


async def test_unauthorized_on_ok_false_401(telegram_env: None) -> None:
    transport = FakeTransport(
        [
            HttpResult(
                200,
                _json_body({"ok": False, "error_code": 401, "description": "Unauthorized"}),
            )
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "unauthorized"


async def test_rate_limited_on_429(telegram_env: None) -> None:
    transport = FakeTransport([_get_me(), HttpResult(429, b"slow down")])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "rate_limited"


async def test_malformed_json(telegram_env: None) -> None:
    transport = FakeTransport([HttpResult(200, b"not-json")])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_oversized_body_is_malformed(telegram_env: None) -> None:
    transport = FakeTransport([HttpResult(200, b"x" * (BODY_MAX_BYTES + 1))])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_502_retries_then_timeout(telegram_env: None) -> None:
    transport = FakeTransport(
        [
            HttpResult(502, b"bad gateway"),
            HttpResult(502, b"bad gateway"),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=2.0)
    assert outcome.error_class == "timeout"
    assert len(transport.calls) == 2


async def test_502_then_success(telegram_env: None) -> None:
    transport = FakeTransport(
        [
            HttpResult(502, b"bad gateway"),
            _get_me(),
            _chat(),
            _chat(username="BitcoinNews", chat_id=-1002),
            _updates([]),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=2.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert len(transport.calls) == 5
