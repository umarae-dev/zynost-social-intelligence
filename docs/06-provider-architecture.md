# 06 — Provider Architecture

> *Traces to specification §2 (sources, common interface, graceful failure, concurrent fetch).*
> This is the **provider-layer architecture** beneath `03-high-level-design.md` (**HLD-01**, **HLD-02**, **HLD-06**, **HLD-10**) and `04-low-level-design.md` (**LLD-01**, **LLD-02**, **LLD-05**–**LLD-07**). It catalogs how independent adapters are built, isolated, and run concurrently, and how they map onto `SocialMention` (doc 05). It does **not** redefine `SocialMention` fields (doc 05), matching algorithms (doc 07), scoring/manipulation formulas (docs 08–10, 15), cache keys (doc 12), or the integration-contract JSON (doc 14).

---

## Scope of this document

In scope: adapter boundaries, the shared fetch contract, per-provider isolation, concurrency, credential gating, and the shared helpers every adapter reuses. Out of scope: what happens to mentions *after* they leave a provider (matching, scoring, manipulation, aggregation — owned by later docs and `HLD-01`).

---

## 1. Sources *(spec §2, `FR-SRC-01`)*

| Provider | `provider` id (**MOD-05**) | Surface |
|----------|------|---------|
| X/Twitter | `x` | Public API, credentialed. |
| Reddit | `reddit` | Public API, credentialed. |
| Telegram | `telegram` | **Public channels only.** No private groups, no scraping. |
| Discord | `discord` | **Only** where a public/authorized API/bot integration exists. If no such access exists for an asset/server, the adapter reports unavailable — it never joins or scrapes unauthorized servers. |

**PRV-01 — Independent adapters, no shared coupling.** Each provider is its own module (`x.py`, `reddit.py`, `telegram.py`, `discord.py`) with no imports between adapters and no shared mutable state (**HLD-01**, **LLD-03**). Adding a fifth source (new file + engine registration) must not touch `matching.py`, `scoring.py`, `manipulation.py`, or `aggregation.py` (`FR-SRC-07`).

---

## 2. Common fetch contract *(spec §2, **LLD-01**)*

Every adapter implements the same interface and nothing else as its public fetch API:

```python
class SocialProvider:
    provider: ProviderId  # identity on ProviderFetchOutcome; not inferred from type
    credential_names: tuple[str, ...]  # CFG-03 set; empty means no env gate (tests)

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]: ...
```

- `provider` is the frozen source id (`x` / `reddit` / `telegram` / `discord`). Isolation copies it onto `ProviderFetchOutcome`; adapters MUST NOT smuggle status via instance flags (**LLD-02**).
- `credential_names` is the CFG-03 presence set. `collect_isolated` calls `credentials_present` **before** `fetch_mentions`; missing/blank → `not_configured` and **no** network (**SEC-03**). Adapters signal `unauthorized` / `malformed` / `rate_limited` by raising `ProviderError(error_class)` — only isolation maps that to status (**PRV-03**).
- `asset` is the canonical identity from doc 05 — adapters use it for query construction (handles, subreddits, channel ids, contract address where relevant), never as a bare ticker string (`FR-ID-02`).
- `since_minutes` is the lookback window the engine derives from the horizon (**HLD-04**; exact windows → doc 15). Adapters do not choose their own window.
- Return value is **normalized** `SocialMention` records only (`FR-NORM-01`). Raw provider JSON, HTML, or SDK objects never leave the adapter — mapping happens inside `fetch_mentions` before it returns (**HLD-02**).

**PRV-02 — Empty result is success.** An empty list means the window had zero matching activity; it is not a failure signal. Availability/failure is a separate outcome carried on `ProviderFetchOutcome` (**LLD-02**, **MOD-08**), not smuggled through the return value or a hidden instance flag.

---

## 3. Isolation: `ProviderFetchOutcome` and `collect_isolated` *(**LLD-02**, **LLD-05**)*

Adapters never call `fetch_mentions` directly from the engine. The engine calls a shared isolation wrapper in `providers/base.py`:

```python
async def collect_isolated(
    provider: SocialProvider,
    asset: AssetIdentity,
    since_minutes: int,
    timeout_s: float,
) -> ProviderFetchOutcome: ...
```

