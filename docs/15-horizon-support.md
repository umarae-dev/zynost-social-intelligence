# 15 — Horizon Support

> *Traces to specification §13 (intraday / swing / position weighting).*
> This is the **horizon-behavior design** beneath `03-high-level-design.md` (**HLD-04**) and `04-low-level-design.md` (`engine.py` lookback, `scoring.py` `horizon` argument). It owns the closed horizon set, provider `since_minutes` lookbacks, mention recency multipliers, and the acceleration-window blend. It does **not** own polar lexicons or classification gates (doc 08, **SCR-**), window-count / velocity / heat / pairwise-agreement *formulas* (doc 09, **MOM-** — this doc **blends** already-computed grains), manipulation ratios (doc 10, **MAN-01** is horizon-free), aggregation mix or `scope` (doc 11, **AGG-03** — no second unofficial mix), Redis keys/TTLs (doc 12, **CCH-**), snapshot series partitioning (doc 13, **SNP-07**), or the public contract shape (doc 14, **INT-02** consumes the applied id).

---

## Role relative to other docs

`00` / `01`: horizon changes **which window of crowd context** is emphasized. It is not a trading timeframe and MUST NOT emit `BUY`/`SELL`/`LONG`/`SHORT` (`FR-INT-04`, `FR-SENT-06`).

