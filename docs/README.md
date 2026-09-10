# Zynost Social Intelligence Engine — Documentation

> **Repository (planned):** `zynost-social-intelligence`
> **Nature:** Standalone, private Python module (Python 3.12+, async-first) that will later be
> integrated into an existing **FastAPI + PostgreSQL + Redis + Celery** backend.
> **Status of this folder:** Numbered design docs `00`–`21` are complete. Implementation has
> started: package skeleton (`zynost_social/`), frozen types in `models.py` (doc 05),
> provider isolation in `providers/base.py`, credential-gated X, Reddit, Telegram, and
> Discord adapters (`providers/x.py`, `providers/reddit.py`, `providers/telegram.py`,
> `providers/discord.py`, doc 06), asset matching in `matching.py` (doc 07),
> deterministic polar + momentum scoring in `scoring.py` (docs 08, 09, 15),
> manipulation / organic-quality assessment in `manipulation.py` (doc 10),
> per-source aggregation / scope in `aggregation.py` (doc 11),
> fail-soft Redis cache in `cache.py` (doc 12),
> honest run provenance in `provenance.py` (doc 14),
> `data_quality` scales in `quality.py` (doc 14), and
> the public `build_social_sentiment` orchestrator in `engine.py` (doc 14), and
> snapshot projection (`project_snapshot`, doc 13), and
> LLM-free / secrets-hygiene scans (`tests/test_llm_free.py`, `tests/test_secrets_hygiene.py`,
> doc 17).

This README is the **master index** for the project documentation. It does **not** contain the full
detail of each topic — instead, each entry below describes *what that document must cover* and maps
it back to the source specification. We will then flesh out each numbered document one at a time
(e.g. `00-project-vision.md`, `01-product-goals.md`, ...), learning the domain as we go.

---

## How to read this folder

- Each concern in the specification gets its **own numbered document**. The numbering gives a
  natural reading order — from high-level "why" (vision, goals) down to "how" (HLD, LLD, per-module
  design) and finally "process" (testing, code quality, definition of done, handoff).
- This README stays intentionally shallow: it is a **table of contents + scope statement** for each
  document. The actual formulas, schemas, class designs, and thresholds live inside the individual
  files, added incrementally.
- Every planned document is traced back to the original specification so **no requirement is lost**.

---

## Guardrails (apply to the entire project)

These constraints come straight from the specification and are **non-negotiable**. Every document
and, later, every line of code must respect them.

- **Do NOT** deploy anything, modify any production server, or require access to the main Zynost backend.
- **Do NOT** use Claude, OpenAI, Gemini, or **any LLM** for sentiment classification.
- **Do NOT** commit real API keys/secrets (only `.env.example` is committed).
- Fake/mock data is allowed **only in tests**, never as the actual production implementation.
- The final module must use **real provider adapters** and **deterministic / provider-driven scoring**.
- This is **NOT** a trading signal engine. It must **NEVER** output `BUY`, `SELL`, `LONG`, or `SHORT`.
- Positive social sentiment must **NOT** automatically mean bullish price direction.
- Never invent missing information; never fabricate/backfill historical data.

---

## Documentation index

| #  | Document | Covers (spec reference) |
|----|----------|--------------------------|
| 00 | Project Vision & Overview | §1 Purpose, intro/framing |
| 01 | Product Goals & Scope | §1 (what it measures / must never output), non-goals |
| 02 | Requirements Specification | Functional + non-functional across all sections |
| 03 | High-Level Design (HLD) | §2, §17 structure, concurrency, data flow |
| 04 | Low-Level Design (LLD) | §17 modules, per-module interfaces & responsibilities |
| 05 | Data Models & Schemas | §3 AssetIdentity, §4 SocialMention, §11 Snapshot |
| 06 | Provider Architecture | §2 sources, common interface, graceful failure |
| 07 | Asset Identity & Matching | §3 canonical identity, collision avoidance |
| 08 | Sentiment Analysis & Scoring | §5 metrics, classification, formulas/weights |
| 09 | Social Activity & Momentum | §6 velocity, heat, acceleration, agreement |
| 10 | Manipulation / Pump Detection | §7 manipulation signals & scores |
| 11 | Aggregation, Source Results & Scope | §8 per-source, §9 asset-specific vs market-wide |
| 12 | Redis Shared Cache | §10 caching strategy & keys |
| 13 | Historical Snapshots | §11 snapshot model & future analysis |
| 14 | Integration Contract & Data Quality | §12 entry point/output, §14 provenance |
| 15 | Horizon Support | §13 intraday/swing/position weighting |
| 16 | Security & Credentials | §15 credentials, §16 security requirements |
| 17 | Testing Strategy | §18 mandatory tests + LLM-free scan |
| 18 | Configuration & Environment | §15 env vars, setup |
| 19 | Code Quality Standards | §20 engineering standards |
| 20 | Definition of Done | §21 completion criteria |
| 21 | Final Handoff | §22 handoff deliverables (HANDOFF.md) |

