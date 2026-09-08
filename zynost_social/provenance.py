"""Honest run diary (docs/14). Pure: no Redis, no network, no wall-clock."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from zynost_social.models import (
    AggregateResult,
    MatchBatch,
    ProvenanceRecord,
    ProviderFetchOutcome,
    ProviderId,
    SourceBundle,
)

PROVIDER_ORDER: Final[tuple[ProviderId, ...]] = ("x", "reddit", "telegram", "discord")


def build_provenance(
    outcomes: Sequence[ProviderFetchOutcome],
    match_batch: MatchBatch,
    aggregate: AggregateResult,
    *,
    served_from_cache: bool,
    observed_at: datetime,
    duplicate_content_ratio: float | None = None,
) -> ProvenanceRecord:
    """Map retained source rows and the match partition into a diary (FR-PROV-02–03).

    ``observed_at`` is the run's collection ``t_0`` for the engine/contract. Freshness
    ``τ`` is the worst stored ``SourceBundle.freshness_seconds`` among available rows,
    never ``datetime.now()`` (CCH-17). A Redis miss is ``served_from_cache=False``
    and is not a provider outcome (CCH-13).
    """
    del observed_at
    rows = _status_rows(aggregate.sources, outcomes)
    available, unavailable = _partition_providers(rows)
    n_a = len(match_batch.asset_mentions)
    unique_authors = len({mention.author_id_hash for mention in match_batch.asset_mentions})
    return ProvenanceRecord(
        providers_available=available,
        providers_unavailable=unavailable,
        observation_count=n_a,
        unique_authors=unique_authors,
        duplicate_content_ratio=None if n_a == 0 else duplicate_content_ratio,
        freshness_seconds=_tau(rows),
        served_from_cache=served_from_cache,
        asset_match_score=match_batch.asset_match_score,
    )


def _status_rows(
    sources: Sequence[SourceBundle],
    outcomes: Sequence[ProviderFetchOutcome],
) -> tuple[SourceBundle, ...]:
    """Prefer retained aggregate rows (AGG-04). Outcomes only if sources were not kept."""
    if sources:
        return tuple(sources)
    return tuple(
        SourceBundle(
            provider=outcome.provider,
            status=outcome.status,
            mention_count=len(outcome.mentions),
            unique_authors=len({m.author_id_hash for m in outcome.mentions}),
            freshness_seconds=outcome.freshness_seconds,
            error_class=outcome.error_class,
        )
        for outcome in outcomes
    )


def _partition_providers(
    rows: Sequence[SourceBundle],
) -> tuple[tuple[ProviderId, ...], tuple[ProviderId, ...]]:
    by_id: dict[ProviderId, SourceBundle] = {}
    for row in rows:
        by_id.setdefault(row.provider, row)
    available: list[ProviderId] = []
    unavailable: list[ProviderId] = []
    for provider in PROVIDER_ORDER:
        bundle = by_id.get(provider)
        if bundle is None:
            continue
        if bundle.status == "available":
            available.append(provider)
        else:
            unavailable.append(provider)
    return tuple(available), tuple(unavailable)


def _tau(rows: Sequence[SourceBundle]) -> float | None:
    """Worst live lag among available rows; null if A=0 or every available lag is unknown."""
    lags = [
        row.freshness_seconds
        for row in rows
        if row.status == "available" and row.freshness_seconds is not None
    ]
    if not lags:
        return None
    return max(lags)
