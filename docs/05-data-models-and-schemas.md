# 05 — Data Models & Schemas

> *Traces to specification §3 (AssetIdentity), §4 (SocialMention), and §11 (SocialSentimentSnapshot).*
> Canonical **field-level schema** for `zynost_social/models.py` (**HLD-08**). Not HLD/LLD, not scoring formulas (docs 08–10, 15), not matching algorithms (doc 07), not cache keys (doc 12), not snapshot persistence (doc 13), and not the public integration-contract JSON (doc 14).

---

## Ownership and role

`models.py` owns these types. Other modules import records only; they do not redefine fields (**HLD-01**, **LLD-03**). Records are immutable dataclass-style values: no I/O, no Redis, no scoring formulas, no provider clients, no mutable identity registry (**NFR-STATE-01**, **HLD-03**).

Pipeline position (**HLD** data flow): providers emit `SocialMention` → matching fills match evidence → scoring / manipulation / aggregation consume typed bundles → engine may **project** a `SocialSentimentSnapshot` from the same computed state and returns a separate JSON dict (doc 14).

**MOD-01 — One schema owner.** Field names in this document are the names `models.py` SHALL use. Renames require updating this doc, guardrails, and dependents together.

---

## Shared conventions

| Convention | Rule | Implements |
|------------|------|------------|
| JSON-safety | Schema values are JSON-serializable primitives (str, int, float, bool, `null`, lists/objects of the same). No `bytes`, no `NaN`/`Inf`, no datetime objects at the **public** boundary. | `NFR-JSON-01` |
| Time | Instants are timezone-aware **UTC**, serialized as ISO-8601 strings (e.g. `2026-09-07T09:00:00Z`). | `FR-SNAP-01` |
| Hashes | `author_id_hash` / `url_hash` are hex strings of a keyed or unkeyed cryptographic hash of the provider identifier / URL. Algorithm lives with hashing helpers in `providers/base.py` (**LLD-05**), not here. | `FR-NORM-03` |
| Privacy | No unnecessary PII: no raw author handles, emails, display names, or full URLs on these models. Prefer hashed/pseudonymous ids. | `FR-NORM-03`, vision privacy principle |
| Determinism | Same field values → same downstream scores. Types carry no hidden clocks or global maps. | `NFR-DET-01`, **HLD-03** |
| Fail-soft enums | Fetch `status` ∈ {`available`, `unavailable`}. Safe `error_class` values below. Empty mention list is **not** unavailable. | **LLD-02**, **HLD-07**, `CON-06` |
| No trade language | No field, enum, or example MAY be `BUY` / `SELL` / `LONG` / `SHORT`. Positive scores are crowd tone, not price direction. | `FR-INT-04`, `FR-SENT-06`, `CON-04` |

**MOD-02 — Closed `error_class` (safe).** On unavailability, `error_class` is one of: `not_configured`, `unauthorized`, `timeout`, `malformed`. Additional **safe** classes MAY be added later without leaking raw API bodies, tokens, or stack traces (`FR-SRC-06`, `FR-AGG-03`, **HLD-10**, **LLD-07**).

**MOD-03 — Closed classification.** Sentiment `classification` is exactly: `strong_positive`, `positive`, `neutral`, `mixed`, `negative`, `strong_negative`, `insufficient_data` (`FR-SENT-03`). No other labels.

Numeric **formulas and thresholds** are not defined here. Where a range is fixed by spec (sentiment `-100..+100`), it is stated. Other metric scales are stored as computed numbers; docs 08–10, 14, 15 own how they are produced.

---

## 1. `AssetIdentity` *(spec §3)*

Canonical identity of the requested crypto asset. Matching **MUST NOT** treat `symbol` as sufficient identity (`FR-ID-02`, **HLD** matching stage). Structured so a future **Canonical Asset Registry** can supply the same fields without redesign (`FR-ID-05`).

### Fields

