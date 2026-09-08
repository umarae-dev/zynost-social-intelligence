# 14 — Integration Contract & Data Quality

> *Traces to specification §12 (main Zynost integration contract) and §14 (provider provenance / data quality).*
> This is the **public surface** beneath `03-high-level-design.md` (**HLD-01**, **HLD-07**, **HLD-09**) and `04-low-level-design.md` (**LLD-04**). It owns the single async entry point, the exact JSON-serializable result dict, how `engine.py` maps `AggregateResult` / `ProvenanceRecord` / `DataQuality` / run `observed_at` into that dict, the `data_quality` object and its numeric scales, and honest provenance. It does **not** own field-level internal schemas (doc 05, **MOD-**), matching rules or the `asset_match_score` *algorithm* (doc 07, **MAT-** — this doc consumes the score and owns the public scale), polar/heat/manipulation/aggregation formulas (docs 08–11, **SCR-** / **MOM-** / **MAN-** / **AGG-**), Redis keys or TTLs (doc 12, **CCH-**), snapshot projection / eligibility / persistence (doc 13, **SNP-**), or horizon weight tables (doc 15).

---

## Role relative to other docs

This document **consumes and never redefines**:

| Consumed | Owner |
|----------|-------|
| `AssetIdentity`, `AggregateResult`, `SourceBundle`, `MatchBatch`, `ScoreBundle`, `ManipulationAssessment`, `ProvenanceRecord`, `DataQuality` | doc 05 **MOD-01**; sketches below fill public mapping only |
| Closed `error_class`; closed classification; provider set; empty ≠ unavailable; no hidden global state | **MOD-02**, **MOD-03**, **MOD-05**, **MOD-08**, **MOD-09** |
| One public async function; stages do not orchestrate | **LLD-04**, `FR-INT-01` |
| FastAPI / PostgreSQL / Celery | **HLD-09**, `CON-02` — consumer concerns |
| Snapshot is not this dict | **SNP-11** |
| Cached-aggregate `observed_at` / freshness = collection time | **CCH-17**, **SNP-05**, `FR-PROV-03` |
| Asset-level `confidence = C_raw × A/P_reg` | **AGG-13** — this doc owns how `confidence` and `data_quality.provider_coverage` appear **without double-counting** |
| Leftovers as a count, never asset tone | **AGG-08** |
| `insufficient_data` gates | **SCR-10**, **AGG-14** |
| All-unavailable: clean `status = unavailable`, no invented scores | `FR-INT-05`, **HLD-07**, **AGG-14** row 1, **SNP-04** |

Vision / goals (`00`, `01`): this surface is **context**, never a signal (`FR-INT-04`, `FR-SENT-06`).

---

## 1. Entry point *(FR-INT-01, LLD-04, HLD-09)*

**INT-01 — One public async function.** Everything else is internal.

```python
async def build_social_sentiment(
    asset: AssetIdentity,
    horizon: str = "swing",
) -> dict
```

| | |
|---|---|
| **Who calls it** | Downstream consumers (later FastAPI/Celery). Tests. Package `__init__` MAY re-export this function only. |
| **Must NOT** | Raise when every provider is down (`FR-INT-05`); persist to PostgreSQL; open FastAPI routes; talk to Celery; import LLM SDKs (`CON-01`); emit `BUY`/`SELL`/`LONG`/`SHORT` (`FR-INT-04`). |

**INT-02 — Unrecognized horizon is safe (`FR-HOR-03`).** Closed applied set is `intraday` \| `swing` \| `position` (`FR-HOR-01`). If `horizon` is missing, empty, or not in that set: **apply `swing`**, never crash, never raise. Report both the requested string and the applied value in `metrics` (§6). Exact window/weight tables stay doc 15; this layer does not invent a second mix (**AGG-03**, **HLD-04**).

The snapshot projector (`project_snapshot` / **SNP-02**) is **not** a second public async entry point. It is a pure adapter over a dict this function already returned.

---

## 2. Frozen identity and closed enums *(FR-INT-02–04, MOD-03)*

**INT-03 — Identity values are constants on every return, including `unavailable`.**

| Field | Value | Implements |
|-------|--------|------------|
| `name` | `"social_sentiment"` | `FR-INT-03` |
| `role` | `"context"` | `FR-INT-03` |
| `source_class` | `"multi_source_social_intelligence"` | `FR-INT-03` |

**INT-04 — Closed enums (no others).**

