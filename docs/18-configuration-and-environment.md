# 18 — Configuration & Environment

> *Traces to specification §15 (credentials, environment variables, setup).*
> This is the **configuration catalog** beneath `NFR-CRED-01` / `CON-03` and `03-high-level-design.md` (**HLD-08** layout, **HLD-10** gating). It owns the frozen environment-variable names, `.env.example` contents, Python 3.12+ / `pyproject.toml` baseline, and local setup steps. It does **not** own how secrets are redacted or hashed (doc 16, **SEC-**), Redis TTLs or the 250 ms timeout (doc 12, **CCH-09** / **CCH-11**), provider HTTP (doc 06), horizon lookbacks (doc 15), test layout (doc 17), or lint/type-check commands (doc 19). FastAPI env injection is a **consumer** concern (**HLD-09**, `CON-02`).

---

## Role relative to other docs

| Consumed | Owner |
|----------|-------|
| Environment only; presence = non-empty strip | **SEC-02**, **SEC-03** |
| Missing creds → `not_configured`, no network | **PRV-05**, **HLD-10**, `CON-06` |
| `REDIS_URL` unset → cache disabled, engine still runs | **CCH-20** |
| Never log `REDIS_URL` or tokens | **SEC-12**, **CCH-20** |
| Package layout `zynost_social/` | **HLD-08** |
| No LLM dependencies | `CON-01`, **TST-LLM** |
| Engine constants (timeouts, caps, weights, keys) | docs 12, 15, 16 — **not** env tunables |

**CFG-01 — Frozen name set.** The engine reads **exactly** the variables in §2. Adding a required name needs this document, `.env.example`, guardrails, and adapters updated together. Callers MUST NOT pass credentials as function arguments to `build_social_sentiment` (`FR-INT-01` stays `asset` + `horizon`).

**CFG-02 — Not tunables.** These are **not** environment variables (already specified elsewhere): provider timeout 10 s (**SEC-05**), Redis 250 ms (**CCH-11**), TTLs (**CCH-09**), `since_minutes` (**HOR-03**), size caps (**SEC-07**), concurrency 4 (**SEC-06**), scoring/horizon weights. Stretching them via env would bypass the spec bands.

---

## 1. Environment catalog *(spec §15)*

All values are strings. **Present** iff the name exists and `strip()` is non-empty (**SEC-03**). Empty string in `.env.example` is **absent** at runtime until the operator fills a private `.env` (never committed).

| Variable | Used by | When absent |
|----------|---------|-------------|
| `X_API_KEY` | `providers/x.py` | See **CFG-03** |
| `X_BEARER_TOKEN` | `providers/x.py` | See **CFG-03** |
| `REDDIT_CLIENT_ID` | `providers/reddit.py` | Reddit `not_configured` |
| `REDDIT_CLIENT_SECRET` | `providers/reddit.py` | Reddit `not_configured` |
| `REDDIT_USER_AGENT` | `providers/reddit.py` | Reddit `not_configured` |
| `TELEGRAM_API_ID` | `providers/telegram.py` | Telegram `not_configured` |
| `TELEGRAM_API_HASH` | `providers/telegram.py` | Telegram `not_configured` |
| `DISCORD_BOT_TOKEN` | `providers/discord.py` | Discord `not_configured` |
| `REDIS_URL` | `cache.py` | Cache disabled for the process (**CCH-20**); **not** a provider `error_class` |

**CFG-03 — Per-adapter presence sets.** An adapter is **configured** only when **every** variable in its set is present. Partial sets (e.g. Reddit id without secret) are `not_configured` and MUST NOT call the network.

| Adapter | Required names |
|---------|----------------|
| X | `X_API_KEY` **and** `X_BEARER_TOKEN` |
| Reddit | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` |
| Telegram | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` |
| Discord | `DISCORD_BOT_TOKEN` |
| Cache | `REDIS_URL` (optional; parseable URL) |

`TELEGRAM_API_ID` is numeric at the provider, but stored as an env **string**; the adapter parses it. Unparseable id → `not_configured` (treat as absent), no traceback in the contract (**SEC-12**).

`REDIS_URL` MUST parse as a Redis URL (e.g. `redis://` / `rediss://`). Unparseable → same as unset (**CCH-20**). Password in the URL is a secret: never log, never put in `.env.example`.

