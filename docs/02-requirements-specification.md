# 02 — Requirements Specification

> *Traces to the specification (functional requirements across §2–§14; non-functional requirements and constraints across §15, §16, §20, and the global guardrails).*
> This document is the **SHALL/MUST catalog** beneath `00-project-vision.md` (the "why") and `01-product-goals.md` (the measurable goals). It enumerates *what the engine must do and how it must behave* as verifiable requirements. It stays **requirements-level**: no formulas, schemas, cache-key lists, class designs, or code — those belong to the later numbered documents.

---

## Conventions

- **SHALL / MUST** = mandatory. **SHOULD** = strongly recommended.
- Each requirement has a stable ID (`FR-…` functional, `NFR-…` non-functional, `CON-…` constraint) so later design docs and tests can cite it.
- Each item is written to be independently **verifiable** (a later test can pass/fail it) and is tagged with the source spec section (`§`).
- IDs are stable once assigned; new requirements append rather than renumber.

---

## Functional requirements

### FR-SRC — Sources & providers *(§2)*

- **FR-SRC-01** The engine SHALL collect social activity through independent provider adapters for X/Twitter, Reddit, Telegram public channels, and Discord (Discord and any other source only where a public/authorized API exists).
- **FR-SRC-02** Every provider adapter SHALL expose a common asynchronous interface for fetching mentions of an asset over a recent time window, so scoring is decoupled from any specific source.
- **FR-SRC-03** Each provider SHALL operate independently: a failure, timeout, or absence of one provider SHALL NOT prevent the others from being collected or scored.
- **FR-SRC-04** The engine SHALL fetch independent providers concurrently rather than sequentially.
- **FR-SRC-05** Each provider SHALL fail gracefully and expose its own status/provenance (available / unavailable and a safe error classification) instead of raising and taking the engine down.
- **FR-SRC-06** No provider SHALL leak credentials, tokens, stack traces, or raw sensitive API errors into results, logs, or the returned contract.
- **FR-SRC-07** The provider architecture SHALL allow a new source to be added later without rewriting sentiment, manipulation, or aggregation logic.

### FR-ID — Asset identity & matching *(§3)*

- **FR-ID-01** The engine SHALL identify a crypto asset using a canonical identity comprising at least: canonical `asset_id`, official name, symbol, aliases, chain/network, contract address, and known official social accounts.
- **FR-ID-02** The engine SHALL NOT match an asset by raw ticker/symbol alone; matching MUST combine multiple signals plus surrounding crypto context.
- **FR-ID-03** The engine SHALL guard against ticker/symbol collisions for ambiguous symbols, including at least: `ONE, IN, ARB, TON, OP, NEAR, APT, LINK`.
- **FR-ID-04** For every matched mention, the engine SHALL expose the matched terms, a matching confidence, and why it matched.
- **FR-ID-05** The identity model SHALL be structured to plug into Zynost's future Canonical Asset Registry without redesign.

### FR-NORM — Mention normalization *(§4)*

- **FR-NORM-01** The engine SHALL normalize every collected mention from any source into a single common mention shape before scoring.
- **FR-NORM-02** Each normalized mention SHALL carry its originating provider, a source-scoped external identifier, the asset it was matched to, its text, a pseudonymous/hashed author identifier, its creation timestamp, an engagement measure, a hashed URL reference, its matched terms, match confidence, language, and source-specific metadata.
- **FR-NORM-03** Normalization SHALL prefer hashed/pseudonymous author and URL identifiers and SHALL avoid persisting unnecessary personal data (PII).
- **FR-NORM-04** Normalization SHALL deduplicate and clean input (e.g. remove exact/near-duplicate and off-topic content) so downstream scoring operates on cleaned, asset-matched mentions.

### FR-SENT — Sentiment scoring & classification *(§5)*

- **FR-SENT-01** The engine SHALL score sentiment deterministically using rules/statistics/provider-derived signals only — never an LLM.
- **FR-SENT-02** The engine SHALL produce a sentiment score in the range **-100..+100**.
- **FR-SENT-03** The engine SHALL classify sentiment as exactly one of: `strong_positive, positive, neutral, mixed, negative, strong_negative, insufficient_data`.
- **FR-SENT-04** The engine SHALL return `insufficient_data` (not a fabricated tone) when there is too little asset-matched evidence to classify.
- **FR-SENT-05** Sentiment output SHALL include, at minimum: positive/neutral/negative counts and ratios, a confidence value, engagement-weighted sentiment, and phrase-intensity measures (bullish/bearish tone intensity and fear / greed-excitement / uncertainty / euphoria intensity).
- **FR-SENT-06** Positive sentiment SHALL NOT be represented as, or converted into, a bullish price-direction claim.

### FR-MOM — Social activity & momentum *(§6)*

