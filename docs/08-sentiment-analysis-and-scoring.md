# 08 — Sentiment Analysis & Scoring

> *Traces to specification §5 (sentiment metrics, classification, formulas/weights).*
> This is the **deterministic scoring design** beneath `03-high-level-design.md` (**HLD-01** scoring stage, **HLD-03**, **HLD-04**) and `04-low-level-design.md` (`scoring.py`). It owns polar counts/ratios, `sentiment_score` in `−100..+100`, classification labels, confidence, engagement-weighted sentiment, and phrase-intensity tracks. It does **not** redefine `SocialMention` / `ScoreBundle` / `MatchBatch` / `BaselineBundle` field lists (doc 05, **MOD-03**, **MOD-06**), matching/collision rules (doc 07, **MAT-***), provider I/O (doc 06, **PRV-***), manipulation formulas (doc 10), aggregation/scope rules in full (doc 11), cache-key lists (doc 12), the integration-contract JSON (doc 14), or horizon weight tables (doc 15).

---

## Role relative to other docs

Doc 04 freezes the module boundary: `score(mentions, horizon, baselines) -> ScoreBundle`. Doc 05 owns the record shapes. Doc 07 decides *which* mentions are asset-specific (`MatchBatch.asset_mentions`). This document decides *how those mentions are scored* (`FR-SENT-01`–`06`). Activity/momentum numbers that also live on `ScoreBundle` (`social_heat`, `mention_velocity`, `social_acceleration`) are computed in the same module but **formulas → doc 09**. Horizon 15m/1h/6h/24h tables → doc 15. Combining per-source `ScoreBundle`s and setting result `scope` → doc 11.

Crowd tone is **context**, not a price call (`00-project-vision.md`, `01-product-goals.md`, `FR-SENT-06`, `FR-INT-04`).

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
| **Who calls it** | `engine.py` only — typically once per available source on that source’s asset-matched subset (`FR-AGG-02` rows), and never as a public API (`FR-INT-01` stays on the engine). |
| **Pure function** | Same mentions + same `horizon` + same `BaselineBundle` → same `ScoreBundle` (`NFR-DET-01`, **HLD-03**). No network, no Redis, no wall-clock, no hidden lexicon mutation (`NFR-STATE-01`). |
| **Must NOT** | Use an LLM (`CON-01`); fetch; match; compute `duplicate_content_ratio` / `organic_score` / other manipulation signals (doc 10); set `scope` (doc 11); invent baselines (`CON-04`, **HLD-07**); emit `BUY`/`SELL`/`LONG`/`SHORT` (`FR-INT-04`). |

**SCR-01 — Local, open, deterministic only.** Classification and all intensities are rules + frozen lexicons + arithmetic. No Claude, OpenAI, Gemini, or any LLM SDK; no generative “explain why” step (`CON-01`, `FR-SENT-01`, **HLD-03**).

**SCR-02 — Engine-only orchestration.** Providers must not score (**PRV-07**). Matching must not score (**MAT-07**). Scoring must not reach back into adapters or `matching.py` (**LLD-04**, **HLD-01**).

---

## 2. Inputs *(cleaned, asset-matched only)*

| Input | Rule |
|-------|------|
| Mentions | **Only** `MatchBatch.asset_mentions` (doc 07, **MAT-05**). Never `market_wide` leftovers. Never unmatched provider output. Implements `FR-SCOPE-03`, `FR-NORM-04`. |
| Mention fields used | `text` and `engagement` (and `created_at` only if the engine-supplied recency slot in `BaselineBundle` is populated — see §8). |
| Not used | `match_rationale`, `matched_terms`, `match_confidence` — matching-owned (**MOD-06**, **MAT-07**). Scoring does not re-decide identity. |
| `horizon` | `intraday` \| `swing` \| `position` (unrecognized handled by engine, `FR-HOR-03`). Behavioral weights → doc 15. |
| `baselines` | Engine-supplied `BaselineBundle` from `cache.py` (doc 05). On Redis miss or Redis down: **empty/zero bundle**, never invented history (`CON-04`, `FR-CACHE-03`, **HLD-07**, **LLD-08**). Sentiment formulas in this doc **do not** read a historical tone from baselines. |

