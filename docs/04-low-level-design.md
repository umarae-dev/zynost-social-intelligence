# 04 — Low-Level Design (LLD)

> *Traces to specification §17 (recommended modules/structure) and §2 (common `SocialProvider` interface).*
> This is the **module-level design under** `03-high-level-design.md`. It freezes responsibilities, call boundaries, and typed function contracts. It is not code and not the HLD data-flow essay. Field-level schemas → doc 05; scoring formulas/weights → docs 08–10, 15; cache keys/TTLs → doc 12; integration-contract JSON → doc 14.

---

## Role relative to HLD

HLD owns *how the system is staged* (**HLD-01**–**HLD-10**). LLD owns *which file does what* and the interfaces those files expose. Layout is **HLD-08** / spec §17. Collection stays separate from scoring and manipulation (**HLD-01**, `NFR-STATE-01`).

Public surface of the package is **one** async function on `engine.py` (`FR-INT-01`). Everything else is internal.

---

## Frozen provider interface *(spec §2)*

**LLD-01 — Common adapter contract.** Every source implements this and nothing else as its fetch API (`FR-SRC-02`, **HLD-02**):

```python
class SocialProvider:
    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]: ...
```

- `asset` is the canonical identity (fields owned by doc 05 / `FR-ID-01`).
- `since_minutes` is the lookback the engine derived from the horizon (**HLD-04**, `FR-HOR-02`; exact windows → doc 15).
- Return value is **normalized** mentions only (`FR-NORM-01`). Raw provider payloads do not leave the adapter.

**LLD-02 — Outcome vs mentions.** `fetch_mentions` returning `[]` means *success with zero mentions*, not failure. Unavailability is a separate outcome (`available` | `unavailable` plus a safe `error_class`). Adapters MUST NOT use hidden instance flags to smuggle status. Isolation (timeout, exception, not-configured) is applied in `providers/base.py` and returns a typed fetch outcome the engine consumes (**HLD-06**, **HLD-07**, `FR-SRC-05`).

---

## Package rules *(apply to every module)*

**LLD-03 — File discipline.** No giant single file; no duplicated provider I/O; no hidden global mutable state (`NFR-STATE-01`). Shared Redis is the only cache of record (**HLD-05**). Scoring, manipulation, and aggregation are deterministic functions of their inputs (`NFR-DET-01`, **HLD-03**, `CON-01`: no LLM). The public result is JSON-safe (`NFR-JSON-01`); exact keys → doc 14. Production adapters are real; mocks exist only in tests (`CON-05`). Never invent data (`CON-04`).

**LLD-04 — Engine-only orchestration.** Only `engine.py` sequences the pipeline and talks to more than one concern. Matching does not fetch. Scoring does not detect manipulation. Manipulation does not set `scope`. Providers do not score.

---

## Module catalog — `zynost_social/`

### `engine.py`

| | |
|---|---|
| **Responsibility** | Sole public orchestrator. Run **HLD** pipeline: concurrent isolated fetch → match → score → manipulate → aggregate → provenance/quality → JSON-safe dict. Map all-unavailable to `status = unavailable` without raising (`FR-INT-05`, **HLD-07**). Apply horizon as a behavioral input (**HLD-04**). |
| **Who calls it** | Downstream consumers (later FastAPI/Celery). Tests. Package `__init__` may re-export this function only. |
| **Interface** | `async def build_social_sentiment(asset: AssetIdentity, horizon: str = "swing") -> dict` (`FR-INT-01`). Unrecognized horizon is handled safely (`FR-HOR-03`) — default/report, never an unhandled crash. Result identity: `name = social_sentiment`, `role = context`, `source_class = multi_source_social_intelligence` (`FR-INT-03`). Never emit `BUY`/`SELL`/`LONG`/`SHORT` (`FR-INT-04`). |
| **Must NOT** | Contain provider HTTP; embed formulas; persist to PostgreSQL; use process-memory as cache of record; raise when every provider is down; import LLM SDKs (`CON-01`, **HLD-09**). |

Internal (not public): construct the provider list, bound-concurrency gather, pass `since_minutes` into adapters, fold `ProviderFetchOutcome` values into matching.

---

### `models.py`

