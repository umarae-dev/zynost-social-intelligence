"""Honest run diary. Mapping: docs/14."""

from collections.abc import Sequence
from datetime import datetime

from zynost_social.models import (
    AggregateResult,
    MatchBatch,
    ProvenanceRecord,
    ProviderFetchOutcome,
)


def build_provenance(
    outcomes: Sequence[ProviderFetchOutcome],
    match_batch: MatchBatch,
    aggregate: AggregateResult,
    *,
    served_from_cache: bool,
    observed_at: datetime,
) -> ProvenanceRecord:
    raise NotImplementedError("provenance not implemented yet")
