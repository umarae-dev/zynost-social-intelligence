# 09 — Social Activity & Momentum

> *Traces to specification §6 (social activity / momentum).*
> This is the **activity and momentum design** beneath `03-high-level-design.md` (**HLD-01** scoring stage, **HLD-03**, **HLD-04**, **HLD-07**) and `04-low-level-design.md` (`scoring.py`). It owns volume, unique authors, windowed rates, burst-aware velocity, heat, acceleration, and source agreement/disagreement. It does **not** redefine `SocialMention` / `ScoreBundle` / `BaselineBundle` / `MatchBatch` field lists (doc 05, **MOD-03**, **MOD-06**), polar formulas, classification labels, or phrase intensities (doc 08, **SCR-***), matching/collision rules (doc 07, **MAT-***), provider I/O (doc 06, **PRV-***), manipulation / pump math (doc 10), aggregation/scope policy in full (doc 11), cache-key lists (doc 12), snapshot persistence (doc 13), the integration-contract JSON (doc 14), or horizon weight tables (doc 15).

---

## Role relative to other docs

Doc 04 freezes the module boundary: `score(mentions, horizon, baselines) -> ScoreBundle`. Doc 08 owns polar tone on that same bundle. This document owns the **non-polar** numbers on the same bundle (`FR-MOM-01`–`07`). Crowd activity is **context**, not a price call (`00-project-vision.md`, `01-product-goals.md`, `FR-MOM-07`, `FR-SENT-06`, `FR-INT-04`).

Horizon 15m/1h/6h/24h **weight tables** → doc 15. Combining `SourceBundle`s, setting `scope`, and `insufficient_data` policy in full → doc 11. This doc still defines the **agreement metric** aggregation will apply.

---

## 1. Module boundary *(LLD scoring module, LLD-04)*

```
def score(
    mentions: Sequence[SocialMention],
    horizon: str,
    baselines: BaselineBundle,
) -> ScoreBundle
```

| | |
|---|---|
| **Who calls it** | `engine.py` only — typically once per **available** source on that source’s `MatchBatch.asset_mentions` subset (`FR-AGG-02`), and optionally on the concatenated asset-matched set for asset-level activity. Not a public API (`FR-INT-01`). |
| **Pure function** | Same mentions + same `horizon` + same `BaselineBundle` → same `ScoreBundle` (`NFR-DET-01`, **HLD-03**). No network, no Redis, no wall-clock, no hidden mutable counters (`NFR-STATE-01`). |
| **Must NOT** | Use an LLM (`CON-01`); fetch; match; compute `duplicate_content_ratio` / `organic_score` / `suspicious_burst_score` or other manipulation signals (doc 10); set `scope` (doc 11); invent baselines (`CON-04`, **HLD-07**); fold heat/velocity into polarity (**SCR-06**); emit `BUY`/`SELL`/`LONG`/`SHORT` (`FR-INT-04`). |

**MOM-01 — Same `score()` as doc 08; different formulas.** Activity fields are computed in `scoring.py` beside polar fields. They MUST NOT be computed in adapters (**PRV-07**) or in `matching.py` (**MAT-07**). Scoring must not reach back into adapters (**LLD-04**, **HLD-01**).

**MOM-02 — Heat is not tone.** `social_heat`, `mention_velocity`, and acceleration **must not** alter `sentiment_score` or `classification` (`FR-MOM-07`, **SCR-06**, `FR-MAN-04`). A spike may be high activity here and an anomaly in doc 10; it is never automatically `positive` or bullish (`FR-SENT-06`).

---

## 2. Inputs *(cleaned, asset-matched only)*

| Input | Rule |
|-------|------|
| Mentions | **Only** `MatchBatch.asset_mentions` (doc 07, **MAT-05**, **SCR-03**). Never `market_wide`. Never unmatched provider output (`FR-SCOPE-03`, `FR-NORM-04`). |
| Fields used | `created_at`, `author_id_hash`, `engagement`. Polar `text` is **not** an activity input. Match rationale is **not** used (**MAT-07**). |
| `horizon` | `intraday` \| `swing` \| `position` (`FR-HOR-03`). Behavioral **reweighting** of window terms → doc 15. This doc always **computes** 1h / 6h / 24h slices from evidence that is actually present. |
| `baselines` | Engine-supplied `BaselineBundle` from `cache.py` (doc 05). Redis miss / Redis down → empty bundle with priors **unknown**, never invented history (`CON-04`, `FR-CACHE-03`, **HLD-07**, **LLD-08**). |