---

## 00 — Project Vision & Overview  *(spec §1)*

Purpose of this document: frame *why* the engine exists and what it fundamentally is.

- The engine is a **production-grade, multi-source crypto Social Sentiment Intelligence Engine**.
- It **collects, normalizes, cleans, scores, and aggregates** social activity for a specific crypto asset.
- Its role in Zynost: the next major **social sentiment intelligence** component, joining 17 existing
  specialized intelligence agents/modules.
- Explicit framing: this is **context/intelligence**, **not** a trading signal engine.

## 01 — Product Goals & Scope  *(spec §1)*

Purpose: define measurable goals and hard non-goals.

- **Must measure:** crowd sentiment, social activity, social momentum, mention velocity, engagement,
  organic vs manipulated chatter, source agreement/disagreement, anomalies.
- **Must never output:** `BUY`, `SELL`, `LONG`, `SHORT`.
- Positive sentiment ≠ bullish price direction (state this explicitly).
- Non-goals: price prediction, trade recommendation, LLM-based classification.

## 02 — Requirements Specification

Purpose: enumerate all **functional** and **non-functional** requirements, cross-referenced to spec sections.

- Functional: sources, identity, normalization, sentiment, momentum, manipulation, aggregation,
  scope handling, caching, snapshots, integration contract, horizons, provenance.
- Non-functional: async-first, strict timeouts, bounded concurrency, graceful degradation, security,
  determinism, JSON-safe output, no hidden global mutable state.
- Constraints: no LLM, no deployment, no real secrets in Git.

## 03 — High-Level Design (HLD)  *(spec §2, §17)*

Purpose: system-level architecture and data flow.

- Component overview: providers → matching → scoring → manipulation → aggregation → provenance/quality → engine.
- **Concurrency model:** fetch independent providers concurrently; strict timeouts; one provider failure
  must never take the engine down.
- Degradation behavior:
  - X unavailable → Reddit + Telegram still work.
  - Reddit unavailable → X + Telegram still work.
  - Only Telegram available → partial result with reduced confidence.
  - All unavailable → `status = unavailable`.
- Separation of concerns: collection logic kept separate from scoring and manipulation detection.
- Recommended project layout (`zynost_social/` package, `providers/`, `tests/`, `examples/`).

## 04 — Low-Level Design (LLD)  *(spec §17)*

Purpose: module-by-module responsibilities, interfaces, and boundaries.

- Modules: `engine.py`, `models.py`, `matching.py`, `scoring.py`, `manipulation.py`,
  `aggregation.py`, `cache.py`, `provenance.py`, `quality.py`.
- Providers package: `providers/base.py`, `x.py`, `reddit.py`, `telegram.py`, `discord.py`.
- Common provider interface (`SocialProvider.fetch_mentions(asset, since_minutes) -> list[SocialMention]`).
- Rules: no giant single file, no duplicated provider logic, clean typed interfaces.

## 05 — Data Models & Schemas  *(spec §3, §4, §11)*

Purpose: define the core data structures.

- **AssetIdentity**: `asset_id, symbol, name, aliases, chain, contract_address, official_x_accounts,
  official_reddit, official_telegram, official_discord`.
- **SocialMention**: `provider, external_id, asset_id, text, author_id_hash, created_at, engagement,
  url_hash, matched_terms, match_confidence, language, metadata`.
- **SocialSentimentSnapshot**: `asset_id, observed_at, sentiment_score, social_heat, mention_count,
  mention_velocity, organic_score, manipulation_risk, source coverage, source agreement, classification`.
- Privacy: avoid persisting unnecessary PII; prefer hashed/pseudonymous author identifiers.

## 06 — Provider Architecture  *(spec §2)*

Purpose: how independent source adapters work.

