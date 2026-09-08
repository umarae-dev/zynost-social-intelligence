# 00 — Project Vision & Overview

> *Traces to specification §1 (Purpose) and the introductory framing.*
> This document is the **"why"** of the project. It is intentionally vision-level:
> no formulas, schemas, or code — those belong to the later numbered documents.

---

## Vision statement

The Zynost Social Intelligence Engine is a deterministic, manipulation-resistant
engine that turns noisy multi-source crypto social chatter into trustworthy,
asset-specific social **context** — never a trading signal.

---

## Problem & motivation

Crypto price action is heavily influenced by crowd behavior, but raw social
chatter is a poor source of truth on its own:

- **It is noisy.** Mentions of a coin are buried in unrelated conversation,
  off-topic memes, and generic market talk that isn't actually about the asset.
- **It is ambiguous.** Many tickers collide with common words or other projects,
  so naive keyword matching attributes chatter to the wrong asset.
- **It is manipulated.** Pump campaigns, coordinated bursts, bot flooding,
  copied shill text, and engagement farming are designed to *look* like organic
  enthusiasm. A raw volume or sentiment count rewards exactly this behavior.
- **It is fragmented.** No single platform tells the whole story, and any one
  source can be rate-limited, unauthorized, or offline at any moment.

Because of this, consuming social data naively is worse than useless — it is
actively misleading. What is needed is a dedicated intelligence layer that
identifies the asset correctly, separates organic discussion from manufactured
noise, and reports how confident it is in what it found. That is the gap this
engine exists to fill.

---

## What it is

A **production-grade, standalone** Python 3.12+, **async-first**, multi-source
crypto **Social Sentiment Intelligence Engine**. For a specific crypto asset it:

1. **Collects** social activity from multiple independent sources,
2. **Normalizes** it into a common shape,
3. **Cleans** it (deduplication, asset matching, noise removal),
4. **Scores** it deterministically for sentiment, activity, momentum, and
   manipulation risk, and
5. **Aggregates** the results into a single, JSON-serializable context object
   with explicit provenance and data-quality reporting.

Every result is traceable: which sources responded, how the asset was matched,
how fresh the data is, and how much of it appears organic.

---

## What it is NOT

- It is **not a trading signal engine.**
- It **never** outputs `BUY`, `SELL`, `LONG`, or `SHORT`.
- **Positive sentiment ≠ bullish price direction.** High social heat or positive
  sentiment is *context about the crowd*, not a prediction about price.
- It does **not** predict prices, recommend trades, or use any LLM to interpret
  meaning or decide direction.

The engine describes what the crowd is doing and how much that description can be
trusted. Deciding what a given social state *means for price* is explicitly the
job of downstream consumers, not this engine.

---

## Role within Zynost

The engine is the next major **social sentiment intelligence** component in the
Zynost platform, joining the **17 existing** specialized intelligence
agents/modules. Its role is **context, not signals**: it contributes one more
independent, well-characterized view of an asset alongside the other components.

It is designed as a clean, standalone module that will later **plug into the
existing FastAPI + PostgreSQL + Redis + Celery backend** and align with the
future **Canonical Asset Registry**, without requiring changes to that backend at
build time.

---

## Value it provides

For a given asset, the engine measures and reports:

- **Crowd sentiment** — the overall positive/neutral/negative tone of discussion.
- **Social activity** — how much the asset is actually being talked about.
- **Social momentum & mention velocity** — whether that activity is accelerating
  or fading, and how fast.
- **Engagement** — how much attention the discussion is attracting.
- **Organic vs. manipulated chatter** — how much of the activity looks genuine
  versus coordinated, copied, or bot-driven.
- **Source agreement / disagreement** — whether the independent sources tell a
  consistent story or contradict each other.
- **Anomalies** — unusual bursts or patterns worth flagging (never automatically
  treated as bullish).

---

## Guiding principles

- **Deterministic (no LLM).** Scoring is deterministic and provider-driven, so
  results are reproducible and explainable — no LLM anywhere in the pipeline.
- **Multi-source with graceful degradation.** Sources are fetched independently;
  one failing source never takes the engine down. Partial coverage yields a
  partial result with reduced confidence; total unavailability is reported
  honestly rather than faked.
- **Manipulation-resistant.** Manufactured hype is detected and down-weighted;
  volume alone is never mistaken for genuine, organic interest.
- **Privacy-conscious.** The engine avoids retaining unnecessary personal data
  and prefers pseudonymous/hashed identifiers.
- **Standalone and non-invasive.** No deployment, no production changes, no
  access to the main Zynost backend; it is built and audited in isolation.
- **Honest about data.** It never invents missing data and never fabricates or
  backfills history; every result carries its own quality and provenance.

---

## Success vision

When integrated, "good" looks like this: given any supported asset and horizon,
the engine returns a single, trustworthy context object that correctly
identifies the asset, distinguishes organic discussion from manipulation,
degrades gracefully when sources are unavailable, and clearly communicates how
confident it is — giving the rest of the Zynost platform a reliable read on the
social crowd **without ever pretending to be a trade recommendation.**