`ProviderFetchOutcome` (shape owned by doc 05) carries `provider`, `status` (`available` | `unavailable`), `mentions`, `error_class`, and `freshness_seconds`. `collect_isolated` is where a provider timeout, exception, malformed response, or missing credential is caught and turned into `status = unavailable` plus a safe `error_class` — it **never** re-raises into the engine (`FR-SRC-03`, `FR-SRC-05`, **HLD-06**).

**PRV-03 — One place converts failure to status.** Adapters raise or return normally; only `collect_isolated` decides status. This keeps every adapter's error handling identical and keeps `x.py`/`reddit.py`/`telegram.py`/`discord.py` free of duplicated try/except scaffolding (**LLD-05**).

---

## 4. Graceful failure requirements *(spec §2, `FR-SRC-03`–`06`)*

Every adapter, without exception:

- Has a **strict timeout** on every outbound call; nothing blocks the engine indefinitely (`NFR-TIMEOUT-01`).
- **Fails gracefully**: a timeout, auth error, rate limit, malformed payload, or crash becomes `unavailable` + a safe `error_class` from the closed set in doc 05 (**MOD-02**) — never an unhandled exception that reaches the engine.
- **Never takes the engine down.** Sibling adapters and the overall run continue regardless of this provider's outcome (**HLD-06**).
- **Exposes provenance/status** — which provider, what status, what error class, freshness when known — so doc 14's provenance/quality stages can report honestly (`FR-PROV-02`–`03`).
- **Never leaks** credentials, tokens, stack traces, or raw provider error bodies into logs, `error_class`, or any returned field (`FR-SRC-06`, `NFR-SEC-04`).

---

## 5. Concurrency *(spec §2, **HLD-06**, **LLD-06**)*

**PRV-04 — Concurrent, bounded, isolated gather.** The engine starts `collect_isolated` for all configured providers **concurrently**, under a bounded concurrency limit (`FR-SRC-04`, `NFR-ASYNC-01`, `NFR-CONC-01`). Each task has its own timeout; one slow or failing provider does not delay or cancel the others. Matching/scoring/manipulation/aggregation run only after the gather completes (successfully or with partial failures) — they never block on a hung provider.

Sockets, credentials, and per-fetch mutable state are never shared across adapters; each owns its own client/session.

---

## 6. Credential gating *(spec §2, §15, **HLD-10**, **LLD-07**, `CON-06`)*

**PRV-05 — Real adapters, credential-activated.** Every adapter is a real implementation against the provider's actual API — never a stub that fabricates data. Whether it *runs* is gated by environment-supplied credentials (catalog → doc 18):

| Situation | Adapter behavior |
|-----------|-------------------|
| Credentials absent | Do not call the network. Outcome `unavailable`, `error_class = "not_configured"`. |
| Credentials present but rejected (expired, revoked, insufficient scope) | Outcome `unavailable`, `error_class = "unauthorized"`. |
| Credentials present and valid | Adapter calls the provider and returns normalized mentions (or `[]`). |

This applies equally to free and paid/rate-limited provider tiers. The engine never demands a purchase, never blocks startup on missing credentials, and never invents mentions to compensate (`CON-04`, `CON-06`). Mocked providers exist only in tests, never in the production adapter code (`CON-05`).

---

## 7. Shared helpers — `providers/base.py` *(**LLD-05**)*

To avoid duplicated logic across `x.py` / `reddit.py` / `telegram.py` / `discord.py`, the following live **once** in `base.py` and are called, not copied:

| Helper | Purpose | Implements |
|--------|---------|------------|
| Timeout wrapper | Enforces the strict per-call timeout used by `collect_isolated`. | `NFR-TIMEOUT-01` |
| Size cap | Rejects/truncates oversized payloads before parsing. | `NFR-SEC-01` |
| Response validation | Guards against malformed/unexpected provider payload shapes. | `NFR-SEC-01` |
| Sanitization | Cleans mention text before it becomes `SocialMention.text`; content is treated as untrusted data, never executed/rendered. | `NFR-SEC-01`, `NFR-SEC-02` |
| Author/URL hashing | Produces `author_id_hash` / `url_hash` consistently (algorithm choice, not schema, lives here). | `FR-NORM-03` |
| Error redaction | Strips tokens/secrets/stack traces before an error becomes a safe `error_class`. | `FR-SRC-06`, `NFR-SEC-04` |
| Retry/backoff | Safe retry on transient failures; bounded so it cannot amplify load on a struggling provider. | `NFR-SEC-03` |
| Credential presence check | Used by adapters to short-circuit to `not_configured` before any network call (**HLD-10**). | `CON-06` |