No other names are required for a correct engine run. Optional consumer vars (log level, FastAPI bind) are **out of this module**.

---

## 2. `.env.example` *(CON-03, SEC-04)*

**CFG-04 — Committed file, empty values.** The repository contains **one** credential template, `.env.example`, with the names above and **empty** right-hand sides (or comments only). Example shape (not live secrets):

```
# Copy to .env (gitignored). Never commit .env.
X_API_KEY=
X_BEARER_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
DISCORD_BOT_TOKEN=
REDIS_URL=
```

`.gitignore` MUST include `.env` and similar private files. `README`, `examples/`, and `tests/` MUST NOT paste filled values (**TST-SEC**).

The engine **MUST NOT** auto-load `.env` from disk (`python-dotenv` is not required). Operators export variables or let the consumer process inject them. Auto-loading a cwd `.env` would hide the `os.environ` contract (**SEC-02**) and risk picking up the wrong file.

Paid APIs: filling these vars is optional. Empty → that adapter stays `not_configured`; the engine still returns a contract (`CON-06`). This document MUST NOT instruct the user to purchase a plan.

---

## 3. Runtime and `pyproject.toml` *(HLD-08, CON-01)*

**CFG-05 — Python 3.12+.** `requires-python = ">=3.12"`. No 3.11 fallback. Async provider I/O (`NFR-ASYNC-01`).

**CFG-06 — Package identity.** Distribution name `zynost-social-intelligence`; import package `zynost_social`. The public function is `build_social_sentiment` (`FR-INT-01`).

`pyproject.toml` SHALL:

- Declare the package and `tests/` as the pytest path (tooling detail MAY live in doc 19).
- List runtime deps needed for HTTP and Redis **clients** (`httpx` for X and Reddit; Redis client later) — never `anthropic`, `openai`, `google.generativeai`, or other LLM SDKs (`CON-01`, **TST-LLM**).
- Commit no secret, no production `REDIS_URL`.

Lockfiles, if added later, are scanned by **TST-LLM**. Exact third-party version pins are an implementation choice as long as they stay LLM-free and compatible with 3.12.

---

## 4. Local setup *(operator steps, not deployment)*

This is **developer setup** for the standalone repo. It is not production deployment (`CON-02`).

1. Python 3.12+ available.
2. Clone the private repo; create a virtualenv; `pip install -e ".[dev]"` (or the extra name chosen at implementation — pytest + pytest-asyncio).
3. Copy `.env.example` → `.env` (gitignored). Fill only the providers you have. Leave others empty.
4. Export or `set -a; source .env; set +a` (or equivalent) so names appear in `os.environ`.
5. Run `pytest` (**TST-01**: default suite does not need live keys).
6. Optional: point `REDIS_URL` at a local Redis. Unset is valid.

No FastAPI app, no Docker-for-production, no Celery worker, no PostgreSQL in this setup (**HLD-09**).

---

## 5. Consumer integration note

When the engine is later imported into FastAPI/Celery, the **consumer** sets the same env names on the worker. This module does not read Kubernetes secrets APIs, AWS SM, or `.env` files inside `build_social_sentiment`. Snapshot persistence remains the consumer’s job (doc 13).

---

## Decision index

| ID | Decision |
|----|----------|
| **CFG-01** | Frozen env name set; credentials never arguments to the public API. |
| **CFG-02** | Timeouts, TTLs, caps, horizon weights are not env-tunable. |
| **CFG-03** | Each adapter requires its full name set; partial → `not_configured`. |
| **CFG-04** | `.env.example` empty values; `.env` gitignored; no dotenv auto-load. |
| **CFG-05** | Python ≥ 3.12. |
| **CFG-06** | Package `zynost-social-intelligence` / `zynost_social`; no LLM deps in `pyproject.toml`. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Redaction, hashing, timeout/cap constants | doc 16 |
| Redis key/TTL/timeout numbers | doc 12 |
| pytest matrix | doc 17 |
| Ruff/mypy/coverage commands | doc 19 |
| Writing the real `.env.example` / `pyproject.toml` on disk | implementation (this doc is the spec for those files) |
| Production secret stores, K8s, FastAPI settings | consumer (`CON-02`, **HLD-09**) |
