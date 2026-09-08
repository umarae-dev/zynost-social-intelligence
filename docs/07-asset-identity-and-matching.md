# 07 — Asset Identity & Matching

> *Traces to specification §3 (canonical identity, collision avoidance).*
> This is the **matching-algorithm design** beneath `03-high-level-design.md` (**HLD-01**, matching stage) and `04-low-level-design.md` (`matching.py`, `match_mentions`). It defines *how* a normalized mention is decided to belong to the requested asset. It does **not** redefine `AssetIdentity` / `SocialMention` / `MatchBatch` field lists (doc 05, **MOD-04**–**MOD-06**), provider I/O or fetch isolation (doc 06), scoring formulas/weights (docs 08–10, 15), aggregation/scope math in full (doc 11), cache keys (doc 12), or the integration-contract JSON (doc 14).

---

## Role relative to other docs

Doc 05 owns the **shape** of `AssetIdentity`, `SocialMention`, and `MatchBatch`. Doc 04 (**LLD** matching module) freezes the module boundary: `matching.py` runs after normalization and before scoring, does not fetch, does not score (`FR-ID-02`–`04`, `FR-SCOPE-01`–`03`). This doc owns the **decision logic** inside that boundary: which signals count, how collisions are rejected, and what evidence every match must carry.

---

## 1. Identity signals used for matching *(spec §3, `FR-ID-01`)*

Matching consumes the full `AssetIdentity` (doc 05, §1), not a single field. Each signal below contributes independently; none is sufficient alone.

| Signal | Source field | Role in matching |
|--------|-------------|-------------------|
| Canonical id | `asset_id` | Join key only — never inferred from mention text, always supplied by the caller. |
| Official name | `name` | Strong signal; matched as a phrase, not a substring of unrelated words. |
| Symbol/ticker | `symbol` | One weak signal among many — see §2. |
| Aliases | `aliases` | Same treatment as `name`: alternate names/spellings/tickers the project is actually known by. |
| Chain/network | `chain` | Context signal — raises confidence when mention text also references the stated chain. |
| Contract address | `contract_address` | Strong signal when present in mention text; an exact contract-address hit is treated as high-confidence identity, not a coincidence. |
| Official social accounts | `official_x_accounts`, `official_reddit`, `official_telegram`, `official_discord` | Strong signal when a mention originates from, replies to, or explicitly references one of these known official identities. |
| Surrounding crypto context | derived from mention `text` | Supporting signal: presence of asset-domain terminology (project-specific terms, chain name, ecosystem vocabulary) alongside a weak signal (e.g. bare symbol) raises confidence; absence lowers it. |
| Project terminology | derived from `name` / `aliases` | Same supporting role as crypto context — project-specific words used the way the community actually uses them. |

**MAT-01 — Multi-signal only.** A match decision is a function of **combined** signals (name/alias/contract/official-account presence plus optional supporting context), never a single field taken in isolation. This implements `FR-ID-01`.

---

## 2. Never ticker-only *(FR-ID-02, MOD-04)*

`symbol` is one input signal, not identity. A bare ticker occurrence in mention text is **never**, by itself, sufficient to accept a mention as asset-specific.

**MAT-02 — Symbol gating rule.** A symbol-only occurrence (ticker present, no name/alias/contract/official-account signal, no supporting crypto context) is rejected as asset-specific and — if it looks like generic crypto chatter — is a candidate for `market_wide` (§4), never for `asset_mentions`. `matching.py` MUST require at least one additional corroborating signal (name/alias phrase, contract address, official-account reference, or clear surrounding crypto context naming the project) before a symbol occurrence counts toward a match.

This is stricter for the collision-prone symbols in §3.

---

## 3. Collision avoidance *(FR-ID-03)*

The following symbols are **known collision risks** because they double as common English words or as tickers for multiple unrelated projects: `ONE`, `IN`, `ARB`, `TON`, `OP`, `NEAR`, `APT`, `LINK`.

| Symbol | Collision source | Why ticker-only fails |
|--------|-------------------|------------------------|
| `ONE` | Common English word ("Harmony ONE" vs. ordinary usage of "one") | Nearly every sentence contains the word "one"; a bare occurrence carries ~no identity signal. |
| `IN` | Common English preposition | Occurs in almost all text; unusable as a standalone signal. |
| `ARB` | Arbitrum token symbol vs. common abbreviation for "arbitrage"/"arbitrator" | Trading/finance discussion uses "arb" generically, unrelated to the Arbitrum project. |
| `TON` | Toncoin symbol vs. unit of weight / colloquial "a ton of X" | Common in unrelated quantity/intensity phrasing. |
| `OP` | Optimism symbol vs. gaming/forum slang ("OP" = "original poster", "overpowered") | Extremely common outside crypto, especially on Reddit. |
| `NEAR` | Near Protocol symbol vs. common English preposition/adverb | Occurs constantly in ordinary sentences. |
| `APT` | Aptos symbol vs. common abbreviation ("apartment", "apt" = "apt description") | Ambiguous outside a crypto-specific context. |
| `LINK` | Chainlink symbol vs. the generic noun/verb "link" (hyperlink, "link below") | One of the most common English words on social platforms. |

