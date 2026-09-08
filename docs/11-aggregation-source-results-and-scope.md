# 11 — Aggregation, Source Results & Scope

> *Traces to specification §8 (per-source results / aggregation) and §9 (asset-specific vs market-wide).*
> This is the **combine-and-scope design** beneath `03-high-level-design.md` (**HLD-01** aggregation stage, **HLD-03**, **HLD-04**, **HLD-07**) and `04-low-level-design.md` (`aggregation.py`, `aggregate`). It owns one asset-level result, retained independent source rows, explicit `scope`, partial-coverage confidence shrinkage, and when asset-level `classification` keeps or overrides per-source `insufficient_data`. It does **not** redefine `SourceBundle` / `MatchBatch` / `ScoreBundle` / `ManipulationAssessment` / `AggregateResult` (doc 05, **MOD-**), polar formulas/lexicons (doc 08, **SCR-**), heat/velocity/acceleration or the pairwise agreement formula (doc 09, **MOM-**), matching/collision rules (doc 07, **MAT-**), provider I/O (doc 06, **PRV-**), manipulation ratios (doc 10, **MAN-**), cache keys (doc 12), snapshot persistence (doc 13), integration-contract JSON / `data_quality` scales (doc 14), or horizon weight tables (doc 15).

---

## Role relative to other docs

`00-project-vision.md` / `01-product-goals.md`: this stage produces **context**, not a signal (`FR-INT-04`, `FR-SENT-06`).

Matching **partitions** mentions (`MAT-05`, `MAT-06`); it does not set result `scope`. Scoring and manipulation emit **per-source** (and optional **concatenated**) bundles on `MatchBatch.asset_mentions` only (`SCR-03`, `MAN-01`). This document **combines** those bundles and sets `scope`. `quality.py` may later consume `organic_score` and coverage; it owns `data_quality` (doc 14).

---

## 1. Module boundary *(LLD aggregation module, LLD-04)*

```
def aggregate(
    per_source: Sequence[SourceBundle],
    match_batch: MatchBatch,
    horizon: str,
    *,
    scores: Mapping[ProviderId, ScoreBundle] | None = None,
    assessments: Mapping[ProviderId, ManipulationAssessment] | None = None,
    union_score: ScoreBundle | None = None,
    union_assessment: ManipulationAssessment | None = None,
) -> AggregateResult
```

Engine-assembled **attachments** (not extra fields on `SourceBundle` — **MOD-01**): `scores` / `assessments` keyed by provider, omitted or `None` when `status = unavailable`. Optional concatenated `union_score` / `union_assessment` on the union of `match_batch.asset_mentions`.

| | |
|---|---|
| **Who calls it** | `engine.py` only, after per-source `score()` / `assess()` (`FR-INT-01` stays on the engine). |
| **Pure function** | Same rows + same `MatchBatch` + same `horizon` → same `AggregateResult` (`NFR-DET-01`, **HLD-03**). No network, no Redis, no wall-clock (`NFR-STATE-01`). |
| **Must NOT** | Use an LLM (`CON-01`); fetch; match; recompute polar lexicons (**SCR-**), heat/velocity/acceleration (**MOM-** except **applying** `MOM-09`), or manipulation ratios (**MAN-**); invent sources or scores (`CON-04`, **HLD-07**); promote `market_wide` into asset evidence (`MAT-05`, `FR-SCOPE-03`); emit `BUY`/`SELL`/`LONG`/`SHORT` (`FR-INT-04`); leak credentials, tokens, stack traces, or raw provider errors (`FR-AGG-03`, `FR-SRC-06`, **MOD-02**). |

**AGG-01 — Engine-only orchestration.** Providers must not aggregate (**PRV-07**, **LLD-04**). Matching must not aggregate or set `scope` (`MAT-05`, `MAT-06`). Aggregation must not call adapters (**HLD-01**).

**AGG-02 — Consume, do not flatten.** Combining uses attached `ScoreBundle` / `ManipulationAssessment` values as already computed. Do not copy those types onto `SourceBundle`. Public per-source columns remain the **MOD** `SourceBundle` list (`FR-AGG-02`).

**AGG-03 — Horizon is already applied.** `horizon` is a run identity / pass-through (`intraday` \| `swing` \| `position`). Per-source `score()` already applied it (**HLD-04**, `FR-HOR-02`). Aggregation MUST NOT invent a second unofficial 15m/1h/6h/24h mix. Weight tables → doc 15. Until that table exists, every aggregation mix uses multiplier **1.0**.

---

