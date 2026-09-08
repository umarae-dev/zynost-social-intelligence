"""X adapter mapping (docs/06). Injected HTTP only — no live network (TST-01)."""

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
from zynost_social.providers.x import (
    SEARCH_URL,
    STATUS_URL_PREFIX,
    HttpGetResult,
    XProvider,
    build_x_query,
)

ASSET = AssetIdentity(
    asset_id="btc",
    symbol="BTC",
    name="Bitcoin",
    aliases=("bitcoin",),
    official_x_accounts=("@bitcoin",),
)
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
FAKE_KEY = "test-not-a-secret"
FAKE_BEARER = "test-not-a-secret"


class FakeGetter:
    def __init__(self, responses: list[HttpGetResult]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
    ) -> HttpGetResult:
        self.calls.append((url, dict(headers), dict(params)))
        if not self._responses:
            raise AssertionError("unexpected extra HTTP call")
        return self._responses.pop(0)


def _json_body(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _provider(getter: FakeGetter) -> XProvider:
    return XProvider(http_get=getter, now=lambda: NOW)


@pytest.fixture
def x_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_API_KEY", FAKE_KEY)
    monkeypatch.setenv("X_BEARER_TOKEN", FAKE_BEARER)


def test_query_is_never_ticker_only() -> None:
    query = build_x_query(ASSET)
    assert "Bitcoin" in query
    assert "from:bitcoin" in query
    assert "$BTC" in query
    assert query != "$BTC"


async def test_success_maps_mentions_and_hashes(x_env: None) -> None:
    getter = FakeGetter(
        [
            HttpGetResult(
                200,
                _json_body(
                    {
                        "data": [
                            {
                                "id": "1001",
                                "author_id": "42",
                                "text": "bitcoin hash rate\x00ok",
                                "created_at": "2026-09-08T11:30:00.000Z",
                                "lang": "en",
                                "public_metrics": {
                                    "like_count": 10,
                                    "retweet_count": 3,
                                    "reply_count": 99,
                                },
                            }
                        ]
                    }
                ),
            )
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.status == "available"
    assert len(outcome.mentions) == 1
    mention = outcome.mentions[0]
    assert mention.provider == "x"
    assert mention.external_id == "1001"
    assert mention.asset_id == "btc"
    assert mention.text == "bitcoin hash rateok"
    assert mention.author_id_hash == hash_identifier("42")
    assert mention.url_hash == hash_url(f"{STATUS_URL_PREFIX}1001")
    assert mention.engagement == 13.0
    assert mention.language == "en"
    assert mention.matched_terms == ()
    assert mention.match_confidence == 0.0
    assert mention.match_rationale == ""
    url, headers, params = getter.calls[0]
    assert url == SEARCH_URL
    assert headers["Authorization"] == f"Bearer {FAKE_BEARER}"
    assert "Bitcoin" in params["query"]
    assert params["start_time"] == "2026-09-08T11:00:00Z"
    assert mention.author_id_hash != "42"


async def test_empty_data_is_available_zero(x_env: None) -> None:
    getter = FakeGetter([HttpGetResult(200, _json_body({"meta": {"result_count": 0}}))])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert outcome.error_class is None


async def test_skips_incomplete_items_in_valid_envelope(x_env: None) -> None:
    getter = FakeGetter(
        [
            HttpGetResult(
                200,
                _json_body(
                    {
                        "data": [
                            {"id": "1", "text": "no author"},
                            {
                                "id": "2",
                                "author_id": "9",
                                "text": "ok",
                                "created_at": "2026-09-08T11:00:00Z",
                            },
                        ]
                    }
                ),
            )
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert [m.external_id for m in outcome.mentions] == ["2"]
    assert outcome.mentions[0].engagement == 0.0


async def test_unauthorized_on_401(x_env: None) -> None:
    getter = FakeGetter([HttpGetResult(401, b'{"title":"Unauthorized"}')])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "unavailable"
    assert outcome.error_class == "unauthorized"
    assert outcome.mentions == ()


async def test_rate_limited_on_429(x_env: None) -> None:
    getter = FakeGetter([HttpGetResult(429, b"slow down")])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "rate_limited"
    assert len(getter.calls) == 1


async def test_malformed_json(x_env: None) -> None:
    getter = FakeGetter([HttpGetResult(200, b"not-json")])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_oversized_body_is_malformed(x_env: None) -> None:
    getter = FakeGetter([HttpGetResult(200, b"x" * (BODY_MAX_BYTES + 1))])
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_502_retries_then_timeout(x_env: None) -> None:
    getter = FakeGetter(
        [
            HttpGetResult(502, b"bad gateway"),
            HttpGetResult(502, b"bad gateway"),
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=2.0)
    assert outcome.error_class == "timeout"
    assert len(getter.calls) == 2


async def test_502_then_success(x_env: None) -> None:
    getter = FakeGetter(
        [
            HttpGetResult(502, b"bad gateway"),
            HttpGetResult(200, _json_body({"data": []})),
        ]
    )
    outcome = await collect_isolated(_provider(getter), ASSET, 60, timeout_s=2.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert len(getter.calls) == 2
