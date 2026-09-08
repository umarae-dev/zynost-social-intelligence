# 17 — Testing Strategy

> *Traces to specification §18 (mandatory automated tests + LLM-free scan).*
> This is the **verification catalog** for the engine. It owns which behaviors MUST have automated tests, how those tests are isolated (mocks vs production adapters), and the LLM-free / secret-hygiene scans. It does **not** own formulas (docs 08–11, 15), Redis key strings (doc 12), the contract JSON schema (doc 14), security constants (doc 16), env setup (doc 18), code-quality tooling (doc 19), or the Definition of Done checklist (doc 20 — this doc is what “all tests pass” refers to).

---

## Role relative to other docs

Every row below is traceable to a spec §18 bullet and to the design ID that defines pass/fail. Product goals (`01`) are the intent; this document is the **assert**.

| Constraint | How tests honor it |
|------------|-------------------|
| `CON-05` | Mocks/fakes **only** under `tests/`. Production `providers/*.py` stay real adapters. |
| `CON-06` | CI MUST NOT require paid live APIs. Credential-absent paths are first-class. |
| `CON-03`, **SEC-04** | No live tokens in fixtures. Fake literals only (`test-not-a-secret`). |
| `CON-01` | **TST-LLM** fails the build if an LLM SDK appears. |
| `CON-02`, **HLD-09** | No tests that deploy, hit production Zynost, or open PostgreSQL. |
| `NFR-DET-01` | Same fixture in → same scores out (run twice). |

**TST-01 — Default: no live network.** Provider tests inject a stub HTTP/SDK layer or a fake `SocialProvider`. Optional live tests, if ever added, MUST be opt-in and never part of the default gate. Redis tests use a fake or a disposable local instance; they MUST tolerate Redis-down (**CCH-12**) without failing the suite when the fake is used.

---

## 1. Layout and runner *(HLD-08)*

```
tests/
  test_providers.py          # isolation, success/failure, timeout, malformed, concurrent
  test_matching.py
  test_scoring.py            # polar + momentum (same score())
  test_manipulation.py
  test_aggregation.py
  test_cache.py
  test_engine.py             # build_social_sentiment contract, degradation, horizons
  test_snapshots.py
  test_quality_provenance.py
  test_json_contract.py
  test_llm_free.py           # repo scan
  test_secrets_hygiene.py    # no live-looking secrets in tree
```

Names are indicative (**HLD-08**); splitting files is allowed. Runner: pytest + pytest-asyncio (Python 3.12+). Public entry under test is `build_social_sentiment` (`FR-INT-01`); unit tests MAY call `score` / `assess` / `match_mentions` / `aggregate` directly.

**TST-02 — Determinism check.** For scoring, manipulation, aggregation, matching, and quality: invoke twice on a frozen fixture; assert structural equality of JSON-safe outputs (`NFR-DET-01`, **HLD-03**).

---

## 2. Provider & collection *(spec §18, FR-SRC, HLD-07)*

Fake adapters implement `SocialProvider.fetch_mentions`. Production adapter **code paths** (mapping, credential gate) are tested with canned bytes, not live calls.