## 2. Inputs and row retention *(FR-AGG-01, FR-AGG-02)*

**AGG-04 — One row per registered provider, including failures.** `per_source` length is the registry for this run (X, Reddit, Telegram, Discord today — **MOD-05**, **HLD-08**). Unavailable / unconfigured / timed-out sources stay as rows; they are **omitted from numeric evidence** (§4). The engine does not drop a failed provider to make coverage look complete (`FR-AGG-01`, **HLD-07**, **HLD-10**).

Each `SourceBundle` exposes at least (`FR-AGG-02`): `status`, `mention_count`, `unique_authors`, `sentiment_score`, `freshness_seconds`, and `error_class` on failure.

| `status` | `sentiment_score` | `error_class` | In evidence set \(E\)? |
|----------|-------------------|---------------|------------------------|
| `available` | From that source's `ScoreBundle` (may be `0` under **SCR-10**) | `null` | Yes |
| `unavailable` | `null` — do not fabricate | Safe class only (**MOD-02**) | No |

**AGG-05 — Empty available ≠ unavailable.** `available` + `mention_count = 0` is a successful quiet fetch (**LLD-02**, **MOD-08**). It is a retained row and counts toward coverage \(A\) (§6). It is **not** a **MOM-09** vote and does not invent a tone.

**AGG-06 — Redaction.** Rows and the aggregate MAY carry `error_class` ∈ {`not_configured`, `unauthorized`, `timeout`, `malformed`, `rate_limited`} (plus later **safe** classes). They MUST NEVER carry credentials, tokens, stack traces, exception `str()`, or raw API bodies (`FR-AGG-03`, `FR-SRC-06`, **MOD-02**, **HLD-10**).

`match_batch` is the matching partition for this run: `asset_mentions` (only scored evidence), `market_wide` (leftovers), `asset_match_score` (for doc 14 — unused here).

---

## 3. Scope *(FR-SCOPE-01–03, MAT-05, MAT-06)*

Closed set: `asset_specific` \| `market_wide` (`FR-SCOPE-01`). Matching does not choose this field; aggregation does.

Let \(N_a = |\texttt{asset\_mentions}|\), \(N_w = |\texttt{market\_wide}|\).

**AGG-07 — Scope from the partition, never by scoring leftovers.**

| Condition | `scope` | Asset-level polar evidence |
|-----------|---------|----------------------------|
| \(N_a \ge 1\) | `asset_specific` | `asset_mentions` only |
| \(N_a = 0\) and \(N_w \ge 1\) | `market_wide` | **None.** Do not score `market_wide` as the requested asset (`FR-SCOPE-03`, `SCR-03`) |
| \(N_a = 0\) and \(N_w = 0\) and \(A \ge 1\) | `asset_specific` | None (quiet). `classification = insufficient_data` |
| \(A = 0\) | Do not claim a complete asset reading | No scores (**HLD-07**, `FR-INT-05`) — engine `status = unavailable` |

**AGG-08 — Leftovers stay leftovers.** Generic crypto chatter MUST NEVER fill `AggregateResult.sentiment_score` / `social_heat` / `mention_velocity` / `organic_score`. It MUST NOT be merged into `asset_mentions` (`MAT-05`). When \(N_w \ge 1\), aggregation MAY emit one JSON-safe anomalies record `{ "type": "market_wide_leftovers", "mention_count": N_w }` so doc 14 can expose broader chatter **separately**. That record is a count, not a scored market tone.

---

## 4. Evidence set, votes, and **MOM-09** *(FR-MOM-05)*

Let \(P_{\mathrm{reg}} = |\texttt{per\_source}|\) (includes unavailable). Let \(A\) = count of rows with `status = available`. Let \(E\) = available rows.

**Vote list \(V\)** for agreement and for source-mean tone when no concatenated polar bundle is used: available rows whose attached `ScoreBundle.classification ≠ insufficient_data` and whose `sentiment_score` is not `null`. A missing source is **not** a vote. An **SCR-10** row is **not** a vote (**MOM-09**).

**AGG-09 — When to call `MOM-09`.** After \(V\) is built, apply doc 09 **MOM-09** to the `sentiment_score` values in \(V\). Do not restate the pairwise formula here. Unavailable / unconfigured / timed-out sources are omitted; \(m = |V| < 2\) → `source_agreement = 0` and `source_disagreement = 0` (one live source is **not** trivial self-agreement — **HLD-07**). Write both fields onto `AggregateResult`. Never invent a sibling score to complete a pair (`CON-04`).

---

