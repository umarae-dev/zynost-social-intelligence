"""data_quality scales. Scales: docs/14."""

from zynost_social.models import (
    AggregateResult,
    DataQuality,
    ManipulationAssessment,
    MatchBatch,
    ProvenanceRecord,
)


def compute_data_quality(
    provenance: ProvenanceRecord,
    match_batch: MatchBatch,
    aggregate: AggregateResult,
    assessment: ManipulationAssessment,
) -> DataQuality:
    raise NotImplementedError("data quality not implemented yet")