**PRV-06 — No provider-specific code in `base.py`.** `base.py` contains no X/Reddit/Telegram/Discord URLs, payload shapes, or credentials-as-globals — only the shared contract, isolation wrapper, and helpers above.

---

## 8. Per-adapter files — I/O and mapping only

`x.py`, `reddit.py`, `telegram.py`, `discord.py` each contain:

- Provider-specific client/session construction, using credentials from environment only (`NFR-CRED-01`).
- The query built from `AssetIdentity` (handles/subreddits/channels/contract address as applicable to that source).
- Mapping from that provider's native response onto `SocialMention` fields (doc 05) — including the engagement measure for that source (e.g. likes+retweets for X, score for Reddit, reactions for Telegram/Discord where exposed), using `base.py` helpers for hashing, sanitization, and validation.

**PRV-07 — No scoring in adapters.** Adapters map and normalize only. Sentiment, manipulation, and match evidence are computed downstream (**LLD-04**); an adapter must never set `matched_terms`, `match_confidence`, or any score-shaped field itself.

### X (`providers/x.py`)

**PRV-X-01 — Recent search, credential-gated.** Endpoint `GET https://api.x.com/2/tweets/search/recent`. Both `X_API_KEY` and `X_BEARER_TOKEN` must be present (**CFG-03**); only the bearer is sent as `Authorization: Bearer …`. The API key is a presence gate, not a second header. Missing/blank → `not_configured` before any GET. HTTP 401/403 → `unauthorized`; 429 → `rate_limited` (no retry); 502–504 → `TransientProviderError` for **SEC-08**; other 4xx → `malformed`. HTTP is injected (`HttpGetter`) so tests never open a socket (**TST-01**). Default transport is `httpx`.

**PRV-X-02 — Query and engagement.** The search query is built from `AssetIdentity` in this order: official name, `from:` official X handles (`@` stripped), contract address if present, aliases, then `$SYMBOL`. Name is always first so the query is never ticker-only (`FR-ID-02`). Clauses are joined with `OR` and truncated from the end to **512** characters (X recent-search operator cap). `start_time` is `now − since_minutes`, clamped to `[1, 7×24×60]` minutes (X recent-search window). `max_results = 100`. Engagement = `like_count + retweet_count`; missing or non-finite native counts → `0`. Author id and `https://x.com/i/web/status/<id>` are hashed (**SEC-11**); plaintext handles/URLs never land on `SocialMention`. Incomplete tweets inside a valid envelope are skipped, not a whole-fetch `malformed`.

---

## 9. Decision index

| ID | Decision |
|----|----------|
| **PRV-01** | Adapters are independent files with no cross-adapter imports or shared mutable state; a fifth provider needs a new file + registration only. |
| **PRV-02** | An empty mention list is a successful, quiet fetch — not an unavailable outcome. |
| **PRV-03** | Only `collect_isolated` in `base.py` converts exceptions/timeouts/malformed responses into `status`/`error_class`; adapters do not each implement this themselves. |
| **PRV-04** | Providers are fetched concurrently under a bounded limit; one provider's failure or slowness never blocks or cancels the others. |
| **PRV-05** | Adapters are real implementations gated by credentials: absent → `not_configured`, rejected → `unauthorized`, valid → live fetch. Never a purchase demand, never fabricated data. |
| **PRV-06** | `base.py` holds only shared, provider-agnostic helpers — no per-provider URLs, payloads, or credential globals. |
| **PRV-07** | Adapters perform I/O and mapping only; scoring/matching/manipulation are strictly downstream. |
| **PRV-X-01** | X recent search via bearer; both env names required; injectable HTTP; status mapping as **PRV-05** / **SEC-08**. |
| **PRV-X-02** | Multi-signal query (never ticker-only); likes+retweets engagement; hashed author/URL; skip bad items. |

---

## Explicitly deferred

| Topic | Owner |
|-------|-------|
| `SocialMention` / `ProviderFetchOutcome` field-level schemas | doc 05 |
| Asset matching algorithm and collision handling | doc 07 |
| Sentiment, momentum, manipulation, horizon formulas | docs 08–10, 15 |
| Per-source aggregation and scope rules | doc 11 |
| Redis cache keys/TTLs | doc 12 |
| Full integration-contract JSON | doc 14 |
| Env var catalog and hashing algorithm choice | docs 16, 18 |
