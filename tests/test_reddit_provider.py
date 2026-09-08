"""Reddit adapter mapping (docs/06). Injected HTTP only — no live network (TST-01)."""

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
from zynost_social.providers.reddit import (
    REDDIT_POST_PREFIX,
    SEARCH_URL,
    TOKEN_URL,
    HttpResult,
    RedditProvider,
    build_reddit_query,
)

ASSET = AssetIdentity(
    asset_id="btc",
    symbol="BTC",
    name="Bitcoin",
    aliases=("bitcoin",),
    official_reddit=("r/bitcoin", "u/bitcoinorg"),
)
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
FAKE_ID = "test-not-a-secret"
FAKE_SECRET = "test-not-a-secret"
FAKE_UA = "zynost-social-intelligence/0.1 by test"


class FakeTransport:
    def __init__(self, responses: list[HttpResult]) -> None:
        self._responses = list(responses)
        self.calls: list[
            tuple[
                str,
                str,
                dict[str, str],
                dict[str, str] | None,
                dict[str, str] | None,
                tuple[str, str] | None,
            ]
        ] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> HttpResult:
        self.calls.append(
            (
                method,
                url,
                dict(headers),
                dict(params) if params is not None else None,
                dict(data) if data is not None else None,
                auth,
            )
        )
        if not self._responses:
            raise AssertionError("unexpected extra HTTP call")
        return self._responses.pop(0)