| Field | Type | Req | Purpose |
|-------|------|-----|---------|
| `asset_id` | `str` (non-empty) | **required** | Stable canonical id. Join key for mentions, snapshots, cache *purpose* (key strings → doc 12). Not a ticker. |
| `symbol` | `str` (non-empty) | **required** | Ticker/symbol as one matching **signal**, never the sole signal. |
| `name` | `str` (non-empty) | **required** | Official project name. |
| `aliases` | `list[str]` | optional (default `[]`) | Alternate names/tickers/spellings. |
| `chain` | `str \| null` | optional | Chain/network (e.g. when the asset is a token). |
| `contract_address` | `str \| null` | optional | On-chain contract when applicable. |
| `official_x_accounts` | `list[str]` | optional (default `[]`) | Known official X/Twitter account identifiers (not user PII of random authors). |
| `official_reddit` | `list[str]` | optional (default `[]`) | Known official Reddit identities (e.g. subreddit / account handles as registry strings). |
| `official_telegram` | `list[str]` | optional (default `[]`) | Known official **public** Telegram channel identifiers. |
| `official_discord` | `list[str]` | optional (default `[]`) | Known official Discord identities **only** where a public/authorized API exists (`FR-SRC-01`). |

Implements `FR-ID-01`. Official-account lists are **identity signals**, not fetched mention authors.

### Invariants

- **MOD-04 — Never ticker-only.** A valid `AssetIdentity` always has `asset_id` **and** `name` in addition to `symbol`. Downstream matching SHALL combine multiple signals plus surrounding crypto context (algorithms → doc 07).
- `asset_id` is unique and opaque to scoring; providers and cache purpose-keys use it, not `symbol`.
- `chain` / `contract_address` MAY be null (e.g. native L1 coins). Null is not “unknown invented chain”.
- Empty official-account lists are allowed; they reduce match signals, they do not invent accounts (`CON-04`).
- Lists contain registry strings only (no access tokens, no private invite links).

### Collision-prone symbols *(matching concern, not a schema enum)*

These symbols are **known collision risks** and MUST be treated as ambiguous in matching (`FR-ID-03`): `ONE`, `IN`, `ARB`, `TON`, `OP`, `NEAR`, `APT`, `LINK`. The schema does **not** special-case them as a type. Collision rules, context, and rejection of ticker-only hits live in **doc 07** (`matching.py`, **LLD** matching module).

---

## 2. `SocialMention` *(spec §4)*

Common normalized mention after provider mapping (**HLD-02**, **LLD-01**, `FR-NORM-01`). Raw provider payloads **MUST NOT** appear on this model (not in `metadata`, not as extra fields) (**HLD** flow note 3).

### Fields

| Field | Type | Req | Purpose |
|-------|------|-----|---------|
| `provider` | `str` | **required** | Source id: `x` \| `reddit` \| `telegram` \| `discord` (**MOD-05**). |
| `external_id` | `str` (non-empty) | **required** | Provider-scoped id (unique **within** that provider, not globally). |
| `asset_id` | `str` | **required** | Canonical id of the **requested** asset (from `AssetIdentity.asset_id`), not a guessed ticker. |
| `text` | `str` | **required** | Sanitized mention body (size-capped; sanitization in `providers/base.py`, **LLD-05**, `NFR-SEC-01`). Treat as untrusted data: never `eval` / execute (`NFR-SEC-02`). |
| `author_id_hash` | `str` | **required** | Pseudonymous author id (hash of provider author id). |
| `created_at` | UTC instant | **required** | Provider-reported creation time, normalized to UTC. |
| `engagement` | `float` (`>= 0`) | **required** | Adapter-normalized non-negative engagement scalar (likes/upvotes/replies/etc. mapped in **doc 06**). Missing native engagement → `0`, never invented viral counts. |
| `url_hash` | `str \| null` | optional | Hash of canonical URL if the mention has one; `null` if none. |
| `matched_terms` | `list[str]` | required after match (default `[]`) | Terms that justified the match (`FR-ID-04`). |
| `match_confidence` | `float` in `[0.0, 1.0]` | required after match (default `0`) | Match strength, **not** the public contract `confidence` field (doc 14). |
| `match_rationale` | `str` | required after match (default `""`) | Short **why it matched** (`FR-ID-04`). Empty before `matching.py`. Not a formula dump. |
| `language` | `str \| null` | optional | BCP-47 / ISO 639 tag when known; `null` if unknown. |
| `metadata` | `dict[str, JSON-safe scalar \| list of scalars]` | optional (default `{}`) | Small **source-specific** flags (e.g. subreddit name, channel id **if not PII**). |

