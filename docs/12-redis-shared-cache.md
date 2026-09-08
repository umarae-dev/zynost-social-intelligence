# 12 — Redis Shared Cache

> *Traces to specification §10 (caching strategy & keys).*
> This is the **shared-cache design** beneath `03-high-level-design.md` (**HLD-05**, **HLD-04**, **HLD-06**, **HLD-07**) and `04-low-level-design.md` (`cache.py`, **LLD-04**, **LLD-08**). It owns the **key strings, TTLs, stored payloads, engine-only call sites, per-call timeout, and fail-soft miss semantics**. It does **not** own scoring/momentum/manipulation/aggregation formulas (docs 08–11, **SCR-**/**MOM-**/**MAN-**/**AGG-**), field-level schemas (doc 05, **MOD-**), matching rules (doc 07, **MAT-**), provider I/O (doc 06, **PRV-**), snapshot persistence in PostgreSQL (doc 13), the integration-contract JSON or `data_quality` scales (doc 14), horizon weight tables (doc 15), or the environment-variable catalog (doc 18).

---

## Role relative to other docs

`cache.py` is a **transport for values other modules already defined**. It consumes, and never redefines: `AssetIdentity.asset_id` as the join key (**MOD-04**), `SocialMention` (**MOD-06**, **MOD-07**), `BaselineBundle`, `AggregateResult`, and `SourceBundle` (doc 05).

Three rules from earlier docs bound everything below:

- **Scoring stays pure.** `score(mentions, horizon, baselines)` gets a `BaselineBundle` **supplied by the engine**. `scoring.py`, `manipulation.py`, and `aggregation.py` MUST NOT talk to Redis (**LLD-04**, **LLD-08**, **HLD-03**, `NFR-DET-01`).
- **A miss is not a missing source.** Cache absence never becomes a provider row, an `error_class`, or a change to provider coverage \(A\) (**AGG-04**, **AGG-05**, **LLD-02**).
- **A miss never becomes data.** Redis down or timed out → miss → continue with the honest empty/zero `BaselineBundle` that docs 08–09 already specify (**MOM-04**, `FR-CACHE-03`, **HLD-07**, `CON-04`).

Baseline **windows** (1h, 6h, 24h) exist because doc 09 computes those slices. Which windows a horizon *emphasizes* — and with what weights — is doc 15. This document defines no horizon mix.

---

## 1. Why Redis and not process memory *(FR-CACHE-01, HLD-05)*

The engine is designed for multiple backend replicas/workers (spec §10). A per-process dictionary would give each replica a private, divergent view of the same asset, and a Celery worker would never see what an API replica just fetched.

**CCH-01 — Redis is the cache of record; process memory is not.** No module-level mention cache, no “last score” dict, no global client singleton mutated at import time (`FR-CACHE-01`, **HLD-05**, **MOD-09**, `NFR-STATE-01`, **LLD-03**). The Redis client and the run’s cache state (§7) are held in an explicit per-run context passed by `engine.py` — not in module globals.

Redis is a **cache**, not storage of record. It holds nothing the engine cannot lose: every key can vanish and the engine still produces a correct, honest result (with unknown priors). Durable history is doc 13’s PostgreSQL snapshot concern; an expired baseline is **never** backfilled (`FR-SNAP-02`, `CON-04`).

**CCH-02 — Engine-only access.** Only `engine.py` calls `cache.py` (**LLD-04**, **LLD-08**). Providers do not cache their own fetches (**PRV-07**); scoring/manipulation/aggregation receive values as arguments.

---

## 2. Key grammar *(FR-CACHE-04, spec §10)*

```
zynost:social:v1:<asset_id>:<purpose>[:<qualifier>]
```

| Segment | Value | Why |
|---------|-------|-----|
| `zynost:social` | fixed | Module namespace. The target Redis is shared with the rest of the Zynost platform; this prefix keeps social-intelligence keys from colliding with other modules (**HLD-09**). |
| `v1` | cache generation | Bumped when a **stored payload shape** changes. Old-generation keys are never read and expire on their own TTL (`FR-CACHE-04`, **CCH-18**). |
| `<asset_id>` | `AssetIdentity.asset_id` | Per-asset isolation (`FR-CACHE-04`). |
| `<purpose>` | `raw` \| `aggregate` \| `baseline` | The three cached concerns of `FR-CACHE-02`. |
| `<qualifier>` | provider \| horizon \| window | Closed sets in **CCH-05**. |

**CCH-03 — Namespaced and versioned, always both.** No key is written outside this grammar. TTL tuning does **not** bump `v1`; a change to what a value contains does.

**CCH-04 — Key on `asset_id`, never on `symbol`.** `symbol` is one matching signal, not identity (**MOD-04**, `FR-ID-02`). Keying on tickers would let the known collision set (`ONE`, `IN`, `ARB`, `TON`, `OP`, `NEAR`, `APT`, `LINK` — **MAT-03**) share one cache entry across unrelated assets.

Because `:` is the key separator, `asset_id` is used verbatim **only** when it matches `[A-Za-z0-9._-]{1,64}`. Otherwise the segment is `sha256(asset_id)` truncated to 32 hex characters. This is deterministic (**HLD-03**) and prevents a malformed or hostile identity string from forging another asset’s key (`NFR-SEC-01`).

---

## 3. Key catalog and what each key stores *(FR-CACHE-02)*

**CCH-05 — Frozen key set.** Providers are the **MOD-05** closed set; horizons are the `FR-HOR-01` set; baseline windows are the doc 09 slice set.

| Key | Stores | Not stored |
|-----|--------|------------|
| `zynost:social:v1:<asset_id>:raw:x` | Normalized, **pre-match** `SocialMention` list from one successful X fetch, plus the fetch envelope (§4). | Raw API bodies, headers, tokens. |
| `…:raw:reddit` | Same, Reddit. | |
| `…:raw:telegram` | Same, Telegram public channels. | |
| `…:raw:discord` | Same, Discord (only where a public/authorized API exists — `FR-SRC-01`). | |
| `…:aggregate:<horizon>` | JSON projection of one computed `AggregateResult` (asset-level numbers, `scope`, `classification`, retained `sources` rows) for `<horizon>` ∈ {`intraday`, `swing`, `position`}. | `data_quality`, provenance, and the contract identity fields — assembled per run by doc 14. |
| `…:baseline:1h` | Small numeric prior record for the 1h window (§5). | Mentions, text, formulas. |
| `…:baseline:6h` | Same, 6h window. | |
| `…:baseline:24h` | Same, 24h window. | |

Ten keys per asset at most (4 raw + 3 aggregate + 3 baseline). There is no per-mention key and no wildcard family, so key growth is bounded and predictable.

**Telegram and Discord are first-class raw keys.** Spec §10 lists `raw:x` and `raw:reddit` as *suggestions*; the provider set is **MOD-05** / `FR-SRC-01`, so all four sources get the same key shape. An unconfigured Discord simply never writes its key (**CCH-16**, **HLD-10**).

**CCH-06 — Raw stores pre-match mentions.** Cached mentions carry `matched_terms = []`, `match_confidence = 0`, `match_rationale = ""` (their pre-match defaults — **MAT-07**). A raw hit still goes through `matching.py` (**LLD** matching note). Matching rules (doc 07) can therefore change without a stale cache serving obsolete match evidence, and `MatchBatch` itself is never cached.

---

## 4. Raw entries are validated by window, not keyed by horizon *(HLD-04, LLD-01)*

`since_minutes` is derived from the horizon (**LLD-01**, doc 15), so two horizons may ask X for different lookbacks. Putting the lookback in the key would fragment the shared cache into one generation per horizon and defeat the point of sharing across replicas.

**CCH-07 — Envelope-validated raw hit.** Each raw value carries `{ "since_minutes": int, "observed_at": ISO-8601 UTC, "provider": str }` alongside the mention list. A hit is usable only when `cached.since_minutes >= requested since_minutes`; otherwise the engine treats it as a **miss** and fetches. When the cached window is wider than requested, the engine narrows the list by `created_at`. Narrowing is **filtering evidence that was actually collected** — it is never extrapolation into hours the fetch did not cover (`CON-04`, **MOM-03** keeps window math on `observed_at`, not wall-clock).

The envelope’s `observed_at` is what feeds `freshness_seconds` for that source (**MOD** `SourceBundle`); a cache hit reports the age of the observation, not the age of the request (`FR-PROV-03`).

---

## 5. Baselines are horizon-free numeric observations *(FR-CACHE-02, FR-MOM-03)*

**CCH-08 — One record per window, shared by all horizons.** Each baseline key holds a small JSON record of numbers observed by the previous completed run:

```
{ "window": "1h" | "6h" | "24h",
  "observed_at": ISO-8601 UTC,
  "mention_count": int,
  "engagement_sum": float,
  "sentiment_score": float | null }
```

These are **observations**, not horizon-weighted outputs, so an `intraday` run and a `position` run may legitimately read the same priors; only the weighting differs (doc 15). Keying baselines by horizon would triple the keys and blind each horizon to priors another horizon already observed.

The engine maps present records onto the `BaselineBundle` slots doc 09 consumes (`observed_at`, `prior_mentions_1h/6h/24h`, `prior_engagement_1h`, `prior_sentiment_score` + `prior_observed_at`). A **missing key means the window is unknown**, which is not the same as a window observed to be quiet (**MOM-04**): `mention_count = 0` in a stored record is a real quiet hour; an absent record must never be read as zero. `sentiment_score` is `null` when the prior run could not classify (**SCR-10**), and a `null` prior never becomes a fabricated delta (**MOM-10**).

---

## 6. TTLs — exact seconds *(FR-CACHE-02, spec §10)*

**CCH-09 — Frozen TTLs. TTL is the only expiry mechanism.**

| Key | TTL (s) | Reasoning |
|-----|---------|-----------|
| `…:aggregate:<horizon>` | **120** | The low end of spec §10’s 2–5 min band, because this value is served *directly* as the caller’s context: delivered social state is never more than two minutes behind its observation. |
| `…:raw:<provider>` | **180** | Mid-band of 2–5 min, and deliberately **longer than the aggregate TTL**: when an aggregate expires, raw entries are still warm for ~60 s, so the recompute that follows is cheap and rarely re-hits provider APIs. Three minutes of drift shifts a 1h window count by at most ~5 %. |
| `…:baseline:1h` | **10 800** (3 h) | “Longer rolling window” (spec §10). Rule: **2 × window, floor 3 h**. A prior survives a few skipped runs, so a quiet period does not erase continuity. |
| `…:baseline:6h` | **43 200** (12 h) | 2 × 6 h. |
| `…:baseline:24h` | **172 800** (48 h) | 2 × 24 h. |

Why `2 × window` rather than “keep forever”: a record older than two window lengths is no longer a credible “previous window”, and letting it expire into **unknown** is the honest outcome (**MOM-04**, `CON-04`). Expiry is the staleness guard; the record’s `observed_at` lets doc 09 anchor its math and doc 14 report freshness.

**CCH-10 — JSON-safe values with a size cap.** Every value is a UTF-8 JSON document of JSON-serializable primitives only — no pickle, no `bytes`, no `NaN`/`Inf`, no datetime objects (`NFR-JSON-01`, doc 05 conventions). A value larger than **256 KiB** is not written (the write is skipped and logged per **CCH-12**); mention text is already size-capped at the adapter (`NFR-SEC-01`), so the cap only guards pathological batches in a Redis shared with other modules.

---

## 7. Timeouts, failure, and fail-soft miss *(NFR-TIMEOUT-01, FR-CACHE-03, HLD-07)*

**CCH-11 — Strict 250 ms timeout per Redis round trip, with batched round trips.** A cache read that costs more than a quarter second is not saving work against the concurrent provider fetch it is meant to avoid. A run uses at most **four** round trips: the aggregate probe, one multi-key read for baselines + raw, one raw write after the gather, and one write for aggregate + baselines after the compute. Worst-case cache overhead per run is therefore bounded at ~1 s even if every call times out — and in practice at 0.25 s because of **CCH-12**.

**CCH-12 — Any error or timeout is a miss; the first failure disables the cache for that run.** A connection error, timeout, decode error, or malformed payload returns *miss* for a read and *skip* for a write. It is logged **once per run** as a redacted line (purpose + failure kind only) and the run continues (`FR-CACHE-03`, **LLD-08**, `NFR-SEC-04`). After the first failure the per-run cache context is marked disabled, so a dead Redis is paid for once rather than on every key — matching **HLD-07**’s “Redis unavailable is a cache miss for the whole run.” The flag lives on the run context, never in module state (**MOD-09**, `NFR-STATE-01`).

`cache.py` **never raises into the engine**. `build_social_sentiment` cannot fail because of Redis (`FR-INT-05`).

**CCH-13 — A cache miss is not a provider outcome.** A miss or a Redis outage MUST NOT create a `SourceBundle` row, set an `error_class` (**MOD-02** classes describe *provider* fetches), or change available-provider count \(A\) or the `A / P_reg` coverage factor (**AGG-04**, **AGG-05**, **AGG-13**, **LLD-02**). Cache health is not provider health; it belongs in logs, not in the contract.

**CCH-14 — A miss never becomes a value.** On miss the engine fetches (raw), computes (aggregate), or passes the explicit empty/zero `BaselineBundle` with **all windows unknown** (baselines). No invented mentions, no invented scores, no invented history, no “previous hour was zero” (`CON-04`, **HLD-07**, **MOM-04**, doc 08 §confidence: a missing baseline is not evidence of disagreement).

| Situation | Cache behavior | Engine result |
|-----------|----------------|---------------|
| Hit, envelope valid | Serve | Skip that fetch / recompute |
| Hit, `since_minutes` too small (**CCH-07**) | Treat as miss | Fetch that provider |
| Key absent | Miss | Fetch / compute; windows unknown |
| Timeout / connection error | Miss, cache disabled for the run, one redacted log | Full pipeline runs; coverage unchanged |
| `REDIS_URL` unset | Cache disabled for the process, one redacted log | Full pipeline runs (**CCH-20**) |
| Oversize value (**CCH-10**) | Write skipped | Result unaffected |

---

## 8. Who touches which key *(LLD call graph)*

`engine.py` only (**CCH-02**). Read phase first, write phase after a successful compute.

```
engine.build_social_sentiment(asset, horizon)
  1 get  …:aggregate:<horizon>                       # hit → may serve without fetching
  2 get  …:baseline:1h|6h|24h + …:raw:<provider>×4   # one batched read (CCH-11)
        #   baselines → BaselineBundle (absent window = unknown)
        #   raw hits  → providers to skip
  3 gather base.collect_isolated(...)                # only for providers that missed
  4 set  …:raw:<provider>                            # best-effort, successful fetches only
     matching → scoring → manipulation → aggregation → provenance → quality
  5 set  …:aggregate:<horizon> + …:baseline:1h|6h|24h  # best-effort, one batched write
```

**CCH-15 — Writes are best-effort and follow a successful compute.** A failed write never changes the returned result. Baselines are written **only when \(A \ge 1\)** (at least one provider available): writing window counts from an all-down run would store a fabricated quiet hour and manufacture a spike for the next run (`CON-04`, **MOM-04**, **HLD-07**). A prior observed under *partial* coverage is a real observation of collected evidence; the cache neither corrects nor extrapolates it, and per-run coverage is already reported by doc 14.

**CCH-16 — Only successful observations are cached.** `status = unavailable` outcomes and `error_class` values are **never** written. A cached failure would freeze a provider out of the next run and would look exactly like the missing source that **CCH-13** forbids. A successful fetch with **zero** mentions *is* cached — that is a real quiet observation (**MOD-08**, **PRV-02**, **AGG-05**).

**CCH-17 — An aggregate hit carries its own `observed_at`.** When the engine serves a cached aggregate for `(asset_id, horizon)`, the cached observation time travels with it so freshness and `observed_at` describe when the evidence was collected, not when the cache was read (`FR-PROV-03`). How that surfaces in the contract and in `data_quality` is doc 14.

**CCH-06** applies on the read side too: a raw hit is re-matched, then re-scored. Per-source `ScoreBundle` / `ManipulationAssessment` are **not** cached — they are deterministic functions of the cached mentions (**HLD-03**), and caching them would serve results from superseded formulas.

---

## 9. Invalidation *(FR-CACHE-04)*

**CCH-18 — TTL-only invalidation plus version bump. No explicit delete.**

- Freshness is handled entirely by **CCH-09** TTLs; a stale entry expires rather than being hunted down.
- A change to any stored payload shape bumps the generation segment (`v1` → `v2`). New readers write and read the new generation; abandoned keys expire on their existing TTL without a migration step (`FR-CACHE-04`).
- No `DEL`, no `KEYS`, no `SCAN`, no `FLUSHDB`, no `EXPIRE` rewrites from the engine. The Redis instance is shared with the rest of the platform; a scan or flush issued by this module could stall or destroy someone else’s keys (**HLD-09**).

The trade-off is accepted deliberately: explicit invalidation would require a key registry plus cross-replica coordination, and the spec asks for freshness-oriented TTLs, not for a purge API. Nothing in the engine depends on immediate invalidation, because every key is reconstructible from a live run.

---

## 10. What MUST NOT go into Redis *(FR-SRC-06, FR-AGG-03, NFR-CRED-01, NFR-SEC-04)*

**CCH-19 — Prohibited payload content.**

| Never stored | Why |
|--------------|-----|
| Credentials, API keys, bearer tokens, `REDIS_URL` itself | Environment-only secrets (`NFR-CRED-01`, `CON-03`). |
| Raw provider response bodies, headers, cookies | Normalized mentions only leave the adapter (**MOD-07**, `FR-NORM-01`). |
| Raw provider error strings, exception `str()`, stack traces | Redaction rule (`FR-SRC-06`, `FR-AGG-03`, **AGG-06**, `NFR-SEC-04`). |
| Plaintext author handles, display names, emails, full URLs | Hashes only (`FR-NORM-03`, doc 05 privacy). |
| `MatchBatch`, per-source score/manipulation bundles | Recomputed deterministically (**CCH-06**). |
| `SocialSentimentSnapshot` rows as history of record | Forward-collected durable persistence is doc 13 / PostgreSQL (`FR-SNAP-01`–`02`). |
| Any `BUY` / `SELL` / `LONG` / `SHORT` string or price claim | `FR-INT-04`, `FR-SENT-06`. |

Cached mention text is untrusted provider content: it is sanitized and size-capped **before** it is written (`NFR-SEC-01`) and is never executed, rendered, `eval`-ed, or used to load code on read (`NFR-SEC-02`).

---

## 11. Configuration *(doc 18 owns the catalog)*

**CCH-20 — `REDIS_URL` from the environment, fail-soft when unset.** The connection string is read from the `REDIS_URL` environment variable only; there is no hard-coded fallback host, no committed value, and only `.env.example` carries a placeholder (`NFR-CRED-01`, `CON-03`). If it is unset or unparseable, the cache is **disabled for the process**: every read is a miss, every write is skipped, one redacted line is logged, and `build_social_sentiment` still returns a complete, honest result (`FR-CACHE-03`).

`REDIS_URL` may embed a password, so it is **never** logged, never placed in an exception message, and never surfaced in the contract or in `error_class` (`NFR-SEC-04`, `FR-SRC-06`). Variable names, defaults, and the full `.env.example` list belong to doc 18; the 250 ms timeout and the TTLs in **CCH-09** are engine constants defined here, not tunables the caller can stretch past the spec bands.

---

## 12. Interface sketch *(LLD `cache.py`, not code)*

Async get/set parameterized by **purpose** and `asset_id` (**LLD** `cache.py` interface). Every function returns a value or a miss; none raises.

```python
CacheContext            # per-run: client handle + `disabled` flag (CCH-01, CCH-12)
RawCacheHit             # mentions + envelope since_minutes / observed_at / provider

async def get_aggregate(ctx, asset_id: str, horizon: str) -> dict | None
async def set_aggregate(ctx, asset_id: str, horizon: str, aggregate: dict) -> None

async def get_baselines(ctx, asset_id: str) -> dict[str, dict]      # absent window = absent key
async def set_baselines(ctx, asset_id: str, records: dict[str, dict]) -> None

async def get_raw(ctx, asset_id: str, provider: str, since_minutes: int
                  ) -> RawCacheHit | None                            # CCH-07 validation inside
async def set_raw(ctx, asset_id: str, provider: str, since_minutes: int,
                  mentions: Sequence[SocialMention], *, observed_at: datetime) -> None

async def get_lookaside(ctx, asset_id: str) -> tuple[raw_envelopes, baseline_records]  # one MGET
async def set_raw_many(...)                                          # one pipeline
async def set_compute(..., *, available_providers: int) -> None      # aggregate + baselines; CCH-15
```

`None` / an absent window means **miss** — never a zero-filled substitute (**CCH-14**). The `set_*` functions return `None` on success *and* on skip: callers must not branch on cache-write success (**CCH-15**). `set_raw` takes collection `observed_at` so freshness is not wall-clock (**CCH-07**, **CCH-17**). `get_raw` returns `RawCacheHit` so that instant is not lost. `set_compute` writes baselines only when `available_providers >= 1`.

---

## Decision index

| ID | Decision |
|----|----------|
| **CCH-01** | Redis is the shared cache of record; no process-memory truth, no module-global client state. |
| **CCH-02** | Only `engine.py` calls `cache.py`; scoring/manipulation/aggregation never touch Redis. |
| **CCH-03** | Every key follows `zynost:social:v1:<asset_id>:<purpose>[:<qualifier>]`; version bumps on payload-shape change. |
| **CCH-04** | Key on `asset_id` (charset-guarded, else `sha256` prefix), never on `symbol`. |
| **CCH-05** | Frozen key set: `raw:{x,reddit,telegram,discord}`, `aggregate:<horizon>`, `baseline:{1h,6h,24h}`. |
| **CCH-06** | Raw stores pre-match mentions; hits are re-matched and re-scored; `MatchBatch` and score bundles are not cached. |
| **CCH-07** | Raw is not keyed by horizon; the envelope’s `since_minutes` validates the hit; wider windows are filtered, never extrapolated. |
| **CCH-08** | Baselines are horizon-free numeric observation records; absent key = unknown window, not zero. |
| **CCH-09** | TTLs: aggregate 120 s, raw 180 s, baseline 1h/6h/24h = 10 800 / 43 200 / 172 800 s (2 × window, 3 h floor). |
| **CCH-10** | JSON-safe UTF-8 values only, ≤ 256 KiB; oversize writes are skipped. |
| **CCH-11** | 250 ms strict timeout per round trip; ≤ 2 batched read round trips, 1 batched write round trip. |
| **CCH-12** | Error/timeout = miss; first failure disables the cache for that run; one redacted log; never raises. |
| **CCH-13** | A cache miss is not a provider outcome: no source row, no `error_class`, coverage \(A\) unchanged. |
| **CCH-14** | A miss never becomes a value: empty/zero `BaselineBundle`, unknown windows, no invented history. |
| **CCH-15** | Writes are best-effort after a successful compute; baselines only when \(A \ge 1\). |
| **CCH-16** | Cache successful observations only (including zero-mention fetches); never cache `unavailable` / `error_class`. |
| **CCH-17** | A served aggregate carries its cached `observed_at` so freshness stays honest. |
| **CCH-18** | Invalidation is TTL-only plus version bump; no `DEL`/`KEYS`/`SCAN`/`FLUSHDB`. |
| **CCH-19** | Never store credentials, raw payloads, raw errors, PII, snapshots-as-history, or trade language. |
| **CCH-20** | `REDIS_URL` from the environment; unset → cache disabled, engine still runs; never log the URL. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Field lists for `SocialMention` / `BaselineBundle` / `AggregateResult` / `SourceBundle` (**MOD-**) | doc 05 |
| Provider I/O, engagement mapping, fetch isolation (**PRV-**) | doc 06 |
| Matching and collision rules (**MAT-**) | doc 07 |
| Polar scoring, classification, confidence (**SCR-**) | doc 08 |
| Heat / velocity / acceleration / agreement formulas (**MOM-**) | doc 09 |
| Manipulation ratios (**MAN-**) | doc 10 |
| Combining sources, `scope`, coverage confidence (**AGG-**) | doc 11 |
| Snapshot persistence and PostgreSQL history | doc 13 |
| Full `build_social_sentiment` JSON, freshness/`data_quality` scales | doc 14 |
| Horizon lookback windows and weight tables | doc 15 |
| Secret-handling posture in depth | doc 16 |
| Cache hit / miss / expiry / unavailable test matrix | doc 17 |
| `.env.example` and the environment-variable catalog | doc 18 |