| Field | Values |
|-------|--------|
| `status` | `available` \| `unavailable` |
| `scope` | `asset_specific` \| `market_wide` |
| `classification` | **MOD-03**: `strong_positive`, `positive`, `neutral`, `mixed`, `negative`, `strong_negative`, `insufficient_data` |

`status = unavailable` means **no provider produced evidence** (\(A = 0\)). Quiet successful fetches (\(A \ge 1\), \(N_a = 0\)) are `status = available` with `classification = insufficient_data` (**MOD-08**, **AGG-05**, **AGG-07**).

Positive / `strong_positive` is crowd tone, never a bullish price claim (`FR-SENT-06`). No field, key, enum, `metrics` name, `anomalies.detail`, or example MAY be or contain `BUY`, `SELL`, `LONG`, or `SHORT` (`FR-INT-04`). Phrase intensities named `bullish_*` / `bearish_*` in `metrics` are **lexicon tracks** (**SCR-12**), not trade advice and not classification labels.

---

## 3. Exact contract skeleton *(FR-INT-02, NFR-JSON-01)*

Every return — success, partial, quiet, cache-served, or all-unavailable — contains **exactly these required keys**. No extra required top-level keys. Unknown keys MUST NOT be added by `engine.py`.

```json
{
  "name": "social_sentiment",
  "role": "context",
  "status": "available",
  "scope": "asset_specific",
  "classification": "positive",
  "sentiment_score": 63.4,
  "confidence": 78.0,
  "social_heat": 84.1,
  "mention_velocity": 2.8,
  "organic_score": 71.0,
  "source_agreement": 0.76,
  "manipulation_risk": 0.17,
  "metrics": {},
  "sources": {},
  "anomalies": [],
  "observed_at": "2026-09-07T15:00:00Z",
  "source_class": "multi_source_social_intelligence",
  "data_quality": {}
}
```

### Required-key types and ranges