**MOD-06 — `match_rationale` is first-class.** Spec §4 lists `matched_terms` and `match_confidence`; `FR-ID-04` also requires *why*. Putting rationale in opaque `metadata` would hide it. `matching.py` fills `matched_terms`, `match_confidence`, and `match_rationale` together (**LLD** `MatchBatch`).

### Invariants

- **Privacy:** no plaintext author handle, email, or full URL. Dedup/flood detection uses hashes and `text`, not PII (`FR-NORM-03`).
- **MOD-07 — No raw payload leak.** `metadata` MUST NOT contain full JSON bodies, auth headers, cookies, or credentials (`FR-SRC-06`).
- Pre-match mentions MAY have empty `matched_terms` / `match_confidence = 0` / empty rationale. **Post-match asset-specific** mentions MUST expose all three (`FR-ID-04`).
- `text` may be empty only if the provider item had no body; empty text is still a valid mention if other fields are set — scoring may treat it as insufficient evidence (doc 08), not as `unavailable`.
- Engagement is a number, not a nested provider object.

---

## 3. `SocialSentimentSnapshot` *(spec §11)*

Compact **point-in-time** social state for later persistence (`FR-SNAP-01`). **Forward-collected only** — never fabricated or backfilled (`FR-SNAP-02`, `CON-04`). Persistence (PostgreSQL, Celery, FastAPI) is a **consumer** concern (**HLD-09**, doc 13). This doc owns **shape only**.

Must retain enough for later time-based analysis (acceleration, regime shifts, price/social divergence, outcome correlation) **without** re-inventing past evidence (`FR-SNAP-03`). It is **not** the live `build_social_sentiment` dict.

### Fields

| Field | Type | Req | Purpose |
|-------|------|-----|---------|
| `asset_id` | `str` | **required** | Same canonical id as `AssetIdentity`. |
| `observed_at` | UTC instant | **required** | Observation time of this evidence (ISO-8601 UTC). |
| `sentiment_score` | `float` in `[-100, +100]` | **required** | Crowd tone (`FR-SENT-02`). Not a price forecast. |
| `social_heat` | `float` | **required** | Activity/heat as computed for this observation (definition → doc 09). |
| `mention_count` | `int` (`>= 0`) | **required** | Count of **asset-matched** mentions informing this snapshot. |
| `mention_velocity` | `float` | **required** | Velocity vs recent baseline (definition → doc 09). |
| `organic_score` | `float` | **required** | Organic-quality reading (definition → doc 10). |
| `manipulation_risk` | `float` | **required** | Manipulation risk (definition → doc 10). |
| `source_coverage` | `float` in `[0.0, 1.0]` | **required** | Compact coverage: available providers / providers expected this run. Missing source ≠ agreement (`FR-MOM-05`). |
| `source_agreement` | `float` in `[0.0, 1.0]` | **required** | Agreement among **available** sources (`FR-MOM-05`). |
| `classification` | classification enum | **required** | **MOD-03** closed set only. |

When all sources are unavailable, the engine returns contract `status = unavailable` (`FR-INT-05`) and **MUST NOT** invent a snapshot of fake scores (`HLD-07`). A snapshot is projected only from an actual computed state.

Insufficient asset-specific evidence: `classification = insufficient_data` (`FR-SENT-04`, `FR-SCOPE-02`) with honest counts/coverage — not a guessed `positive`/`negative`.

---

## 4. Internal DTOs *(named in LLD; owned by `models.py`)*

Brief typed records for module boundaries (**LLD** catalog). Not the public JSON contract.

### `ProviderFetchOutcome` — `providers/base.py` → engine (**LLD-02**, **LLD-05**)

| Field | Type | Purpose |
|-------|------|---------|
| `provider` | `str` (`x` \| `reddit` \| `telegram` \| `discord`) | Which adapter. |
| `status` | `available` \| `unavailable` | Isolation result. |
| `mentions` | `list[SocialMention]` | Normalized mentions if available; `[]` if none or if unavailable. |
| `error_class` | `str \| null` | Set when `unavailable` (**MOD-02**). `null` when available. |
| `freshness_seconds` | `float \| null` | Age of data when known; `null` if unused/unknown. |