**MOM-03 — Observation anchor `t_0`, not `datetime.now()`.** Window math is relative to `baselines.observed_at` (the run’s observation instant, same clock as the future snapshot). If that slot is empty and the mention set is non-empty, `t_0 = max(created_at)` — still a function of inputs, not wall-clock. If the set is empty, all window counts are 0.

**MOM-04 — Unknown prior ≠ quiet prior.** Each cached window is either **known** (engine stored a count, including `0` = observed quiet) or **unknown** (miss / empty bundle). Unknown MUST NOT be treated as “previous hour had zero mentions”; that would fabricate a spike vs silence (`CON-04`). Doc 05’s empty/zero bundle on miss maps to **all windows unknown**.

Slots this math consumes (shape remains doc 05; names here are the scoring contract):

- `observed_at` (`t_0`)
- `prior_mentions_1h` / `prior_mentions_6h` / `prior_mentions_24h` — each `int` or unknown
- `prior_engagement_1h` — `float` or unknown
- `prior_sentiment_score` + `prior_observed_at` — optional, for time-delta structure only (`FR-MOM-06`); never backfilled

Mentions with `created_at > t_0` (clock skew) are kept in the current 1h last bin. Mentions older than 24h still count in **total** \(n\) but not in \(n_{1h}/n_{6h}/n_{24h}\). If the fetch window was shorter than 24h, \(n_{24h}\) is only what arrived — the scorer does not invent the missing hours (**HLD-04** points at doc 15 for how long the engine asks providers to look back).

---

## 3. Volume and unique authors *(FR-MOM-01)*

Let \(n\) = number of input mentions (asset-matched, including empty `text`).

Let \(U\) = number of distinct `author_id_hash` values (the required hashed author id, doc 05). Identical hashes collapse; distinct hashes count separately. Empty-string hashes, if present, are one identity, not “unknown authors to impute.”

These two numbers are activity, not quality. `unique_author_ratio` and organic/pump interpretation → doc 10.

`score([])`: \(n = 0\), \(U = 0\), all rates/heat/velocity/acceleration numeric fields `0`, acceleration state `holding` (no evidence of change), agreement not computed inside a single empty source call.

---

## 4. Window counts *(FR-MOM-03)*

Half-open intervals on `[t_0 - W, t_0]`:

\[
n_{1h} = \#\{i : t_0 - 1\mathrm{h} < created\_at_i \le t_0\}
\]

\[
n_{6h},\ n_{24h}\ \text{analogous with } 6\mathrm{h},\ 24\mathrm{h}.
\]

**Current vs previous 1h.** `mentions_1h_current` \(= n_{1h}\). `mentions_1h_previous` \(= b_{1h}\) when the 1h prior is **known**, else omitted/zero **and** flagged unknown (do not narrate a change).

**Relative change** when the matching prior is known; **0** when unknown (no claimed change):

\[
\Delta_{6h} =
\begin{cases}
\dfrac{n_{6h} - b_{6h}}{\max(b_{6h}, 1)} & \text{6h prior known} \\
0 & \text{unknown}
\end{cases}
\qquad
\Delta_{24h}\ \text{analogous.}
\]

Denominator floor **1** avoids division by zero when a **known** prior is quiet. That is an observed quiet hour, not a miss.

Emit the raw counts and the relative changes on `ScoreBundle` (additional JSON-safe numerics; public mapping → doc 14).

---

## 5. Mention velocity — burst ≠ even *(FR-MOM-02)*

A total of \(N\) mentions in one hour must **not** produce the same velocity if they all arrived in one 15-minute slice versus four equal slices.

Partition the current 1h into **four** adjacent 15-minute bins \(B_0..B_3\) ending at \(t_0\) (15m is the spec’s short-horizon grain; **weights** across grains stay in doc 15). Let \(n^{(j)}\) be the bin counts; \(\sum_j n^{(j)} = n_{1h}\).

**Rate vs baseline**

\[
\rho =
\begin{cases}
\dfrac{n_{1h}}{\max(b_{1h}, 1)} & \text{1h prior known} \\
1 & \text{unknown (neutral; do not invent a baseline rate)}
\end{cases}
\]

