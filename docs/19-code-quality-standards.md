# 19 — Code Quality Standards

> *Traces to specification §20 (engineering standards).*
> This is the **implementation bar** beneath `03-high-level-design.md` (**HLD-03**, **HLD-08**) and `04-low-level-design.md` (**LLD-03**–**LLD-05**). It owns language, typing, module size, comments, tooling, and how exceptions may cross boundaries. It does **not** own formulas (docs 08–11, 15), the test matrix (doc 17), env names (doc 18), security constants (doc 16), or the Definition of Done roll-up (doc 20).

---

## Role relative to other docs

Spec §20 and `NFR-ASYNC-01` / `NFR-DET-01` / `NFR-JSON-01` / `NFR-STATE-01` are the requirements. **LLD-03** already forbids giant files, duplicated adapter logic, hidden globals, LLMs, and invented data. This document turns those into checks an implementer (and later CI) can apply without restating HLD/LLD.

| Consumed | Owner |
|----------|-------|
| One public async entry; stages isolated | **LLD-04**, `FR-INT-01` |
| Helpers once in `providers/base.py` | **LLD-05**, **PRV-06** |
| Python ≥ 3.12, package names | **CFG-05**, **CFG-06** |
| Isolation catch-all; no secret logs | **PRV-03**, **SEC-12** |
| JSON-safe contract | **INT-06** |
| pytest suite | doc 17 |

---

## 1. Language and typing *(spec §20)*

**ENG-01 — Python 3.12+.** Same floor as **CFG-05**. Use 3.12 syntax where it clarifies (e.g. `type` aliases, `X | None`). No 3.11 shims.

**ENG-02 — Type hints on public and module interfaces.** Every function listed in LLD (`build_social_sentiment`, `fetch_mentions`, `match_mentions`, `score`, `assess`, `aggregate`, cache get/set, `build_provenance`, `compute_data_quality`, `collect_isolated`) SHALL have complete parameter and return annotations. Dataclass-style models (**MOD-01**) are typed field-for-field. `Any` is allowed only at the JSON dict boundary after sanitization, or for a typed `Mapping[str, …]` of JSON-safe values.

CI SHOULD run a static checker (mypy or equivalent in strict-ish mode on `zynost_social/`). Exact `pyproject` tool tables are implementation, but a clean typecheck is part of the bar.

**ENG-03 — Async-first I/O, sync-pure core.** `async def` is required for provider fetch, `collect_isolated`, `cache.py`, and `build_social_sentiment` (`NFR-ASYNC-01`). `matching.py`, `scoring.py`, `manipulation.py`, `aggregation.py`, `provenance.py`, `quality.py`, and snapshot projection are **synchronous** and CPU-bound: they MUST NOT `await`, open sockets, or read Redis (**LLD-04**, **CCH-02**, **HLD-03**). Do not wrap pure functions in dummy async.

No `time.sleep` on the event loop; provider backoff uses `asyncio.sleep` inside the **SEC-08** budget.

---

## 2. Modular architecture *(HLD-08, LLD-03)*

**ENG-04 — One concern per LLD file.** The catalog in doc 04 is the file list. Do not merge scoring into `engine.py`. Do not put HTTP in `scoring.py`. Adding a fifth provider is a new `providers/<name>.py` plus registration (`FR-SRC-07`).

**ENG-05 — No giant single file.** A module that exceeds **~600 lines** of code (excluding comments) is a signal to split — not a hard compiler limit, a review fail if the extra is a second concern. Adapter files stay I/O+mapping only (**PRV-07**).

**ENG-06 — No duplicated provider logic.** Timeout, size cap, sanitize, hash, redact, retry, isolation live in `base.py` (**LLD-05**). Copy-paste of those blocks into `x.py` / `reddit.py` / `telegram.py` / `discord.py` is non-compliant.

**ENG-07 — No hidden global mutable state.** Forbidden: module-level mention dicts, “last score”, global Redis client mutated at import, mutable lexicon patched at runtime, process-memory as cache of record (**MOD-09**, **CCH-01**, `NFR-STATE-01`). Redis client and run flags live on an explicit context passed by `engine.py`. Lexicons are frozen in-repo constants (**SCR-01**).