**MOD-08 — Empty list ≠ unavailable.** `status = available` and `mentions = []` means a successful fetch with **zero** mentions (quiet asset or empty window). `unavailable` is timeout, malformed payload, missing credentials (`not_configured` / `unauthorized`), etc. Adapters MUST NOT smuggle status in hidden instance flags (**LLD-02**, **HLD-10**, `CON-06`).

### `MatchBatch` — `matching.py` (**LLD** matching)

| Field | Type | Purpose |
|-------|------|---------|
| `asset_mentions` | `list[SocialMention]` | Mentions accepted as asset-specific; each has `matched_terms`, `match_confidence`, `match_rationale`. |
| `market_wide` | `list[SocialMention]` | Optional leftovers (generic crypto chatter). Never scored as the requested asset (`FR-SCOPE-03`). |
| `asset_match_score` | `float` | Overall match-quality input for `quality.py` (`FR-PROV-01`). Scale → doc 14. |

Algorithms and collision handling → **doc 07**.

### `BaselineBundle` — supplied by engine from `cache.py` into `scoring.py`

| Field | Type | Purpose |
|-------|------|---------|
| `mentions_1h` / related window counts | numeric | Prior-window baselines for velocity/heat (`FR-MOM-03`). |
| `mentions_24h` (and other rolling windows the scorer needs) | numeric | Longer baseline. |

Opaque numeric bundle: **not** Redis key names (doc 12). Missing cache → scorer receives an explicit empty/zero bundle, never invented history (`FR-CACHE-03`, `CON-04`). Horizon chooses which windows matter (doc 15).

### `ScoreBundle` — `scoring.py`

| Field | Type | Purpose |
|-------|------|---------|
| `sentiment_score` | `[-100, +100]` | Deterministic tone (`FR-SENT-02`). |
| `classification` | classification enum | **MOD-03**. |
| `confidence` | `float` | Evidence confidence (public field mapping → doc 14). |
| `social_heat` | `float` | Heat input to aggregate/snapshot. |
| `mention_velocity` | `float` | Velocity input. |
| `social_acceleration` | `float` | Acceleration input (`FR-MOM-04`). |
| `mention_count` | `int` | Mentions used. |
| `unique_authors` | `int` | Distinct `author_id_hash` values. |
| `horizon` | `str` | Horizon actually applied (`intraday` \| `swing` \| `position`). |

Phrase-intensity and count/ratio **breakdowns** required by `FR-SENT-05` live as additional JSON-safe numeric fields on this bundle (names aligned in docs 08 / 14). **No formulas here.**

### `ManipulationAssessment` — `manipulation.py`

JSON-safe floats named as spec §7 / `FR-MAN-02`: `duplicate_content_ratio`, `unique_author_ratio`, `engagement_concentration`, `author_concentration`, `suspicious_burst_score`, `spam_bot_suspicion`, `manipulation_risk`, `organic_score`, `anomaly_score`. Formulas → **doc 10**. A spike is an anomaly/risk, not a bullish label (`FR-MAN-04`).

### `SourceBundle` — per-provider row into `aggregation.py` (`FR-AGG-02`)

| Field | Type | Purpose |
|-------|------|---------|
| `provider` | `str` | Source id. |
| `status` | `available` \| `unavailable` | Same meaning as fetch outcome. |
| `mention_count` | `int` | Asset-matched count when available. |
| `unique_authors` | `int` | When available. |
| `sentiment_score` | `float \| null` | When available; `null` if unavailable (do not fabricate). |
| `freshness_seconds` | `float \| null` | When available. |
| `error_class` | `str \| null` | On failure only. |

### `AggregateResult` — `aggregation.py`

| Field | Type | Purpose |
|-------|------|---------|
| `scope` | `asset_specific` \| `market_wide` | `FR-SCOPE-01`. |
| `classification` | classification enum | Asset-level label; `insufficient_data` when appropriate (`FR-SCOPE-02`). |
| `sentiment_score` | `[-100, +100]` or omitted/null only when status cannot support a score | Combined tone of **available** evidence. |
| `confidence` | `float` | Reduced when coverage is partial (`FR-AGG-04`). |
| `social_heat`, `mention_velocity`, `organic_score`, `manipulation_risk`, `source_agreement` | `float` | Asset-level metrics (formulas → docs 09–11). |
| `mention_count` | `int` | Combined asset-matched count. |
| `sources` | `list[SourceBundle]` | Independent rows retained (`FR-AGG-01`). |
| `anomalies` | `list` of JSON-safe records | Flagged patterns; not trade advice (`FR-MAN-04`). |

