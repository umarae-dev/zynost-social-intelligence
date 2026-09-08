"""Per-source combine and scope. Rules: docs/11."""

from collections.abc import Sequence

from zynost_social.models import AggregateResult, MatchBatch, SourceBundle


def aggregate(
    per_source: Sequence[SourceBundle],
    match_batch: MatchBatch,
    horizon: str,
) -> AggregateResult:
    raise NotImplementedError("aggregation not implemented yet")