| ID | Case | Passes when | Cites |
|----|------|-------------|-------|
| **TST-P-01** | X success | `status = available`, mentions normalized, no raw JSON on the model | **PRV-07**, **MOD-07** |
| **TST-P-02** | X failure | engine continues; X row `unavailable` + safe `error_class` | **HLD-07**, **PRV-03** |
| **TST-P-03** | Reddit success | same as P-01 for `reddit` | `FR-SRC-01` |
| **TST-P-04** | Reddit failure | same as P-02 | **HLD-07** |
| **TST-P-05** | Telegram success | public-channel mapping; empty list allowed | **PRV-02** |
| **TST-P-06** | Telegram failure | isolated; others still run | `FR-SRC-03` |
| **TST-P-07** | Discord failure | `unavailable` (timeout / `not_configured` / `unauthorized`); **no scrape** | `FR-SRC-01`, **SEC-10** |
| **TST-P-08** | All unavailable | `build_social_sentiment` **returns** (does not raise); `status = unavailable`; scores **null** not quiet-zeros | `FR-INT-05`, **INT-05**, **HLD-07** |
| **TST-P-09** | One provider only (e.g. Telegram) | partial result; `confidence` shrunk by \(A/P_{\mathrm{reg}}\); agreement `0` if \(m < 2\) | **AGG-13**, **MOM-09** |
| **TST-P-10** | Partial mix (some up, some down) | failed ids listed in `sources` / `data_quality`; live sources scored | **AGG-04**, **QLY-01** |
| **TST-P-11** | Timeout | expires inside **SEC-05** (10 s in prod; tests MAY freeze time / short timeout); `error_class = timeout`; siblings complete | **LLD-06**, `NFR-TIMEOUT-01` |
| **TST-P-12** | Malformed body | `error_class = malformed`; no crash; no raw body in result/logs | **SEC-07**, **INT-10** |
| **TST-P-13** | Concurrent collection | four stubs started together; one slow does not cancel others | **PRV-04**, `FR-SRC-04` |
| **TST-P-14** | Zero mentions | `available` + `[]` ≠ `unavailable`; quiet contract (`insufficient_data`, zeros, not `unavailable`) | **MOD-08**, **AGG-05** |
| **TST-P-15** | Stale / old `created_at` | window math uses \(t_0\) (**MOM-03**); does not invent extra hours | **HOR-03**, `CON-04` |
| **TST-P-16** | Missing credentials | no network; `not_configured` | **SEC-03**, **HLD-10** |
| **TST-P-17** | Present-but-rejected creds | `unauthorized` | **PRV-05** |

Discord **success** is covered with canned authorized payloads (`tests/test_discord_provider.py`). It MUST NOT be required to pass CI via a live bot (**TST-01**).

---

## 3. Matching *(spec §18, MAT-)*

Use synthetic `SocialMention` lists (no providers required).

| ID | Case | Passes when | Cites |
|----|------|-------------|-------|
| **TST-M-01** | Ticker-only | bare `ONE` / similar **not** in `asset_mentions` | **MAT-02**, `FR-ID-02` |
| **TST-M-02** | Collision-prone (`ONE, IN, ARB, TON, OP, NEAR, APT, LINK`) | symbol alone rejected even with one generic crypto word | **MAT-03** |
| **TST-M-03** | Ambiguous ticker + corroboration | name / contract / official account / multi-context **accepts** | **MAT-01** |
| **TST-M-04** | Alias phrase | alias hit with corroboration → asset-specific + `matched_terms` | **MAT-07**, `FR-ID-04` |
| **TST-M-05** | Contract address exact | high-confidence asset match | **MAT-01** |
| **TST-M-06** | Official account | origin/reference to official list → match evidence filled | **MAT-07** |
| **TST-M-07** | Market-wide vs asset-specific | leftovers in `market_wide`, never scored as the asset | **MAT-05**, **AGG-08**, `FR-SCOPE-03` |
| **TST-M-08** | Evidence fields | every `asset_mentions` row has terms, `match_confidence`, `match_rationale` | **MOD-06** |

---

## 4. Scoring, momentum, manipulation, aggregation *(spec §18)*

Frozen mention fixtures; call `score` / `assess` / `aggregate` (and engine where the contract is the assertion).

