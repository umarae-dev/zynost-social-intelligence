# 01 — Product Goals & Scope

> *Traces to specification §1 (what the engine measures, what it must never output, and non-goals).*
> This is the **measurable, testable layer** beneath `00-project-vision.md`. It defines what "done" means at the goals level. Formulas, schemas, and code belong to later documents.

---

## Measurable goals

The engine must measure the following for a **specific crypto asset**. Each goal is phrased so a later test can pass or fail it. Same inputs must yield the same outputs (deterministic). Missing or unusable evidence is reported as such — never guessed.

- **Crowd sentiment** — Report the overall tone of asset-matched discussion (positive, negative, mixed, or insufficient). Later tests can assert that identical mention sets produce the same reading, and that too little evidence yields `insufficient_data` rather than a fabricated tone.
- **Social activity** — Report how much the asset is actually being discussed (volume and uniqueness of discussion). Later tests can distinguish a talked-about asset from a silent one, and a zero-mention case from a populated one.
- **Social momentum** — Report whether that activity is accelerating, holding, or fading. Later tests can assert that a rising window scores differently from a flat or declining one (horizons change this behavior; see below).
- **Mention velocity** — Report how fast new mentions are arriving relative to a recent baseline. Later tests can assert that a burst of new mentions raises velocity versus the same count spread evenly, without treating the burst as automatically positive.
- **Engagement** — Report how much attention the discussion is attracting (not mention count alone). Later tests can assert that high-engagement mentions weigh differently from ignored ones.
- **Organic vs. manipulated chatter** — Separate genuine, diverse discussion from coordinated, copied, flooded, or bot-like activity. Later tests can assert that many near-identical shill posts from few accounts do **not** outscore fewer independent genuine discussions on organic quality, and that a spike is never auto-labeled bullish.
- **Source agreement / disagreement** — Report whether independent sources tell a consistent story. Later tests can assert that aligned sources raise agreement and conflicting sources lower it (and that a missing source is not treated as agreement).
- **Anomalies** — Flag unusual bursts or patterns worth inspecting. Later tests can assert that an anomalous burst is recorded as an anomaly **without** being recast as positive sentiment or a price-direction claim.

---

## In-scope capabilities

These capabilities are in scope because they are required to make the goals above real and testable. They are **not** a design dump — later docs own the how.

- **Multi-source collection** — Independent adapters for X/Twitter, Reddit, Telegram public channels, and Discord (only where a public/authorized API exists). Sources are fetched independently; adding a later provider must not require rewriting scoring.
- **Robust asset identity and matching** — Identify the asset by more than a raw ticker (canonical id, name, aliases, chain/contract, official accounts, surrounding crypto context). Ambiguous symbols must not silently collide. Every match exposes what matched and how confident the match is.
- **Deterministic sentiment scoring** — Score and classify from rules/statistics/provider data only. No LLM. Results are reproducible and explainable.
- **Manipulation / pump resistance** — Detect duplicated/copied campaigns, author flooding, coordinated bursts, engagement concentration, and bot-like repetition, and down-weight them in organic quality. Volume alone is never treated as genuine interest.
- **Aggregation with per-source results** — Combine sources into one asset-level result **and** keep independent per-source status/metrics. A failed source is reported as failed (safe error class); credentials, stack traces, and raw provider errors are never exposed.
- **Scope distinction** — Explicitly distinguish `asset_specific` vs `market_wide`. Insufficient asset-specific discussion yields `insufficient_data` where appropriate. Generic crypto/market chatter must never be passed off as discussion of the requested asset.
- **Horizon-aware behavior** — At least `intraday`, `swing`, and `position`. The horizon must change time windows/weighting, not just a label on the result.
- **Redis-backed shared caching** — Shared Redis is the cache of truth (not process memory). Redis failure degrades gracefully; it must not invent data or take the engine down.
- **JSON-serializable integration contract** — A single public entry point returns a JSON-safe context object (`role = context`) with data quality and provenance: which sources responded or failed, freshness, match quality, and how organic the evidence is.

Historical snapshot **support** (forward-collected evidence only) is required by the spec and will be specified in a later document; this goals layer only requires that history is never fabricated or backfilled.

---

## Hard non-goals

The engine **must never**:

- Output `BUY`, `SELL`, `LONG`, or `SHORT` (in any field, label, or example).
- Treat positive sentiment, high social heat, or a mention spike as **bullish price direction**. Positive sentiment is crowd tone, not a price call.
- Predict price, forecast returns, or claim that social state implies future price movement.
- Recommend trades or positions.
- Use any LLM (Claude, OpenAI, Gemini, or any other) for classification, scoring, matching, or interpretation.
- Deploy, modify production servers, or require access to the main Zynost backend.
- Invent missing information, or fabricate/backfill historical snapshots.

Mocks and fixtures are allowed **only in tests**, never as the production implementation. Paid/credentialed providers are implemented fully but activate only when credentials exist; without them they report unavailable and the engine degrades — they are not a license to fake data or to require a purchase.

---

## Constraints & guardrails (scope boundaries)

These are non-negotiable scope boundaries, not implementation notes:

- **No LLM anywhere** in the pipeline or dependencies.
- **No real secrets in Git** (or Git history); credentials from environment only; commit `.env.example` only.
- **Graceful degradation** — one source failing never takes the engine down; partial coverage yields a partial result with reduced confidence; all sources down → `status = unavailable`. Never invent a substitute.
- **Standalone and non-invasive** — built and audited in isolation; later integration into FastAPI + PostgreSQL + Redis + Celery is a consumer concern, not a build-time change to that backend.

---

## Success criteria (goals-level)

A correct, complete engine at the **goals** level looks like this (the numbered Definition of Done lives in document 20):

- For a supported asset and horizon, it measures all eight required dimensions above and returns one JSON-serializable **context** object with quality and provenance.
- Asset matching is never ticker-only; scope is honest (`asset_specific` vs `market_wide` vs `insufficient_data`).
- Organic discussion is distinguishable from manipulated chatter; anomalies are flagged without becoming trade or price claims.
- Horizons change behavior; Redis is shared cache; sources fail independently and degrade cleanly.
- It never emits `BUY`/`SELL`/`LONG`/`SHORT`, never predicts price, never uses an LLM, never invents or backfills data, and never touches production.
