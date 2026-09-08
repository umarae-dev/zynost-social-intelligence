# 03 — High-Level Design (HLD)

> *Traces to specification §2 (sources, concurrency, graceful degradation) and §17 (recommended structure).*
> This is the **system-level architecture** beneath `02-requirements-specification.md`. It describes how the engine is structured and how data flows. It is not code: no class signatures, formulas, cache-key lists, or field-by-field schemas (those belong to LLD, models, cache, and scoring docs). Requirement IDs (`FR-…` / `NFR-…` / `CON-…`) are cited where a design choice implements them.

---

## Design intent

The engine is a **standalone, async-first Python 3.12+ module** that, for one asset and one horizon, collects social activity from independent providers, matches it to that asset, scores it deterministically, assesses manipulation, aggregates coverage, and returns a single JSON-serializable **context** object with provenance and data quality (`FR-INT-01`–`03`, `CON-01`).

Collection is a separate concern from scoring and manipulation (`NFR-STATE-01`, spec §17). A later FastAPI + PostgreSQL + Redis + Celery integration is a **consumer** of this module, not part of this design (`CON-02`).

---

## Component overview

| Stage | Responsibility | Owned later by |
|-------|----------------|----------------|
| **Providers** | Independent adapters (X/Twitter, Reddit, Telegram public channels, Discord only where a public/authorized API exists). Each fetches a recent window and normalizes to a common mention shape. | docs 05, 06 |
| **Matching** | Asset identification using canonical identity (never ticker-only); collision avoidance; match terms / confidence / rationale on every mention. | docs 05, 07 |
| **Scoring** | Deterministic sentiment, activity, momentum, and horizon-aware weighting. No LLM. | docs 08, 09, 15 |
| **Manipulation** | Organic vs coordinated/copied/flooded chatter; down-weight organic quality; flag anomalies without recasting them as positive or bullish. | doc 10 |
| **Aggregation** | Combine available sources into one asset-level result; retain independent per-source status; set `asset_specific` vs `market_wide`. | doc 11 |
| **Cache** | Shared Redis as cache of record for raw fetches, aggregates, and baselines — not process memory. | doc 12 |
| **Provenance / quality** | Honest coverage, freshness, match quality, organic ratio; safe per-source `error_class` on failure. | doc 14 |
| **Engine** | Public orchestration entry point: run the pipeline, apply horizon, assemble the integration-contract dict. | docs 04, 14 |

**HLD-01 — Separation of concerns.** Provider I/O and normalization stay in `providers/`. Matching, scoring, manipulation, aggregation, cache, provenance, and quality are distinct modules. Adding a provider must not rewrite scoring, manipulation, or aggregation (`FR-SRC-07`).

**HLD-02 — Common provider contract.** Every adapter implements the same asynchronous fetch of mentions for an asset over a recent time window and returns the common mention model (`FR-SRC-02`, `FR-NORM-01`). The HLD does not freeze signatures; LLD (doc 04) owns the interface.

**HLD-03 — Deterministic core.** Scoring, manipulation, and aggregation are pure with respect to their inputs: same cleaned mentions + same horizon → same numbers and labels (`NFR-DET-01`). Hidden global mutable state is forbidden (`NFR-STATE-01`).

---

## Canonical data flow

```
                    ┌─────────────────────────────────────────┐
                    │  Redis (shared cache of record)          │
                    │  raw per-provider · aggregate · baselines│
                    └──────────▲──────────────────▲────────────┘
                               │ read/write       │
build_social_sentiment(asset, horizon)
        │
        ▼
  concurrent provider fetch ──► normalize mentions
        │                         │
        │ (timeout / fail isolated)
        ▼                         ▼
     matching (identity, collisions, match evidence)
        │
        ▼
     scoring (sentiment, activity, momentum; horizon weights)
        │
        ▼
     manipulation (organic vs manufactured; anomalies)
        │
        ▼
     aggregation (per-source + asset-level; scope)
        │
        ▼
     provenance / data quality
        │
        ▼
     JSON-serializable context dict  (status, classification, metrics, …)
```