**SCR-03 — Asset-matched evidence only.** A mention that failed gating is invisible to `score()`. Quiet or collision-heavy assets yield a short or empty list (**MAT-06**); scoring reports that honestly as `insufficient_data` when the gate in §6 fires — it does not loosen matching.

**SCR-04 — Text + engagement, not match rationale.** Identity “why” stays on the mention for provenance; polar math does not consume it.

Empty `text` is a valid mention (doc 05) but contributes **no** polar hits: it is excluded from `n_eff` (defined below), not treated as `unavailable` (providers already succeeded — **LLD-02**, **PRV-02**).

---

## 3. Polar vs directional vs emotion tracks *(FR-SENT-05, FR-SENT-06)*

Three **independent** lexicon families, all frozen lists of lowercase phrases (1–3 tokens), longest-phrase-first, case-insensitive, token-boundary match (no substring inside unrelated words):

| Track | What it measures | Feeds |
|-------|------------------|--------|
| **Polar** | Approval/criticism of the project or discussion (praise, disappointment, scam-talk, etc.) | Per-mention class → counts/ratios → `sentiment_score` / engagement-weighted sentiment / classification |
| **Directional** | Crowd *price-direction language* (e.g. moon/pump vs dump/crash phrasing) | `bullish_phrase_intensity`, `bearish_phrase_intensity` **only** |
| **Emotion** | Fear, greed/excitement, uncertainty, euphoria lexical hits | The four intensity fields **only** |

**SCR-05 — Positive ≠ bullish.** Directional phrases **do not** increment polar positive/negative hits. Measuring that the crowd used moon-language is not a license to label the result as a price call, and polar-positive must not be renamed or mapped to bullish price direction (`FR-SENT-06`). Intensities are descriptive of language, not `LONG`/`SHORT`.

**SCR-06 — Volume is not tone.** Mention count, spikes, heat, and velocity **must not** be added into `sentiment_score` or flip a class toward `positive` (`FR-MOM-07`, `FR-MAN-04`). A spike is activity (doc 09) and/or an anomaly (doc 10), not automatic positivity.

Lexicon files (or tables inside `scoring.py`) are versioned constants in-repo. Same text → same hits. No stemming model, no embeddings, no remote word lists at runtime. Illustrative category members belong in implementation comments/tests; this document freezes *matching rules and formulas*, not a marketing word cloud.

Non-English `language` with zero lexicon hits → polar **neutral** and zero intensities for that mention. No machine translation (`CON-01`, `CON-04`).

---

## 4. Per-mention polarity *(closed labels: positive / neutral / negative)*

Let `pos_hits` / `neg_hits` be the sums of polar-lexicon match weights. Every lexicon entry has weight **1.0** unless a later documented revision changes a specific phrase (no silent weights).

\[
p_{\mathrm{raw}} =
\begin{cases}
0 & \text{if } pos\_hits + neg\_hits = 0 \\
\dfrac{pos\_hits - neg\_hits}{pos\_hits + neg\_hits} & \text{otherwise}
\end{cases}
\quad \in [-1, +1]
\]

**SCR-07 — Mention class threshold \(\tau_{\mathrm{pol}} = 0.20\).** A mention leaves neutral only when one polar side is at least a 3:2 majority of polar hits (\(p_{\mathrm{raw}} \ge 0.20\) is a 60/40 split).

| Condition | Mention class \(s_i\) |
|-----------|------------------------|
| \(p_{\mathrm{raw}} \ge 0.20\) | `positive` (\(s_i = +1\)) |
| \(p_{\mathrm{raw}} \le -0.20\) | `negative` (\(s_i = -1\)) |
| otherwise | `neutral` (\(s_i = 0\)) |

Ties and empty polar hits are **neutral**, not guessed tone.

Let \(n\) = number of input mentions (asset-matched).  
Let \(n_{\mathrm{eff}}\) = number of those with non-empty `text` after sanitization.  
\(N_{\mathrm{pos}}, N_{\mathrm{neu}}, N_{\mathrm{neg}}\) count mention classes among the \(n_{\mathrm{eff}}\) mentions.

**Ratios** (among \(n_{\mathrm{eff}}\); if \(n_{\mathrm{eff}} = 0\), all ratios \(= 0\)):

