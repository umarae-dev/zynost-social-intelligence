# 13 — Historical Snapshots

> *Traces to specification §11 (historical snapshots).*
> This is the **snapshot-of-record design** beneath `03-high-level-design.md` (**HLD-03**, **HLD-04**, **HLD-07**, **HLD-09**) and `04-low-level-design.md` (**LLD-04** engine orchestration). It owns **when a snapshot is projected, from what state, what it means, what a consumer may persist, and the honesty rules that keep the history real**. It does **not** own the field list of `SocialSentimentSnapshot` (doc 05, **MOD-**), any scoring / momentum / manipulation / aggregation formula (docs 08–11, **SCR-** / **MOM-** / **MAN-** / **AGG-**), Redis keys or TTLs (doc 12, **CCH-**), the public integration-contract JSON or `data_quality` scales (doc 14), or horizon weight tables (doc 15).

---

## Role relative to other docs

This document **consumes and never redefines**:

| Consumed | Owner |
|----------|-------|
| `SocialSentimentSnapshot` fields — `asset_id`, `observed_at`, `sentiment_score`, `social_heat`, `mention_count`, `mention_velocity`, `organic_score`, `manipulation_risk`, `source_coverage`, `source_agreement`, `classification` | doc 05 §3 (**MOD-01**) |
| Closed classification set incl. `insufficient_data` | doc 05 **MOD-03** |
| `AggregateResult`, `SourceBundle` | doc 05 §4; combining rules doc 11 |
| Snapshot vs live-result split | doc 05 §5 |
| Observation anchor \(t_0\); unknown-vs-quiet priors | doc 09 **MOM-03**, **MOM-04**, **MOM-10** |
| Coverage \(A / P_{\mathrm{reg}}\); keep/override `insufficient_data`; all-down behavior | doc 11 **AGG-04**, **AGG-07**, **AGG-13**, **AGG-14** |
| Cache is expiring, not history; served-aggregate `observed_at` | doc 12 **CCH-01**, **CCH-08**, **CCH-14**, **CCH-17**, **CCH-19** |

**Consumer boundary (HLD-09, CON-02).** PostgreSQL, Celery, and FastAPI are **consumer** concerns. This module **MUST NOT** open a database connection, ship a persistence layer, run migrations, define ORM models, or schedule jobs. It hands the consumer a value; the consumer decides whether, where, and how long to keep it.

**Cache is not history (CCH-01, CCH-19).** Redis baselines are an **expiring cache**; snapshots are **durable evidence**. An expired or missing baseline is **unknown** (**MOM-04**, **CCH-08**, **CCH-14**) and **MUST NOT** be reconstructed, interpolated, or backfilled into a snapshot.

---

## 1. What a snapshot is *(FR-SNAP-01)*

A snapshot is **one reading, taken once, of what was actually collected** — a compact row of the asset-level social state at a single observation instant, sized for later persistence by a consumer.

**SNP-01 — The snapshot is the module's only durable-evidence shape.** Everything else the engine produces is transient: provider payloads never leave the adapter (**MOD-07**), raw/aggregate/baseline cache entries expire on TTL (**CCH-09**), and the contract dict is a per-call response (doc 14). A sequence of snapshots is therefore the **only** way the platform accumulates real social history, and the only artifact whose correctness matters months after the run.

That is why the rules below are stricter than for any cached value: a wrong cache entry expires in minutes, a wrong snapshot is a permanent lie in someone's time series.

**SNP-11 — A snapshot is not the contract dict.** It carries **no** contract identity fields (`name`, `role`, `source_class`), no `metrics`, no `sources` rows, no `anomalies`, and no `data_quality` object (`FR-INT-03`, doc 05 §5). Those are doc 14's per-call response surface. A consumer that wants provider-level detail persists it separately under its own schema; this module does not widen the snapshot to carry it.

---

## 2. Who projects a snapshot, and from what *(LLD-04, NFR-DET-01, HLD-03)*

**SNP-02 — `engine.py` is the sole projector, from the same computed state that produced the run result.** Projection happens **after** `aggregation.aggregate()` and **beside** the contract assembly (**LLD-04** call graph, which already defers "snapshot projection → doc 13"). It reads the run's `AggregateResult` and the run's observation instant; it does not re-run the pipeline, re-score anything, or fetch a second time.

