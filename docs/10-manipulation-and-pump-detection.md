# 10 — Manipulation / Pump Detection

> *Traces to specification §7 (manipulation signals & scores).*
> This is the **manipulation-resistance design** beneath `03-high-level-design.md` (**HLD-01** manipulation stage, **HLD-03**, **HLD-04**) and `04-low-level-design.md` (`manipulation.py`, `assess`). It owns organic-quality down-weighting and the named manipulation signals (`FR-MAN-01`–`04`). It does **not** redefine `SocialMention` / `ManipulationAssessment` / `MatchBatch` field lists (doc 05, **MOD-**), polar scoring/classification/phrase lexicons (doc 08, **SCR-**), activity/heat/velocity/acceleration formulas (doc 09, **MOM-**), matching/collision rules (doc 07, **MAT-**), provider I/O (doc 06, **PRV-**), aggregation/scope rules (doc 11), cache keys (doc 12), snapshot persistence (doc 13), the integration-contract JSON (doc 14), or horizon weight tables (doc 15).

---

## Role relative to other docs

Doc 04 freezes the module boundary: `assess(mentions) -> ManipulationAssessment`. Doc 07 decides *which* mentions are asset-specific (`MatchBatch.asset_mentions`). Doc 08/09 decide crowd *tone* and *activity magnitude* on that same set. This document decides whether the **pattern** behind that set looks organic or manufactured (`FR-MAN-01`–`03`), and whether the batch as a whole looks anomalous (`FR-MAN-04`). Doc 09 may call a spike high activity; this doc may call the same spike an anomaly — they are independent, non-duplicated readings of the same evidence (`FR-MOM-07`, `FR-MAN-04`).

Manipulation resistance is **risk/context**, not a price call (`00-project-vision.md`, `01-product-goals.md`, `FR-SENT-06`, `FR-INT-04`).

---

## 1. Module boundary *(LLD manipulation module, LLD-04)*

```
def assess(
    mentions: Sequence[SocialMention],
) -> ManipulationAssessment
```

| | |
|---|---|
| **Who calls it** | `engine.py` only — typically once per available source on that source's `MatchBatch.asset_mentions` subset (`FR-AGG-02` rows) and/or on the concatenated asset-matched set for asset-level manipulation context. Not a public API (`FR-INT-01`). |
| **Pure function** | Same mentions → same `ManipulationAssessment` (`NFR-DET-01`, **HLD-03**). No network, no Redis, no `datetime.now()` (only `created_at` values already on the mentions), no hidden lexicon/state (`NFR-STATE-01`). |
| **Must NOT** | Use an LLM (`CON-01`); fetch; match; compute `sentiment_score` / `classification` / phrase intensities (doc 08, **SCR-\***); compute `social_heat` / `mention_velocity` / `social_acceleration` / `source_agreement` (doc 09, **MOM-\***); set `scope` (doc 11); read/write Redis or baselines (**LLD-08**, cache is engine-only); emit `BUY`/`SELL`/`LONG`/`SHORT` (`FR-INT-04`). |

**MAN-01 — Engine-only orchestration, restricted input.** Providers must not compute manipulation signals (**PRV-07**). Matching must not compute manipulation signals (**MAT-07**). `assess()` consumes **only** `MatchBatch.asset_mentions` (doc 07, **MAT-05**) — never `market_wide`, never unmatched provider output (`FR-SCOPE-03`). This mirrors the scoring input rule (**SCR-03**) so both modules reason about the same cleaned evidence.

Unlike `scoring.py`, `assess()` receives **no `BaselineBundle` and no `horizon`** (per the frozen signature above). Every formula in this document is therefore self-referential to the current batch — it cannot and does not compare against a prior-hour count, which keeps it structurally distinct from doc 09's baseline-relative velocity/heat math.

---

## 2. Inputs *(cleaned, asset-matched only)*