## 5. Combining available metrics *(FR-AGG-01)*

Let \(n_i\) = `mention_count` on evidence row \(i\) (asset-matched, already **MAT-05**-clean). Let \(o_i\) = that source's `organic_score` (**MAN-13**, range `[0, 100]`). Let \(C_i\), \(S_i\), \(H_i\), \(\nu_i\), \(R_i\) be that source's `confidence`, `sentiment_score`, `social_heat`, `mention_velocity`, `manipulation_risk`.

**AGG-10 — Concatenated bundles win for union physics.** If the engine supplied concatenated `score()` / `assess()` on `match_batch.asset_mentions`, use those values for asset-level `sentiment_score`, `classification` (subject to §7), `social_heat`, `mention_velocity`, `organic_score`, `manipulation_risk`, and raw polar `confidence` **before** the coverage factor (§6). Union heat/velocity is not the mean of per-source heats; union manipulation can see a campaign split across providers. Per-source rows stay independent (`FR-AGG-01`).

**AGG-11 — Fallback mix (no concatenated bundles).** Only over \(E\). If a denominator is 0, the corresponding asset-level numeric is `0` (or `null` for `sentiment_score` when \(A = 0\)), never a guessed history.

Tone weight (organic-quality down-weight so volume from a manipulated source cannot dominate crowd tone — `FR-MAN-03`, **SCR-06** does not fold heat into tone):

\[
w_i^{\mathrm{tone}} = n_i \cdot \frac{o_i}{100}
\]

Activity weight (a spike must remain visible as **activity**, while risk is a separate field — `FR-MOM-07`, `FR-MAN-04`):

\[
w_i^{\mathrm{act}} = n_i
\]

| Asset-level field | Fallback formula | Over |
|-------------------|------------------|------|
| `sentiment_score` | \(\sum w_i^{\mathrm{tone}} S_i / \sum w_i^{\mathrm{tone}}\); clip `[-100, +100]` | \(V\) with \(n_i > 0\) |
| `social_heat` | \(\sum w_i^{\mathrm{act}} H_i / \sum w_i^{\mathrm{act}}\) | \(E\) with \(n_i > 0\) |
| `mention_velocity` | \(\sum w_i^{\mathrm{act}} \nu_i / \sum w_i^{\mathrm{act}}\) | \(E\) with \(n_i > 0\) |
| `organic_score` | \(\sum w_i^{\mathrm{act}} o_i / \sum w_i^{\mathrm{act}}\) | \(E\) with \(n_i > 0\) |
| `manipulation_risk` | \(\sum w_i^{\mathrm{act}} R_i / \sum w_i^{\mathrm{act}}\) | \(E\) with \(n_i > 0\) |

If \(A = 0\): do **not** emit a fake mix (`FR-INT-05`, **HLD-07**). `sentiment_score` is `null`; heat/velocity/organic/manipulation_risk are omitted or `0` only as honest empty numerics, never as a complete-looking board.