Detail of how sources combine → **doc 11**.

### `ProvenanceRecord` — `provenance.py`

Honest run diary (`FR-PROV-02`–`03`): per-provider `status` / `error_class`, observation counts, duplicate-filtering impact, freshness. No secrets, no raw error bodies (`NFR-SEC-04`). Input to quality and to the contract’s `sources` / `data_quality` mapping (doc 14).

### `DataQuality` — `quality.py`

At least (`FR-PROV-01`): `provider_coverage`, `freshness_score`, `asset_match_score`, `organic_data_ratio`. Numeric scales and the public object → **doc 14**. MUST NOT invent an organic ratio when mentions were unavailable (**LLD** quality **Must NOT**).

---

## 5. Snapshot vs live `build_social_sentiment` result

| | `SocialSentimentSnapshot` | Public result `dict` |
|--|---------------------------|----------------------|
| Owner | This doc / `models.py` | **Doc 14** (`FR-INT-02`) |
| Purpose | Compact evidence row for later store/analysis | Integration contract (`role = context`) |
| Size | Spec §11 fields only | Full: `name`, `role`, `status`, `scope`, `metrics`, `sources`, `anomalies`, `data_quality`, … |
| When | Projected from a **computed** run | Always returned by the entry point, including `status = unavailable` |
| Persistence | Consumer (doc 13, **HLD-09**) | Not persisted by this module |

Fixed contract identity (`name = social_sentiment`, `role = context`, `source_class = multi_source_social_intelligence`) does **not** belong on the snapshot (`FR-INT-03`).

---

## 6. Privacy, JSON-safety, state

- Hash author and URL at the adapter boundary (**LLD-05**); models store hashes only.
- Public and snapshot serialization: ISO-8601 times, finite numbers, string enums, no datetime/set/bytes.
- **MOD-09 — No hidden global mutable state.** No module-level dict of identities, mention caches, or “last score”. Shared cache of record is Redis via `cache.py` (**HLD-05**, **LLD-08**, `NFR-STATE-01`).
- Types are immutable after construction so concurrent provider tasks cannot mutate a shared mention in place (**HLD-06**).

---

## Decision index

| ID | Decision |
|----|----------|
| **MOD-01** | `models.py` is the single owner of these field names. |
| **MOD-02** | Safe `error_class`: `not_configured`, `unauthorized`, `timeout`, `malformed` (+ later safe classes only). |
| **MOD-03** | Classification is a closed seven-label set including `insufficient_data`. |
| **MOD-04** | `AssetIdentity` is never ticker-only; `asset_id` + `name` + `symbol` required. |
| **MOD-05** | `provider` closed set: `x`, `reddit`, `telegram`, `discord`. |
| **MOD-06** | `match_rationale` is a first-class mention field (with terms + confidence) for `FR-ID-04`. |
| **MOD-07** | `SocialMention.metadata` is JSON-safe and MUST NOT hold raw provider payloads. |
| **MOD-08** | `available` + `[]` ≠ `unavailable` (**LLD-02**). |
| **MOD-09** | Immutable records; no process-global mutable model state. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Matching algorithms, collision rules, “surrounding crypto context” | doc 07 |
| Sentiment / momentum formulas, phrase intensities, heat/velocity definitions | docs 08, 09 |
| Manipulation formulas | doc 10 |
| Per-source aggregation math and scope rules in depth | doc 11 |
| Redis keys and TTLs | doc 12 |
| Snapshot persistence and historical analysis jobs | doc 13 |
| Full `build_social_sentiment` JSON contract and `data_quality` scales | doc 14 |
| Horizon window/weight tables | doc 15 |
| Provider payload → mention mapping (engagement, ids) | doc 06 |
| Hash algorithm and env/credentials | docs 16, 18 |