| Input | Rule |
|-------|------|
| Mentions | **Only** `MatchBatch.asset_mentions`. Never `market_wide`. Never unmatched provider output. |
| Fields used | `text`, `author_id_hash`, `engagement`, `url_hash`, `created_at`. |
| Not used | `matched_terms`, `match_confidence`, `match_rationale` (matching-owned, **MOD-06**, **MAT-07**) — identity "why" is not a manipulation input. |

Let \(n\) = number of input mentions (asset-matched, including empty `text`, consistent with doc 09's \(n\)). Let \(U\) = number of distinct `author_id_hash` values.

**MAN-02 — Hash-only identity.** All author and link reasoning below uses `author_id_hash` / `url_hash` only (doc 05). No raw handle, email, or full URL is read, compared, or persisted by this module — consistent with the privacy principle in `00-project-vision.md` and doc 05 §6.

---

## 3. Content-duplicate detection *(FR-MAN-01, FR-MAN-02)*

Deterministic, rule-based text comparison — no embeddings, no LLM (`CON-01`).

**Normalization:** lowercase; strip substrings that look like URLs (URL repetition is handled separately via `url_hash`, §4, so it is not double-counted as text duplication); strip punctuation; collapse whitespace; trim. Mentions whose normalized text is empty are excluded from duplicate detection entirely (no fingerprint to compare, same treatment as doc 08's empty-text exclusion — not treated as `unavailable`). Let \(n_{\mathrm{fp}}\) = mentions with a non-empty normalized text.

**Fingerprint:**

- Tokenize the normalized text on whitespace into words.
- If the token count is \(\ge k_{\mathrm{shingle}}\), the fingerprint is the set of all contiguous \(k_{\mathrm{shingle}}\)-word shingles.
- If the token count is \(< k_{\mathrm{shingle}}\) (short text), the fingerprint is the single-element set containing the whole normalized string.

**MAN-03 — Duplicate constants.** \(k_{\mathrm{shingle}} = 5\) (five-word windows: long enough that common short phrases don't collide, short enough to survive minor copy-paste edits) and duplicate threshold \(\theta_{\mathrm{dup}} = 0.60\) (majority shingle overlap).

Two mentions \(i, j\) are **content duplicates** if their normalized texts are identical, **or** their fingerprint sets \(F_i, F_j\) satisfy:

\[
J(F_i, F_j) = \frac{|F_i \cap F_j|}{|F_i \cup F_j|} \ge \theta_{\mathrm{dup}}
\]

**MAN-04 — `duplicate_content_ratio`.**

\[
\mathrm{duplicate\_content\_ratio} =
\begin{cases}
0 & n_{\mathrm{fp}} = 0 \\
\dfrac{\#\{i : i \text{ has} \ge 1 \text{ content-duplicate partner}\}}{n_{\mathrm{fp}}} & \text{otherwise}
\end{cases}
\]

Captures duplicate posts, copied campaign text, and suspiciously similar messages (`FR-MAN-01`) in one measurement.

---

## 4. Repeated URLs *(FR-MAN-01)*

Let \(n_{\mathrm{url}}\) = number of mentions with non-null `url_hash`.

**MAN-05 — `repeated_url_ratio`** (internal signal feeding §7):

\[
\mathrm{repeated\_url\_ratio} =
\begin{cases}
0 & n_{\mathrm{url}} = 0 \\
\dfrac{\#\{i : \mathrm{url\_hash}_i \text{ shared by} \ge 1 \text{ other mention}\}}{n_{\mathrm{url}}} & \text{otherwise}
\end{cases}
\]

Uses `url_hash` only — never a raw URL string (**MAN-02**).

---

## 5. Author diversity and concentration *(FR-MAN-01, FR-MAN-02)*

**MAN-06 — `unique_author_ratio`.**

\[
\mathrm{unique\_author\_ratio} =
\begin{cases}
0 & n = 0 \\
U / n & \text{otherwise}
\end{cases}
\quad \in [0, 1]
\]

Captures repeated-author flooding and low-author-diversity pumps directly (`FR-MAN-01`). Low ratio ⇒ a small set of identities behind a lot of mentions.

**Gini-based concentration** — the shared formula used for both `author_concentration` and `engagement_concentration`. For \(m\) non-negative values \(x_1, \ldots, x_m\) with total \(\Sigma x = \sum x_k\), sorted ascending as \(x_{(1)} \le \ldots \le x_{(m)}\):

\[
\mathrm{gini}(x_1, \ldots, x_m) =
\begin{cases}
0 & m \le 1 \text{ or } \Sigma x = 0 \\
\dfrac{2 \sum_{k=1}^{m} k \cdot x_{(k)}}{m \cdot \Sigma x} - \dfrac{m+1}{m} & \text{otherwise}
\end{cases}
\quad \in [0, 1]
\]

0 = perfectly even distribution across the \(m\) entities; 1 = one entity holds everything.

**MAN-07 — `author_concentration`.** Group `asset_mentions` by `author_id_hash`; let the per-author mention counts be \(c_1, \ldots, c_U\).

\[
\mathrm{author\_concentration} =
\begin{cases}
0 & n \le 1 \\
1 & U = 1 \text{ and } n > 1 \\
\mathrm{gini}(c_1, \ldots, c_U) & U \ge 2
\end{cases}
\]

The \(U = 1\) special case exists because the shared Gini formula returns 0 for a single entity (no inequality to measure among one value) — but one author producing every mention in a multi-mention batch **is** maximal concentration, not "nothing to measure." `author_concentration` answers "is the content dominated by a few identities" and is distinct from `unique_author_ratio` (a plain diversity ratio): a batch can have many authors overall yet still be dominated by a handful of them (moderate `unique_author_ratio`, high `author_concentration`).

---

## 6. Engagement concentration *(FR-MAN-01, FR-MAN-02)*

**MAN-08 — `engagement_concentration`.** Let \(e_1, \ldots, e_n\) be the per-mention `engagement` values (already \(\ge 0\), doc 06).

\[
\mathrm{engagement\_concentration} =
\begin{cases}
0 & n \le 1 \\
\mathrm{gini}(e_1, \ldots, e_n) & n \ge 2
\end{cases}
\]

Captures "extreme engagement concentration" (`FR-MAN-01`): a handful of posts absorbing nearly all likes/upvotes/replies while the rest sit near zero.

---

## 7. Coordinated bursts *(FR-MAN-01, FR-MAN-02)*

`assess()` has no baseline and no `t_0` anchor (**MAN-01**), so a "burst" here cannot mean "more than the historical rate" — that reading belongs to doc 09 (`MOM-05`, `mention_velocity`). This module instead asks whether mentions that are already **content duplicates** (§3) were posted **suspiciously close together in time**, which is the timing signature of scripted/coordinated posting rather than organic, staggered re-discovery of a topic.

**MAN-09 — Coordinated pair.** Two mentions \(i, j\) form a coordinated pair if they are content duplicates (§3, **MAN-03**) **and** \(|\mathrm{created\_at}_i - \mathrm{created\_at}_j| \le \delta_{\mathrm{burst}}\), with \(\delta_{\mathrm{burst}} = 300\) seconds (5 minutes — long enough to catch a scripted drop, short enough that two people independently rediscovering the same news over hours does not qualify).

\[
\mathrm{suspicious\_burst\_score} =
\begin{cases}
0 & n = 0 \\
\dfrac{\#\{i : i \text{ is in} \ge 1 \text{ coordinated pair}\}}{n} & \text{otherwise}
\end{cases}
\quad \in [0, 1]
\]

This is deliberately narrower than raw temporal clustering: a genuine breaking-news spike with distinct, non-duplicate wording from many independent accounts does **not** raise `suspicious_burst_score`, even though it would raise doc 09's `mention_velocity`/`social_heat`. That is the intended split of responsibility — doc 09 says "this is a lot of activity, fast"; this doc says "and this specific slice of it looks scripted, or not."

---

## 8. Spam / bot suspicion *(FR-MAN-01, FR-MAN-02)*

**MAN-10 — `spam_bot_suspicion`.** Weighted combination of the duplication, link-repetition, and authorship signals already computed above (weights sum to 1):

\[
\mathrm{spam\_bot\_suspicion} = \mathrm{clip}_{[0,1]}\big(
0.45 \cdot \mathrm{duplicate\_content\_ratio}
+ 0.30 \cdot \mathrm{repeated\_url\_ratio}
+ 0.25 \cdot \mathrm{author\_concentration}
\big)
\]

Rationale for the weights: near-identical templated text is the strongest bot/spam tell (0.45); a shared link across many posts is the classic drop-and-repeat pattern (0.30); a few identities repeating the pattern corroborates it (0.25). No single input can push the score alone without at least partial support from another.

---

## 9. Overall anomaly reading *(FR-MAN-01, FR-MAN-04, FR-MOM-07)*

A second, **self-referential** timing signal — independent of any duplicate-content pairing — measures how irregular the batch's own posting rhythm is, without comparing to any external baseline (still consistent with **MAN-01**: no baseline/`t_0` input at all).

Sort `created_at` ascending as \(t_1 \le \ldots \le t_n\). For \(n \ge 2\), let the inter-arrival gaps be \(g_k = t_{k+1} - t_k\) (seconds), \(k = 1, \ldots, n-1\), with mean \(\bar g\) and standard deviation \(\sigma_g\).

\[
\mathrm{CV} =
\begin{cases}
1 & n \ge 2 \text{ and } \bar g = 0 \\
\sigma_g / \bar g & n \ge 2 \text{ and } \bar g > 0 \\
0 & n < 2
\end{cases}
\]

(\(\bar g = 0\) with \(n \ge 2\) means every mention landed in the same instant — maximal irregularity, not an undefined ratio.)

\[
\mathrm{anomaly\_intensity\_time} = \mathrm{clip}_{[0,1]}\!\left(\frac{\mathrm{CV}}{1 + \mathrm{CV}}\right) \in [0, 1)
\]

Even, human-paced posting has low coefficient of variation (→ 0); a batch that is nearly all-at-once or wildly irregular pushes \(\mathrm{CV}\) up. This is a dispersion statistic over the batch's own gaps, not a bin-share-vs-baseline computation — it does not reuse doc 09's \(\rho\)/\(\kappa\) (**MOM-05**) formula shape or inputs.

**MAN-11 — `anomaly_score`.**

\[
\mathrm{anomaly\_score} = \mathrm{clip}_{[0,1]}\big(0.5 \cdot \mathrm{anomaly\_intensity\_time} + 0.5 \cdot \mathrm{manipulation\_risk}\big)
\]

(`manipulation_risk` defined next, §10 — `anomaly_score` is computed after it.) A batch can be anomalous through irregular timing alone (real breaking news, `manipulation_risk` low) or through content/author manipulation alone (steady drip of shill posts, timing unremarkable) or both. Either path is exposed as *risk/context*, never as an automatic `positive`/bullish read (`FR-MAN-04`, `FR-MOM-07`) — the aggregation layer (doc 11) decides how this feeds the public `anomalies` list; this field is the manipulation-side input to that decision.

---

## 10. Overall manipulation risk and organic score *(FR-MAN-02, FR-MAN-03)*

**MAN-12 — `manipulation_risk`.** Weighted composite of every signal above (weights sum to 1):

\[
\mathrm{manipulation\_risk} = \mathrm{clip}_{[0,1]}\big(
0.30 \cdot \mathrm{duplicate\_content\_ratio}
+ 0.20 \cdot (1 - \mathrm{unique\_author\_ratio})
+ 0.15 \cdot \mathrm{engagement\_concentration}
+ 0.15 \cdot \mathrm{suspicious\_burst\_score}
+ 0.20 \cdot \mathrm{spam\_bot\_suspicion}
\big)
\]

If \(n = 0\): every term above is 0 by its own base-case definition, so `manipulation_risk = 0`. This is an honest "no evidence of manipulation was observed" — it is not a claim that the (nonexistent) chatter is organic; low-evidence handling for the public result is owned by `quality.py` / aggregation (doc 11, 14), not fabricated here.

**MAN-13 — `organic_score`.**

\[
\mathrm{organic\_score} = \mathrm{clip}_{[0,100]}\big(100 \times (1 - \mathrm{manipulation\_risk})\big)
\]

**Why `FR-MAN-03` holds by construction:** every term feeding `manipulation_risk` is a **ratio, ratio-complement, Gini coefficient, or clipped coefficient-of-variation** — none of them is proportional to the raw mention count \(n\). Posting 500 near-identical mentions instead of 50 does not, by itself, move `duplicate_content_ratio`, `author_concentration`, or `suspicious_burst_score` — those are already normalized to the batch size. Volume alone therefore cannot dilute or outrun the manipulation signals; only genuine diversity (more distinct authors, more distinct wording, less concentrated engagement, less coordinated timing) lowers `manipulation_risk` and raises `organic_score`.

**Illustrative check (not a formula, a sanity example):** 500 near-identical shill posts from 20 accounts — `duplicate_content_ratio` ≈ 1.0, `unique_author_ratio` = 20/500 = 0.04, `suspicious_burst_score` high if the posts cluster in time — yields `manipulation_risk` well above 0.5 and `organic_score` well below 30. Eighty independent, differently-worded genuine discussions from ~70 distinct authors — low duplication, `unique_author_ratio` ≈ 0.875, low `suspicious_burst_score` — yields `manipulation_risk` well under 0.15 and `organic_score` well above 85. The 80 genuine mentions score more organic than the 500 shill posts despite the large gap in raw volume, exactly as `FR-MAN-03` requires.

---

## 11. Pattern coverage *(FR-MAN-01)*

| Required detection (`FR-MAN-01`) | Primarily captured by |
|-----------------------------------|------------------------|
| Duplicate posts, copied campaign text, suspiciously similar messages | `duplicate_content_ratio` (**MAN-04**) |
| Repeated URLs | `repeated_url_ratio` → `spam_bot_suspicion` (**MAN-05**, **MAN-10**) |
| Repeated-author flooding, low-author-diversity pumps | `unique_author_ratio` (**MAN-06**), `author_concentration` (**MAN-07**) |
| Coordinated bursts | `suspicious_burst_score` (**MAN-09**) |
| Extreme engagement concentration | `engagement_concentration` (**MAN-08**) |
| Sudden mention spikes | `anomaly_intensity_time` term inside `anomaly_score` (**MAN-11**) — magnitude of the spike is doc 09's `mention_velocity` / `social_acceleration`; this doc reads the same window's timing *regularity*, not its size |
| Campaign/shill behavior | Composite `spam_bot_suspicion` and `manipulation_risk` (**MAN-10**, **MAN-12**) |
| Bot-like repetition | `spam_bot_suspicion` (**MAN-10**) |

---

## 12. Outputs *(`ManipulationAssessment`, doc 05)*

All fields are JSON-safe floats (`NFR-JSON-01`); no `NaN`/`Inf` — clip instead:

| Output | Range | Rule |
|--------|-------|------|
| `duplicate_content_ratio` | `[0, 1]` | §3, **MAN-04** |
| `unique_author_ratio` | `[0, 1]` | §5, **MAN-06** |
| `engagement_concentration` | `[0, 1]` | §6, **MAN-08** |
| `author_concentration` | `[0, 1]` | §5, **MAN-07** |
| `suspicious_burst_score` | `[0, 1]` | §7, **MAN-09** |
| `spam_bot_suspicion` | `[0, 1]` | §8, **MAN-10** |
| `manipulation_risk` | `[0, 1]` | §10, **MAN-12** |
| `organic_score` | `[0, 100]` | §10, **MAN-13** |
| `anomaly_score` | `[0, 1]` | §9, **MAN-11** |

Names match doc 05's `ManipulationAssessment` and `FR-MAN-02` exactly, including `spam_bot_suspicion`.

---

## 13. Pipeline position and fail-soft

Canonical flow (**HLD**): providers → match → score (polar + momentum) → **manipulate** → aggregate → provenance/quality.

- `assess()` runs on whatever asset-matched mentions actually arrived; it does not wait on a dead provider (**HLD-06**).
- It does not substitute mentions for an unavailable source (**HLD-07**, `NFR-DEGRADE-01`).
- All providers down is an engine `status = unavailable` problem (`FR-INT-05`) — `assess()` is simply not fed invented rows.
- `assess([])` is valid: every signal is 0 by base case, `organic_score = 100` (no evidence observed, not a verdict of organic — see **MAN-12**).
- Never emits `BUY`/`SELL`/`LONG`/`SHORT`; never recasts a spike or a low `manipulation_risk` as bullish (`FR-INT-04`, `FR-SENT-06`, `FR-MOM-07`, `FR-MAN-04`).

---

## Decision index

| ID | Decision |
|----|----------|
| **MAN-01** | `assess()` is engine-only, takes only `MatchBatch.asset_mentions`, and receives no baseline/horizon — every formula is self-referential to the current batch. |
| **MAN-02** | All author/link reasoning uses `author_id_hash` / `url_hash` only; no raw PII read or persisted. |
| **MAN-03** | Duplicate detection: 5-word shingles, normalized text, Jaccard ≥ 0.60 or exact match; empty text excluded. |
| **MAN-04** | `duplicate_content_ratio` = share of fingerprint-bearing mentions with ≥1 duplicate partner. |
| **MAN-05** | `repeated_url_ratio` = share of `url_hash`-bearing mentions whose hash repeats. |
| **MAN-06** | `unique_author_ratio = U / n`. |
| **MAN-07** | `author_concentration` = Gini of per-author mention counts; `U = 1` special-cased to 1 (not 0). |
| **MAN-08** | `engagement_concentration` = Gini of per-mention engagement values. |
| **MAN-09** | `suspicious_burst_score` = share of mentions in a coordinated pair (content-duplicate **and** ≤300s apart). |
| **MAN-10** | `spam_bot_suspicion = 0.45·dup + 0.30·repeated_url + 0.25·author_concentration`. |
| **MAN-11** | `anomaly_score = 0.5·anomaly_intensity_time + 0.5·manipulation_risk`; timing term is a self-referential coefficient-of-variation over inter-arrival gaps, no baseline. |
| **MAN-12** | `manipulation_risk = 0.30·dup + 0.20·(1-unique_author_ratio) + 0.15·engagement_conc + 0.15·burst + 0.20·spam_bot`. |
| **MAN-13** | `organic_score = 100·(1-manipulation_risk)`; every input term is scale-invariant (ratio/Gini/CV), which is the mechanism guaranteeing `FR-MAN-03`. |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| `SocialMention` / `ManipulationAssessment` / `MatchBatch` field lists | doc 05 |
| Polar sentiment, classification, phrase lexicons | doc 08 |
| Social heat, mention velocity, social acceleration, source agreement | doc 09 |
| Matching, collisions, `market_wide` partition | doc 07 |
| Provider engagement/URL mapping | doc 06 |
| Combining per-source assessments into the public `anomalies` list; `scope` | doc 11 |
| Redis keys/TTLs | doc 12 |
| Snapshot persistence | doc 13 |
| Full `build_social_sentiment` JSON and `data_quality` scales | doc 14 |
| Horizon weight tables | doc 15 |
