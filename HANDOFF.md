# Handoff — zynost-social-intelligence

Standalone Python 3.12+ context module (`import zynost_social`). Not a trading
signal service. Do not merge into another product repo, deploy, or request
production credentials. Independent audit happens before any consumer
integration (`docs/21`, **HAN-02**, **DOD-15**).

## Implemented providers

Four adapters implement `SocialProvider.fetch_mentions(asset, since_minutes)`
(**MOD-05**). Isolation is `collect_isolated` in `providers/base.py`. Missing or
partial credentials → `status = unavailable`, `error_class = not_configured`,
no network, no invented mentions (**PRV-05**, `CON-06`). Live paid subscriptions
are not required.

| Provider | File | When configured | When not configured |
|----------|------|-----------------|---------------------|
| X | `providers/x.py` | Recent search via HTTP; query is never ticker-only | No call; `not_configured` |
| Reddit | `providers/reddit.py` | OAuth client-credentials then `/search`; query is never ticker-only | No call; `not_configured` |
| Telegram | `providers/telegram.py` | Bot HTTP API; **public channels** listed on the asset only (no invites, groups, or scrape) | No call; `not_configured` |
| Discord | `providers/discord.py` | Bot REST; **authorized guild text/announcement** channels listed on the asset only (no invites, DMs, or guild crawl) | No call; `not_configured` |

Safe `error_class` values: `not_configured`, `unauthorized`, `timeout`,
`malformed`, `rate_limited`. One adapter down does not take the engine down.

## Provider API requirements

Names are **CFG-03** (empty values in `.env.example`). The engine does not
auto-load `.env`. Default `pytest` does not open paid sockets (**TST-01**).

| Adapter | Required environment names (all present or `not_configured`) |
|---------|--------------------------------------------------------------|
| X | `X_API_KEY`, `X_BEARER_TOKEN` |
| Reddit | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` |
| Telegram | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` |
| Discord | `DISCORD_BOT_TOKEN` |
| Cache (optional) | `REDIS_URL` |

Telegram and Discord have no global search: with credentials but no official
public/authorized channels on `AssetIdentity`, the fetch is a quiet empty list
(`FR-SRC-01`, **PRV-T-02**, **PRV-D-02**).

## Architecture summary

Concurrent isolated providers → match → score → manipulate → aggregate →
provenance / quality → `build_social_sentiment` dict (**HLD-01**, **LLD-04**).
Redis is optional shared lookaside in `cache.py` (not process-memory truth).
`project_snapshot` is a sync compact row for a later consumer store; this
module does not write PostgreSQL (**SNP-03**, **HLD-09**).

## Test count / result

- Command: `pytest`
- Collected: **210**
- Result: **210 passed** (2026-09-10)
- **TST-LLM** (`tests/test_llm_free.py`) and **TST-SEC** (`tests/test_secrets_hygiene.py`) passed as part of that run

Optional operator checks in `README.md`: `ruff check zynost_social tests`,
`mypy zynost_social`.

## Limitations

- Unconfigured or paid adapters stay `unavailable` with reduced \(A / P_{\mathrm{reg}}\). The engine never invents mentions to fill gaps.
- Redis is optional. Unset / down / miss is a cache miss, **not** a provider `error_class` (**CCH-13**, **CCH-20**).
- Partial coverage shrinks `confidence` (**AGG-13**). One live source is not full coverage.
- Quiet (`available` + zero asset mentions) is not all-down (`status = unavailable`, null scores) (**INT-05**).
- Positive class, high heat, or a mention spike is **not** a bullish price claim (`FR-SENT-06`).
- Do not mix horizons in one snapshot series; the consumer partitions (**SNP-07**).
- This module does not persist history, run Celery, or expose HTTP routes (**HLD-09**).
- No LLM. Scoring is frozen lexicons and arithmetic.

## Exact public integration function

```text
async def build_social_sentiment(asset: AssetIdentity, horizon: str = "swing") -> dict
```

Package: `zynost_social`. Frozen identity: `name = social_sentiment`,
`role = context`, `source_class = multi_source_social_intelligence`
(`FR-INT-01`–`03`). Required keys and `data_quality` fields: `docs/14` and
`examples/example_result.json` (fixture-shaped, not a live API response).

Also exported: sync `project_snapshot(result, *, asset_id)` →
`SocialSentimentSnapshot | None`. Not a second async entry (**SNP-02**,
**INT-01**). Returns `None` when every provider is down.

## Intentionally deferred

Closed set (**HAN-05**). None of these are skipped Definition of Done rows.

- FastAPI routes, Celery scheduling, PostgreSQL migrations
- Merge into the main Zynost backend
- Live paid API subscriptions
- Price/OHLCV correlation, backtests, trade labels (**SNP-12**)
- Extra providers beyond X, Reddit, Telegram, Discord