Projection is a **pure, deterministic mapping**: same computed state → same snapshot, byte for byte (`NFR-DET-01`, **HLD-03**). It performs no I/O, no `await`, no Redis call, no clock read, and holds no state between runs (**MOD-09**, `NFR-STATE-01`).

Because it is pure, projection **cannot introduce a number that aggregation did not compute**. Every snapshot field is a copy or a documented ratio of retained rows — never a fresh calculation:

| Snapshot field | Sourced from |
|----------------|--------------|
| `asset_id` | `AssetIdentity.asset_id` for the run (**MOD-04**) |
| `observed_at` | The run's observation anchor (§4) |
| `sentiment_score`, `classification` | `AggregateResult` (**AGG-11** / **AGG-10**, **AGG-14**) |
| `social_heat`, `mention_velocity`, `organic_score`, `manipulation_risk` | `AggregateResult` (doc 11 §5, consuming **MOM-** / **MAN-** values) |
| `mention_count` | `AggregateResult.mention_count` \(= N_a\) (**AGG-12**) |
| `source_agreement` | `AggregateResult.source_agreement` (**AGG-09** / **MOM-09**) |
| `source_coverage` | doc 05's definition — available providers / providers expected this run — read off the retained `sources` rows, which **AGG-04** guarantees include failures. It is the same \(A / P_{\mathrm{reg}}\) ratio **AGG-13** uses as its confidence factor. The `data_quality.provider_coverage` **scale** stays doc 14's. |

No snapshot field is computed from mentions, text, baselines, or wall-clock at projection time.

### Interface sketch *(not code)*

```python
# engine-internal: the canonical projection, from computed state
def _project_snapshot(
    aggregate: AggregateResult,
    asset_id: str,
    observed_at: str,                  # ISO-8601 UTC, the run's t_0 (SNP-05)
) -> SocialSentimentSnapshot | None    # None when the run is not projectable (SNP-04)

# public, pure, synchronous: the same projection over the returned contract dict
def project_snapshot(result: dict) -> SocialSentimentSnapshot | None
```

The type itself lives in `models.py` (**MOD-01**); only the projection logic lives here. The public form is a **pure adapter over the dict the entry point already returned** — it is not a second orchestration path, is not async, and never triggers a run. `FR-INT-01`'s "one public async entry point" (**LLD-04**) is therefore unchanged.

**Equivalence is required:** for any run, both forms MUST yield the identical snapshot (or identically `None`). If they could diverge, a consumer persisting from the dict would be storing a different history than the engine believes it observed.

---

## 3. When a snapshot exists — and when none does *(HLD-07, FR-INT-05, AGG-14)*

**SNP-04 — Projection eligibility. A snapshot of invented scores is never produced; an honest empty reading is.** First matching row wins.

| # | Run outcome | Reference | Snapshot |
|---|-------------|-----------|----------|
| 1 | \(A = 0\) — every provider unavailable / unconfigured / timed out; contract `status = unavailable` | **AGG-14** row 1, **HLD-07**, `FR-INT-05` | **None.** No row at all. |
| 2 | \(A \ge 1\), \(N_a \ge 1\), \(n_{\mathrm{eff}} \ge 5\) — asset-specific evidence classified | **AGG-14** rows 4–5 | **Yes**, full reading |
| 3 | \(A \ge 1\), \(N_a \ge 1\), \(n_{\mathrm{eff}} < 5\) — thin asset-specific evidence | **AGG-14** row 3 | **Yes**, `classification = insufficient_data` |
| 4 | \(A \ge 1\), \(N_a = 0\), \(N_w = 0\) — quiet asset, providers answered | **AGG-07** row 3, **AGG-05** | **Yes**, `mention_count = 0`, `insufficient_data` |
| 5 | \(A \ge 1\), \(N_a = 0\), \(N_w \ge 1\) — only market-wide leftovers | **AGG-07** row 2, **AGG-14** row 2 | **Yes**, `mention_count = 0`, `insufficient_data`. The leftover count \(N_w\) is **not** the snapshot's `mention_count` (`FR-SCOPE-03`, **AGG-08**) |
| 6 | Partial coverage (e.g. only Telegram available) | **HLD-07**, **AGG-13** | **Yes**, with honest `source_coverage < 1` |
| 7 | Redis down, or every cache read a miss | **CCH-12**–**CCH-14** | **Yes**, unchanged — a cache miss is not a missing source (**CCH-13**), so \(A\) is unaffected |
| 8 | The run was served from a cached aggregate | **CCH-17** | **Yes**, carrying the **cached** `observed_at` (§4) — the same observation, not a new one |

