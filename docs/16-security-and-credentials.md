# 16 — Security & Credentials

> *Traces to specification §15 (credentials / environment) and §16 (security requirements).*
> This is the **engine security posture** beneath `03-high-level-design.md` (**HLD-06**, **HLD-09**, **HLD-10**) and `04-low-level-design.md` (**LLD-05**, **LLD-06**, **LLD-07**). It owns how secrets are obtained and kept out of Git/logs/results, input sanitization and size caps, hashing of author/URL identifiers, provider timeouts and bounded concurrency, response validation, safe retry, and the no-execute rule. It does **not** own adapter I/O shapes (doc 06, **PRV-**), field lists (doc 05, **MOD-**), Redis keys/TTLs/250 ms cache timeout (doc 12, **CCH-**), the public contract redaction mapping (doc 14, **INT-10** — consumed), horizon lookbacks (doc 15, **HOR-03**), the `.env.example` name catalog and setup (doc 18), or FastAPI/PostgreSQL/Celery auth (**HLD-09**, `CON-02`).

---

## Role relative to other docs

Vision (`00` / `01`): untrusted social text is **evidence**, never code or a rendered page.

| Consumed | Owner |
|----------|-------|
| Helpers live once in `providers/base.py` | **LLD-05**, **PRV-03**, **PRV-06** |
| Credential gating: absent → `not_configured`, rejected → `unauthorized` | **PRV-05**, **HLD-10**, `CON-06` |
| Safe `error_class` set | **MOD-02**, **AGG-06**, **INT-10** |
| Empty fetch ≠ down | **MOD-08**, **PRV-02** |
| Redis: 250 ms, miss ≠ provider, never store secrets | **CCH-11**, **CCH-13**, **CCH-19**, **CCH-20** |
| PII: hashes on the model, not handles | **MOD-07**, `FR-NORM-03` |
| No LLM SDKs | `CON-01` |
| No deployment / production access | `CON-02` |

**SEC-01 — Untrusted by default.** Every provider body, mention `text`, and Redis payload is untrusted input. Scoring reads strings as data. Nothing in this module interprets social content as code, markup to render, or a module to import (`NFR-SEC-02`).

---

## 1. Credentials *(spec §15, NFR-CRED-01, CON-03, CON-06)*

**SEC-02 — Environment only.** Credentials are read with `os.environ` (or an equivalent process-environment API) at **adapter construction / first fetch**, never from files in the repo, never from `AssetIdentity`, never from mention `metadata`, never from Redis. No default host, token, or URL is hard-coded.

Names (placeholders only here; **doc 18 owns the catalog and `.env.example`**): `X_API_KEY`, `X_BEARER_TOKEN`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `DISCORD_BOT_TOKEN`, `REDIS_URL`.

**SEC-03 — Presence check before the network (**PRV-05**).** A credential is **present** iff the variable exists and `strip()` is non-empty. Missing/blank → do **not** call the network; `collect_isolated` yields `unavailable` + `error_class = "not_configured"`. HTTP 401/403 (or equivalent SDK auth failure) after a present credential → `unauthorized`. The engine MUST NOT prompt to purchase a plan (`CON-06`).

**SEC-04 — What Git may contain.** Only `.env.example` with **empty** values or obvious non-secrets (`changeme` is still a placeholder, never a live key). Forbidden in the repository, `README`, `examples/`, and **tests**: live tokens, bearer strings, `REDIS_URL` with a password, private keys. Tests MAY use clearly fake literals (e.g. `test-not-a-secret`) in-process; they MUST NOT read a developer’s real `.env` (`CON-03`, `CON-05`).

Adapters MAY hold a client object on the instance after construction; they MUST NOT store tokens in module globals (**PRV-06**, **MOD-09**).

`REDIS_URL` unset/unparseable → cache disabled, engine continues (**CCH-20**). That is not a provider `error_class`.

---

## 2. Timeouts and bounded concurrency *(NFR-TIMEOUT-01, NFR-CONC-01, HLD-06, LLD-06)*

**SEC-05 — Provider call budget `T_prov = 10` seconds.** `collect_isolated(..., timeout_s=10.0)` wraps the whole `fetch_mentions` (including **SEC-08** retries). On expiry: `error_class = "timeout"`, siblings continue (`FR-SRC-03`). Ten seconds is long enough for a normal HTTP/API round trip and short enough that four concurrent adapters cannot stall a worker for minutes. It is **not** a Redis timeout — that remains **CCH-11** (250 ms).

**SEC-06 — Concurrency cap `N_conc = 4`.** `engine.py` gathers adapters under a semaphore of 4 (`NFR-CONC-01`). Today \(P_{\mathrm{reg}} = 4\) (**MOD-05**), so all may run at once; a fifth adapter waits. The cap is on **in-flight provider fetches**, not on CPU scoring. Redis round trips are sequential/batched per **CCH-11** and do not share this semaphore.