| | |
|---|---|
| **Responsibility** | Shared types: `AssetIdentity`, `SocialMention`, `SocialSentimentSnapshot`, plus small LLD DTOs used across modules (`ProviderFetchOutcome`, per-source result, manipulation assessment, aggregate bundle). Field lists → doc 05. |
| **Who calls it** | All other `zynost_social` modules (import types only). |
| **Interface** | Immutable / dataclass-style records. JSON-safe primitives at the contract boundary. Snapshot *shape* support (`FR-SNAP-01`); engine may project a snapshot from the same computed state. |
| **Must NOT** | I/O, Redis, scoring formulas, provider clients, or mutable registries. |

---

### `matching.py`

| | |
|---|---|
| **Responsibility** | Attach asset match evidence to normalized mentions; reject ticker-only hits; avoid collisions (`ONE`, `IN`, `ARB`, `TON`, `OP`, `NEAR`, `APT`, `LINK`); distinguish usable asset-specific mentions from market-wide/off-topic (`FR-ID-02`–`04`, `FR-SCOPE-01`–`03`). |
| **Who calls it** | `engine.py` after collection (and cache-hit raw mentions still go through match if not already matched). |
| **Interface** | `def match_mentions(asset: AssetIdentity, mentions: Sequence[SocialMention]) -> MatchBatch` where `MatchBatch` exposes: asset-matched mentions (each with `matched_terms`, `match_confidence`, rationale), optional market-wide leftovers, and an overall match-quality input for `quality.py`. Algorithms → doc 07. |
| **Must NOT** | Call providers; score sentiment; treat generic crypto chatter as asset-specific (`FR-SCOPE-03`). |

---

### `scoring.py`

| | |
|---|---|
| **Responsibility** | Deterministic sentiment, activity, and momentum on **cleaned, asset-matched** mentions; horizon-aware windows/weights (`FR-SENT-*`, `FR-MOM-*`, `FR-HOR-02`). Classification is one of the spec labels including `insufficient_data` (`FR-SENT-03`–`04`). |
| **Who calls it** | `engine.py` (per available source and/or on the matched set as aggregation needs). May read baselines **via** `cache.py` only through values the engine supplies — scoring stays pure given mentions + horizon + baselines. |
| **Interface** | `def score(mentions: Sequence[SocialMention], horizon: str, baselines: BaselineBundle) -> ScoreBundle`. `ScoreBundle` includes sentiment in `-100..+100`, classification, confidence, heat/velocity/acceleration inputs — **not** the formula text (docs 08, 09, 15). |
| **Must NOT** | Use an LLM (`CON-01`); call the network; recast a spike as bullish (`FR-SENT-06`, `FR-MOM-07`); implement manipulation ratios (that is `manipulation.py`). |

---

### `manipulation.py`

| | |
|---|---|
| **Responsibility** | Detect coordinated/copied/flooded/bot-like chatter; compute manipulation signals; down-weight organic quality (`FR-MAN-01`–`03`). Spikes are anomalies, not positive/bullish (`FR-MAN-04`). |
| **Who calls it** | `engine.py` after scoring inputs exist (mentions + optional score context). Runs on the same matched set; does not replace scoring. |
| **Interface** | `def assess(mentions: Sequence[SocialMention]) -> ManipulationAssessment` with the spec signal names (`duplicate_content_ratio`, `unique_author_ratio`, `engagement_concentration`, `author_concentration`, `suspicious_burst_score`, spam/bot suspicion, `manipulation_risk`, `organic_score`, `anomaly_score`). Exact formulas → doc 10. |
| **Must NOT** | Fetch or match; emit trade/price language; live in the same functions as sentiment classification. |

---

### `aggregation.py`

| | |
|---|---|
| **Responsibility** | Combine available per-source bundles into one asset-level result; retain independent source rows; set `scope`; reduce confidence when coverage is partial; never treat a missing source as agreement (`FR-AGG-01`–`04`, `FR-MOM-05`). |
| **Who calls it** | `engine.py` after per-source score + manipulation. |
| **Interface** | `def aggregate(per_source, match_batch, horizon, *, scores=None, assessments=None, union_score=None, union_assessment=None) -> AggregateResult`. Attachments stay off `SourceBundle` (**AGG-02**). Each `SourceBundle` carries at least status, mention_count, unique_authors, sentiment_score, freshness_seconds, and `error_class` on failure (`FR-AGG-02`). Scope ∈ {`asset_specific`, `market_wide`}. Insufficient asset-specific evidence → `insufficient_data` where appropriate (`FR-SCOPE-02`). Detail → doc 11. |
| **Must NOT** | Invent sources; hide failures; leak credentials/stack traces (`FR-AGG-03`); call providers. |