**Why row 1 is the only "none".** With \(A = 0\) there is no collected evidence at all: **AGG-11** leaves `sentiment_score` `null` and doc 05 requires it, so any row would have to be manufactured. Writing `sentiment_score = 0`, `social_heat = 0`, `mention_count = 0` for an all-down run would be indistinguishable from row 4 — a genuinely observed quiet hour — and would manufacture a spike for the next comparison. That is exactly the fabrication `CON-04` and **HLD-07** forbid, and it is the same reasoning **CCH-15** uses to refuse baseline writes when \(A = 0\).

**Why rows 3–5 *are* snapshots.** "We looked and found nothing about this asset" is a real observation, and it is the observation that makes a later gap interpretable. This is symmetric with **CCH-16** / **MOD-08** / **AGG-05**: a successful fetch with zero mentions is evidence, not absence of evidence. Suppressing these rows would leave a consumer unable to tell an observed quiet stretch from a period when nobody ran the engine.

**SNP-06 — `classification` is the validity gate for the numbers.** In rows 3–5 the numeric fields are the honest empty numerics **AGG-11** already specifies (typically `0`), **not** a reading of neutral tone. A snapshot with `classification = insufficient_data` and/or `mention_count = 0` MUST NOT be charted or aggregated as a tone observation. `sentiment_score = 0` under `insufficient_data` means *no measurement*; `sentiment_score = 0` with a real classification and `mention_count > 0` means *measured neutral*. Consumers MUST read `classification`, `mention_count`, and `source_coverage` before using any numeric field.

**SNP-03 — This module never persists.** Projection returns a value. No `INSERT`, no connection pool, no migration, no ORM class, no Celery task, no retention job (**HLD-09**, `CON-02`). A consumer that never calls the projection is a valid consumer.

---

## 4. `observed_at` semantics *(FR-SNAP-01, MOM-03, CCH-17, FR-PROV-03)*

**SNP-05 — `observed_at` is the run's observation anchor \(t_0\), never wall-clock at write time. One observation, one snapshot.**

`observed_at` is **the instant the evidence describes** — the same \(t_0\) doc 09 **MOM-03** anchors all window math to, and the same instant the contract's `observed_at` reports (doc 14). It is *not*:

- the moment `build_social_sentiment` was called,
- the moment the projection ran,
- the moment the consumer executed its `INSERT`.

Those three drift apart under queueing, retries, and worker backlog. If a snapshot were stamped at write time, then `mention_velocity` and `social_acceleration` — computed on windows ending at \(t_0\) (**MOM-05**, **MOM-08**) — would be attached to a different instant than the one they measure, and every later derivative over the series would be computed on a skewed axis. Anchoring on \(t_0\) keeps a snapshot internally consistent: the fields and the timestamp describe the same window.

**Cache-served runs (CCH-17, FR-PROV-03).** When the engine serves a cached aggregate, the cached `observed_at` travels with it, so the snapshot describes **when the evidence was collected, not when the cache was read**. Two calls a minute apart that both hit the same cached aggregate are **one observation**, and both project the *same* snapshot — same `observed_at`, same numbers, by **SNP-02**'s determinism. This is a feature, not a duplicate: it lets a consumer make `(asset_id, observed_at)` the identity of an observation and treat a re-projection as idempotent rather than as new history. Stamping cache hits with read time would instead inflate the series with fake observations of stale evidence, and would report a freshness the evidence does not support (`FR-PROV-03`).

Format is doc 05's convention: timezone-aware **UTC**, serialized ISO-8601 (e.g. `2026-09-07T09:00:00Z`), `NFR-JSON-01`.

---

## 5. Forward-collected only *(FR-SNAP-02, CON-04)*

**SNP-09 — Nothing is ever written for a past instant.** A snapshot may only describe the run that produced it.

Forbidden, without exception:

- **Backfill.** No snapshot for an instant before the run's own \(t_0\).
- **Fabrication.** No row for a period nobody observed.
- **Interpolation.** No synthetic mid-points between two real snapshots, no smoothing, no resampling into a fixed grid **inside this module**.
- **Re-derivation.** A later run's evidence MUST NOT be used to reconstruct what an earlier instant "would have looked like". Provider windows shift, deletions happen, and matching rules evolve (**CCH-06**); a reconstruction is a guess wearing a timestamp.
- **Amendment.** An existing snapshot is not rewritten when better coverage arrives later. The honest record is two readings with different `source_coverage`, not one improved reading.

**SNP-10 — An unknown baseline stays unknown.** A missing or TTL-expired baseline record is **unknown**, not zero (**MOM-04**, **CCH-08**, **CCH-14**). The affected momentum fields are the honest neutral values doc 09 already specifies (\(\rho = 1\), \(\alpha = 0\), state `holding`, unknown deltas omitted — **MOM-05**, **MOM-08**, **MOM-10**). Projection MUST NOT reach into an older snapshot to substitute the expired prior and MUST NOT re-derive a delta from it. Doing so would let durable history silently repair a cache gap, which is backfill by another route.

**Irregular cadence and gaps are legitimate and MUST stay visible.** Nothing guarantees a fixed interval: the consumer's schedule, worker backlog, cache hits collapsing into one observation (**SNP-05**), and all-down runs producing no row (**SNP-04** row 1) all make the series uneven. A gap means "not observed", which is genuinely different from "observed and quiet" (rows 3–5). Any analysis that requires an even grid does that alignment **downstream and explicitly**, on the consumer's side, where the assumption is visible and reversible — never by writing interpolated rows into the store of record.

---

## 6. Horizon and coverage honesty *(HLD-04, AGG-13)*

Two facts make a naive time series misleading, and both are resolved by disclosure rather than by adding fields to doc 05's model.

### Horizon

**HLD-04** / `FR-HOR-02` mean the horizon is behavioral: `intraday`, `swing`, and `position` derive different lookbacks and weight different windows (doc 15), so **the same underlying window legitimately yields different numbers under different horizons**. A `swing` snapshot and an `intraday` snapshot taken at the same \(t_0\) are two different measurements, not a contradiction.