No unbounded `gather` of user-controlled URLs. Adapters open only the provider API they implement; they MUST NOT follow arbitrary links found in mention text (**SEC-01**).

---

## 3. Size caps and payload validation *(NFR-SEC-01)*

**SEC-07 — Caps (justified).** All are engine constants, not caller-tunable past these ceilings.

| Cap | Limit | On exceed | Why |
|-----|-------|-----------|-----|
| Raw HTTP/SDK body | **1 MiB** (1 048 576 bytes) | Do not parse; `malformed` | Stops zip/JSON bombs before `json.loads` |
| Mention `text` after sanitize | **1024** Unicode code points, truncate | Keep prefix; still a valid mention | Bounds scoring/Redis; Twitter-scale plus headroom. Truncation is not `unavailable` |
| Mentions per provider per fetch | **128** newest by `created_at` | Drop older | **SCR-13** saturates at 40; 128 is headroom without unbounded RAM. Newest-first keeps the **HOR-** recency window |
| `metadata` keys/values | JSON-safe scalars only; each string **≤ 64** chars; **≤ 8** keys | Drop the extra | **MOD-07** |
| `horizon_requested` in metrics | already size-capped in **INT-08** | — | Contract surface |
| Redis value | **256 KiB** | skip write | **CCH-10** (consumed) |

**Validation (before mapping to `SocialMention`):**

- Body must parse as the adapter’s expected JSON/object shape; otherwise `malformed` (**PRV-03**).
- Required native fields missing → skip that item, do not fail the whole fetch unless the envelope itself is unusable.
- No `pickle`, no `yaml.load`, no `eval` of JSON, no `marshal`. `json.loads` of a size-capped UTF-8 body only.
- Numeric fields: finite only; non-finite → treat as missing (`0` engagement per doc 06, never invented virality).

Malformed envelope → `unavailable` + `malformed`, `mentions = []`. Partial item failures inside a valid envelope are omitted items, not engine crashes (`NFR-DEGRADE-01`).

---

## 4. Sanitize and never execute *(NFR-SEC-01, NFR-SEC-02)*

**SEC-09 — Text sanitization in `base.py` (once).** Applied before `SocialMention.text` is set:

1. Decode as UTF-8 with errors replaced; then operate on a Python `str`.
2. Strip NUL (`U+0000`) and other C0/C1 controls except `\t` / `\n` / `\r`.
3. Collapse `\r\n` / `\r` to `\n`.
4. Truncate to **SEC-07** text cap.
5. Do **not** HTML-unescape into executable entities; do **not** interpret Markdown.

**SEC-10 — No execute, no render, no load.** Forbidden on provider data and on cached mention text:

- `eval`, `exec`, `compile`, `ast.literal_eval` on mention text (JSON parse of **API envelopes** in adapters is allowed).
- `importlib` / `__import__` / `pkgutil` driven by provider strings.
- Shelling out (`subprocess`) with mention text.
- Rendering HTML/Markdown to a DOM, opening URLs from text, running WASM.

Scoring lexicons are **in-repo frozen lists** (**SCR-01**), not downloaded from mentions. Matching does not execute `match_rationale`.

Telegram: **public channels only**. Discord: **only** a public/authorized API; never scrape, never join unauthorized guilds (`FR-SRC-01`, **LLD** Discord Must NOT).

---

## 5. Author / URL hashing *(FR-NORM-03, LLD-05)*

Doc 05 stores `author_id_hash` / `url_hash`; this document owns the algorithm.

**SEC-11 — SHA-256, unkeyed, lowercase hex.** UTF-8 bytes of the provider’s author id string, or of a canonical URL string. Same helper for both. Empty / missing URL → `url_hash = null` (do not hash `""`). Output length 64 hex chars.

Unkeyed: this is **pseudonymity in the pipeline**, not password storage. A pepper would be another secret (`NFR-CRED-01`) and would break determinism across replicas unless shared. SHA-256 matches **CCH-04**’s hostile-`asset_id` segment, so one primitive is used for untrusted identifiers.

Never hash credentials. Never put the preimage (handle, email, raw URL) on the model, in Redis (**CCH-19**), in the contract, or in logs.

---

## 6. Retry / backoff *(NFR-SEC-03)*

**SEC-08 — At most one retry, inside `T_prov`.**

| Outcome | Retry? |
|---------|--------|
| Timeout / connection reset / HTTP 502–504 | **Once**, wait **200 ms**, then second attempt if elapsed \(< T_{\mathrm{prov}}\) |
| `not_configured` / `unauthorized` / HTTP 401/403 | **No** |
| `malformed` / HTTP 4xx other than 429 | **No** |
| HTTP 429 | **No** — retrying amplifies load. Map to safe `error_class = "rate_limited"` (**MOD-02** allows later safe classes) |
| Success, including `[]` | **No** |