| Consumed | Owner |
|----------|-------|
| Applied ids after unknown-horizon handling | doc 14 **INT-02**, `FR-HOR-03` |
| `score(mentions, horizon, baselines)` | **LLD** scoring; polar **SCR-08**/**SCR-09**; activity **MOM-03**–**MOM-08** |
| 15-minute bins inside the current 1h; \(n_{1h}/n_{6h}/n_{24h}\); \(\Delta_{6h}\)/\(\Delta_{24h}\); \(\alpha\) | doc 09 |
| \(\tau_{\mathrm{acc}} = 0.25\) | **MOM-08** — this doc applies it to the **blended** \(\alpha\), and does not change the threshold |
| `assess()` ignores horizon | **MAN-01** |
| Aggregate does not invent a second 15m/1h/6h/24h mix | **AGG-03** |
| Raw cache envelope `since_minutes`; aggregate keyed by horizon; baselines shared | **CCH-07**, **CCH-05**, **CCH-08** |
| Snapshots have no `horizon` field; consumer partitions series | **SNP-07** |

**HOR-01 — Horizon is behavioral (`FR-HOR-02`, `HLD-04`).** Two runs at the same \(t_0\) and the same mentions MUST be allowed to differ in lookback, recency-weighted tone, and blended acceleration. Sticking `horizon` on an otherwise identical board is non-compliant.

---

## 1. Closed set and who applies it *(FR-HOR-01, FR-HOR-03, LLD-04)*

**HOR-02 — Applied set.** Exactly `intraday` \| `swing` \| `position`. Default argument is `swing` (`FR-INT-01`).

Engine applies **INT-02** *before* lookback or `score()`: unrecognized / empty → applied `swing`, report both strings in `metrics`. Scoring receives only an applied id. Adapters never choose a window (**PRV-07**, **LLD-01**): they receive `since_minutes`.

```
engine.build_social_sentiment(asset, horizon)
  applied = INT-02(horizon)                          # swing if unknown
  since_minutes = HOR-03[applied]
  gather fetch_mentions(..., since_minutes)
  score(..., horizon=applied, baselines)             # HOR-04 + HOR-05 inside score()
  assess(...)                                        # no horizon (MAN-01)
  aggregate(..., horizon=applied)                    # pass-through identity (AGG-03)
```

`ScoreBundle.horizon` is the **applied** id (doc 05). Manipulation, matching, quality scales, and `data_quality` formulas do not take a horizon table.

---

## 2. Intent *(spec §13)*

| Applied | What the reading is for | What must dominate |
|---------|-------------------------|-------------------|
| `intraday` | Near-term social *change* | 15m / 1h / 6h acceleration and recency |
| `swing` | Day-scale regime (default) | 6h / 24h behavior |
| `position` | Multi-day sustained discussion | Longer lookback + 24h grain; **downweight** a last-15m spike so it cannot pose as a durable regime. Organic quality stays **MAN-13** (not folded into tone — **SCR-06**) |

All three still compute the **same MOM window counts** from whatever mentions actually arrived (doc 09). This document only (a) how far the engine *asks* providers to look and (b) how already-computed grains are *weighted*.

---

## 3. Provider lookback — `since_minutes` *(LLD-01, CCH-07)*

**HOR-03 — Exact lookbacks.** Minutes, integers, no silent padding.

| Applied | `since_minutes` | Hours | Why |
|---------|-----------------|-------|-----|
| `intraday` | `360` | 6 h | Covers the 15m / 1h / 6h grains spec names. \(n_{24h}\) MAY be partial; the scorer MUST NOT invent the missing hours (**MOM-03**, `CON-04`) |
| `swing` | `1440` | 24 h | Covers 6h and 24h slices plus the 1h burst math **MOM-05** still runs |
| `position` | `4320` | 72 h | \(3 \times\) the swing window: enough to see a **sustained** regime vs a single-day burst. Not 7 d — that duration is a **consumer outcome** window (`FR-SNAP-03`), not this module’s fetch |

One `since_minutes` is passed to **every** provider on the run. Adapters MUST NOT widen or shrink it.

**Cache interaction (consumed, not redefined).** Raw keys are **not** keyed by horizon (**CCH-07**): a wider cached fetch may serve a narrower request by filtering `created_at`; a narrower cache is a miss. Aggregates **are** keyed by applied horizon (**CCH-05**) because **HOR-01** makes those payloads different. Baselines stay horizon-free observations (**CCH-08**); only the blend differs.

---

## 4. Mention recency *(FR-HOR-02, SCR-08 / SCR-09 slot)*

Doc 08 left a multiplier of **1.0** until this table existed. Classification still uses the (now recency-weighted) equal-weight score, not engagement (**SCR-09**). Confidence still uses unweighted \(n_{\mathrm{eff}}\) (**SCR-13**): recency MUST NOT invent extra evidence.

Let \(a_i = \max(0,\ t_0 - created\_at_i)\) in seconds (**MOM-03** clock). Mentions with `created_at > t_0` (skew) sit in the newest bucket.

**HOR-04 — Recency multiplier \(r_i\).** Half-open age buckets. Each row sums to a *profile*, not to 1.0 — these are per-mention multipliers.

| Age of mention | `intraday` | `swing` | `position` |
|----------------|------------|---------|------------|
| \(a_i \le 15\mathrm{min}\) | **1.00** | 0.70 | 0.40 |
| \(15\mathrm{min} < a_i \le 1\mathrm{h}\) | **0.85** | 0.80 | 0.50 |
| \(1\mathrm{h} < a_i \le 6\mathrm{h}\) | **0.50** | **1.00** | 0.75 |
| \(6\mathrm{h} < a_i \le 24\mathrm{h}\) | 0.15 | **0.90** | **1.00** |
| \(a_i > 24\mathrm{h}\) | 0.05 | 0.20 | **0.85** |

**Why these numbers.** Intraday peaks on the spec’s short grains and barely uses chatter older than 6h (and often never fetched it). Swing peaks on 6h and still treats 24h as first-class; a last-15m burst is real but not the whole day. Position **inverts** the 15m peak (0.40 vs 1.00) so a coordinated last-slice dump cannot dominate a 72h reading — that is the “sustained regime” requirement without touching **MAN-** formulas. Mentions older than 24h exist only when lookback is `position` (or a wide cache hit); other horizons keep a small residual so a filtered-wide cache does not zero them silently.

**Where \(r_i\) attaches** (do not replace **SCR-08** / **SCR-09** algebra, only the mention weight):

- Equal-weight tone: mention \(i\) contributes \(r_i\) instead of \(1\) in the **SCR-08** sums (still only over \(n_{\mathrm{eff}}\) non-empty texts).
- Engagement-weighted tone: **SCR-09** \(w_i\) becomes \(w_i \cdot r_i\).
- Empty text: still excluded from \(n_{\mathrm{eff}}\); \(r_i\) is irrelevant.
- Phrase intensities (**SCR-12**): **not** recency-weighted (incidence of language in the evidence set, not a regime reading).
- Heat / velocity / unique authors / mention counts: **not** recency-weighted (those are **MOM-** activity, not tone).

If \(n_{\mathrm{eff}} = 0\), **SCR-08** / **SCR-10** still emit `0` / `insufficient_data`. Recency does not manufacture a tone (`CON-04`).

---

## 5. Acceleration blend *(FR-HOR-02, MOM-08)*

Doc 09 always emits \(\alpha\) (1h), \(\Delta_{6h}\), \(\Delta_{24h}\), and four 15m bins. Until now `social_acceleration` **was** \(\alpha\) only. This section is the mix **MOM-08** deferred.

**HOR-05 — 15m grain from existing bins.** Let \(n^{(0..3)}\) be the **MOM-05** bins ending at \(t_0\) (\(B_3\) = newest 15m). Do not invent a fifth window.

\[
\alpha_{15\mathrm{m}} =
\begin{cases}
0 & n_{1h} = 0 \\
\dfrac{n^{(3)} - n^{(2)}}{\max(n^{(2)}, 1)} & \mathrm{otherwise}
\end{cases}
\]

Let \(\alpha_{1h} = \alpha\) (**MOM-08**), \(\alpha_{6h} = \Delta_{6h}\), \(\alpha_{24h} = \Delta_{24h}\). A grain is **known** when its MOM prior (or, for 15m, \(n_{1h} > 0\)) is known; unknown 6h/24h priors already force \(\Delta = 0\) **and** are flagged unknown (**MOM-04**) — those grains are **dropped**, not treated as “no change.”

**HOR-06 — Blend weights (sum to 1.0 before drop).**

| Grain | `intraday` | `swing` | `position` |
|-------|------------|---------|------------|
| \(\alpha_{15\mathrm{m}}\) | **0.40** | 0.05 | 0.00 |
| \(\alpha_{1h}\) | **0.35** | 0.15 | 0.10 |
| \(\alpha_{6h}\) | **0.25** | **0.40** | 0.30 |
| \(\alpha_{24h}\) | 0.00 | **0.40** | **0.60** |

Intraday: 15m+1h+6h = 1.00, matching spec wording; 24h weight is zero even if \(n_{24h}\) exists. Swing: 6h+24h = 0.80, short grains residual only. Position: 24h is the majority; 15m is **exactly 0** so a last-slice spike cannot move `acceleration_state`.

Let \(K\) be the set of grains with non-zero table weight **and** known input. If \(K = \emptyset\): blended \(\alpha^\star = 0\) (same as **MOM-08** unknown). Else renormalize:

\[
\alpha^\star = \sum_{g \in K} \frac{w_g}{\sum_{h \in K} w_h}\, \alpha_g
\]

**Emitted `social_acceleration` is \(\alpha^\star\).** `acceleration_state` uses **MOM-08** \(\tau_{\mathrm{acc}} = 0.25\) on \(\alpha^\star\) (accelerating / holding / fading). Do not introduce a second threshold.

**HOR-07 — What is *not* blended.** `mention_velocity` stays **MOM-05** (\(\rho\kappa\) on the 1h + 15m burst). `social_heat` stays **MOM-07**. `source_agreement` stays **MOM-09**. Aggregation fallback weights stay **AGG-11**; **AGG-03** remains “no second mix.” Horizon does not rewrite those formulas — it already moved acceleration and tone.

---

## 6. Organic discussion on `position`

**HOR-08 — Organic is a separate field.** Spec’s “organic discussion” for `position` is implemented by **HOR-03** (72h lookback) + **HOR-04** (downweight 15m) + **HOR-06** (zero 15m acceleration weight). `organic_score` / `organic_data_ratio` remain **MAN-13** / **QLY-04**. Engine MUST NOT add `organic_score` into `sentiment_score` or flip `classification` toward `positive` because organic is high (**SCR-06**, `FR-MAN-04`).

`assess()` is unchanged and still has no `horizon` argument (**MAN-01**).

---

## 7. Determinism, fail-soft, contract

Same mentions + same applied horizon + same `BaselineBundle` → same \(r_i\) and \(\alpha^\star\) (`NFR-DET-01`, **HLD-03**). No wall-clock inside the tables (**MOM-03**).

Redis miss → unknown priors → those acceleration grains drop (**HOR-06**), recency still uses \(t_0\) and `created_at`. Missing baseline is not disagreement (**SCR-13**, **QLY-06**).

All-unavailable: no scores (**INT-05**, **HLD-07**); `metrics` still reports `horizon_requested` / `horizon_applied` (**INT-08**). Horizons never invent mentions.

Public dict: `metrics.horizon_applied` is the id that selected **HOR-03**–**HOR-06**. Snapshot still has **no** horizon field (**SNP-07**); consumers MUST NOT interleave horizons in one series.

No LLM (`CON-01`). No trade language (`FR-INT-04`).

---

## 8. Interface sketch *(not a package)*

```python
APPLIED = ("intraday", "swing", "position")

LOOKBACK_MINUTES = {"intraday": 360, "swing": 1440, "position": 4320}

# age_seconds = max(0, t_0 - created_at); newest bucket if created_at > t_0
def recency_multiplier(age_seconds: float, horizon: str) -> float: ...

# known_* false → grain omitted, weights renormalized (HOR-06)
def blend_acceleration(
    alpha_15m: float, known_15m: bool,
    alpha_1h: float,  known_1h: bool,
    alpha_6h: float,  known_6h: bool,
    alpha_24h: float, known_24h: bool,
    horizon: str,
) -> float: ...
```

`engine.py` maps **INT-02** → `LOOKBACK_MINUTES`. `score()` calls the two helpers after **MOM-** / **SCR-** primitives exist. `aggregation.py` does not call them.

---

## Worked distinction *(same evidence, different applied horizon)*

Illustrative only — not a test fixture of invented history. Suppose a quiet 24h then a large last-15m copy-paste burst (high **MAN-** risk on the batch, high **MOM-05** \(\kappa\)).

| Applied | Recency on the burst | \(\alpha^\star\) | Reading |
|---------|----------------------|------------------|---------|
| `intraday` | \(r_i = 1.00\) | 15m+1h-heavy | Burst **dominates** tone and acceleration — correct for near-term change |
| `swing` | \(r_i = 0.70\) | 6h+24h-heavy | Burst visible; day-scale \(\Delta\) still matters |
| `position` | \(r_i = 0.40\) | 15m weight **0** | Burst cannot set `acceleration_state` by itself; 72h tone stays available |

`mention_velocity` can be high on all three (same **MOM-05**); that is activity, not a bullish label (`FR-MOM-07`).

---

## Decision index

| ID | Decision |
|----|----------|
| **HOR-01** | Horizon changes lookback, recency-weighted tone, and blended acceleration — not a label (**HLD-04**, `FR-HOR-02`). |
| **HOR-02** | Applied set `intraday` \| `swing` \| `position`; engine resolves unknowns via **INT-02** before this table. |
| **HOR-03** | `since_minutes`: 360 / 1440 / 4320. One value per run, all providers. |
| **HOR-04** | Recency \(r_i\) by age bucket (table §4); scales **SCR-08** counts and **SCR-09** \(w_i\) only. |
| **HOR-05** | \(\alpha_{15\mathrm{m}}\) from **MOM-05** bins \(B_3\) vs \(B_2\); no new fetch window. |
| **HOR-06** | Acceleration blend weights §5; unknown grains dropped and renormalized; `acceleration_state` uses **MOM-08** \(\tau_{\mathrm{acc}}=0.25\) on \(\alpha^\star\). |
| **HOR-07** | Velocity, heat, agreement, aggregation fallback, manipulation: not re-mixed here. |
| **HOR-08** | `position` emphasizes sustained discussion via lookback + recency + zero 15m acceleration weight; `organic_score` stays **MAN-13**. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| Polar lexicons, \(N_{\min}\), confidence formula | doc 08 (**SCR-**) |
| Window counts, \(\rho\kappa\), heat weights, **MOM-09** | doc 09 |
| Manipulation signals | doc 10 (**MAN-01**) |
| Source combining / `scope` | doc 11 (**AGG-03**) |
| Cache keys / envelope / TTLs | doc 12 |
| Snapshot field list / series partitioning | docs 05, 13 |
| Contract JSON / `metrics.horizon_*` | doc 14 |
| Security / env catalog | docs 16, 18 |