\[
r_{\mathrm{pos}} = \frac{N_{\mathrm{pos}}}{n_{\mathrm{eff}}},\quad
r_{\mathrm{neu}} = \frac{N_{\mathrm{neu}}}{n_{\mathrm{eff}}},\quad
r_{\mathrm{neg}} = \frac{N_{\mathrm{neg}}}{n_{\mathrm{eff}}}
\]

---

## 5. `sentiment_score` and engagement-weighted sentiment *(FR-SENT-02, FR-SENT-05)*

**SCR-08 — Equal-weight crowd tone.**

\[
\mathrm{sentiment\_score} =
\begin{cases}
0 & \text{if } n_{\mathrm{eff}} = 0 \\
\mathrm{clip}_{[-100,+100]}\left(100 \times \dfrac{N_{\mathrm{pos}} - N_{\mathrm{neg}}}{n_{\mathrm{eff}}}\right) & \text{otherwise}
\end{cases}
\]

Neutral mentions pull the score toward 0 because they sit in the denominator. This is intentional: a board that is mostly non-polar is not a strong crowd tone.

**SCR-09 — Engagement weight.** Adapter-normalized `engagement` is already \(\ge 0\) (doc 06 mapping; missing native engagement → `0`, never invented virality).

\[
w_i = 1 + \ln(1 + \mathrm{engagement}_i)
\]

(Natural log. Floor \(1\) so a zero-engagement mention still counts once.)

\[
\mathrm{engagement\_weighted\_sentiment} =
\begin{cases}
0 & \text{if } n_{\mathrm{eff}} = 0 \\
\mathrm{clip}_{[-100,+100]}\left(100 \times \dfrac{\sum_i w_i s_i}{\sum_i w_i}\right) & \text{sum over } n_{\mathrm{eff}} \text{ mentions}
\end{cases}
\]

These two numbers are **siblings**, not aliases: equal-weight answers “share of mentions”; engagement-weighted answers “share of attention.” Classification in §6 uses **equal-weight** `sentiment_score` plus ratios so a few high-engagement posts cannot relabel the whole crowd by themselves. Both are emitted (`FR-SENT-05`).

Optional horizon recency multipliers (doc 15) may scale \(w_i\) or a separate time weight; if the engine has not supplied that table/slot, the multiplier is **1.0**. Sentiment never fabricates a 15m/1h/6h/24h history to fill the gap (**HLD-04** points here; tables stay in doc 15).

---

## 6. Classification *(FR-SENT-03, FR-SENT-04, MOD-03)*

Closed set only: `strong_positive`, `positive`, `neutral`, `mixed`, `negative`, `strong_negative`, `insufficient_data`.

### Insufficient evidence gate

**SCR-10 — \(N_{\min} = 5\) non-empty asset-matched mentions.** Below this, polarity ratios are too unstable to claim a tone. The module emits `classification = insufficient_data`, keeps honest counts/ratios (often zeros), and sets `sentiment_score = 0` and `engagement_weighted_sentiment = 0` — **not** a fabricated `positive`/`negative` (`FR-SENT-04`, `FR-SCOPE-02`, **MAT-06**). Aggregation **consumes** this label; full scope/`asset_specific` vs `market_wide` policy → doc 11.

\(n_{\mathrm{eff}} = 0\) (including empty `asset_mentions`) is the same gate.

### Decision tree *(first match wins)*

Constants (all on the equal-weight score/ratio scale):

| Symbol | Value | Meaning |
|--------|-------|---------|
| \(N_{\min}\) | 5 | Minimum \(n_{\mathrm{eff}}\) for any tone label |
| \(r_{\mathrm{mix}}\) | 0.20 | Both polar sides at least 20% of \(n_{\mathrm{eff}}\) → bipolar / mixed |
| \(S_{\mathrm{strong}}\) | 60 | \|score\| for *strong_* labels |
| \(S_{\mathrm{tone}}\) | 20 | \|score\| for ordinary positive/negative vs residual neutral |

1. If \(n_{\mathrm{eff}} < N_{\min}\) → **`insufficient_data`**
2. Else if \(r_{\mathrm{pos}} \ge r_{\mathrm{mix}}\) **and** \(r_{\mathrm{neg}} \ge r_{\mathrm{mix}}\) → **`mixed`**
3. Else if `sentiment_score` \(\ge S_{\mathrm{strong}}\) → **`strong_positive`**
4. Else if `sentiment_score` \(\ge S_{\mathrm{tone}}\) → **`positive`**
5. Else if `sentiment_score` \(\le -S_{\mathrm{strong}}\) → **`strong_negative`**
6. Else if `sentiment_score` \(\le -S_{\mathrm{tone}}\) → **`negative`**
7. Else → **`neutral`**