- **Sources:** X/Twitter, Reddit, Telegram public channels, Discord (only where public/authorized API exists).
- Common interface; architecture must make adding providers later easy.
- Every provider must: work independently, use strict timeouts, fail gracefully, never take the whole
  engine down, expose provenance/status, never leak credentials or raw sensitive API errors.
- Fetch independent providers concurrently.

## 07 — Asset Identity & Matching  *(spec §3)*

Purpose: identify a crypto asset robustly (never ticker-only).

- Matching signals: canonical `asset_id`, official name, symbol, aliases, chain/network,
  contract address, known official social accounts, surrounding crypto context, project terminology.
- **Collision avoidance** for ambiguous symbols: `ONE, IN, ARB, TON, OP, NEAR, APT, LINK`.
- For every matched mention expose: matched terms, matching confidence, why it matched.
- Designed to plug into Zynost's future **Canonical Asset Registry**.

## 08 — Sentiment Analysis & Scoring  *(spec §5)*

Purpose: deterministic (non-LLM) sentiment scoring.

- **No LLM** — deterministic/open/local approach appropriate for production.
- Outputs (minimum): positive/neutral/negative counts and ratios; sentiment score `-100..+100`;
  confidence; engagement-weighted sentiment; bullish & bearish phrase intensity; fear, greed/excitement,
  uncertainty, and euphoria intensity.
- **Classification** must be one of: `strong_positive, positive, neutral, mixed, negative,
  strong_negative, insufficient_data`.
- Document exact formulas/weights — no unexplained magic numbers.

## 09 — Social Activity & Momentum  *(spec §6)*

Purpose: activity and momentum metrics.

- Metrics: total mentions, unique authors, mention velocity, current 1h mentions, previous 1h baseline,
  6h change, 24h change, engagement velocity, social heat, social acceleration, source agreement,
  source disagreement, data freshness, coverage.
- Must later support statements like: *"BTC social sentiment moved from +18 to +67 in 3 hours."*

## 10 — Manipulation / Pump Detection  *(spec §7)*

Purpose: resistance to manipulated chatter.

- Detect: duplicate posts, copied campaign text, repeated URLs, repeated-author flooding, coordinated
  bursts, suspiciously similar messages, low-author-diversity pumps, extreme engagement concentration,
  sudden mention spikes, campaign/shill behavior, bot-like repetition.
- Compute: `duplicate_content_ratio, unique_author_ratio, engagement_concentration,
  author_concentration, suspicious_burst_score, spam/bot suspicion, manipulation_risk, organic_score,
  anomaly_score`.
- Principle: 500 near-identical shill posts from 20 accounts must **not** score more organic than 80
  independent genuine discussions. A spike is never automatically positive/bullish.

## 11 — Aggregation, Source Results & Scope  *(spec §8, §9)*

Purpose: combine per-source data and distinguish scope.

- **Per-provider results**: status, mention_count, unique_authors, sentiment_score, freshness_seconds,
  and `error_class` on failure. Never expose credentials, stack traces, tokens, or sensitive errors.
- **Scope:** explicitly distinguish `asset_specific` vs `market_wide`.
- If insufficient asset-specific discussion: return `insufficient_data` where appropriate; optionally
  expose broader market sentiment separately; never pass generic crypto chatter off as asset-specific.

## 12 — Redis Shared Cache  *(spec §10)*

Purpose: shared caching for multiple replicas/workers.

- Redis is the **shared** cache (not process memory as source of truth).
- Suggested TTLs: raw provider fetch 2–5 min; computed aggregate 2–5 min; historical trend baselines
  longer rolling windows.
- Suggested keys: `zynost:social:v1:<asset_id>:raw:x`, `...:raw:reddit`, `...:aggregate`,
  `...:baseline:1h`, `...:baseline:24h`.
- Handle Redis failure gracefully where reasonable.

## 13 — Historical Snapshots  *(spec §11)*

Purpose: compact snapshot model for later PostgreSQL persistence.

- Stores the snapshot fields listed in §05.
- **Forward-collected evidence** only — do not fabricate/backfill.
- Must later support: sentiment acceleration, regime shifts, price/social divergence, correlations with
  future 4h/24h/7d outcomes, whether social bursts preceded or followed price movement.

## 14 — Integration Contract & Data Quality  *(spec §12, §14)*

Purpose: the public entry point and output shape.