**Intra-hour concentration** (even spread → 1; all mass in one bin → 4):

\[
\kappa =
\begin{cases}
1 & n_{1h} = 0 \\
\dfrac{4 \cdot \max_j n^{(j)}}{n_{1h}} & \text{otherwise}
\end{cases}
\quad \in [1, 4]
\]

**MOM-05 — Burst-aware velocity.**

\[
\mathrm{mention\_velocity} = \rho \times \kappa
\]

Same \(n_{1h}\) and same \(b_{1h}\): even spread \(\kappa = 1\); single-bin burst \(\kappa = 4\); velocity differs by that factor. Unbounded ratio (contract example is a multiple like `2.8`, not a 0–100 score). JSON-safe finite float; no `NaN`/`Inf`.

---

## 6. Engagement velocity *(FR-MOM-03)*

Let \(E_{1h} = \sum_i \mathrm{engagement}_i\) over mentions in the current 1h (`engagement` already \(\ge 0\), doc 06; missing native → `0`, never invented virality).

Let \(E^{(j)}\) be the same sum per 15-minute bin.

\[
\eta =
\begin{cases}
\dfrac{1 + E_{1h}}{1 + E_{1h}^{\mathrm{prior}}} & \text{1h engagement prior known} \\
1 & \text{unknown}
\end{cases}
\]

(+1 in numerator and denominator so a known quiet prior with \(E=0\) is well-defined without treating miss as quiet.)

\[
\kappa_E =
\begin{cases}
1 & E_{1h} = 0 \\
\dfrac{4 \cdot \max_j E^{(j)}}{E_{1h}} & \text{otherwise}
\end{cases}
\quad \in [1, 4]
\]

**MOM-06**

\[
\mathrm{engagement\_velocity} = \eta \times \kappa_E
\]

Polar engagement-weighted **tone** remains **SCR-09**; this is attention **rate**, not sentiment.

---

## 7. Social heat *(FR-MOM-04)*

Scale **0..100**. Mix of **how much**, **how many speakers**, **how fast vs baseline**, and **how much attention** — not polarity.

Saturations (documented caps, not sample-size claims):

| Symbol | Value | Role |
|--------|-------|------|
| \(N_*\) | 100 | Volume saturates at 100 asset-matched mentions in the scored set |
| \(A_*\) | 50 | Unique-author saturates at 50 distinct hashes |
| \(V_*\) | 8 | Velocity term saturates when \(\ln(1+\mathrm{mention\_velocity}) / \ln(1+8) = 1\) |
| \(E_*\) | 500 | Engagement sum saturates at 500 adapter-normalized units in the current 1h |

\[
v = \mathrm{clip}_{[0,1]}\!\left(\frac{\ln(1+n)}{\ln(1+N_*)}\right)
\quad
a = \mathrm{clip}_{[0,1]}\!\left(\frac{U}{A_*}\right)
\]

\[
u = \mathrm{clip}_{[0,1]}\!\left(\frac{\ln(1+\mathrm{mention\_velocity})}{\ln(1+V_*)}\right)
\quad
e = \mathrm{clip}_{[0,1]}\!\left(\frac{\ln(1+E_{1h})}{\ln(1+E_*)}\right)
\]

**MOM-07 — Heat weights (sum to 1).** Volume 0.35, unique authors 0.25, velocity 0.25, engagement 0.15.

\[
\mathrm{social\_heat} = \mathrm{clip}_{[0,100]}\big(100 \times (0.35\,v + 0.25\,a + 0.25\,u + 0.15\,e)\big)
\]

The author term is **breadth of activity**, not `unique_author_ratio` / `organic_score` (doc 10). High heat with `classification = negative` is valid.

---

## 8. Social acceleration *(FR-MOM-04)*

Numeric first derivative of the 1h count vs the known previous 1h; closed label for accelerating / holding / fading.

\[
\alpha =
\begin{cases}
\dfrac{n_{1h} - b_{1h}}{\max(b_{1h}, 1)} & \text{1h prior known} \\
0 & \text{unknown}
\end{cases}
\]

**MOM-08 — \(\tau_{\mathrm{acc}} = 0.25\).** A 25% rise or drop vs the known previous hour is the smallest change labeled as a regime, not noise around holding.