**MAT-03 — Collision rejection rule.** For any asset whose `symbol` (or an alias) is in this list, `matching.py` MUST NOT accept a match based on the symbol/alias occurrence alone, regardless of supporting-context thresholds used for non-collision symbols elsewhere. Acceptance requires **at least one** of: an exact `name` phrase match, a `contract_address` occurrence, an official-account reference, or multiple independent corroborating context terms (not a single generic crypto word). A single generic crypto word ("crypto", "token", "coin") next to the bare symbol is **not** sufficient corroboration for a collision-prone symbol.

**MAT-04 — No invented disambiguation.** Rejecting a collision-prone occurrence never fabricates an alternate identity for it (e.g. guessing the mention is "about arbitrage, not Arbitrum"). The engine simply does not attribute the mention to the requested asset; it may still be considered for `market_wide` if it is genuine crypto chatter (§4). This keeps `CON-04` intact — no invented identity in either direction.

This list is the **minimum** set (`FR-ID-03` says "at least"); the same gating rule extends to any future symbol identified as ambiguous without a schema change (doc 05 §1 already notes this is a matching concern, not a schema enum).

---

## 4. `MatchBatch`: asset-specific vs market-wide *(FR-SCOPE-01–03)*

`match_mentions(asset, mentions) -> MatchBatch` (doc 04, doc 05 §4) partitions normalized mentions into:

- **`asset_mentions`** — passed §1–§3 gating; each carries `matched_terms`, `match_confidence`, `match_rationale` (§5).
- **`market_wide`** — recognizable generic crypto/market chatter that did **not** pass asset-specific gating (e.g. rejected collision-prone symbol occurrence, or crypto discussion with no asset signal at all). Optional; never treated as evidence about the requested asset.
- **`asset_match_score`** — a single match-quality input consumed by `quality.py` (doc 05 §4, `FR-PROV-01`); this document does not define its scale (owned alongside doc 14).

**MAT-05 — No relabeling market-wide as asset-specific.** Nothing downstream (scoring, aggregation) may promote a `market_wide` mention into the asset-specific set. That boundary is decided once, in `matching.py`, and is final for the run (`FR-SCOPE-03`, **LLD-04**).

**MAT-06 — Insufficient evidence is a matching/quality input, not a fabricated match.** If gating rejects most or all mentions for an asset (e.g. a quiet or heavily ambiguous-symbol asset), `matching.py` returns a small or empty `asset_mentions` list and a correspondingly low `asset_match_score`. It MUST NOT lower the gating bar to manufacture enough "matches" to look complete. Downstream, this low count/score is exactly the input that leads aggregation toward `insufficient_data` (`FR-SCOPE-02`, doc 11) — matching does not decide that label itself, it only supplies honest evidence.

The distinction between `asset_specific` and `market_wide` **result scope** (the aggregate-level field in the public contract) is owned by doc 11; this document owns only the mention-level partition that feeds it.

---

## 5. Match evidence on every mention *(FR-ID-04, MOD-06)*

Every mention placed in `asset_mentions` MUST carry three fields, filled together by `matching.py` and only by `matching.py`:

| Field | Meaning |
|-------|---------|
| `matched_terms` | The literal terms/signals that justified the match (e.g. the alias string, the contract address, the official-account handle, matched context terms). |
| `match_confidence` | A `[0.0, 1.0]` strength for this specific match (distinct from the public contract `confidence`, doc 14). |
| `match_rationale` | A short, human-readable statement of *why* it matched (e.g. "alias phrase + chain context", "official account reply", "contract address exact match"). Not a formula dump — a short justification string. |

**MAT-07 — Pre-match vs post-match ownership.** Providers (`x.py`, `reddit.py`, `telegram.py`, `discord.py`) normalize mentions with these three fields **empty/default** (`matched_terms = []`, `match_confidence = 0`, `match_rationale = ""`); they MUST NOT set them (`PRV-07`, doc 06). Only `matching.py` fills them, and only once a mention clears §1–§3 gating. This keeps identity decisions in one place and keeps provider adapters free of matching logic (**LLD-04**).

`match_confidence` is a matching-internal strength score. It answers "how sure is the identity match", not "how sentimentally significant is this mention" — the two are never conflated.

---