| ID | Case | Passes when | Cites |
|----|------|-------------|-------|
| **TST-S-01** | Polar scoring | known positive/negative/neutral texts → expected class band; range `[-100, +100]` | **SCR-08**, `FR-SENT-02` |
| **TST-S-02** | `insufficient_data` | \(n_{\mathrm{eff}} < 5\) → that label, not a fabricated `positive` | **SCR-10**, **AGG-14** |
| **TST-S-03** | Engagement-weighted ≠ equal-weight | high-engagement minority does **not** relabel classification (class uses equal-weight) | **SCR-09** |
| **TST-S-04** | Burst vs even velocity | same \(n_{1h}\), one 15m bin vs four → higher `mention_velocity` on the burst; **not** auto-`positive` | **MOM-05**, `FR-MOM-07` |
| **TST-S-05** | Source agreement | two available, aligned scores → high agreement; opposed → lower; one source → `0` | **MOM-09**, **AGG-09** |
| **TST-S-06** | Missing source ≠ agreement | unavailable sibling omitted from votes | `FR-MOM-05` |
| **TST-S-07** | Duplicate content | near-identical copies raise `duplicate_content_ratio` | **MAN-04** |
| **TST-S-08** | Repeated-author flood | low `unique_author_ratio` / high `author_concentration` | **MAN-** §5 |
| **TST-S-09** | Coordinated campaign | copies + tight timing raise burst / risk | **MAN-** §7 |
| **TST-S-10** | Sudden burst | high activity **and** anomaly/risk; classification not flipped bullish | **AGG-15**, `FR-MAN-04` |
| **TST-S-11** | Organic vs shill | 80 diverse genuine mentions score **more organic** than 500 near-identical from 20 accounts | **MAN-13**, `FR-MAN-03` |
| **TST-S-12** | `manipulation_risk` / `organic_score` | ranges `[0, 1]` and `[0, 100]`; empty batch does not publish `organic_data_ratio = 1` on the contract | **QLY-04**, **MAN-12** |
| **TST-S-13** | Leftovers | `market_wide` count in anomalies, not in asset `sentiment_score` | **AGG-08** |
| **TST-S-14** | Positive ≠ bullish | no `BUY`/`SELL`/`LONG`/`SHORT`; polar-positive is not a price claim | `FR-INT-04`, `FR-SENT-06` |

---

## 5. Cache *(spec §18, CCH-)*

Fake Redis or a test double implementing `cache.py` get/set.

| ID | Case | Passes when | Cites |
|----|------|-------------|-------|
| **TST-C-01** | Hit | second call can skip fetch for that purpose; numbers match first compute | **CCH-06**, **CCH-17** |
| **TST-C-02** | Miss | pipeline still returns an honest result; no invented baseline | **CCH-14**, **MOM-04** |
| **TST-C-03** | Expiry | after TTL, next read is miss (fake clock or TTL=0 fixture) | **CCH-09** |
| **TST-C-04** | Redis unavailable | miss semantics; `build_social_sentiment` does not raise; **no** `error_class` for Redis | **CCH-12**, **CCH-13** |
| **TST-C-05** | Narrow vs wide `since_minutes` | smaller envelope than requested → miss; wider → filter, not extrapolate | **CCH-07** |
| **TST-C-06** | Cache-served `observed_at` | equals collection \(t_0\), not read time | **CCH-17**, **INT-07** |
| **TST-C-07** | All-down not written as quiet baseline | \(A = 0\) → no baseline write | **CCH-15** |

---

## 6. Contract, JSON, horizons, snapshots, quality *(spec §18 + later docs)*

