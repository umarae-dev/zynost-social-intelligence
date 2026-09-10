# 21 — Final Handoff

> *Traces to specification §22 (handoff deliverables).*
> This is the **delivery procedure** after `20-definition-of-done.md` (**DOD-00**). It owns what leaves the engineering machine: repository state, `HANDOFF.md`, example output, and the hard stop (no merge, no deploy, no production credentials). It does **not** restate the fifteen DoD tests, rewrite architecture (docs 03–16), or create FastAPI/Celery/PostgreSQL artifacts (**HLD-09**, `CON-02`).

---

## Role

Handoff happens **once the implemented module satisfies all fifteen DoD rows**. This document is the packing list and the `HANDOFF.md` outline. The auditor reads the private repo + `HANDOFF.md`; they do not need access to the main Zynost backend.

Vision (`00` / `01`): the deliverable is a **context** engine, not a signal service.

---

## 1. When handoff is allowed *(DOD-00, CON-02)*

**HAN-01 — DoD first.** Do not write `HANDOFF.md` as if the engine were complete while **DOD-01**–**DOD-15** are open. A docs-only tree (this `docs/` folder) is **not** a handoff of the module.

**HAN-02 — Standalone repo only.** Push (or leave) work on the **private** `zynost-social-intelligence` repository. MUST NOT merge into another product repo, MUST NOT deploy, MUST NOT request production API keys or production `REDIS_URL` (`CON-02`, **DOD-15**, spec §22). Independent audit happens **before** any later consumer integration.

---

## 2. Working tree and required files *(HLD-08)*

**HAN-03 — Clean tree, complete artifacts.** At handoff:

| Artifact | Rule |
|----------|------|
| Git | Clean working tree (no stray secrets, no untracked live `.env`) |
| `README.md` | Formulas/limitations (**DOD-14**); how to install/run tests (**CFG** setup) |
| `.env.example` | Empty values, frozen names (**CFG-04**) |
| `pyproject.toml` | 3.12+, no LLM deps (**CFG-05**, **CFG-06**, **TST-LLM**) |
| `zynost_social/` | Layout **HLD-08**; real adapters (**DOD-02**, **DOD-03**) |
| `tests/` | Matrix doc 17; default suite green (**DOD-01**) |
| `examples/example_result.json` | JSON-safe contract example; **no secrets**; identity trio **INT-03**; MUST NOT use `BUY`/`SELL`/`LONG`/`SHORT` |
| `docs/` | Numbered design docs consistent with the code |
| `HANDOFF.md` | §3 below |

`.env` is gitignored and **absent** from the handoff commit.

---

## 3. `HANDOFF.md` *(spec §22)*

**HAN-04 — One file at repo root.** Written at implementation time. Sections below are **required**. Keep it factual; no marketing.

### Implemented providers

List **MOD-05**: X, Reddit, Telegram (public channels), Discord (authorized API only). For each: configured vs `not_configured` behavior (**PRV-05**), not a purchase demand (`CON-06`).

### Provider API requirements

Point at **CFG-03** names. State that CI does not need live keys (**TST-01**). Discord/Telegram surface limits (`FR-SRC-01`).

### Architecture summary

Short: providers → match → score → manipulate → aggregate → provenance/quality → `build_social_sentiment` dict; Redis via `cache.py`; snapshot projection is not persistence (**SNP-03**). Cite **HLD-01** / **LLD-04**; do not paste the HLD essay.

### Test count / result

Command, number of tests collected, pass/fail, and that **TST-LLM** and **TST-SEC** passed. Date of the run. No need to paste full logs.

### Limitations *(honest)*

Must include at least:

- Unconfigured / paid adapters → `unavailable` + reduced coverage; no invented mentions.
- Redis optional; miss is not a provider failure (**CCH-13**, **CCH-20**).
- Partial coverage shrinks `confidence` (**AGG-13**).
- Quiet ≠ all-down (**INT-05**).
- Positive / high heat ≠ bullish price (`FR-SENT-06`).
- Horizons must not be mixed in one snapshot series (**SNP-07**).
- Module does not store PostgreSQL history; consumer persists (**HLD-09**).
- No LLM; lexicons/rules only.

### Exact public integration function

```text
async def build_social_sentiment(asset: AssetIdentity, horizon: str = "swing") -> dict
```

Package: `zynost_social`. Identity: `name = social_sentiment`, `role = context`, `source_class = multi_source_social_intelligence` (`FR-INT-01`–`03`). Point at `examples/example_result.json` and doc 14 for the key list. Snapshot helper, if exported, is **sync** and **not** a second async entry (**SNP-02**, **INT-01**).

### Intentionally deferred

**HAN-05 — Closed deferred set.** Only consumer/out-of-scope items, not skipped DoD:

- FastAPI routes, Celery scheduling, PostgreSQL migrations
- Merge into the main Zynost backend
- Live paid API subscriptions
- Price/OHLCV correlation, backtests, trade labels (**SNP-12**)
- Extra providers beyond **MOD-05** (architecture allows a later file; not required for this handoff)

Do not list “mocked X adapter” as deferred — that would fail **DOD-03**.

---

## 4. Example output

**HAN-06 — `examples/example_result.json`.** One representative **available** asset-specific result that `json.loads` accepts (**INT-06**). May be produced from a test fixture, not from production credentials. Include `data_quality` and `sources` with safe `error_class` only if illustrating partial coverage. MUST NOT contain tokens, handles, raw URLs, or stack traces (**SEC-12**, **INT-10**).

An all-unavailable example MAY live beside it or in tests; if present, it must follow **INT-05**.

---

## 5. After push

The auditor uses this repo in isolation. Integrators later call `build_social_sentiment` from their FastAPI/Celery workers and persist snapshots themselves. Nothing in handoff authorizes production changes.

---

## Decision index

| ID | Decision |
|----|----------|
| **HAN-01** | Handoff only after **DOD-00**. |
| **HAN-02** | Private standalone repo; no merge, deploy, or production credentials. |
| **HAN-03** | Clean tree plus README, `.env.example`, tests, example JSON, docs, package. |
| **HAN-04** | `HANDOFF.md` required sections (providers, APIs, architecture, tests, limitations, public function, deferred). |
| **HAN-05** | Deferred = consumer/out-of-scope only, not incomplete DoD. |
| **HAN-06** | Example JSON is contract-shaped, fixture-safe, secret-free. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Clean commit / private standalone push | operator (**HAN-02**, **HAN-03**) |
| DoD evidence rows | doc 20 |
| Consumer integration | **HLD-09** |