**SCR-11 — Mixed before strong.** A board that is both ≥20% positive and ≥20% negative is `mixed` even if the net score is large. Net score without a second polar side uses the strong/ordinary/neutral ladder. `neutral` is residual: weak net score **and** not mixed (typically high \(r_{\mathrm{neu}}\)).

No other strings. No `bullish`/`bearish` classification labels.

---

## 7. Phrase intensities *(0..100)*

For each lexicon \(L\) ∈ {bullish, bearish, fear, greed/excitement, uncertainty, euphoria}:

Let \(h_{i,L}\) = number of phrase hits in mention \(i\).  
Let \(c_{i,L} = \min(1, h_{i,L} / 2)\). Two hits saturate a mention so one copypasta slogan cannot dominate (**intensity**, not raw dump of campaign text — organic vs copy is still doc 10).

**SCR-12 — Incidence-capped intensity.**

\[
\mathrm{intensity}_L =
\begin{cases}
0 & \text{if } n_{\mathrm{eff}} = 0 \\
\mathrm{clip}_{[0,100]}\left(100 \times \dfrac{\sum_i c_{i,L}}{n_{\mathrm{eff}}}\right) & \text{otherwise}
\end{cases}
\]

Field names on `ScoreBundle` (doc 05 allows additional JSON-safe numerics; contract mapping → doc 14):

- `bullish_phrase_intensity`, `bearish_phrase_intensity`
- `fear_intensity`, `greed_excitement_intensity`, `uncertainty_intensity`, `euphoria_intensity`

Greed and excitement share **one** lexicon and **one** output (`FR-SENT-05` “greed/excitement”). Intensities do not alter `classification`. They never appear as trade advice.

---

## 8. Confidence *(FR-SENT-05)*

Scale **0..100** (same numeric style as the integration-contract example; public field mapping → doc 14).

**SCR-13 — Sample size saturates at \(N_{\mathrm{full}} = 40\).** Not a claim of statistical significance; a documented cap so 400 near-duplicate mentions cannot print `confidence = 100` from volume alone (duplicates themselves are a manipulation concern — doc 10).

\[
\mathrm{coverage} = \mathrm{clip}_{[0,1]}\left(\frac{n_{\mathrm{eff}}}{N_{\mathrm{full}}}\right)
\]

Polar agreement (1 = one-sided or empty polar mass; 0 = equal pos/neg):

\[
\mathrm{agreement} = 1 - \frac{2 \cdot \min(r_{\mathrm{pos}}, r_{\mathrm{neg}})}{\max(r_{\mathrm{pos}} + r_{\mathrm{neg}}, 10^{-9})}
\]

Empty-text share among all \(n\) mentions (0 if \(n = 0\)): \(e = n_{\mathrm{empty}} / n\).

\[
\mathrm{confidence} = \mathrm{clip}_{[0,100]}\Big(
  100 \times \mathrm{coverage} \times \big(0.40 + 0.60 \times \mathrm{agreement}\big) \times \big(1 - 0.50 \times e\big)
\Big)
\]

- Floor **0.40** on the agreement term: a well-sampled `mixed` board still has moderate confidence *in the mixed reading*, not zero.
- `insufficient_data` still emits this formula (typically low because `coverage` is small). It does not invent a high-confidence tone.
- Partial **provider** coverage reducing aggregate confidence is owned by aggregation/quality (`FR-AGG-04`, docs 11 and 14), not by inventing extra mentions here.

`BaselineBundle` empty → no extra penalty or bonus to confidence from “missing history.” Missing cache is not evidence of disagreement (**HLD-07**).

Recency: if doc 15 supplies a time-decay applied by the engine into mention weights, confidence still uses \(n_{\mathrm{eff}}\) as above (count of evidence), not a reconstructed past window.

---

## 9. Outputs (minimum)

Emitted on `ScoreBundle` (shape: doc 05; this list is the **scoring contract** for `FR-SENT-02`–`05`):