| ID | Case | Passes when | Cites |
|----|------|-------------|-------|
| **TST-I-01** | JSON serializable | `json.dumps(result)` with no default; no `NaN`/`Inf`/datetime/bytes | **INT-06**, `NFR-JSON-01` |
| **TST-I-02** | Required keys + identity | every **INT** required key; `name`/`role`/`source_class` frozen | `FR-INT-02`–`03` |
| **TST-I-03** | All-unavailable JSON | matches **INT-05** (null scores, `insufficient_data`, sources with `error_class`) | **INT-05** |
| **TST-I-04** | Unknown horizon | does not raise; `horizon_applied = swing` | **INT-02**, `FR-HOR-03` |
| **TST-I-05** | Horizons differ | same mentions + baselines, `intraday` vs `position` → different recency/acceleration and/or lookback (`since_minutes` 360 vs 4320) | **HOR-01**, **HOR-03**, `FR-HOR-02` |
| **TST-I-06** | `data_quality` | four FR-PROV-01 fields; coverage \(A/P_{\mathrm{reg}}\); no double-multiply of confidence | **QLY-05**, `FR-PROV-01`–`03` |
| **TST-I-07** | Quiet ≠ unavailable | \(A \ge 1\), \(N_a = 0\) → `available` | **AGG-07** |
| **TST-I-08** | Snapshot eligibility | \(A = 0\) → `project_snapshot` is `None`; quiet → a row; cache-served → same `observed_at` | **SNP-04**, **SNP-05** |
| **TST-I-09** | Snapshot ≠ contract | snapshot lacks `name`/`metrics`/`sources`/`data_quality` | **SNP-11** |

---

## 7. LLM-free scan *(spec §18, CON-01)*

**TST-LLM — Repository scan (mandatory, default gate).**

Walk the repo (exclude `.git`, virtualenvs, `*.pdf`). **Fail** if any file contains imports or dependency references for:

- `anthropic`, `openai`, `google.generativeai`
- Claude API / OpenAI API client usage
- Gemini client libraries

Case-insensitive token scan on `pyproject.toml`, lockfiles, `requirements*`, and `*.py`. Comments that only *forbid* LLMs (this docs tree, skills) MUST be excluded via an allowlist of documentation paths **or** by scanning **code and dependency manifests only** (`zynost_social/`, `tests/`, `pyproject.toml`). Docs may mention the forbidden names while stating they are banned.

Zero LLM dependency in the install graph.

---

## 8. Secrets hygiene *(CON-03, SEC-04)*

**TST-SEC — Tree scan (mandatory).** Fail if `tests/`, `examples/`, `README*`, or package source contain substrings that look like live credentials (Bearer tokens, `sk-` live keys, `redis://` with a password). `.env.example` MAY list **names** with empty values. Fake `test-not-a-secret` is allowed.

Not a substitute for **SEC-12** redaction unit tests (exception path → no stack in `sources`).

---

## 9. What the suite MUST NOT do

- Call paid provider APIs or require the user to subscribe (`CON-06`).
- Deploy, migrate PostgreSQL, start Celery, or hit the main Zynost backend (`CON-02`).
- Use production adapters that return invented mentions (`CON-04`, `CON-05`).
- Interleave horizons in one snapshot-equality assertion (**SNP-07**).
- Treat Redis miss as a provider failure (**CCH-13**).

---

## Decision index

| ID | Decision |
|----|----------|
| **TST-01** | Default suite is offline; mocks only under `tests/`. |
| **TST-02** | Deterministic modules are invoked twice on frozen fixtures. |
| **TST-P-*** | Spec §18 collection matrix including Discord failure, all-down, concurrent, quiet, stale. |
| **TST-M-*** | Collision, alias, contract, official, market-wide partition. |
| **TST-S-*** | Polar, velocity burst, agreement, manipulation vs organic, no trade language. |
| **TST-C-*** | Redis hit/miss/expiry/down plus envelope and `observed_at` honesty. |
| **TST-I-*** | JSON contract, horizons differ, quality, snapshot eligibility. |
| **TST-LLM** | Fail on LLM SDK imports/deps in code and manifests. |
| **TST-SEC** | Fail on live-looking secrets in tests/examples/README/package. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| pytest config in `pyproject.toml`, Python 3.12 install | doc 18 / 19 |
| Definition of Done roll-up (“all tests pass”) | doc 20 |
| Handoff test-count reporting | doc 21 |
| Formula details asserted against | docs 08–11, 15 (tests cite IDs, do not copy algebra) |