Flow notes (architecture only):

1. **Entry.** One public async function accepts an `AssetIdentity` and a horizon (default `swing`) and returns a JSON-safe dict (`FR-INT-01`, `NFR-JSON-01`). Fixed contract identity: `name = social_sentiment`, `role = context`, `source_class = multi_source_social_intelligence`.
2. **Collect.** Independent providers run concurrently against a horizon-appropriate recent window. Cache may satisfy a fetch; Redis miss or Redis down is not a crash (`FR-CACHE-01`–`03`).
3. **Normalize then match.** Raw provider payloads never reach scoring. Mentions carry hashed/pseudonymous identifiers (`FR-NORM-02`–`03`). Matching is not ticker-only (`FR-ID-02`).
4. **Score then manipulate.** Sentiment and activity are computed on cleaned, asset-matched mentions. Manipulation is a separate stage so volume cannot silently become “organic interest” (`FR-MAN-03`, `FR-MOM-07`).
5. **Aggregate with scope.** Available sources combine into one asset-level result; missing sources are not treated as agreement (`FR-MOM-05`, `FR-AGG-01`). Scope is explicit (`FR-SCOPE-01`–`03`).
6. **Prove quality.** `data_quality` and per-source status reflect actual coverage and freshness (`FR-PROV-01`–`03`). Partial coverage reduces confidence (`FR-AGG-04`).
7. **Snapshot.** A compact point-in-time view of the same computed state is supported for later persistence; it is forward-collected only (`FR-SNAP-01`–`02`). Persistence itself is a consumer concern.

**HLD-04 — Horizon is a behavioral input.** `intraday` / `swing` / `position` change which windows and weights the pipeline uses, not a label stuck on an otherwise identical result (`FR-HOR-01`–`02`). Exact weights live in doc 15.

**HLD-05 — Redis is shared cache of record.** Multiple workers/replicas share raw fetches, aggregates, and longer-lived baselines. Process memory is not the source of truth. Redis failure: continue without cache; never invent values (`FR-CACHE-01`–`03`). Exact keys and TTLs live in doc 12.

---

## Concurrency model

**HLD-06 — Concurrent, isolated, bounded collection.**

- Independent providers are fetched **concurrently**, not sequentially (`FR-SRC-04`, `NFR-ASYNC-01`).
- Every external call (providers and Redis) has a **strict timeout**; nothing may block the engine indefinitely (`NFR-TIMEOUT-01`).
- Concurrency is **bounded** so the engine cannot exhaust resources or overwhelm providers (`NFR-CONC-01`).
- A provider timeout, malformed payload, auth failure, or crash is **contained**: that adapter reports unavailable + a safe `error_class`; remaining adapters still complete (`FR-SRC-03`, `FR-SRC-05`).
- One provider failure **must never take the engine down**.
- Retry/backoff, if used, is safe and must not amplify load (`NFR-SEC-03`).
- Scoring/manipulation/aggregation run on whatever mentions actually arrived; they do not wait on a dead provider.

Provider-specific networking stays inside each adapter. The engine orchestrates gather-with-isolation; it does not share sockets, credentials, or mutable fetch state across adapters.

---

## Degradation behavior

**HLD-07 — Fail-soft, never invent.** Missing, timed-out, unauthorized, or unconfigured sources are omitted from evidence. Substituting guessed mentions, scores, or history is forbidden (`NFR-DEGRADE-01`, `CON-04`). Confidence and coverage shrink with coverage (`FR-AGG-04`). All sources down → `status = unavailable` without raising (`FR-INT-05`).

Explicit cases from spec §2 (same pattern applies to Discord when that adapter is present):