| Output | Rule |
|--------|------|
| `positive_count` / `neutral_count` / `negative_count` | \(N_{\mathrm{pos}}, N_{\mathrm{neu}}, N_{\mathrm{neg}}\) |
| `positive_ratio` / `neutral_ratio` / `negative_ratio` | \(r_{\mathrm{pos}}, r_{\mathrm{neu}}, r_{\mathrm{neg}}\) |
| `sentiment_score` | §5, clipped `[-100, +100]` |
| `engagement_weighted_sentiment` | §5, same clip |
| `classification` | §6, **MOD-03** only |
| `confidence` | §8, `[0, 100]` |
| Phrase intensities | §7, `[0, 100]` |
| `mention_count` | \(n\) (asset-matched inputs, including empty text) |
| `horizon` | Horizon actually applied |

Heat/velocity/acceleration and unique authors: **present on the bundle**, defined in **doc 09**. Manipulation: **not** on this bundle’s formulas (**LLD** `manipulation.py`).

JSON-safe floats/ints only (`NFR-JSON-01`). No `NaN`/`Inf`. Clip instead of failing.

---

## 10. Pipeline position and fail-soft

Canonical flow (**HLD**): providers → match → **score** → manipulate → aggregate → provenance/quality.

- Scoring runs on whatever asset-matched mentions actually arrived; it does not wait on a dead provider (**HLD-06**).
- It does not substitute mentions for an unavailable source (**HLD-07**, `NFR-DEGRADE-01`).
- All providers down is an engine `status = unavailable` problem (`FR-INT-05`) — scoring is simply not fed invented rows.
- `score([])` is valid: zeros + `insufficient_data` + low confidence.

---

## Decision index

| ID | Decision |
|----|----------|
| **SCR-01** | Deterministic local lexicons/rules only; no LLM for class or intensity. |
| **SCR-02** | Only `engine.py` calls `score()`; providers (**PRV-07**) and matching (**MAT-07**) do not score. |
| **SCR-03** | Score `MatchBatch.asset_mentions` only; never `market_wide` or unmatched output. |
| **SCR-04** | Polar math uses mention `text` + `engagement` (plus optional engine recency); not `match_rationale`. |
| **SCR-05** | Directional lexicons feed intensities only; positive tone is never recast as bullish price; no `BUY`/`SELL`/`LONG`/`SHORT`. |
| **SCR-06** | Spikes/volume/heat do not shift polarity or classification toward positive. |
| **SCR-07** | Per-mention polar threshold \(\tau_{\mathrm{pol}} = 0.20\) (3:2 hit majority); else neutral. |
| **SCR-08** | `sentiment_score = 100 \times (N_{\mathrm{pos}} - N_{\mathrm{neg}}) / n_{\mathrm{eff}}`, clip `[-100, +100]`. |
| **SCR-09** | Engagement weight \(w_i = 1 + \ln(1 + e_i)\); separate `engagement_weighted_sentiment`; classification uses equal-weight score. |
| **SCR-10** | `insufficient_data` when \(n_{\mathrm{eff}} < 5\); zeros, not a fabricated tone. |
| **SCR-11** | Classification tree: insufficient → mixed (\(r_{\mathrm{pos}}\) and \(r_{\mathrm{neg}} \ge 0.20\)) → strong at \|60\| → ordinary at \|20\| → else `neutral`. |
| **SCR-12** | Phrase intensity = 100 × mean of \(\min(1, hits/2)\) over \(n_{\mathrm{eff}}\). |
| **SCR-13** | Confidence = 100 × coverage(\(N_{\mathrm{full}}=40\)) × (0.40 + 0.60 × agreement) × (1 − 0.50 × empty-text share). |

---

## Explicitly deferred

| Topic | Owner |
|-------|--------|
| `SocialMention` / `ScoreBundle` / `MatchBatch` / `BaselineBundle` field lists | doc 05 |
| Provider engagement mapping and fetch isolation | doc 06 |
| Matching, collisions, `market_wide` partition | doc 07 |
| Social heat, velocity, acceleration, source agreement | doc 09 |
| `duplicate_content_ratio`, `organic_score`, burst/manipulation | doc 10 |
| Combining per-source scores; `scope`; when aggregation keeps or overrides `insufficient_data` | doc 11 |
| Redis keys/TTLs | doc 12 |
| Full `build_social_sentiment` JSON and `data_quality` scales | doc 14 |
| Exact 15m/1h/6h/24h horizon weight tables | doc 15 |
