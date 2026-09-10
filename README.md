# Zynost Social Intelligence Engine

Standalone Python 3.12+ module (`zynost-social-intelligence`, import `zynost_social`) that
collects, matches, scores, and aggregates **crypto social discussion for one asset**. It is
**context**, not a trading signal. It never outputs trade directions, never treats positive
crowd tone as a price call, and never uses an LLM.

Later integration into FastAPI + PostgreSQL + Redis + Celery is a **consumer** concern. This
repo does not deploy, migrate, or touch the main Zynost backend.

Design algebra and thresholds live in `docs/` (`00`–`21`). This file is the operator-facing
summary required by Definition of Done (**DOD-14**).

## What it measures

For a canonical `AssetIdentity` and a horizon (`intraday` / `swing` / `position`):

- Crowd sentiment (polar tone, not price direction)
- Social activity and momentum (volume, unique authors, heat, acceleration)
- Mention velocity (burst-aware, not auto-positive)
- Engagement (separate from equal-weight classification)
- Organic vs manipulated chatter
- Source agreement / disagreement (a missing source is not agreement)
- Anomalies (flagged as context, not as a price claim)

Matching is never ticker-only. Collision-prone symbols (`ONE`, `IN`, `ARB`, `TON`, `OP`,
`NEAR`, `APT`, `LINK`) need name, contract, official account, or multiple context terms.

## Public API

```python
from zynost_social import AssetIdentity, build_social_sentiment, project_snapshot

result = await build_social_sentiment(asset, horizon="swing")
row = project_snapshot(result, asset_id=asset.asset_id)  # None if every provider is down
```

`build_social_sentiment` returns a JSON-safe dict. Frozen identity:

- `name = social_sentiment`
- `role = context`
- `source_class = multi_source_social_intelligence`

Required keys: `name`, `role`, `status`, `scope`, `classification`, `sentiment_score`,
`confidence`, `social_heat`, `mention_velocity`, `organic_score`, `source_agreement`,
`manipulation_risk`, `metrics`, `sources`, `anomalies`, `observed_at`, `source_class`,
`data_quality`.

`data_quality` always includes `provider_coverage`, `freshness_score`, `asset_match_score`,
and `organic_data_ratio`. Classification is one of `strong_positive`, `positive`, `neutral`,
`mixed`, `negative`, `strong_negative`, `insufficient_data`. A fixture-shaped sample (not a
live API response) is in `examples/example_result.json`.

`project_snapshot` is a **sync** compact row for later consumer persistence. This module does
not write PostgreSQL.

Pipeline: concurrent isolated providers → match → score → manipulate → aggregate →
provenance / quality → contract dict. Redis is an optional shared cache, not process memory.

## Scoring, activity, manipulation, aggregation

Full formulas: `docs/08` (**SCR-**), `docs/09` (**MOM-**), `docs/10` (**MAN-**),
`docs/11` (**AGG-**). Short form:

| Piece | Behavior |
|-------|----------|
| Polar score (**SCR-08**) | \(100 \times (N_{\mathrm{pos}} - N_{\mathrm{neg}}) / n_{\mathrm{eff}}\), clipped to \([-100, +100]\) |
| Classification (**SCR-10**, **SCR-11**) | \(n_{\mathrm{eff}} < 5\) → `insufficient_data`; else mixed if both polar ratios \(\ge 0.20\); else strong at \(\lvert 60 \rvert\), ordinary at \(\lvert 20 \rvert\), else `neutral` |
| Engagement (**SCR-09**) | \(w_i = 1 + \ln(1 + e_i)\); **class uses equal-weight** (recency-adjusted), not engagement |
| Heat (**MOM-07**) | \(100 \times (0.35\,v + 0.25\,a + 0.25\,u + 0.15\,e)\); heat/velocity do **not** flip polarity |
| Velocity (**MOM-05**) | Burst factor on 15-minute bins inside the current hour; a burst is not a positive class |
| Duplicates (**MAN-03**) | 5-word shingles, Jaccard \(\ge 0.60\) or exact normalized match |
| Organic (**MAN-13**) | `organic_score = 100 × (1 − manipulation_risk)`; ratios/Gini, not raw volume |
| Coverage (**AGG-13**) | `confidence = C_raw × A / P_reg` with frozen \(P_{\mathrm{reg}} = 4\) |
| Scope (**AGG-07**) | `asset_specific` vs `market_wide`; leftovers are not scored as the asset |

Directional / moon-language intensities are **language tracks**, not polar hits (**SCR-05**).

## Horizons (**HOR-**, `docs/15`)

Horizon changes lookback, recency weights, and acceleration blend — not only a label.
Unknown / empty horizon applies `swing` and reports both strings.

| Applied | Provider `since_minutes` | Emphasis |
|---------|--------------------------|----------|
| `intraday` | 360 (6 h) | 15 m / 1 h / 6 h recency and acceleration |
| `swing` (default) | 1440 (24 h) | 6 h / 24 h regime |
| `position` | 4320 (72 h) | Sustained window; last-15 m spike is downweighted |

Manipulation assessment has **no** horizon argument. Do not mix horizons in one snapshot
series (consumer partitions; **SNP-07**).

## Setup

Python **3.12+**. Credentials are process environment only; the engine does **not** auto-load
`.env`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` (gitignored). Fill only providers you actually have; leave the
rest empty. Export into the environment, for example `set -a; source .env; set +a`.

| Adapter | Required names (all present or the adapter is `not_configured`) |
|---------|------------------------------------------------------------------|
| X | `X_API_KEY`, `X_BEARER_TOKEN` |
| Reddit | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` |
| Telegram | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (public channels) |
| Discord | `DISCORD_BOT_TOKEN` (authorized guild text/announcement only; no scrape) |
| Cache (optional) | `REDIS_URL` |

Paid or restricted APIs are **not** required. Empty credentials → that source is
`unavailable` / `not_configured`; the engine still returns a contract. Default tests never
open paid sockets.

```bash
pytest
ruff check zynost_social tests
mypy zynost_social
```

The suite includes an LLM-import scan and a secrets-hygiene scan.

## Limitations

- Unconfigured or paid adapters stay `unavailable` with reduced \(A / P_{\mathrm{reg}}\). The
  engine never invents mentions to fill gaps.
- Redis is optional. Unset / down is a cache miss, **not** a provider `error_class`.
- Partial coverage shrinks `confidence`. One live source is not full coverage.
- Quiet (`available` + zero asset mentions) is not all-down (`status = unavailable`, null scores).
- Positive class, high heat, or a mention spike is **not** a bullish price claim.
- All four providers down → `unavailable`; snapshot projection is `None` (no backfill).
- Matching rejects ticker-only hits; generic market chatter is `market_wide`, not the asset.
- No LLM in code or dependencies. Scoring is frozen lexicons and arithmetic.
- This module does not persist history, run Celery, or expose HTTP routes.

## Docs

Start at [`docs/README.md`](docs/README.md). Integration contract: `docs/14`. Security and
env catalog: `docs/16`, `docs/18`.
