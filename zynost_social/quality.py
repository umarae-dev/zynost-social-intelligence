"""data_quality scales (docs/14). Pure: no Redis, no network, no wall-clock."""

from __future__ import annotations

import math
from typing import Final

from zynost_social.models import (
    AggregateResult,
    DataQuality,
    ManipulationAssessment,
    MatchBatch,
    ProvenanceRecord,
)
from zynost_social.provenance import PROVIDER_ORDER

T_SAT_SECONDS: Final = 3600.0  # QLY-02: MOM current 1h; not a Redis TTL (CCH-09)
P_REG: Final = len(PROVIDER_ORDER)  # MOD-05 closed set; do not shrink (QLY-01)


def compute_data_quality(
    provenance: ProvenanceRecord,
    match_batch: MatchBatch,
    aggregate: AggregateResult,
    assessment: ManipulationAssessment,
) -> DataQuality:
    """Map the run diary into FR-PROV-01 scales (QLY-01–06).

    ``MatchBatch`` and ``ManipulationAssessment`` stay on the LLD signature for
    the engine. Organic ratio is ``AggregateResult.organic_score / 100`` only
    when there is asset evidence (QLY-04); empty-batch ``organic_score = 100``
    is not a public organic claim (MAN-12). Coverage is reported, never
    multiplied onto ``aggregate.confidence`` (QLY-05). ``τ`` is the stored
    diary lag, never ``datetime.now()`` (CCH-17).
    """
    del match_batch
    del assessment
    available = len(provenance.providers_available)
    n_a = provenance.observation_count
    tau = provenance.freshness_seconds
    organic: float | None = None
    if n_a >= 1 and available >= 1:
        organic = _clip(aggregate.organic_score / 100.0, 0.0, 1.0)
    return DataQuality(
        provider_coverage=available / P_REG,
        freshness_score=_freshness_score(tau),
        asset_match_score=provenance.asset_match_score,
        organic_data_ratio=organic,
        providers_available=provenance.providers_available,
        providers_unavailable=provenance.providers_unavailable,
        observation_count=n_a,
        unique_authors=provenance.unique_authors,
        duplicate_content_ratio=provenance.duplicate_content_ratio,
        freshness_seconds=tau,
        served_from_cache=provenance.served_from_cache,
    )


def _freshness_score(tau: float | None) -> float | None:
    if tau is None:
        return None
    return _clip(100.0 * (1.0 - tau / T_SAT_SECONDS), 0.0, 100.0)


def _clip(value: float, lo: float, hi: float) -> float:
    if value != value or value in (math.inf, -math.inf):
        return lo
    return max(lo, min(hi, value))