---

### `cache.py`

| | |
|---|---|
| **Responsibility** | Shared Redis adapter: get/set raw per-provider payloads (or already-normalized mention lists), computed aggregates, and longer-lived baselines (**HLD-05**, `FR-CACHE-01`–`04`). Every Redis call has a strict timeout (`NFR-TIMEOUT-01`). |
| **Who calls it** | `engine.py` (and only engine, so scoring stays pure). |
| **Interface** | Async `get` / `set` operations parameterized by *purpose* (raw provider / aggregate / baseline window) and `asset_id`. On Redis error or timeout: behave as miss; log a redacted failure; continue (`FR-CACHE-03`). **Key strings and TTLs are not defined here** → doc 12. |
| **Must NOT** | Be process-memory as source of truth; fabricate values on miss; crash the engine; embed scoring. |

---

### `provenance.py`

| | |
|---|---|
| **Responsibility** | Honest “what happened” for the run: which providers responded or failed, observation counts, duplicate-filtering impact, freshness (`FR-PROV-02`–`03`). Safe `error_class` only (`FR-SRC-06`). |
| **Who calls it** | `engine.py` when assembling the contract. |
| **Interface** | `def build_provenance(outcomes, match_batch, aggregate, *, served_from_cache, observed_at, duplicate_content_ratio=None) -> ProvenanceRecord`. Keyword `duplicate_content_ratio` is the **MAN-04** scalar (live assessment or cache envelope); `observed_at` is collection \(t_0\) for the caller and is not used to recompute freshness. Detail → doc 14. |
| **Must NOT** | Claim coverage the evidence does not support; include secrets or raw API error bodies (`NFR-SEC-04`). |

---

### `quality.py`

| | |
|---|---|
| **Responsibility** | `data_quality` metrics: at least `provider_coverage`, `freshness_score`, `asset_match_score`, `organic_data_ratio` (`FR-PROV-01`). Partial coverage lowers confidence in concert with aggregation (`FR-AGG-04`). |
| **Who calls it** | `engine.py` (may take `ProvenanceRecord` + `ManipulationAssessment` + `AggregateResult`). |
| **Interface** | `def compute_data_quality(provenance, match_batch, aggregate, assessment) -> DataQuality`. Coverage is \(A/P_{\mathrm{reg}}\) with frozen \(P_{\mathrm{reg}}=4\) (**QLY-01**, **QLY-05**); freshness from stored \(\tau\) vs \(T_{\mathrm{sat}}=3600\,\mathrm{s}\) (**QLY-02**); organic ratio only when \(N_a \ge 1\) and \(A \ge 1\) (**QLY-04**). Detail → doc 14. |
| **Must NOT** | Fetch, score sentiment, or invent organic ratio when mentions were unavailable. |

---

## Providers package — `zynost_social/providers/`

### `base.py`

| | |
|---|---|
| **Responsibility** | Abstract `SocialProvider` (**LLD-01**); shared timeout, size-cap, response-validation, sanitization, redaction, retry/backoff that does not amplify load (`NFR-SEC-01`–`03`); `collect_isolated(...)` → `ProviderFetchOutcome`. Credential presence check used by adapters (**HLD-10**, `CON-06`). |
| **Who calls it** | Concrete adapters (inherit/use helpers). `engine.py` (isolation helper / provider registry construction). |
| **Interface** | `async def collect_isolated(provider: SocialProvider, asset: AssetIdentity, since_minutes: int, timeout_s: float) -> ProviderFetchOutcome`. Outcome: `status`, `mentions`, optional `error_class` (`not_configured`, `unauthorized`, `timeout`, `malformed`, and other safe classes), `freshness_seconds` when available. Exceptions from `fetch_mentions` are caught here and **never** propagate to take the engine down (`FR-SRC-03`, **HLD-06**). |
| **Must NOT** | Contain X/Reddit/Telegram/Discord-specific URLs or payloads; store credentials on the class as globals. |