| Condition | `acceleration_state` |
|-----------|----------------------|
| \(\alpha > 0.25\) | `accelerating` |
| \(\alpha < -0.25\) | `fading` |
| otherwise | `holding` |

`social_acceleration` on `ScoreBundle` **is** \(\alpha\) (signed float). Unknown prior → \(\alpha = 0\) and `holding`: the engine does not guess that activity is fading.

Doc 15 MAY reweight a blend of 1h / 6h / 24h derivatives for `intraday` vs `swing` vs `position`. Until that table exists, **do not** apply a second unofficial mix: \(\alpha\) is the 1h formula above (`FR-HOR-02` is satisfied by computing all windows in §4; weighting is doc 15). Optional 6h/24h derivatives \(\Delta_{6h}\), \(\Delta_{24h}\) stay as separate emitted changes.

Closed label set only: `accelerating`, `holding`, `fading`. No `bullish`/`bearish` acceleration names.

---

## 9. Source agreement and disagreement *(FR-MOM-05)*

Computed from **independent available** sources’ polar `sentiment_score` values (doc 08), **not** from heat. Unavailable / unconfigured / timed-out sources are omitted (**HLD-07**, **HLD-10**). A missing source is **not** agreement.

Per-source `score()` cannot see siblings. Engine/aggregation calls this helper on the available rows (doc 11 owns *when* to call it and how `SourceBundle`s combine; this doc owns the metric).

Let \(S_1,\ldots,S_m\) be `sentiment_score` values in \([-100,+100]\) from sources with `status = available` **and** `classification ≠ insufficient_data` (a source with too little evidence is not a vote). \(m=0\) if all down or all insufficient.

**MOM-09 — Pairwise tone agreement; \(m < 2\) is not agreement.**

\[
\mathrm{source\_agreement} =
\begin{cases}
0 & m < 2 \\
\dfrac{2}{m(m-1)} \displaystyle\sum_{1 \le i < j \le m} \left(1 - \dfrac{|S_i - S_j|}{200}\right) & m \ge 2
\end{cases}
\quad \in [0,1]
\]

\[
\mathrm{source\_disagreement} =
\begin{cases}
0 & m < 2 \\
1 - \mathrm{source\_agreement} & m \ge 2
\end{cases}
\]

One live source (e.g. only Telegram) → agreement **0**, not `1.0` “trivial self-agreement” (**HLD-07** matrix: partial ≠ full multi-source agreement). Identical scores → 1. Opposite extremes \(\pm 100\) → 0.

Do not substitute a guessed score for an unavailable provider to “complete” the pair.

---

## 10. Freshness, coverage, and later time statements

**Data freshness** (spec §6; not the full `data_quality` object — doc 14):

\[
\mathrm{freshness\_seconds} =
\begin{cases}
\max(0,\ t_0 - \max_i created\_at_i) & n > 0 \\
\mathrm{null} & n = 0
\end{cases}
\]

Age of the **newest** asset-matched mention. Empty set → `null`, not “fresh.” Per-provider `freshness_seconds` on `SourceBundle` stays the fetch-outcome field (doc 05).

**Coverage (this doc):** temporal bin occupancy in the current 1h, not provider_coverage.