def _json_body(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _token_ok() -> HttpResult:
    return HttpResult(
        200,
        _json_body({"access_token": "test-not-a-secret", "token_type": "bearer"}),
    )


def _listing(children: list[object] | None) -> HttpResult:
    payload: dict[str, object] = {"kind": "Listing", "data": {}}
    if children is not None:
        payload["data"] = {"children": children}
    return HttpResult(200, _json_body(payload))


def _child(
    *,
    post_id: str = "abc123",
    author_fullname: str = "t2_42",
    title: str = "Bitcoin hash rate",
    selftext: str = "ok",
    created_utc: float = 1757331000.0,
    score: int = 12,
    permalink: str = "/r/bitcoin/comments/abc123/bitcoin_hash_rate/",
    subreddit: str = "bitcoin",
) -> dict[str, object]:
    return {
        "kind": "t3",
        "data": {
            "id": post_id,
            "author_fullname": author_fullname,
            "title": title,
            "selftext": selftext,
            "created_utc": created_utc,
            "score": score,
            "permalink": permalink,
            "subreddit": subreddit,
        },
    }


def _provider(transport: FakeTransport) -> RedditProvider:
    return RedditProvider(http=transport, now=lambda: NOW)


@pytest.fixture
def reddit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", FAKE_ID)
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", FAKE_SECRET)
    monkeypatch.setenv("REDDIT_USER_AGENT", FAKE_UA)


def test_query_is_never_ticker_only() -> None:
    query = build_reddit_query(ASSET)
    assert "Bitcoin" in query
    assert "subreddit:bitcoin" in query
    assert "author:bitcoinorg" in query
    assert "BTC" in query
    assert query != "BTC"


async def test_success_maps_mentions_and_hashes(reddit_env: None) -> None:
    created = datetime(2026, 9, 8, 11, 30, tzinfo=UTC).timestamp()
    transport = FakeTransport(
        [
            _token_ok(),
            _listing(
                [
                    _child(
                        title="bitcoin hash rate\x00ok",
                        selftext="more",
                        created_utc=created,
                        score=10,
                    )
                ]
            ),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, since_minutes=60, timeout_s=1.0)
    assert outcome.status == "available"
    assert len(outcome.mentions) == 1
    mention = outcome.mentions[0]
    assert mention.provider == "reddit"
    assert mention.external_id == "abc123"
    assert mention.asset_id == "btc"
    assert mention.text == "bitcoin hash rateok\nmore"
    assert mention.author_id_hash == hash_identifier("t2_42")
    assert mention.url_hash == hash_url(
        f"{REDDIT_POST_PREFIX}/r/bitcoin/comments/abc123/bitcoin_hash_rate/"
    )
    assert mention.engagement == 10.0
    assert mention.metadata["subreddit"] == "bitcoin"
    assert mention.matched_terms == ()
    assert mention.match_confidence == 0.0
    assert mention.author_id_hash != "t2_42"
    token_call, search_call = transport.calls
    assert token_call[0] == "POST"
    assert token_call[1] == TOKEN_URL
    assert token_call[2]["User-Agent"] == FAKE_UA
    assert token_call[4] == {"grant_type": "client_credentials"}
    assert token_call[5] == (FAKE_ID, FAKE_SECRET)
    assert search_call[0] == "GET"
    assert search_call[1] == SEARCH_URL
    assert search_call[2]["Authorization"] == "Bearer test-not-a-secret"
    assert "Bitcoin" in (search_call[3] or {})["q"]
    assert (search_call[3] or {})["sort"] == "new"


async def test_empty_listing_is_available_zero(reddit_env: None) -> None:
    transport = FakeTransport([_token_ok(), _listing([])])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert outcome.error_class is None


async def test_missing_children_is_available_zero(reddit_env: None) -> None:
    transport = FakeTransport([_token_ok(), _listing(None)])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()


async def test_skips_incomplete_and_old_items(reddit_env: None) -> None:
    in_window = datetime(2026, 9, 8, 11, 30, tzinfo=UTC).timestamp()
    too_old = datetime(2026, 9, 8, 10, 0, tzinfo=UTC).timestamp()
    transport = FakeTransport(
        [
            _token_ok(),
            _listing(
                [
                    {"kind": "t3", "data": {"id": "1", "title": "no author"}},
                    _child(post_id="old", created_utc=too_old),
                    _child(post_id="ok", created_utc=in_window, score=3),
                ]
            ),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "available"
    assert [m.external_id for m in outcome.mentions] == ["ok"]
    assert outcome.mentions[0].engagement == 3.0


async def test_unauthorized_on_token_401(reddit_env: None) -> None:
    transport = FakeTransport([HttpResult(401, b'{"error":"invalid_grant"}')])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.status == "unavailable"
    assert outcome.error_class == "unauthorized"
    assert outcome.mentions == ()
    assert len(transport.calls) == 1


async def test_unauthorized_on_search_403(reddit_env: None) -> None:
    transport = FakeTransport([_token_ok(), HttpResult(403, b"forbidden")])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "unauthorized"


async def test_rate_limited_on_429(reddit_env: None) -> None:
    transport = FakeTransport([_token_ok(), HttpResult(429, b"slow down")])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "rate_limited"
    assert len(transport.calls) == 2


async def test_malformed_token_json(reddit_env: None) -> None:
    transport = FakeTransport([HttpResult(200, b"not-json")])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_missing_access_token_is_malformed(reddit_env: None) -> None:
    transport = FakeTransport([HttpResult(200, _json_body({"token_type": "bearer"}))])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_oversized_search_body_is_malformed(reddit_env: None) -> None:
    transport = FakeTransport([_token_ok(), HttpResult(200, b"x" * (BODY_MAX_BYTES + 1))])
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=1.0)
    assert outcome.error_class == "malformed"


async def test_502_retries_then_timeout(reddit_env: None) -> None:
    transport = FakeTransport(
        [
            HttpResult(502, b"bad gateway"),
            HttpResult(502, b"bad gateway"),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=2.0)
    assert outcome.error_class == "timeout"
    assert len(transport.calls) == 2


async def test_502_then_success(reddit_env: None) -> None:
    transport = FakeTransport(
        [
            HttpResult(502, b"bad gateway"),
            _token_ok(),
            _listing([]),
        ]
    )
    outcome = await collect_isolated(_provider(transport), ASSET, 60, timeout_s=2.0)
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert len(transport.calls) == 3