- **FR-MOM-01** The engine SHALL report social activity for the asset, including at least total mentions and unique authors.
- **FR-MOM-02** The engine SHALL report mention velocity — the rate of new mentions relative to a recent baseline — and MUST distinguish a burst from the same count spread evenly.
- **FR-MOM-03** The engine SHALL report activity change over recent windows (current 1h vs. previous 1h baseline, 6h change, 24h change) and an engagement-velocity measure.
- **FR-MOM-04** The engine SHALL report a social-heat measure and a social-acceleration measure indicating whether activity is accelerating, holding, or fading.
- **FR-MOM-05** The engine SHALL report source agreement and source disagreement across independent sources, and MUST NOT treat a missing/unavailable source as agreement.
- **FR-MOM-06** Momentum metrics SHALL be sufficient to later express changes over time (e.g. that an asset's social sentiment moved between two values over a stated interval).
- **FR-MOM-07** A mention spike or high activity SHALL NOT be automatically labeled positive or bullish.

### FR-MAN — Manipulation / pump detection *(§7)*

- **FR-MAN-01** The engine SHALL detect manipulated chatter, including at least: duplicate/copied campaign text, repeated URLs, repeated-author flooding, coordinated bursts, suspiciously similar messages, low-author-diversity pumps, extreme engagement concentration, sudden mention spikes, and bot-like repetition.
- **FR-MAN-02** The engine SHALL compute manipulation signals including at least: `duplicate_content_ratio, unique_author_ratio, engagement_concentration, author_concentration, suspicious_burst_score, spam/bot suspicion, manipulation_risk, organic_score, anomaly_score`.
- **FR-MAN-03** Detected manipulation SHALL down-weight organic quality such that many near-identical shill posts from few accounts do NOT score more organic than fewer independent genuine discussions.
- **FR-MAN-04** A spike or anomaly SHALL be recorded as an anomaly/risk and SHALL NOT be recast as positive sentiment or a price-direction claim.

### FR-AGG — Per-source results & aggregation *(§8)*

- **FR-AGG-01** The engine SHALL aggregate all available sources into a single asset-level result while retaining independent per-source results.
- **FR-AGG-02** Each per-source result SHALL expose at least: status, mention count, unique authors, sentiment score, freshness (seconds), and — on failure — a safe `error_class`.
- **FR-AGG-03** Per-source and aggregate output SHALL NEVER expose credentials, tokens, stack traces, or raw provider errors.
- **FR-AGG-04** Aggregate confidence SHALL reflect coverage: partial coverage (some sources unavailable) SHALL yield reduced confidence rather than a falsely complete result.

### FR-SCOPE — Asset-specific vs market-wide scope *(§9)*

- **FR-SCOPE-01** The engine SHALL explicitly report scope as `asset_specific` or `market_wide`.
- **FR-SCOPE-02** When there is insufficient asset-specific discussion, the engine SHALL report `insufficient_data` where appropriate rather than inflating asset-specific results.
- **FR-SCOPE-03** Generic crypto/market-wide chatter SHALL NEVER be presented as discussion of the requested asset; broader market sentiment MAY be exposed separately when relevant.

### FR-CACHE — Redis shared cache *(§10)*

- **FR-CACHE-01** The engine SHALL use a shared Redis instance as the cache of record for collected and computed results — process memory SHALL NOT be the source of truth (so multiple replicas/workers share state).
- **FR-CACHE-02** The engine SHALL cache raw per-provider fetches and computed aggregates with short freshness-oriented TTLs, and SHALL cache historical baselines with longer rolling windows.
- **FR-CACHE-03** The engine SHALL degrade gracefully on Redis failure: it MUST NOT crash and MUST NOT invent data when the cache is unavailable.
- **FR-CACHE-04** Cached values SHALL be namespaced/versioned per asset so different assets and cache generations do not collide. *(Exact keys are defined in doc 12.)*

### FR-SNAP — Historical snapshots *(§11)*

- **FR-SNAP-01** The engine SHALL support producing a compact point-in-time snapshot of an asset's social state suitable for later persistence, including at least: `asset_id, observed_at, sentiment_score, social_heat, mention_count, mention_velocity, organic_score, manipulation_risk`, source coverage, source agreement, and classification.
- **FR-SNAP-02** Snapshots SHALL contain only forward-collected evidence; the engine SHALL NEVER fabricate or backfill historical snapshots.
- **FR-SNAP-03** The snapshot model SHALL retain enough information to later support time-based analysis (e.g. sentiment acceleration, regime shifts, price/social divergence, and correlation with future outcomes) without requiring re-fabrication of past data.

### FR-INT — Integration contract *(§12)*

- **FR-INT-01** The engine SHALL expose a single asynchronous public entry point that accepts an asset identity and a horizon (defaulting to `swing`) and returns a result object.
- **FR-INT-02** The returned result SHALL be JSON-serializable and SHALL contain the fixed contract fields: `name, role, status, scope, classification, sentiment_score, confidence, social_heat, mention_velocity, organic_score, source_agreement, manipulation_risk, metrics, sources, anomalies, observed_at, source_class, data_quality`.
- **FR-INT-03** The fixed values SHALL be `name = social_sentiment`, `role = context`, and `source_class = multi_source_social_intelligence`.
- **FR-INT-04** The result SHALL NEVER contain `BUY`, `SELL`, `LONG`, or `SHORT`, and SHALL NEVER contain a price prediction or trade recommendation in any field, label, or example.
- **FR-INT-05** When all sources are unavailable, the entry point SHALL return cleanly with `status = unavailable` rather than raising. *(Exact output shape is owned by doc 14.)*

### FR-HOR — Horizons *(§13)*

- **FR-HOR-01** The engine SHALL support at least the horizons `intraday`, `swing`, and `position`.
- **FR-HOR-02** The horizon SHALL change engine behavior (time windows and/or metric weighting), not merely a label on the output: `intraday` emphasizes short-window acceleration (15m/1h/6h), `swing` emphasizes 6h/24h behavior, and `position` emphasizes a longer sustained social regime and organic discussion.
- **FR-HOR-03** An unrecognized or unsupported horizon SHALL be handled safely (e.g. defaulting or reporting), never causing an unhandled failure. *(Exact weighting is owned by doc 15.)*

### FR-PROV — Provenance & data quality *(§14)*

- **FR-PROV-01** Every result SHALL include a `data_quality` section exposing at least: `provider_coverage, freshness_score, asset_match_score, organic_data_ratio`.
- **FR-PROV-02** Data quality/provenance SHALL make it possible to tell which providers responded and which failed, how fresh the data is, how many observations informed the result, and the impact of duplicate filtering on coverage.
- **FR-PROV-03** Provenance SHALL be honest: it MUST reflect actual coverage and freshness and MUST NOT report data quality that the collected evidence does not support.

---

## Non-functional requirements

- **NFR-ASYNC-01** *(§20)* All network/provider I/O SHALL be async-first, allowing independent providers to be collected concurrently.
- **NFR-TIMEOUT-01** *(§16)* Every external call (providers, Redis) SHALL enforce a strict timeout; no call may block the engine indefinitely.
- **NFR-CONC-01** *(§16)* Concurrency SHALL be bounded so the engine cannot exhaust resources or overwhelm providers.
- **NFR-DEGRADE-01** *(§2, §8)* The engine SHALL degrade gracefully: one source down → others still produce a result; only one source available → partial result with reduced confidence; all sources down → `status = unavailable`. A missing source is never substituted with invented data.
- **NFR-DET-01** *(§20)* Scoring and aggregation SHALL be deterministic: identical inputs SHALL yield identical outputs (no LLM, no hidden randomness).
- **NFR-JSON-01** *(§12, §20)* The returned result SHALL be JSON-safe (only JSON-serializable types) so it can cross the integration boundary unchanged.
- **NFR-STATE-01** *(§20)* The engine SHALL NOT rely on hidden global mutable state; shared state lives in the explicit Redis cache, and modules keep collection separate from scoring and manipulation.
- **NFR-SEC-01** *(§16)* The engine SHALL sanitize provider content, cap input sizes, validate API responses, and safely handle malformed payloads.
- **NFR-SEC-02** *(§16)* The engine SHALL NOT execute, render, or dynamically load social content; **no `eval`/`exec`** and no dynamic code loading from provider data.
- **NFR-SEC-03** *(§16)* The engine SHALL use safe retry/backoff on external calls without amplifying load or leaking internal detail.
- **NFR-SEC-04** *(§16)* The engine SHALL NOT log secrets and SHALL redact sensitive provider error details in logs and results.
- **NFR-CRED-01** *(§15)* Credentials SHALL be read from environment variables only; the engine SHALL NOT contain hard-coded secrets and SHALL commit only `.env.example`.

---

## Constraints (restated as requirements)

*(Global guardrails; see doc 01 for the goals-level framing. Listed here so tests can cite them directly.)*

- **CON-01** No LLM anywhere in the pipeline or dependencies (no `anthropic`/`openai`/`google.generativeai`, Claude, Gemini, or any LLM) for sentiment, scoring, matching, or interpretation.
- **CON-02** No deployment and no production changes: the engine SHALL NOT deploy anything, modify any production server, or require access to the main Zynost backend.
- **CON-03** No real secrets in Git or Git history; only `.env.example` is committed, and no secrets appear in README, examples, or tests.
- **CON-04** The engine SHALL NEVER invent missing data or fabricate/backfill historical snapshots.
- **CON-05** Fake/mock data is permitted **only in tests**; the production implementation SHALL use real provider adapters and deterministic/provider-driven scoring.
- **CON-06** Paid/credentialed providers SHALL be implemented fully but activate only when credentials are present; when unconfigured they report `status = unavailable` (e.g. `error_class = "not_configured"`/`"unauthorized"`) and degrade gracefully — never demanding a purchase and never faking data.
