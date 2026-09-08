"""Provider isolation (docs/06, 16). Mocked adapters only — no live network."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from zynost_social.models import (
    AssetIdentity,
    ProviderId,
    SocialMention,
)
from zynost_social.providers.base import (
    BODY_MAX_BYTES,
    MENTIONS_MAX,
    ProviderError,
    SocialProvider,
    TransientProviderError,
    cap_mentions,
    collect_isolated,
    credentials_present,
    hash_identifier,
    hash_url,
    sanitize_text,
    validate_body_size,
)
from zynost_social.providers.discord import DiscordProvider
from zynost_social.providers.reddit import RedditProvider
from zynost_social.providers.telegram import TelegramProvider
from zynost_social.providers.x import XProvider

ASSET = AssetIdentity(asset_id="btc", symbol="BTC", name="Bitcoin")


def _mention(
    *,
    provider: ProviderId = "x",
    external_id: str = "1",
    created_at: datetime | None = None,
    text: str = "bitcoin hash rate",
) -> SocialMention:
    return SocialMention(
        provider=provider,
        external_id=external_id,
        asset_id="btc",
        text=text,
        author_id_hash=hash_identifier("author-1"),
        created_at=created_at or datetime(2026, 9, 8, tzinfo=UTC),
        engagement=0.0,
    )


class FakeProvider(SocialProvider):
    def __init__(
        self,
        provider: ProviderId,
        *,
        mentions: list[SocialMention] | None = None,
        result: object | None = None,
        error: BaseException | None = None,
        delay_s: float = 0.0,
        credential_names: tuple[str, ...] = (),
    ) -> None:
        self._provider = provider
        self._mentions = mentions if mentions is not None else []
        self._result = result
        self._error = error
        self._delay_s = delay_s
        self._credential_names = credential_names
        self.fetch_calls = 0

    @property
    def provider(self) -> ProviderId:
        return self._provider

    @property
    def credential_names(self) -> tuple[str, ...]:
        return self._credential_names

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        self.fetch_calls += 1
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result  # type: ignore[return-value]
        return list(self._mentions)


async def test_success_normalizes_mentions() -> None:
    mention = _mention()
    fake = FakeProvider("x", mentions=[mention])
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.error_class is None
    assert outcome.mentions == (mention,)
    assert outcome.freshness_seconds == 0.0


async def test_empty_list_is_available_not_unavailable() -> None:
    fake = FakeProvider("reddit", mentions=[])
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert outcome.error_class is None


async def test_timeout_does_not_raise() -> None:
    fake = FakeProvider("telegram", delay_s=1.0)
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=0.05)
    assert outcome.status == "unavailable"
    assert outcome.error_class == "timeout"
    assert outcome.mentions == ()


async def test_timeout_retry_then_success() -> None:
    calls = {"n": 0}

    class Flaky(FakeProvider):
        async def fetch_mentions(
            self,
            asset: AssetIdentity,
            since_minutes: int,
        ) -> list[SocialMention]:
            self.fetch_calls += 1
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError
            return [_mention(provider="discord")]

    fake = Flaky("discord")
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.status == "available"
    assert fake.fetch_calls == 2


async def test_crash_becomes_malformed_without_leaking_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeProvider("x", error=RuntimeError("Bearer test-not-a-secret leaked"))
    with caplog.at_level(logging.INFO, logger="zynost_social.providers"):
        outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.status == "unavailable"
    assert outcome.error_class == "malformed"
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "Bearer" not in combined
    assert "test-not-a-secret" not in combined
    assert "Traceback" not in combined


async def test_malformed_return_type() -> None:
    fake = FakeProvider("x", result={"raw": "payload"})
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_unauthorized_from_adapter() -> None:
    fake = FakeProvider("x", error=ProviderError("unauthorized"))
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.error_class == "unauthorized"
    assert outcome.status == "unavailable"


async def test_rate_limited_is_not_retried() -> None:
    fake = FakeProvider("reddit", error=ProviderError("rate_limited"))
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.error_class == "rate_limited"
    assert fake.fetch_calls == 1


async def test_transient_exhausted_maps_to_timeout() -> None:
    fake = FakeProvider("x", error=TransientProviderError())
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.error_class == "timeout"
    assert fake.fetch_calls == 2


async def test_missing_credentials_skips_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_API_KEY", raising=False)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    names = ("X_API_KEY", "X_BEARER_TOKEN")
    fake = FakeProvider("x", mentions=[_mention()], credential_names=names)
    outcome = await collect_isolated(fake, ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.error_class == "not_configured"
    assert fake.fetch_calls == 0


async def test_blank_credential_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_API_KEY", "   ")
    monkeypatch.setenv("X_BEARER_TOKEN", "test-not-a-secret")
    assert credentials_present(("X_API_KEY", "X_BEARER_TOKEN")) is False


async def test_x_adapter_is_not_configured_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_API_KEY", raising=False)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    outcome = await collect_isolated(XProvider(), ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.provider == "x"
    assert outcome.error_class == "not_configured"


async def test_reddit_adapter_is_not_configured_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    outcome = await collect_isolated(RedditProvider(), ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.provider == "reddit"
    assert outcome.error_class == "not_configured"


async def test_telegram_adapter_is_not_configured_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    outcome = await collect_isolated(TelegramProvider(), ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.provider == "telegram"
    assert outcome.error_class == "not_configured"


async def test_discord_adapter_is_not_configured_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    outcome = await collect_isolated(DiscordProvider(), ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.provider == "discord"
    assert outcome.error_class == "not_configured"


async def test_concurrent_timeout_does_not_cancel_siblings() -> None:
    slow = FakeProvider("x", delay_s=1.0)
    fast_reddit = FakeProvider("reddit", mentions=[_mention(provider="reddit")])
    fast_tg = FakeProvider("telegram", mentions=[])
    fast_dc = FakeProvider("discord", mentions=[])
    reddit_out, x_out, tg_out, dc_out = await asyncio.gather(
        collect_isolated(fast_reddit, ASSET, 60, timeout_s=1.0),
        collect_isolated(slow, ASSET, 60, timeout_s=0.05),
        collect_isolated(fast_tg, ASSET, 60, timeout_s=1.0),
        collect_isolated(fast_dc, ASSET, 60, timeout_s=1.0),
    )
    assert reddit_out.status == "available"
    assert x_out.error_class == "timeout"
    assert tg_out.status == "available"
    assert dc_out.status == "available"


def test_sanitize_strips_controls_and_caps() -> None:
    raw = "ok\x00bad\r\nline" + ("x" * 2000)
    cleaned = sanitize_text(raw)
    assert "\x00" not in cleaned
    assert "\r" not in cleaned
    assert "\n" in cleaned
    assert len(cleaned) == 1024


def test_hash_identifier_is_sha256_hex() -> None:
    digest = hash_identifier("alice")
    assert digest == hash_identifier("alice")
    assert len(digest) == 64
    assert digest != hash_identifier("bob")
    assert hash_url(None) is None
    assert hash_url("") is None


def test_body_over_cap_is_malformed() -> None:
    with pytest.raises(ProviderError) as exc:
        validate_body_size(b"x" * (BODY_MAX_BYTES + 1))
    assert exc.value.error_class == "malformed"


def test_cap_mentions_keeps_newest() -> None:
    base = datetime(2026, 9, 8, tzinfo=UTC)
    mentions = [
        _mention(external_id=str(i), created_at=base + timedelta(seconds=i))
        for i in range(MENTIONS_MAX + 5)
    ]
    capped = cap_mentions(mentions)
    assert len(capped) == MENTIONS_MAX
    assert capped[0].external_id == str(MENTIONS_MAX + 4)