\[
\mathrm{activity\_coverage} = \frac{\#\{j : n^{(j)} \ge 1\}}{4} \in \{0, 0.25, 0.5, 0.75, 1\}
\]

(with \(n_{1h}=0\) → `0`). Provider/source coverage and confidence shrinkage → docs 11 and 14 (`FR-AGG-04`, `FR-PROV-01`). **MOM-09** already forbids using coverage gaps as agreement.

**FR-MOM-06 — Deltas for later snapshots, not persistence.** Snapshot **storage** is doc 13; contract JSON is doc 14. This module emits enough **time-window math** that a consumer can later say a score moved between two observations:

If `prior_sentiment_score` and `prior_observed_at` are **known** (forward-collected prior run, never backfilled — `FR-SNAP-02`, `CON-04`):

\[
\Delta S = \mathrm{sentiment\_score} - S_{\mathrm{prior}}
\qquad
\Delta t_{\mathrm{hours}} = (t_0 - t_{\mathrm{prior}}) / 3600
\]

Example shape only: \(S_{\mathrm{prior}}=18\), current \(=67\), \(\Delta t_{\mathrm{hours}}=3\) supports “moved from +18 to +67 in 3 hours.” If priors are unknown: do not emit a fabricated \(\Delta S\). Polar `sentiment_score` itself remains **SCR-08**.

---

## 11. Outputs on `ScoreBundle`

Shape: doc 05. Polar columns: doc 08. This list is the **momentum contract** (`FR-MOM-01`–`06`):

| Output | Rule |
|--------|------|
| `mention_count` | \(n\) |
| `unique_authors` | \(U\) |
| `mentions_1h` / previous-1h / \(\Delta_{6h}\) / \(\Delta_{24h}\) | §4 |
| `mention_velocity` | §5, \(\rho\kappa\) |
| `engagement_velocity` | §6 |
| `social_heat` | §7, `[0, 100]` |
| `social_acceleration` | \(\alpha\) (§8) |
| `acceleration_state` | `accelerating` \| `holding` \| `fading` |
| `source_agreement` / `source_disagreement` | §9, `[0, 1]` — filled when the helper runs on \(\ge 1\) source row; per-source bundle MAY leave them `0` until aggregation |
| `freshness_seconds` / `activity_coverage` | §10 |
| `sentiment_change` / `sentiment_change_hours` | §10 when priors known |

JSON-safe ints/floats/`null` only (`NFR-JSON-01`). Clip instead of `NaN`.

---

## 12. Pipeline position

Canonical flow (**HLD**): providers → match → **score (polar + momentum)** → manipulate → aggregate → provenance/quality.

- Momentum runs on mentions that actually arrived; it does not wait on a dead provider (**HLD-06**).
- It does not fill windows from an unavailable source (**HLD-07**, `NFR-DEGRADE-01`).
- All providers down → engine `status = unavailable` (`FR-INT-05`); do not invent heat or velocity.
- Manipulation may later flag the same spike; it must not rewrite these formulas (doc 10, **LLD-04**).

---

## Decision index

| ID | Decision |
|----|----------|
| **MOM-01** | Activity/momentum live in `score()` / `scoring.py`; providers (**PRV-07**) and matching (**MAT-07**) do not compute heat/velocity. |
| **MOM-02** | Heat, velocity, and spikes never shift polarity or imply bullish price; no `BUY`/`SELL`/`LONG`/`SHORT`. |
| **MOM-03** | Windows use engine `observed_at` (else max `created_at`); no wall-clock. |
| **MOM-04** | Unknown cache priors ≠ quiet priors; miss must not look like a burst vs silence. |
| **MOM-05** | `mention_velocity = ρ × κ` with 15m bins so bursts ≠ even counts (`FR-MOM-02`). |
| **MOM-06** | `engagement_velocity = η × κ_E` on adapter-normalized engagement sums. |
| **MOM-07** | `social_heat = 100 × (0.35 v + 0.25 a + 0.25 u + 0.15 e)` with documented saturations \(N_*=100\), \(A_*=50\), \(V_*=8\), \(E_*=500\). |
| **MOM-08** | \(\alpha = (n_{1h}-b_{1h})/\max(b_{1h},1)\) when known; state at \(\|\alpha\| > 0.25\); unknown → holding. |
| **MOM-09** | Agreement is mean pairwise \(1 - \|S_i-S_j\|/200\) over available, classifiable sources; \(m<2\) → 0; missing source ≠ agree. |
| **MOM-10** | Sentiment deltas for later narratives require a real prior observation; never backfill (`FR-MOM-06`, `CON-04`). |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| `SocialMention` / `ScoreBundle` / `MatchBatch` / `BaselineBundle` field lists | doc 05 |
| Provider engagement mapping and fetch isolation | doc 06 |
| Matching, collisions, `market_wide` partition | doc 07 |
| Polar score, classification, phrase lexicons, scoring confidence | doc 08 |
| `duplicate_content_ratio`, `organic_score`, burst/manipulation | doc 10 |
| Combining `SourceBundle`s; `scope`; insufficient_data policy | doc 11 |
| Redis keys/TTLs | doc 12 |
| Snapshot persistence | doc 13 |
| Full `build_social_sentiment` JSON and `data_quality` scales | doc 14 |
| Exact 15m/1h/6h/24h horizon **weight** tables | doc 15 |