**LLD-05 — Helpers live once.** Timeout wrappers, JSON/parse guards, author/URL hashing hooks, and error redaction live in `base.py`. Adapters call them; they do not copy-paste them (`FR-SRC-07`).

### `x.py` · `reddit.py` · `telegram.py` · `discord.py`

| | |
|---|---|
| **Responsibility** | Source-specific I/O and mapping onto `SocialMention`. Credentials from environment only (`NFR-CRED-01`). Telegram: **public channels**. Discord: **only** where a public/authorized API exists (`FR-SRC-01`). |
| **Who calls it** | `engine.py` via `SocialProvider` / `collect_isolated` — never from scoring. |
| **Interface** | Same `fetch_mentions` as **LLD-01**. If credentials are missing: do not call the network; outcome `unavailable` + `error_class = "not_configured"` (or `"unauthorized"` when present-but-rejected) (**HLD-10**). |
| **Must NOT** | Invent mentions; implement scoring/manipulation; leak tokens in logs or results (`FR-SRC-06`); require a paid subscription to construct the adapter (`CON-06`). Discord must not scrape unauthorized surfaces. |

Adding a fifth adapter means a new file plus engine registration — not edits to `scoring.py` / `manipulation.py` / `aggregation.py` (`FR-SRC-07`).

---

## Concurrency and isolation *(module-level)*

**LLD-06 — Gather with bounds.** `engine.py` starts independent `collect_isolated` tasks **concurrently** (`FR-SRC-04`, `NFR-ASYNC-01`), under a **bounded** semaphore (`NFR-CONC-01`). Each task has a **strict per-call timeout** (`NFR-TIMEOUT-01`). A provider exception is converted to `unavailable` inside `base.py`; it does not cancel sibling tasks and does not escape `build_social_sentiment`.

Scoring / manipulation / aggregation run only after the gather completes (successfully or with partial failures). They never wait on a hung socket.

All-unavailable after gather → return contract dict with `status = unavailable`; do not raise (**HLD-07**, `FR-INT-05`).

---

## Call graph (interfaces only)

```
consumer
  └─ engine.build_social_sentiment(asset, horizon)
        ├─ cache get (raw / aggregate / baselines)     # miss or Redis down → continue
        ├─ gather: base.collect_isolated(x|reddit|telegram|discord)
        │     └─ SocialProvider.fetch_mentions
        ├─ matching.match_mentions
        ├─ scoring.score          # per available source + matched set as needed
        ├─ manipulation.assess
        ├─ aggregation.aggregate
        ├─ provenance.build_provenance
        ├─ quality.compute_data_quality
        ├─ cache set (best-effort)
        └─ dict  (shape → doc 14; snapshot projection → doc 13)
```

---

## Decision index

| ID | Decision |
|----|----------|
| **LLD-01** | Freeze `SocialProvider.fetch_mentions(asset, since_minutes) -> list[SocialMention]` from spec §2. |
| **LLD-02** | Empty mention list ≠ unavailable; status/`error_class` travel on `ProviderFetchOutcome`, not hidden state. |
| **LLD-03** | No giant file, no duplicated adapter logic, no global mutable cache, no LLM, deterministic core, JSON-safe public result. |
| **LLD-04** | `engine.py` is the only public orchestrator; stages do not reach across concerns. |
| **LLD-05** | Timeout, isolation, sanitization, and redaction helpers live in `providers/base.py`. |
| **LLD-06** | Concurrent bounded gather; per-call timeouts; provider exceptions never take the engine down. |
| **LLD-07** | Unconfigured/paid adapters: real code, gated by credentials; `not_configured` / `unauthorized`; never invent data (**HLD-10**). |
| **LLD-08** | Redis via `cache.py` only; failure = miss; keys/TTLs deferred to doc 12. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Field-by-field `AssetIdentity` / `SocialMention` / snapshot schemas | doc 05 |
| Matching algorithm and collision rules in depth | doc 07 |
| Sentiment / momentum formulas and classification thresholds | docs 08, 09 |
| Manipulation formulas | doc 10 |
| Per-source aggregation and scope rules in depth | doc 11 |
| Redis key names and TTLs | doc 12 |
| Snapshot persistence semantics | doc 13 |
| Full integration-contract JSON | doc 14 |
| Horizon weight tables | doc 15 |
| Env var catalog | doc 18 |