## 6. Determinism and no invention *(CON-01, CON-04, HLD-03)*

**MAT-08 — Deterministic, rule-based matching.** Matching is pure: same `AssetIdentity` + same mention text → same gating decision, same `matched_terms`/`match_confidence`/`match_rationale`, every time (`NFR-DET-01`, **HLD-03**). No LLM or generative step is used to decide or explain a match (`CON-01`).

**MAT-09 — Never invent identity.** `matching.py` consumes `AssetIdentity` exactly as supplied. It MUST NOT guess a missing alias, contract address, or official account, and MUST NOT widen the collision-prone list's gating bar to compensate for a sparsely populated identity (`CON-04`). An asset with few identity signals simply matches fewer mentions with lower confidence — that is honest evidence, not a defect to paper over.

---

## 7. Plugging into the future Canonical Asset Registry *(FR-ID-05)*

**MAT-10 — Consumer, not owner, of identity.** `matching.py` treats `AssetIdentity` as an opaque input value. It has no knowledge of *where* the identity came from (hand-authored config today, a future Canonical Asset Registry lookup tomorrow) and holds no mutable registry of its own (**MOD-09**, `NFR-STATE-01`). Swapping the identity source later requires no change to gating rules, collision handling, or evidence fields — only the caller that constructs `AssetIdentity` changes. This is the same boundary doc 05 already fixes at the schema level (`FR-ID-05`); this document keeps the algorithm side of that boundary equally decoupled.

---

## Decision index

| ID | Decision |
|----|----------|
| **MAT-01** | Matching combines multiple identity signals; no single field decides a match. |
| **MAT-02** | A bare symbol occurrence needs at least one corroborating signal before counting as a match. |
| **MAT-03** | Collision-prone symbols (`ONE, IN, ARB, TON, OP, NEAR, APT, LINK`) require strong corroboration (name/contract/official-account/multiple context terms), never symbol-alone or a single generic crypto word. |
| **MAT-04** | Rejecting a collision-prone occurrence never fabricates an alternate meaning for it. |
| **MAT-05** | `market_wide` mentions are never promoted to asset-specific downstream. |
| **MAT-06** | Low/zero match yield is honest evidence for `quality.py`/aggregation, not a reason to loosen gating. |
| **MAT-07** | Only `matching.py` sets `matched_terms` / `match_confidence` / `match_rationale`; providers leave them at default. |
| **MAT-08** | Matching is deterministic and rule-based — no LLM. |
| **MAT-09** | Matching never invents identity fields or official accounts beyond what `AssetIdentity` supplies. |
| **MAT-10** | `matching.py` consumes `AssetIdentity` as an opaque value; no owned mutable registry, ready for a future Canonical Asset Registry swap. |
| **MAT-11** | Per-mention `match_confidence` is the max fired class (contract 0.95 … symbol+generic 0.45); batch `asset_match_score` is the QLY-03 mean. |

---

## 8. Implemented confidence classes *(this slice)*

`matching.py` assigns **one** `match_confidence` per accepted mention: the **maximum** class that fired (never a sum of overlapping signals).

| Class | Confidence | When it fires |
|-------|------------|----------------|
| Contract address exact (case-insensitive substring) | `0.95` | `contract_address` occurs in text |
| Official account origin or reference | `0.85` | Text `@handle` / `r/` / `t.me/` / snowflake, or metadata `subreddit` / `channel` / `channel_id` / `guild_id` matches an official identity |
| Official name phrase | `0.80` | `name` as a whole-word phrase |
| Non-collision alias phrase | `0.70` | `aliases` phrase that is not in the collision set |
| Non-collision symbol + chain or project term | `0.60` | Ticker plus chain or a name/alias token of length ≥ 4 |
| Collision symbol + multiple context terms | `0.55` | Collision ticker plus ≥ 1 non-generic context (chain or project term) **and** ≥ 2 independent context terms total |
| Non-collision symbol + generic crypto word | `0.45` | Ticker plus at least one generic crypto/market term |

`MatchBatch.asset_match_score` is the **QLY-03** mean of those confidences, or `0` when `asset_mentions` is empty. Off-topic text is dropped; generic crypto chatter that fails gating goes to `market_wide` with empty match evidence.

---

## Explicitly deferred

| Topic | Owner |
|-------|-------|
| `AssetIdentity` / `SocialMention` / `MatchBatch` field-level schemas | doc 05 |
| Provider query construction and fetch isolation | doc 06 |
| Sentiment/momentum/manipulation formulas | docs 08–10, 15 |
| Aggregate-level `scope` computation and `insufficient_data` rules in full | doc 11 |
| Redis cache keys/TTLs | doc 12 |
| `asset_match_score` numeric scale and full `data_quality` object | doc 14 |