No jittered infinite loops. No retry storm across the four adapters (each isolates its own one retry). Backoff delay is included in the 10 s budget. Logs on retry: provider id + `retry` + attempt number — **no** body, **no** URL query with tokens.

---

## 7. Redaction and logging *(NFR-SEC-04, FR-SRC-06, FR-AGG-03)*

**SEC-12 — Results and logs carry `error_class`, never the exception.** `collect_isolated` catches everything. The contract and `SourceBundle` expose **MOD-02** / `rate_limited` only (**INT-10**, **AGG-06**). Exception `str()`, stack traces, response bodies, `WWW-Authenticate`, and redirect `Location` with tokens MUST NOT appear in:

- the public dict, `metrics`, `anomalies.detail`, `data_quality`
- Redis values (**CCH-19**)
- log lines (including `exc_info=True` on provider paths)

Allowed log fields: `provider`, `error_class`, cache `purpose`, `served_from_cache`, `horizon_applied`, `asset_id` **as already charset-guarded** (**CCH-04**). Forbidden: env values, `Authorization`, `REDIS_URL`, bearer tokens, cookies, raw JSON errors.

**SEC-13 — Secret scanner posture for humans.** Treat any string matching `Bearer `, `api_key=`, or a `redis://` URL with a password as a leak if it would be committed or logged. Implementation MAY add a small denylist redactor in the log helper; it MUST NOT be the only control — **SEC-02** and **SEC-12** are the controls.

Scoring/manipulation logs MUST NOT print mention `text` at info level (PII and untrusted content). Tests may assert on text in-process.

---

## 8. Surfaces that must stay clean

| Surface | Rule |
|---------|------|
| `build_social_sentiment` dict | **INT-06**, **INT-10** |
| `SocialMention` | hashes + sanitized text; **MOD-07** |
| Redis | **CCH-19**; sanitized before write |
| Snapshot | no PII, no sources blob (**SNP-11**) |
| Git | **SEC-04**, `CON-03` |
| LLM imports | none (`CON-01`; scan is doc 17) |

No `BUY`/`SELL`/`LONG`/`SHORT` in errors or logs (`FR-INT-04`).

---

## 9. Interface sketch *(LLD-05, not a package)*

```python
T_PROV_S = 10.0
N_CONC = 4
BODY_MAX_BYTES = 1_048_576
TEXT_MAX_CHARS = 1024
MENTIONS_MAX = 128

def credentials_present(names: tuple[str, ...]) -> bool: ...   # non-empty env; never log values

def sanitize_text(raw: str) -> str: ...                      # SEC-09

def hash_identifier(preimage: str) -> str: ...               # SEC-11 SHA-256 hex

def redact_for_log(kind: str, provider: str) -> None: ...    # SEC-12; no secrets

async def collect_isolated(...) -> ProviderFetchOutcome:     # timeout, retry, catch-all
```

`engine.py` owns the semaphore (**SEC-06**). Adapters call helpers; they do not reimplement them (**PRV-06**).

---

## Decision index

| ID | Decision |
|----|----------|
| **SEC-01** | Provider and cache payloads are untrusted data, never code or a document to render. |
| **SEC-02** | Credentials only from the process environment; no hard-coded secrets. |
| **SEC-03** | Missing creds → `not_configured` without I/O; rejected creds → `unauthorized` (**HLD-10**). |
| **SEC-04** | Git/README/examples/tests contain no live secrets; only `.env.example` placeholders. |
| **SEC-05** | Provider timeout **10 s** including retries; Redis timeout stays **CCH-11**. |
| **SEC-06** | At most **4** in-flight provider fetches. |
| **SEC-07** | Body 1 MiB / text 1024 chars / 128 mentions / metadata bounds; malformed envelope → `malformed`. |
| **SEC-08** | ≤1 retry, 200 ms, only transient transport/5xx; never retry auth, 429, or malformed. 429 → `rate_limited`. |
| **SEC-09** | Shared text sanitizer: controls stripped, length capped. |
| **SEC-10** | No `eval`/`exec`/dynamic import/subprocess/render of social content. |
| **SEC-11** | SHA-256 hex hashes for author id and URL; no pepper; no preimages stored. |
| **SEC-12** | Logs and results: safe `error_class` only; never tokens, traces, or raw API errors. |
| **SEC-13** | Denylist redaction is defense-in-depth, not a substitute for **SEC-02**/**SEC-12**. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Exact `.env.example` lines, Python 3.12+ setup, `pyproject.toml` | doc 18 |
| Adapter URLs, query construction, engagement mapping | doc 06 |
| Redis key grammar / TTLs / 250 ms | doc 12 |
| Contract field list | doc 14 |
| LLM-free repo scan as a test | doc 17 |
| Consumer TLS, FastAPI auth, DB roles | **HLD-09** / `CON-02` |
