# 20 — Definition of Done

> *Traces to specification §21 (completion criteria).*
> This is the **objective gate** for the implemented `zynost-social-intelligence` module. It owns the fifteen spec criteria, what evidence satisfies each, and what is explicitly *not* required. It does **not** own the test list (doc 17), handoff artifacts (doc 21), or consumer FastAPI/PostgreSQL/Celery wiring (**HLD-09**, `CON-02`). Completing `docs/00`–`19` is design work; it does **not** by itself mark the engine done.

---

## How to use this checklist

Every row is **pass/fail**. “Done” means **all fifteen** are true on the private repo, with a clean test run (**ENG-13**). Partial implementation (e.g. three providers mocked in production code) is **not** done (`CON-05`).

Evidence is automated tests (**TST-***), scans (**TST-LLM**, **TST-SEC**), and inspectable files (`README`, `.env.example`, package layout). No production access is needed or allowed (`CON-02`).

---

## The fifteen criteria *(spec §21)*

| # | Criterion | Passes when | Evidence | Cites |
|---|---------|-------------|----------|-------|
| **DOD-01** | All mandatory tests pass | Default pytest gate is green, including collection, matching, scoring, manipulation, cache, JSON, horizons, snapshots, **TST-LLM**, **TST-SEC** | CI / local `pytest` | doc 17, **ENG-13** |
| **DOD-02** | Real provider architecture | Four adapters (`x`, `reddit`, `telegram`, `discord`) implement `SocialProvider.fetch_mentions`; shared isolation in `base.py`; adding a fifth does not rewrite scoring | Package layout **HLD-08**; code review vs **PRV-01** | **LLD-01**, `FR-SRC-07` |
| **DOD-03** | No fake/mock production impl | Production `providers/*.py` call real APIs when configured; fakes exist **only** under `tests/` | **TST-01**, no stub returns invented mentions in package code | `CON-05`, `CON-04` |
| **DOD-04** | No LLM | No LLM SDK in code or install graph; scoring/matching/manipulation are deterministic | **TST-LLM** fail-closed | `CON-01`, **SCR-01** |
| **DOD-05** | Single-provider failure tolerated | One adapter `unavailable` → others still score; engine does not raise | **TST-P-02**, **TST-P-09**, **TST-P-10** | **HLD-07**, `FR-SRC-03` |
| **DOD-06** | All-failure returns unavailable cleanly | All down → return dict, `status = unavailable`, **null** scores (not quiet zeros), no raise | **TST-P-08**, **TST-I-03** | `FR-INT-05`, **INT-05** |
| **DOD-07** | Identity is not ticker-only | Bare/collision symbols rejected; aliases/contract/official accounts work; `market_wide` not scored as the asset | **TST-M-01**–**TST-M-07** | **MAT-02**, **MAT-03**, `FR-ID-02` |
| **DOD-08** | Manipulation detection works | Duplicates, floods, campaigns, bursts raise risk; 80 organic > 500 shill on `organic_score`; spike ≠ bullish class | **TST-S-07**–**TST-S-12** | **MAN-13**, `FR-MAN-03`–`04` |
| **DOD-09** | Redis caching works | Hit/miss/expiry/down; miss ≠ provider row; `observed_at` is collection time; no process-memory truth | **TST-C-01**–**TST-C-07** | **CCH-01**, **CCH-12**, **CCH-17** |
| **DOD-10** | Snapshot support | Projection from computed state; \(A = 0\) → `None`; quiet → honest row; no backfill; module does not persist to PostgreSQL | **TST-I-08**, **TST-I-09** | **SNP-03**, **SNP-04**, `FR-SNAP-02` |
| **DOD-11** | Horizons behave differently | `intraday` / `swing` / `position` change lookback and/or weights, not only a label; unknown → `swing` | **TST-I-04**, **TST-I-05** | **HOR-01**, `FR-HOR-02`–`03` |
| **DOD-12** | Exact integration contract | `build_social_sentiment(asset, horizon="swing") -> dict`; required keys; frozen `name`/`role`/`source_class`; JSON-safe; `data_quality` four fields; no `BUY`/`SELL`/`LONG`/`SHORT` | **TST-I-01**, **TST-I-02**, **TST-S-14** | `FR-INT-01`–`04`, **INT-03**, **QLY-01** |
| **DOD-13** | No secrets in Git/history | Only `.env.example` with empty values; no live tokens in README/examples/tests; history clean of accidental keys | **TST-SEC**; `.gitignore` has `.env` | `CON-03`, **CFG-04**, **SEC-04** |
| **DOD-14** | README explains formulas and limitations | Root `README.md` states what is measured, cites or summarizes **SCR-**/**MOM-**/**MAN-**/**AGG-**/**HOR-** behavior, and lists limitations (partial coverage, no price claim, unconfigured adapters, Redis optional) | Inspect `README.md` | spec §21, doc 21 |
| **DOD-15** | No deployment / production changes | Repo is standalone; no merge into the main Zynost backend; no prod deploys, migrations, or production credential requests | Process + **HLD-09** | `CON-02` |

**DOD-00 — All fifteen required.** Skipping **DOD-14** (thin README) or **DOD-03** (mocked adapters “until keys exist”) is not a complete engine. Unconfigured adapters in production code are real + gated (**PRV-05**), which **does** satisfy **DOD-03**.

---

## Also required (implied by the fifteen)

These are not extra scope; they are how the rows above hold:

- Engineering bar **ENG-01**–**ENG-13** (3.12, types, isolation, no giant file).
- Credentials from env only (**CFG-01**, **SEC-02**); paid APIs never demanded (`CON-06`).
- Goals-level measures exist on the contract: sentiment, activity, momentum, velocity, engagement, organic vs manipulated, agreement, anomalies (`01`).
- Positive sentiment is not a bullish price claim (`FR-SENT-06`).

---

## Explicitly not required for “done”

| Out of scope | Why |
|--------------|-----|
| FastAPI routes, Celery beat, PostgreSQL schema in this repo | **HLD-09**, **SNP-03**, `CON-02` |
| Live paid provider calls in CI | **TST-01**, `CON-06` |
| Merging into another repository | spec §22 / doc 21 |
| Price data, backtests, trade signals | **SNP-12**, `01` non-goals |
| Deploying or requesting production credentials | **DOD-15** |

Docs `00`–`19` remaining consistent with the code is expected at handoff (doc 21); they are not a sixteenth spec-§21 bullet.

---

## Decision index

| ID | Decision |
|----|----------|
| **DOD-00** | Engine done ⇔ all fifteen rows pass. |
| **DOD-01**–**DOD-15** | One row per spec §21 criterion, with test/file evidence and owning design IDs. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Clean commit / private standalone push | doc 21 (**HAN-02**, **HAN-03**) |
| Test implementation details | doc 17 |
| README prose itself | written at implementation; quality judged by **DOD-14** |