| Situation | Engine behavior |
|-----------|-----------------|
| **X unavailable** | Reddit + Telegram still collect and score. Result is partial; X appears unavailable with a safe `error_class`. Confidence reduced vs full coverage. |
| **Reddit unavailable** | X + Telegram still work. Same partial / reduced-confidence rule. |
| **Only Telegram available** | Partial result from Telegram only; reduced confidence. Not treated as full multi-source agreement. |
| **All unavailable** | `status = unavailable`. No fabricated sentiment, heat, or mentions. |

Paid or unconfigured providers **degrade the same way**: implement the real adapter, but with missing credentials they report `status = unavailable` and a safe `error_class` such as `not_configured` or `unauthorized`. They do not demand a purchase, break the engine, or invent data (`CON-06`). Mocks exist only in tests (`CON-05`).

Redis unavailable is a cache miss for the whole run, not an engine-down event (`FR-CACHE-03`).

---

## Recommended project layout

**HLD-08 — Spec §17 layout.** One package, no giant single file, no duplicated provider logic.

```
zynost-social-intelligence/
  zynost_social/
    __init__.py
    engine.py          # orchestration + public entry point
    models.py          # core types (owned by doc 05)
    matching.py
    scoring.py
    manipulation.py
    aggregation.py
    cache.py           # Redis client/adapter, not in-process truth
    provenance.py
    quality.py
    providers/
      __init__.py
      base.py          # shared adapter contract / helpers
      x.py
      reddit.py
      telegram.py
      discord.py
  tests/               # engine, matching, scoring, manipulation, cache, provider failures, …
  examples/            # example_result.json (no secrets)
  .env.example
  README.md
  pyproject.toml
```

Credentials are environment-only; only `.env.example` is committed (`NFR-CRED-01`, `CON-03`). Test file names in §17 are indicative; the testing strategy (doc 17) owns the full matrix.

---

## Boundaries (in / out)

**In this module**

- Provider adapters, matching, deterministic scoring, manipulation, aggregation, Redis cache usage, snapshot *shape* support, integration-contract output.
- Security posture that belongs to the engine: timeouts, bounded concurrency, sanitization, size caps, validated responses, no `eval`/`exec`, no execution/rendering of social content, redacted errors (`NFR-SEC-01`–`04`).

**Out of this module (consumer / later docs / forbidden)**

- FastAPI routes, PostgreSQL persistence, Celery workers, and wiring into the main Zynost backend (`CON-02`).
- Deployment, production servers, production credentials.
- Any LLM SDK or LLM-based classification (`CON-01`).
- Trading labels (`BUY`/`SELL`/`LONG`/`SHORT`) or price-direction claims (`FR-INT-04`, `FR-SENT-06`).
- Exact formulas, mention/snapshot field lists, cache keys, and class signatures — docs 04–16.

**HLD-09 — Standalone audit boundary.** The repository is built and tested in isolation. Downstream may call `build_social_sentiment` and persist snapshots; this HLD does not include those systems.

---

## Decision index (for LLD citation)

| ID | Decision |
|----|----------|
| **HLD-01** | Collection / matching / scoring / manipulation / aggregation / provenance are separate stages. |
| **HLD-02** | All providers share one async fetch contract and a common mention shape. |
| **HLD-03** | Scoring and aggregation are deterministic; no hidden global mutable state. |
| **HLD-04** | Horizon changes windows/weights, not metadata only. |
| **HLD-05** | Shared Redis is cache of record; process memory is not. |
| **HLD-06** | Concurrent fetch, strict timeouts, bounded concurrency, per-provider isolation. |
| **HLD-07** | Fail-soft degradation matrix; never invent data; all-down → `unavailable`. |
| **HLD-08** | Package layout per spec §17. |
| **HLD-09** | Standalone module; FastAPI/PostgreSQL/Redis/Celery integration is a consumer concern. |
| **HLD-10** | Unconfigured/paid providers are first-class unavailable sources (`not_configured` / `unauthorized`), not stubs that fake data. |