`SocialSentimentSnapshot` has **no** `horizon` field, and this document does **not** add one (**MOD-01**; adding a field is doc 05's decision, and the spec §11 field list is closed).

**SNP-07 — Horizon is consumer-held context; a series is single-horizon by construction.** The consumer **chose** the horizon when it called the entry point, so it already knows it and does not need the module to echo it back. The division of responsibility is explicit:

- **The module guarantees:** every snapshot is a deterministic projection of one run at one horizon (**SNP-02**), and identical computed state at the same horizon always yields the identical snapshot.
- **The module does not guarantee:** that two snapshots are comparable. It cannot, because it sees one run at a time.
- **The consumer owns:** partitioning its stored series by the horizon it requested, and **not interleaving horizons in a single series**. Mixing them produces artificial jumps that are an artifact of weighting, not of crowd behavior.

A consumer that persists more than one horizon per asset keeps that discriminator in **its own** column (see §8) — a consumer-schema concern, not a change to the module's model.

### Coverage

**AGG-13** makes partial coverage visible rather than smoothed over: an unconfigured Discord (**HLD-10**) and a timed-out X both shrink \(A\). A snapshot is therefore **a reading of what was actually collected**, not of everything that was said.

**SNP-08 — Coverage travels with the reading; the module never normalizes across it.** `source_coverage` is on the model precisely so a consumer can see that a value came from one live source rather than four. Projection MUST NOT rescale, extrapolate, or "coverage-correct" any field to guess what full coverage would have shown (`CON-04`) — that would be inventing the missing sources' contribution. `source_coverage` and `source_agreement` together let a consumer downweight or filter thin readings; how it weights them is its analysis, not this module's claim. (`source_agreement = 0` at \(m < 2\) is doc 09 **MOM-09**'s deliberate refusal to call one live source self-agreement, not a coverage penalty applied twice.)

---

## 7. What a sequence of snapshots supports *(FR-SNAP-03)*

The shape must let later analysis run **without re-fabricating past data**. Spec §11's target analyses split cleanly into what the snapshot sequence carries and what must come from outside this module.

**Derivable from a sequence of snapshots alone**

| Analysis | How, from the retained fields |
|----------|-------------------------------|
| **Sentiment acceleration** | Successive differences of `sentiment_score` over real `observed_at` deltas, then their rate of change. This is the **cross-run** derivative and belongs to the consumer; doc 09 **MOM-08**'s \(\alpha\) is the **intra-run** 1h derivative and is a different quantity. Compute only across adjacent *actual* observations; a gap yields "unknown", not a straight line. |
| **Regime shifts** | Sustained movement of `classification`, `sentiment_score` level, and `social_heat` across a run of consecutive snapshots — a durable change rather than one reading. |
| **Burst characterisation** | `social_heat` and `mention_velocity` peaks, read against `organic_score` and `manipulation_risk` at the same instant, so a manufactured burst is distinguishable from a broad one (`FR-MAN-03`). |
| **Evidence quality over time** | `mention_count`, `source_coverage`, `source_agreement` — how much was actually seen, and how consistent the sources were, for every point on the curve. |
| **Time-delta statements** | Two real observations support "moved from +18 to +67 in 3 hours" (**MOM-10**, `FR-MOM-06`) — because both endpoints were observed, not inferred. |

**Requires data this module does not have**

| Analysis | What is missing |
|----------|-----------------|
| **Price / social divergence** | A price series. The consumer joins its own price history on `observed_at`. This module supplies the social side only. |
| **Correlation with future 4h / 24h / 7d outcomes** | Forward outcome labels from the consumer's market data. Alignment must respect irregular cadence — nearest-preceding observation, never interpolation across a gap (**SNP-09**). |
| **Whether a social burst preceded or followed a price move** | Price-move timestamps, plus dense enough snapshot cadence to order the two. If the surrounding interval has no observation, the honest answer is **unknown** — never "the burst came first because there is no earlier row". Cadence is a consumer choice (§8), so this resolution is bought by the consumer, not asserted by the module. |

**SNP-12 — Explicitly not in this module.** No price data, no OHLCV ingestion, no market-data adapter, no correlation engine, no historical regime classifier, no backtester, no prediction or forecast of any kind. This module produces the honest social series that such analysis would need; it does not perform the analysis (`00-project-vision.md`, `01-product-goals.md`: **context**, not signal).

**SNP-13 — No trade language, no price claim.** A positive or `strong_positive` snapshot is crowd tone, never a bullish price claim (`FR-SENT-06`). High `social_heat` or high `mention_velocity` is activity, never a directional call (**MOM-02**, `FR-MOM-07`). A spike is an anomaly/risk (`FR-MAN-04`). No snapshot field, value, index name, table name, or example in this document may be or contain `BUY`, `SELL`, `LONG`, or `SHORT` (`FR-INT-04`). A consumer's derived analysis inherits this constraint.

---

## 8. Consumer persistence guidance *(non-normative)*

The module hands over a value; the consumer owns the store. This section is **advice for that consumer**, not a deliverable of this repository (**SNP-03**, **HLD-09**, `CON-02`).

**SNP-14 — Persistence hygiene the module guarantees, and the consumer must not undo.**

- **JSON-safe primitives only.** Every snapshot field is a `str`, `int`, `float`, or string enum — no `bytes`, no `NaN`/`Inf`, no datetime objects at the boundary (`NFR-JSON-01`, doc 05 conventions). `observed_at` is an **ISO-8601 UTC** string (§4).
- **Nothing sensitive crosses.** By construction the snapshot carries **no** mention text, no author handles or `author_id_hash` values, no URLs or `url_hash` values, no PII, no credentials, and no raw provider errors or stack traces (`FR-NORM-03`, `FR-SRC-06`, `NFR-SEC-04`, **AGG-06**, **CCH-19**). It is eleven scalars and an asset id. A consumer MUST NOT enrich a persisted row with mention text or author identifiers "for traceability".
- **Idempotent identity.** `(asset_id, observed_at)` identifies an observation (**SNP-05**). Re-projecting the same run — including a cache-served repeat — yields the same row, so an upsert on that pair is safe and duplicate-free.
- **Append-only.** Rows are never amended in place (**SNP-09**). A better-covered later reading is a new row.

**Indicative shape sketch** — one column per doc 05 field, plus whatever discriminator the consumer needs. Illustrative only: **no DDL, no migration, and no ORM model is shipped by this module.**

| Column | From | Note |
|--------|------|------|
| `asset_id` | snapshot | Join key; part of the natural identity |
| `observed_at` | snapshot | UTC instant; part of the natural identity |
| `sentiment_score`, `social_heat`, `mention_count`, `mention_velocity`, `organic_score`, `manipulation_risk`, `source_coverage`, `source_agreement`, `classification` | snapshot | Copied verbatim; **SNP-06** gating applies on read |
| *horizon discriminator* | **consumer** | Only if the consumer persists more than one horizon per asset (**SNP-07**). It is the consumer's column, not a snapshot field. |

Useful access patterns are "latest reading for an asset" and "ordered window for an asset", so an index on `(asset_id, observed_at DESC)` — plus the horizon discriminator when present — covers the queries §7 implies. Uniqueness on the same tuple enforces the idempotent identity above.

**Retention and cadence belong to the consumer.** How often the engine is invoked and how long rows are kept are the consumer's operational choices (they depend on its Celery schedule, storage budget, and the analysis window it cares about). This module invents no retention policy, no rollup, no downsampling, and no compaction — and it notes only that **cadence sets the resolution of every §7 conclusion**, and that deleting old rows permanently removes evidence that cannot be re-collected (**SNP-09**).

---

## Decision index

| ID | Decision |
|----|----------|
| **SNP-01** | The snapshot is the module's only durable-evidence shape; everything else expires or is per-call. |
| **SNP-02** | `engine.py` projects it, purely and deterministically, from the same computed state as the run result; the public form is a pure adapter over the returned dict and MUST agree with it. |
| **SNP-03** | The module never persists: no DB connection, migration, ORM, or scheduler (**HLD-09**, `CON-02`). |
| **SNP-04** | Eligibility: \(A = 0\) → **no snapshot**; quiet / `insufficient_data` / `market_wide` / partial / cache-served → an honest snapshot. Never a snapshot of invented scores. |
| **SNP-05** | `observed_at` is the run's observation anchor \(t_0\), never write time; a cache-served run reuses the cached instant, so `(asset_id, observed_at)` identifies one observation. |
| **SNP-06** | `classification`, `mention_count`, and `source_coverage` gate the numeric fields; `0` under `insufficient_data` means *no measurement*, not neutral tone. |
| **SNP-07** | No `horizon` field is added; the module guarantees per-run determinism, the consumer partitions its series and MUST NOT interleave horizons. |
| **SNP-08** | Coverage travels with the reading; no coverage-correction, rescaling, or extrapolation to imagined full coverage. |
| **SNP-09** | Forward-collected only: no backfill, fabrication, interpolation, re-derivation, or amendment. Gaps stay visible as gaps. |
| **SNP-10** | An expired or missing baseline is unknown and is never reconstructed from history into a snapshot (**MOM-04**, **CCH-08**, **CCH-14**). |
| **SNP-11** | The snapshot is not the contract dict: no `name` / `role` / `source_class`, no `metrics` / `sources` / `anomalies` / `data_quality`. |
| **SNP-12** | Out of this module: price data, correlation engine, historical regime classifier, backtesting, prediction. |
| **SNP-13** | Crowd tone is never a price claim; no `BUY` / `SELL` / `LONG` / `SHORT` in any field, index, or example. |
| **SNP-14** | Persistence hygiene: JSON-safe primitives, ISO-8601 UTC, no PII / text / handles / credentials / raw errors; idempotent on `(asset_id, observed_at)`; append-only. Retention and cadence are the consumer's. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| `SocialSentimentSnapshot` / `AggregateResult` / `SourceBundle` field lists (**MOD-**) | doc 05 |
| Provider I/O and fetch isolation (**PRV-**) | doc 06 |
| Matching and collision rules (**MAT-**) | doc 07 |
| Polar scoring, classification ladder, confidence (**SCR-**) | doc 08 |
| Heat / velocity / acceleration / agreement formulas (**MOM-**) | doc 09 |
| Manipulation ratios (**MAN-**) | doc 10 |
| Combining sources, `scope`, coverage confidence (**AGG-**) | doc 11 |
| Redis keys, TTLs, baseline records (**CCH-**) | doc 12 |
| Full `build_social_sentiment` JSON, `observed_at` in the contract, `data_quality` scales | doc 14 |
| Horizon lookback windows and weight tables | doc 15 |
| Snapshot-projection test matrix (all-down → none; quiet → row; cache-served idempotence; JSON-safety) | doc 17 |
| PostgreSQL schema, migrations, Celery scheduling, retention | **consumer** (out of scope — **HLD-09**, `CON-02`) |