- Entry point (approx): `async def build_social_sentiment(asset: AssetIdentity, horizon: str = "swing") -> dict`.
- Result must be **JSON serializable** with the exact contract fields (name, role, status, scope,
  classification, sentiment_score, confidence, social_heat, mention_velocity, organic_score,
  source_agreement, manipulation_risk, metrics, sources, anomalies, observed_at, source_class, data_quality).
- Fixed values: `name = social_sentiment`, `role = context`, `source_class = multi_source_social_intelligence`.
- **data_quality** must expose: provider_coverage, freshness_score, asset_match_score, organic_data_ratio
  (and enough to know which providers responded/failed, freshness, observation counts, duplicate filtering impact, coverage).

## 15 — Horizon Support  *(spec §13)*

Purpose: make horizons change behavior, not just metadata.

- Support at least: `intraday`, `swing`, `position`.
- Weighting concept: intraday → higher weight on 15m/1h/6h acceleration; swing → 6h/24h behavior;
  position → longer sustained social regime and organic discussion.
- Document the exact horizon weighting.

## 16 — Security & Credentials  *(spec §15, §16)*

Purpose: security posture and secret handling.

- Credentials only from environment variables; commit only `.env.example`; never put secrets in
  README/examples/tests.
- Security requirements: sanitize provider content, cap input sizes, strict network timeouts, bounded
  concurrency, validate API responses, safe retry/backoff, protect against malformed payloads, do not
  execute/render social content, no eval/exec, no dynamic code loading from provider data, avoid logging
  secrets, redact sensitive provider error details.

## 17 — Testing Strategy  *(spec §18)*

Purpose: enumerate mandatory automated tests.

- Provider success/failure for X, Reddit, Telegram; Discord failure; all-unavailable; one-provider-only;
  partial success; timeout; malformed response; concurrent collection; zero mentions; stale data.
- Content/manipulation: duplicate content, repeated-author flooding, coordinated campaign, sudden burst,
  organic discussion, sentiment scoring, engagement-weighted sentiment, source agreement/disagreement,
  manipulation risk, organic score.
- Matching: ticker collision, ambiguous ticker, alias matching, contract-address matching, official
  account matching, market-wide vs asset-specific.
- Cache: Redis hit/miss/expiration/unavailable. Plus JSON serialization and intraday/swing/position behavior.
- **LLM-free scan test:** fails if imports/references exist for `anthropic`, `openai`,
  `google.generativeai`, Gemini, Claude API, or OpenAI API. Zero LLM dependency.

## 18 — Configuration & Environment  *(spec §15)*

Purpose: configuration and setup.

- `.env.example` variables: `X_API_KEY, X_BEARER_TOKEN, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET,
  REDDIT_USER_AGENT, TELEGRAM_API_ID, TELEGRAM_API_HASH, DISCORD_BOT_TOKEN, REDIS_URL`.
- Setup instructions, Python 3.12+, `pyproject.toml`.

## 19 — Code Quality Standards  *(spec §20)*

Purpose: engineering bar for the implementation.

- Python 3.12+, async-first networking, type hints, clean interfaces, modular architecture,
  production-safe exception handling, no giant single file, no duplicated provider logic, no hidden
  global mutable state, predictable deterministic calculations, JSON-safe results, useful comments only.

## 20 — Definition of Done  *(spec §21)*

Purpose: objective completion checklist.

- All 15 criteria from the spec (all tests pass; real provider architecture; no fake/mock production
  impl; no LLM; single-provider failure tolerated; all-failure returns unavailable cleanly; identity
  avoids ticker-only matching; manipulation detection works; Redis caching works; snapshot support;
  horizons behave differently; exact integration contract; no secrets in Git/history; README explains
  formulas/limitations; no deployment/production changes).

## 21 — Final Handoff  *(spec §22)*

Purpose: deliverables at completion.

- Push to the private repo; clean working tree; include README, `.env.example`, all tests, example output.
- Provide `HANDOFF.md` with: implemented providers, provider API requirements, architecture summary,
  test count/result, limitations, exact public integration function, any intentionally deferred features.
- Do NOT merge into another repository, deploy, or request production credentials. The repo will be
  independently audited before integration.

---

## Next steps

Remaining work: root `README.md`, `examples/example_result.json`, then
`HANDOFF.md`.
Do not invent mentions, do not add
FastAPI/Celery/PostgreSQL, and do not use an LLM. This README is updated only if the overall
structure or implementation status changes.