| Key | JSON type | Scale / closed set | Sourced from |
|-----|-----------|-------------------|--------------|
| `name`, `role`, `source_class` | string | **INT-03** | engine constants |
| `status` | string | **INT-04** | \(A = 0\) → `unavailable`; else `available` |
| `scope` | string | **INT-04** | `AggregateResult.scope` (**AGG-07**). If \(A = 0\): `asset_specific` (the request was about this asset; do not invent `market_wide`) |
| `classification` | string | **MOD-03** | `AggregateResult.classification` (**AGG-14**) |
| `sentiment_score` | number \| `null` | `[-100, +100]` or `null` | **AGG-10**/**AGG-11**; `null` iff \(A = 0\) |
| `confidence` | number | `[0, 100]` | **AGG-13** as already computed. Engine MUST NOT multiply by `provider_coverage` again (**QLY-05**) |
| `social_heat` | number \| `null` | `[0, 100]` or `null` | aggregate; `null` iff \(A = 0\) |
| `mention_velocity` | number \| `null` | finite number or `null` | aggregate; `null` iff \(A = 0\) |
| `organic_score` | number \| `null` | `[0, 100]` or `null` | aggregate (**MAN-13**); `null` iff \(A = 0\) |
| `source_agreement` | number \| `null` | `[0, 1]` or `null` | **AGG-09** / **MOM-09**; `null` iff \(A = 0\) (do not print `0` as “disagreement” when nobody voted) |
| `manipulation_risk` | number \| `null` | `[0, 1]` or `null` | aggregate; `null` iff \(A = 0\) |
| `metrics` | object | §6 allowlist | `ScoreBundle` extras + horizon report |
| `sources` | object | §7, keyed by **MOD-05** | retained `SourceBundle` rows (**AGG-04**) |
| `anomalies` | array | §8 | **AGG-15** as-is |
| `observed_at` | string | ISO-8601 UTC | run \(t_0\) (**MOM-03**, **SNP-05**, **CCH-17**) |
| `data_quality` | object | §9 | `quality.py` |

**INT-05 — `null` vs `0` on the board.** When \(A = 0\), asset-level score fields are **`null`**, not `0`. A quiet hour (\(A \ge 1\), \(N_a = 0\)) uses the honest empty numerics aggregation already emits (typically `0`) with `status = available` and `classification = insufficient_data`. Collapsing all-down into zeros would be indistinguishable from quiet and would invent a complete-looking board (**HLD-07**, `CON-04`, same reasoning as **SNP-04** row 1 / **CCH-15**).

`confidence = 0` is valid in both cases (no usable confidence). It is not a tone.

---

## 4. JSON-safety *(NFR-JSON-01, doc 05 conventions)*

**INT-06 — Primitives only at the boundary.** After mapping, `engine.py` sanitizes the dict so `json.dumps` succeeds without a default hook:

- Types: `str`, `int`, `float`, `bool`, `null`, and lists/objects of the same.
- `observed_at` is an ISO-8601 UTC **string** (e.g. `2026-09-07T15:00:00Z`), never a datetime object.
- Numbers MUST be finite. `NaN` / `±Inf` → treat as `null` on nullable fields; on fields that must be numeric when \(A \ge 1\), clip to the documented range instead of emitting non-JSON.
- No `bytes`, no sets, no tuples left unconverted, no exception objects.

This is the same convention models already require at the public boundary (doc 05). Internal dataclasses stay internal.

---

## 5. Engine mapping *(LLD-04)*

**INT-07 — `engine.py` orchestrates; it does not recompute formulas.** Call graph is already **LLD-04** / **CCH-08**: cache probe → gather → match → score → manipulate → aggregate → `build_provenance` → `compute_data_quality` → sanitize dict. Snapshot projection is **beside** this assembly (**SNP-02**), not inside the dict (**SNP-11**).

| Contract field | Mapping |
|----------------|---------|
| Identity trio | **INT-03** constants |
| `status` | `unavailable` iff \(A = 0\) else `available` |
| `scope`, `classification`, top-level scores, `confidence` | copy from `AggregateResult` with **INT-05** nulling when \(A = 0\) |
| `metrics` | §6 from concatenated `ScoreBundle` when present, else omit optional keys |
| `sources` | §7 from `AggregateResult.sources` |
| `anomalies` | §8 from `AggregateResult.anomalies` |
| `observed_at` | run \(t_0\); on aggregate cache hit, the **cached** instant (**CCH-17**), not cache-read time |
| `data_quality` | `DataQuality` → public object (§9) |

On an aggregate cache hit, provenance and quality are **reassembled** from the cached `AggregateResult`, the cached \(t_0\), and the quality-input scalars that travel with that envelope (`asset_match_score`, plus the duplicate/observation counts provenance needs). The public `data_quality` object itself is **not** what Redis stores (**CCH-05**). `MatchBatch` is not cached (**CCH-06**); that is why the match-score **scalar** must ride with the aggregate envelope as an input, not as a second pipeline.

A Redis miss is **not** written into `sources` or `error_class` (**CCH-13**).

---

## 6. `metrics` *(extras only)*

Top-level already carries `sentiment_score`, `confidence`, `social_heat`, `mention_velocity`, `organic_score`, `source_agreement`, `manipulation_risk`. `metrics` MUST NOT repeat those keys.

**INT-08 — Allowlist.** Each value is a JSON-safe number, `null`, or a closed string. Omit a key when the input is unknown (missing baseline ≠ `0` — **MOM-04**, **CCH-14**, **SCR-13**). Do not dump formulas, mention text, or PII.

| Key | When present | Source (consumed, not redefined) |
|-----|----------------|----------------------------------|
| `horizon_requested` | always | raw `horizon` argument, size-capped string |
| `horizon_applied` | always | `intraday` \| `swing` \| `position` after **INT-02** |
| `mention_count` | \(A \ge 1\) | **AGG-12** \(N_a\) |
| `unique_authors` | \(A \ge 1\) | **AGG-12** |
| `social_acceleration` | known | **MOM** |
| `acceleration_state` | known | `accelerating` \| `holding` \| `fading` |
| `engagement_weighted_sentiment` | \(A \ge 1\) and polar bundle present | **SCR** |
| `positive_count` / `neutral_count` / `negative_count` | polar bundle present | **SCR** |
| `positive_ratio` / `neutral_ratio` / `negative_ratio` | polar bundle present | **SCR** |
| `bullish_phrase_intensity`, `bearish_phrase_intensity`, `fear_intensity`, `greed_excitement_intensity`, `uncertainty_intensity`, `euphoria_intensity` | polar bundle present | **SCR-12**, `[0, 100]` |
| `mentions_1h`, `mentions_prev_1h`, `delta_6h`, `delta_24h` | window **known** | **MOM**; omit if unknown |
| `engagement_velocity` | known | **MOM** |
| `activity_coverage` | known | **MOM** temporal occupancy — **not** `provider_coverage` |
| `source_disagreement` | \(A \ge 1\) | **MOM-09** / **AGG-09**, `[0, 1]` |
| `sentiment_change`, `sentiment_change_hours` | prior observation **known** | **MOM** / `FR-MOM-06`; omit if unknown — never backfill |

When \(A = 0\): `metrics` contains **only** `horizon_requested` and `horizon_applied`.

`market_wide` leftover volume is **not** a metric here; it is an anomalies count (**AGG-08**).

---

## 7. `sources` *(FR-AGG-01–03, FR-SRC-06, FR-PROV-02)*

**INT-09 — Object keyed by provider id, one key per registered source.** Keys are the **MOD-05** closed set (`x`, `reddit`, `telegram`, `discord`). Length equals \(P_{\mathrm{reg}}\) (**AGG-04**), including `unavailable` / `not_configured`. Engine MUST NOT drop a failed provider to look complete.

Each value:

| Field | Type | Rule |
|-------|------|------|
| `status` | `available` \| `unavailable` | same as `SourceBundle` |
| `mention_count` | int `≥ 0` | `0` when unavailable or quiet |
| `unique_authors` | int `≥ 0` | `0` when unavailable or quiet |
| `sentiment_score` | number \| `null` | `null` when unavailable — never fabricated (**AGG-04**) |
| `freshness_seconds` | number \| `null` | fetch/collection age relative to \(t_0\); on cache-served raw/aggregate, age of **that observation**, not of the read (**CCH-17**) |
| `error_class` | string \| `null` | `null` when available. When unavailable: **MOD-02** only (`not_configured`, `unauthorized`, `timeout`, `malformed`, `rate_limited`, plus later **safe** classes) |

**INT-10 — Redaction.** `sources` MUST NEVER contain credentials, tokens, stack traces, exception `str()`, raw API bodies, or provider error text (`FR-AGG-03`, `FR-SRC-06`, **AGG-06**, `NFR-SEC-04`).

Empty available (`mention_count = 0`) stays `status = available` and `error_class = null` (**MOD-08**).

---

## 8. `anomalies` *(AGG-15, FR-MAN-04)*

Pass through the **AGG-15** list unchanged: JSON-safe records `{ "type", "provider", "score", "detail" }`.

- A spike is risk/context. It MUST NOT flip `classification` toward `positive` and MUST NOT be labeled bullish (`FR-MAN-04`, `FR-MOM-07`).
- `type = market_wide_leftovers` carries leftover **count** \(N_w\) in `score` — not an asset tone (**AGG-08**).
- Never emit trade language.

When \(A = 0\): `anomalies = []`.

---

## 9. Provenance vs quality vs engine

```
provenance.build_provenance(outcomes, match_batch, aggregate, *, served_from_cache, observed_at, duplicate_content_ratio=None)
    -> ProvenanceRecord          # diary: who answered, counts, duplicate impact, freshness τ

quality.compute_data_quality(provenance, match_batch, aggregate, assessment)
    -> DataQuality               # the four FR-PROV-01 scales + supporting honesty fields

engine.py                        # identity, status, JSON sanitize, nulling (INT-05); only orchestrator
```

Both helpers are **pure** (`NFR-DET-01`, **HLD-03**, **MOD-09**): no Redis, no network, no wall-clock. Freshness uses stored `freshness_seconds` and \(t_0\), never `datetime.now()` at assembly (**CCH-17**, **MOM-03**).

### `ProvenanceRecord` *(FR-PROV-02–03)*

| Field | Type | Meaning |
|-------|------|---------|
| `providers_available` | `list[str]` | **MOD-05** ids with `status = available` |
| `providers_unavailable` | `list[str]` | the rest of \(P_{\mathrm{reg}}\) |
| `observation_count` | int | \(N_a\) — asset-matched mentions that informed the result |
| `unique_authors` | int | **AGG-12** |
| `duplicate_content_ratio` | float \| `null` | concatenated (or union) **MAN-04** when \(N_a \ge 1\); `null` when \(N_a = 0\) (no filter was applied). Copied from the keyword argument (live `ManipulationAssessment` or cache-envelope scalar); never recomputed here |
| `freshness_seconds` | float \| `null` | \(\tau\) = **max** of non-null `SourceBundle.freshness_seconds` over **available** rows (worst live lag). `null` if \(A = 0\) or every available row has `null` |
| `served_from_cache` | bool | `true` only for an **aggregate** cache hit. A Redis **miss** is `false` and is not a provider outcome (**CCH-13**) |
| `asset_match_score` | float | copy of `MatchBatch.asset_match_score` already on **QLY-03** scale |

Duplicate-filtering **impact**: `observation_count` plus `duplicate_content_ratio` (fraction of fingerprintable mentions that had a duplicate partner — **MAN-04**). This doc does not restate shingle math.

### `DataQuality` → public `data_quality`

**QLY-01 — Required keys (`FR-PROV-01`) plus enough for `FR-PROV-02`.**

| Key | Type | Range | Computation |
|-----|------|-------|-------------|
| `provider_coverage` | number | `[0, 1]` | \(A / P_{\mathrm{reg}}\) — **same ratio** **AGG-13** uses as a confidence **factor**. Unconfigured Discord and a timed-out X both shrink \(A\). Do not shrink \(P_{\mathrm{reg}}\) to the live set |
| `freshness_score` | number \| `null` | `[0, 100]` or `null` | **QLY-02** |
| `asset_match_score` | number | `[0, 1]` | **QLY-03** |
| `organic_data_ratio` | number \| `null` | `[0, 1]` or `null` | **QLY-04** |
| `providers_available` | array of strings | **MOD-05** | from provenance |
| `providers_unavailable` | array of strings | **MOD-05** | from provenance |
| `observation_count` | int | `≥ 0` | \(N_a\) |
| `unique_authors` | int | `≥ 0` | **AGG-12** |
| `duplicate_content_ratio` | number \| `null` | `[0, 1]` or `null` | from provenance |
| `freshness_seconds` | number \| `null` | `≥ 0` or `null` | \(\tau\) |
| `served_from_cache` | bool | | from provenance |

`provider_coverage` here is the public coverage reading. Snapshot `source_coverage` is the same ratio on a different object (**SNP** / doc 05) — not a third formula.

**QLY-02 — `freshness_score`.** Let \(T_{\mathrm{sat}} = 3600\) seconds (the MOM **current 1h** slice: evidence whose newest collection lag is a full hour has exhausted “now”). Not a Redis TTL (**CCH-09** is cache expiry, not social freshness).

\[
\mathrm{freshness\_score} =
\begin{cases}
\mathrm{null} & \tau\ \mathrm{is\ null} \\
\mathrm{clip}_{[0,100]}\big(100 \times (1 - \tau / T_{\mathrm{sat}})\big) & \mathrm{otherwise}
\end{cases}
\]

`null` means “no freshness claim,” not “perfectly fresh.” Quiet available sources with `freshness_seconds = null` therefore do **not** print `100`.

On a cache-served aggregate, \(\tau\) is whatever was stored on the cached source rows at **collection** \(t_0\). Do not recompute \(\tau\) from wall-clock minus `observed_at` (`FR-PROV-03`, **CCH-17**).

**QLY-03 — `asset_match_score` (public scale).** Range `[0, 1]`. Matching owns *how* each mention got `match_confidence` (**MAT-***); this doc owns the batch scale:

- \(N_a = 0\) → `0` (no asset matches — honest, not “unknown”)
- \(N_a \ge 1\) → mean of `match_confidence` over `asset_mentions`

`MatchBatch.asset_match_score` MUST already be this number (doc 07 deferred the scale here). Per-mention `match_confidence` is **not** the contract `confidence` field.

**QLY-04 — `organic_data_ratio`.** When there **is** asset-matched evidence (\(N_a \ge 1\) and \(A \ge 1\)): `organic_score / 100` from `AggregateResult` (**MAN-13** already `[0, 100]`).

When \(N_a = 0\) **or** \(A = 0\): **`null`**. Do **not** copy `assess([]).organic_score = 100` (**MAN-12** / **MAN-13** empty-set base case is “no manipulation observed,” not a public claim that missing chatter is organic). LLD quality **Must NOT** invent an organic ratio when mentions were unavailable.

**QLY-05 — No double-counting coverage.** `confidence` already includes \(A/P_{\mathrm{reg}}\) (**AGG-13**). `data_quality.provider_coverage` **reports** that ratio. Consumers MUST NOT multiply them again. Quality MUST NOT rewrite `AggregateResult.confidence`.

**QLY-06 — Honesty (`FR-PROV-03`).** MUST NOT report quality the collected evidence does not support:

| Situation | Forbidden claim |
|-----------|-----------------|
| Missing Redis baseline | disagreement, or a fabricated window delta (**CCH-14**, **SCR-13**) |
| Missing / timed-out / unconfigured source | agreement, or a vote (**MOM-09**, **AGG-09**, `FR-MOM-05`) |
| Redis miss / Redis down | a `sources` row or `error_class` (**CCH-13**) |
| \(A = 0\) | non-null `sentiment_score` / heat / velocity / organic_score / agreement / manipulation_risk; non-null `organic_data_ratio`; non-null `freshness_score` |
| Quiet \(N_a = 0\), \(A \ge 1\) | `organic_data_ratio` as 1.0; `classification` other than `insufficient_data` |
| `scope = market_wide` | leftovers scored as the asset (**AGG-08**, `FR-SCOPE-03`) |
| Partial coverage | `provider_coverage = 1` or unshrunk `confidence` |

---

## 10. Scenario matrix *(contract always returned; snapshot may be None)*

Aligned with **AGG-07** / **AGG-14**, **HLD-07**, **SNP-04**. The contract is **always** a dict. A snapshot is projected only when **SNP-04** allows; it is **never** this dict (**SNP-11**).

| Scenario | `status` | `scope` | `classification` | Top-level scores | `data_quality` highlights | Snapshot |
|----------|----------|---------|------------------|------------------|---------------------------|----------|
| All unavailable (\(A = 0\)) | `unavailable` | `asset_specific` | `insufficient_data` | **`null`** (except `confidence = 0`) | `provider_coverage = 0`; `freshness_score` / `organic_data_ratio` **`null`**; `asset_match_score = 0`; all providers in `providers_unavailable`; `observation_count = 0`; `metrics` = horizons only; `anomalies = []`; every `sources[*].error_class` set | **None** (**SNP-04** row 1) |
| Quiet: \(A \ge 1\), \(N_a = 0\), \(N_w = 0\) | `available` | `asset_specific` | `insufficient_data` | honest zeros from **AGG-11** | coverage \(A/P_{\mathrm{reg}}\); `organic_data_ratio = null`; `asset_match_score = 0` | Yes, empty reading |
| Only leftovers: \(N_a = 0\), \(N_w \ge 1\) | `available` | `market_wide` | `insufficient_data` | not leftover tone; zeros / empty asset board | `organic_data_ratio = null`; anomalies MAY include `market_wide_leftovers` with count \(N_w\) | Yes, `mention_count = 0` |
| Thin asset evidence (\(n_{\mathrm{eff}} < 5\)) | `available` | `asset_specific` | `insufficient_data` (**SCR-10**, **AGG-14**) | honest upstream numbers, not a fabricated `positive` | `observation_count = N_a`; organic ratio only if \(N_a \ge 1\) | Yes |
| Partial coverage (e.g. only Telegram) | `available` | per **AGG-07** | per **AGG-14** | from \(E\); `confidence` already × \(A/P_{\mathrm{reg}}\); agreement `0` if \(m < 2\) | `provider_coverage < 1`; failed ids listed; **CCH-13** does not list Redis | Yes |
| Cache-served aggregate | same as the cached observation | same | same | same numbers | same \(\tau\) / scores as collection; `served_from_cache = true`; `observed_at` = cached \(t_0\) | Yes, same observation (**SNP-05**) |
| Classified asset-specific (\(n_{\mathrm{eff}} \ge 5\)) | `available` | `asset_specific` | **MOD-03** tone label | full board | all four FR-PROV-01 fields populated (organic ratio defined) | Yes |

### All-unavailable JSON (normative example)

```json
{
  "name": "social_sentiment",
  "role": "context",
  "status": "unavailable",
  "scope": "asset_specific",
  "classification": "insufficient_data",
  "sentiment_score": null,
  "confidence": 0,
  "social_heat": null,
  "mention_velocity": null,
  "organic_score": null,
  "source_agreement": null,
  "manipulation_risk": null,
  "metrics": {
    "horizon_requested": "swing",
    "horizon_applied": "swing"
  },
  "sources": {
    "x":        {"status": "unavailable", "mention_count": 0, "unique_authors": 0, "sentiment_score": null, "freshness_seconds": null, "error_class": "timeout"},
    "reddit":   {"status": "unavailable", "mention_count": 0, "unique_authors": 0, "sentiment_score": null, "freshness_seconds": null, "error_class": "timeout"},
    "telegram": {"status": "unavailable", "mention_count": 0, "unique_authors": 0, "sentiment_score": null, "freshness_seconds": null, "error_class": "not_configured"},
    "discord":  {"status": "unavailable", "mention_count": 0, "unique_authors": 0, "sentiment_score": null, "freshness_seconds": null, "error_class": "not_configured"}
  },
  "anomalies": [],
  "observed_at": "2026-09-07T15:00:00Z",
  "source_class": "multi_source_social_intelligence",
  "data_quality": {
    "provider_coverage": 0.0,
    "freshness_score": null,
    "asset_match_score": 0.0,
    "organic_data_ratio": null,
    "providers_available": [],
    "providers_unavailable": ["x", "reddit", "telegram", "discord"],
    "observation_count": 0,
    "unique_authors": 0,
    "duplicate_content_ratio": null,
    "freshness_seconds": null,
    "served_from_cache": false
  }
}
```

This object is **not** a snapshot: it still carries `name` / `role` / `source_class` / `metrics` / `sources` / `anomalies` / `data_quality`, which **SNP-11** forbids on the snapshot. `observed_at` is the attempt’s \(t_0\) (when collection was tried), not an invented quiet hour.

`error_class` values in the example are illustrative members of **MOD-02**, not a required combination.

---

## Decision index

| ID | Decision |
|----|----------|
| **INT-01** | Sole public async API: `build_social_sentiment(asset, horizon="swing") -> dict` (**LLD-04**, `FR-INT-01`). |
| **INT-02** | Unknown horizon → apply `swing` and report both strings; never crash (`FR-HOR-03`). Weights stay doc 15. |
| **INT-03** | `name` / `role` / `source_class` frozen on every return (`FR-INT-03`). |
| **INT-04** | Closed `status` / `scope` / `classification`; no trade language (`FR-INT-04`, `FR-SENT-06`, **MOD-03**). |
| **INT-05** | \(A = 0\) → `status = unavailable` and **null** asset scores; quiet zeros stay on `available` + `insufficient_data`. |
| **INT-06** | JSON-safe primitives only; ISO-8601 UTC; finite numbers (`NFR-JSON-01`). |
| **INT-07** | Engine maps existing objects; no second formula pass; cache-hit quality rebuilt from envelope + cached \(t_0\) (**CCH-17**). |
| **INT-08** | `metrics` allowlist; no duplication of top-level scores; omit unknown windows. |
| **INT-09** | `sources` is an object keyed by **MOD-05**, including failures (**AGG-04**). |
| **INT-10** | Safe `error_class` only; no secrets, traces, or raw errors. |
| **QLY-01** | Public `data_quality` includes FR-PROV-01 four plus provenance lists/counts (`FR-PROV-02`). |
| **QLY-02** | `freshness_score` from worst live `freshness_seconds` vs \(T_{\mathrm{sat}} = 3600\,\mathrm{s}\); `null` when \(\tau\) unknown; never wall-clock on cache hits. |
| **QLY-03** | Public `asset_match_score` ∈ `[0, 1]` = mean mention `match_confidence`, or `0` if \(N_a = 0\). |
| **QLY-04** | `organic_data_ratio = organic_score/100` only when \(N_a \ge 1\); else `null`. |
| **QLY-05** | `provider_coverage = A/P_reg` is reported, not multiplied onto `confidence` a second time (**AGG-13**). |
| **QLY-06** | Honesty: missing baseline ≠ disagreement; missing source ≠ agreement; Redis miss ≠ provider outcome; no invented quality (`FR-PROV-03`). |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Field lists for `AssetIdentity` / mentions / DTOs | doc 05 (**MOD-**) |
| Matching / collision / per-mention confidence algorithm | doc 07 (**MAT-**) |
| Polar formulas, classification tree, phrase lexicons | doc 08 (**SCR-**) |
| Heat / velocity / acceleration / pairwise agreement | doc 09 (**MOM-**) |
| Manipulation ratios and `organic_score` formula | doc 10 (**MAN-**) |
| Combining rules, scope table, anomalies thresholds | doc 11 (**AGG-**) |
| Redis keys, TTLs, timeouts | doc 12 (**CCH-**) |
| Snapshot eligibility, persistence, consumer store | doc 13 (**SNP-**); PostgreSQL/Celery/FastAPI = **HLD-09** / `CON-02` |
| Horizon weight tables | doc 15 |
| Credential env catalog | doc 16 / 18 |