---

## 3. Determinism and JSON *(NFR-DET-01, NFR-JSON-01)*

**ENG-08 — Predictable calculations.** Same mentions + same applied horizon + same `BaselineBundle` → same `ScoreBundle` / `ManipulationAssessment` / `AggregateResult` (**HLD-03**, **TST-02**). Forbidden in those modules: `random` without a fixture-only seeded path (production path: none), `datetime.now()` / `time.time()` (**MOM-03** uses \(t_0\)), unordered `set` iteration where order affects output (sort or use stable structures), hidden clocks.

**ENG-09 — JSON-safe public result.** `engine.py` applies **INT-06** before return. Internal dataclasses MAY use richer types; they MUST NOT leak through `build_social_sentiment`. No `pickle` in cache (**CCH-10**).

---

## 4. Exceptions *(NFR-DEGRADE-01, FR-INT-05)*

**ENG-10 — Production-safe handling.** Provider and Redis exceptions are converted at their isolation boundary (`collect_isolated`, `cache.py`) and **do not** propagate out of `build_social_sentiment`. All-unavailable returns a dict (`FR-INT-05`). Programming errors (assertion on invariant in tests) MAY fail tests; they MUST NOT dump stack traces into `sources` (**INT-10**).

Do not use bare `except:`. Do not `except Exception` in `scoring.py` to invent a zero score — let unit tests catch bugs; fail-soft is for **external** I/O, not for hiding formula errors (`CON-04`).

---

## 5. Comments and names

**ENG-11 — Useful comments only.** Comment *why* (spec ID, non-obvious invariant). Do not narrate `i += 1`. Do not paste formula essays into code when docs 08–11 exist — a one-line cite (`# MOM-05`) is enough. No commented-out dead code in the merge.

Public names match docs (`sentiment_score`, `error_class`, `insufficient_data`). No `bullish_signal`, no `BUY` in identifiers (`FR-INT-04`).

---

## 6. Tooling *(CI bar)*

**ENG-12 — Format and lint.** Ruff (or equivalent) format + lint on `zynost_social/` and `tests/`. Line length 88–100 (pick one in `pyproject.toml` and keep it). Import order is a linter concern, not a style debate in review.

**ENG-13 — Tests are the gate.** `pytest` as in doc 17, including **TST-LLM** and **TST-SEC**. Coverage percentage is not a spec number; **every TST-* row** must exist as an automated test. Typecheck + lint + pytest form the default CI.

Dev extra in `pyproject.toml` (name is **CFG** implementation): pytest, pytest-asyncio, ruff, mypy — still **no** LLM packages.

---

## 7. What this bar is not

- Not a license to add FastAPI/Celery to this repo (**HLD-09**).
- Not a license to tune **CFG-02** constants via flags.
- Not README marketing. Root `README` (doc 20/21) must explain formulas/limitations; this file does not replace it.

---

## Decision index

| ID | Decision |
|----|----------|
| **ENG-01** | Python 3.12+ only. |
| **ENG-02** | Typed LLD interfaces and models; static check on the package. |
| **ENG-03** | Async I/O and engine; sync pure scoring/manipulation/aggregation/quality. |
| **ENG-04** | File list follows LLD; no cross-concern dumps. |
| **ENG-05** | ~600 LOC/module as a split signal; no giant `engine.py`. |
| **ENG-06** | Shared adapter helpers only in `base.py`. |
| **ENG-07** | No module-global mutable caches or clients. |
| **ENG-08** | Deterministic core; no wall-clock or hidden RNG in formulas. |
| **ENG-09** | JSON-safe dict at the public boundary. |
| **ENG-10** | I/O exceptions isolated; formula bugs are not silently zeroed. |
| **ENG-11** | Why-comments and spec cites; no trade-language names. |
| **ENG-12** | Ruff (or equivalent) format + lint. |
| **ENG-13** | pytest matrix + LLM/secret scans are required CI. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Exact ruff/mypy `pyproject` keys and version pins | implementation under **CFG-06** |
| Test case list | doc 17 |
| DoD checklist | doc 20 |
| Root README formula write-up | doc 21 / implementation README |