**AGG-12 — Counts from the partition, not a sum of unique authors.** `mention_count` = \(N_a\). `unique_authors` = distinct `author_id_hash` on `asset_mentions` (same definition as doc 09's \(U\) on the union). Do not sum per-source `unique_authors` (cross-source authors would double-count).

Do not mix horizons. Do not add heat or velocity into `sentiment_score` (**SCR-06**).

---

## 6. Partial coverage shrinks confidence *(FR-AGG-04, HLD-07)*

Per-source `confidence` is **SCR-13** (sample/polarity inside one source). This layer adds **provider** coverage. `quality.py` owns `provider_coverage` / `data_quality` scales (doc 14); this formula is the aggregate `confidence` field.

Let \(C_{\mathrm{raw}}\) = concatenated `ScoreBundle.confidence` if present; else mention-weighted mean \(\sum n_i C_i / \sum n_i\) over \(E\) with \(n_i > 0\); else `0`.

\[
\mathrm{confidence} = \mathrm{clip}_{[0,100]}\!\left( C_{\mathrm{raw}} \times \frac{A}{P_{\mathrm{reg}}} \right)
\]

**AGG-13 — \(A / P_{\mathrm{reg}}\) is mandatory.** Unconfigured Discord (**HLD-10**) and a timed-out X both reduce \(A\). Only-Telegram available → factor \(1/P_{\mathrm{reg}}\) (typically \(1/4\)), not “full confidence from one busy source.” Do not inflate \(P_{\mathrm{reg}}\) down to the live set — that would make partial coverage look complete (`FR-AGG-04`).

Unavailable rows are omitted from \(C_{\mathrm{raw}}\) (no fake \(C_i = 0\) **and** a coverage factor — that would double-penalize). The coverage factor is the sole missing-source penalty.

---

## 7. Asset-level `classification` vs **SCR-10** *(FR-SCOPE-02, FR-SENT-03, MOD-03)*

Closed labels: the **MOD-03** / **SCR** set only. Per-source **SCR-10** (\(N_{\min} = 5\) non-empty asset-matched mentions) stays on each row. This section owns the **asset-level** label.

Let \(n_{\mathrm{eff}}\) = count of `asset_mentions` with non-empty `text` (same gate **SCR-10** uses; aggregation does not re-run lexicons).

**AGG-14 — Keep / override table.** First matching row wins.

| # | Condition | Asset-level `classification` |
|---|-----------|------------------------------|
| 1 | \(A = 0\) | `insufficient_data` (no invented tone). Engine sets contract `status = unavailable` (`FR-INT-05`). Distinguish from quiet via source `error_class` rows. |
| 2 | `scope = market_wide` (table **AGG-07**) | `insufficient_data` — leftovers are not an asset tone (`FR-SCOPE-02`, `FR-SCOPE-03`) |
| 3 | \(n_{\mathrm{eff}} < 5\) | **Keep** `insufficient_data` even if some source row looks busy with empty text, and even if fallback \(S_{\mathrm{agg}}\) is large (**SCR-10**, `MAT-06`, `FR-SCOPE-02`). Scores stay honest zeros/`0` from upstream, not a fabricated `positive`. |
| 4 | \(n_{\mathrm{eff}} \ge 5\) and concatenated `ScoreBundle` present | **Override** per-source `insufficient_data`: use the concatenated `classification` (union can clear \(N_{\min}\) when each source did not). |
| 5 | \(n_{\mathrm{eff}} \ge 5\) and no concatenated polar bundle | **Override** per-source `insufficient_data`. If \(V\) contains at least one \(S \ge 20\) and at least one \(S \le -20\), label `mixed` (consume **SCR-11** thresholds \(S_{\mathrm{tone}} = 20\), \(S_{\mathrm{strong}} = 60\); do not copy phrase lexicons). Else map \(S_{\mathrm{agg}}\) with the same ladder: \(\ge 60\) `strong_positive`, \(\ge 20\) `positive`, \(\le -60\) `strong_negative`, \(\le -20\) `negative`, else `neutral`. |

Never inflate a quiet or collision-starved asset into `positive` by relaxing matching (**MAT-06**) or by scoring `market_wide`.

Positive / `strong_positive` is **not** bullish price (`FR-SENT-06`). No `bullish`/`bearish` class strings (**SCR-11**).

---

## 8. Anomalies list *(FR-MAN-04, FR-MOM-07, MAN-11)*

Inputs: attached / concatenated `ManipulationAssessment` (`organic_score`, `manipulation_risk`, `anomaly_score` — **MAN-11**–**13**). Do not recompute ratios.

**AGG-15 — Threshold \(\theta_{\mathrm{flag}} = 0.50\).** Midpoint of the `[0, 1]` composites: at least as much risk/anomaly mass as not. Not a trade trigger.

JSON-safe records only (`NFR-JSON-01`). Shape:

```
{ "type": str, "provider": str | null, "score": float, "detail": str }
```

`provider` is **MOD-05** or `null` (asset-level). `detail` is a short label (`manipulation_risk`, `anomaly_score`, `source_disagreement`, `market_wide_leftovers`) — never a stack trace or raw error.

| Emit when | `type` | `score` |
|-----------|--------|---------|
| Concat or any evidence-row `anomaly_score` \(\ge 0.50\) | `anomaly` | that `anomaly_score` |
| Concat or any evidence-row `manipulation_risk` \(\ge 0.50\) | `manipulation_risk` | that `manipulation_risk` |
| **MOM-09** \(m \ge 2\) and `source_agreement < 0.50` | `source_disagreement` | `source_disagreement` |
| **AGG-08** leftovers | `market_wide_leftovers` | \(N_w\) as integer count (not a risk ratio) |

One record per `(type, provider)` pair. A spike/anomaly is **risk/context**. It MUST NOT flip `classification` toward `positive` and MUST NOT be labeled bullish (`FR-MAN-04`, `FR-MOM-07`, **SCR-06**).

Never emit `BUY`, `SELL`, `LONG`, `SHORT`, or price-direction wording (`FR-INT-04`).

---

## 9. All-down, partial, and quiet *(HLD-07)*

| Situation | Aggregation |
|-----------|-------------|
| All `unavailable` | No fake mix. Rows retained with `error_class`. `confidence = 0`. Engine `status = unavailable` (`FR-INT-05`). |
| One source available (e.g. Telegram) | Partial result from \(E\); `confidence` × \(A/P_{\mathrm{reg}}\); **MOM-09** agreement `0`. |
| Mix of available + `not_configured` / `timeout` | Omit the dead from \(E\) and \(V\); keep their rows; shrink confidence. |
| All available, \(N_a = 0\) | Quiet: `insufficient_data`, `scope` per **AGG-07**. Not `unavailable`. |
| `n_{\mathrm{eff}} < 5` but \(N_w\) large | `insufficient_data`; optional leftovers record; **never** score leftovers as the asset. |

Redis miss is not a missing source (doc 12 / **LLD-08**); it does not change \(A\).

---

## 10. Outputs *(`AggregateResult`, doc 05)*

| Field | Rule |
|-------|------|
| `scope` | **AGG-07** |
| `classification` | **AGG-14** |
| `sentiment_score` | §5; `null` if \(A = 0\) |
| `confidence` | **AGG-13** |
| `social_heat`, `mention_velocity`, `organic_score`, `manipulation_risk` | §5 (consume **MOM-** / **MAN-** values) |
| `source_agreement` | **AGG-09** / **MOM-09** |
| `source_disagreement` | **AGG-09** / **MOM-09** (`1 - agreement`; `0` if \(m < 2\)) |
| `mention_count` | \(N_a\) |
| `sources` | Full `per_source` list, unmodified except that attachments are not serialized on the row (`FR-AGG-01`) |
| `anomalies` | **AGG-15** |

Engine maps this object into the public dict (doc 14). This module does not set `name` / `role` / `source_class` / `data_quality`.

---

## Decision index

| ID | Decision |
|----|----------|
| **AGG-01** | Only `engine.py` calls `aggregate()`; providers (**PRV-07**) and matching do not combine sources or set `scope`. |
| **AGG-02** | Combine attached `ScoreBundle` / `ManipulationAssessment`; do not extend `SourceBundle` fields. |
| **AGG-03** | No second horizon mix; doc 15 owns weight tables. |
| **AGG-04** | Retain one row per registered provider, including unavailable. |
| **AGG-05** | `available` + `[]` is quiet, not down (**LLD-02**, **MOD-08**). |
| **AGG-06** | Safe `error_class` only; no secrets, traces, or raw errors. |
| **AGG-07** | `scope` from \(N_a\) / \(N_w\); never from leftover scoring. |
| **AGG-08** | `market_wide` MAY appear as a count record; never as asset mentions or asset scores. |
| **AGG-09** | Call **MOM-09** on votes \(V\) only; omit missing and **SCR-10** sources. |
| **AGG-10** | Concatenated bundles, when present, are the asset-level polar/activity/manipulation numbers. |
| **AGG-11** | Fallback: tone weighted by \(n_i \cdot o_i/100\); heat/velocity/organic/risk weighted by \(n_i\). |
| **AGG-12** | Union `mention_count` / `unique_authors` from `asset_mentions`, not summed uniqueness. |
| **AGG-13** | `confidence = C_raw × A / P_reg`; partial coverage cannot look complete. |
| **AGG-14** | Keep `insufficient_data` when \(A = 0\), `scope = market_wide`, or \(n_{\mathrm{eff}} < 5\); override per-source **SCR-10** when the union clears \(N_{\min}\). |
| **AGG-15** | Anomalies from **MAN-11**/`manipulation_risk` at \(0.50\), disagreement, leftovers; never trade advice. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Field lists for `SourceBundle` / `AggregateResult` / `MatchBatch` / **MOD-** | doc 05 |
| Polar scoring, classification tree, phrase lexicons | doc 08 (**SCR-**; this doc consumes **SCR-10** / **SCR-11** thresholds) |
| Heat / velocity / acceleration; pairwise agreement formula | doc 09 (**MOM-**; this doc applies **MOM-09**) |
| Matching, collisions, `market_wide` partition | doc 07 |
| Provider I/O | doc 06 |
| Manipulation ratio formulas | doc 10 (**MAN-**; this doc maps scores into `anomalies`) |
| Redis keys / TTLs | doc 12 |
| Snapshot persistence | doc 13 |
| Full `build_social_sentiment` JSON and `data_quality` scales | doc 14 |
| Horizon weight tables | doc 15 |
