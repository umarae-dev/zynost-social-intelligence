"""Shared immutable records (docs/05). No I/O, Redis, formulas, or provider clients."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal

ProviderId = Literal["x", "reddit", "telegram", "discord"]
FetchStatus = Literal["available", "unavailable"]
ErrorClass = Literal["not_configured", "unauthorized", "timeout", "malformed", "rate_limited"]
Classification = Literal[
    "strong_positive",
    "positive",
    "neutral",
    "mixed",
    "negative",
    "strong_negative",
    "insufficient_data",
]
Scope = Literal["asset_specific", "market_wide"]
Horizon = Literal["intraday", "swing", "position"]
AccelerationState = Literal["accelerating", "holding", "fading"]
JsonScalar = str | int | float | bool | None
MetadataValue = JsonScalar | tuple[JsonScalar, ...]


def _require_nonempty(name: str, value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must be a non-empty string")
    return stripped


def _aware_utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


def _freeze_str_seq(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(values)


def _freeze_metadata(
    metadata: Mapping[str, MetadataValue | Sequence[JsonScalar]],
) -> Mapping[str, MetadataValue]:
    frozen: dict[str, MetadataValue] = {}
    for key, raw in metadata.items():
        if isinstance(raw, (str, int, float, bool)) or raw is None:
            frozen[key] = raw
        else:
            frozen[key] = tuple(raw)
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetIdentity:
    """Canonical asset. Never ticker-only (MOD-04): asset_id + name + symbol required."""

    asset_id: str
    symbol: str
    name: str
    aliases: tuple[str, ...] = ()
    chain: str | None = None
    contract_address: str | None = None
    official_x_accounts: tuple[str, ...] = ()
    official_reddit: tuple[str, ...] = ()
    official_telegram: tuple[str, ...] = ()
    official_discord: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _require_nonempty("asset_id", self.asset_id))
        object.__setattr__(self, "symbol", _require_nonempty("symbol", self.symbol))
        object.__setattr__(self, "name", _require_nonempty("name", self.name))
        object.__setattr__(self, "aliases", _freeze_str_seq(self.aliases))
        object.__setattr__(self, "official_x_accounts", _freeze_str_seq(self.official_x_accounts))
        object.__setattr__(self, "official_reddit", _freeze_str_seq(self.official_reddit))
        object.__setattr__(self, "official_telegram", _freeze_str_seq(self.official_telegram))
        object.__setattr__(self, "official_discord", _freeze_str_seq(self.official_discord))


@dataclass(frozen=True, slots=True, kw_only=True)
class SocialMention:
    """Normalized mention after adapter mapping. No raw provider payloads (MOD-07)."""

    provider: ProviderId
    external_id: str
    asset_id: str
    text: str
    author_id_hash: str
    created_at: datetime
    engagement: float
    url_hash: str | None = None
    matched_terms: tuple[str, ...] = ()
    match_confidence: float = 0.0
    match_rationale: str = ""
    language: str | None = None
    metadata: Mapping[str, MetadataValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "external_id", _require_nonempty("external_id", self.external_id))
        object.__setattr__(self, "asset_id", _require_nonempty("asset_id", self.asset_id))
        object.__setattr__(
            self,
            "author_id_hash",
            _require_nonempty("author_id_hash", self.author_id_hash),
        )
        object.__setattr__(self, "created_at", _aware_utc("created_at", self.created_at))
        if self.engagement < 0:
            raise ValueError("engagement must be >= 0")
        if not 0.0 <= self.match_confidence <= 1.0:
            raise ValueError("match_confidence must be in [0.0, 1.0]")
        object.__setattr__(self, "matched_terms", _freeze_str_seq(self.matched_terms))
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True, kw_only=True)
class SocialSentimentSnapshot:
    """Compact evidence row for later consumer persistence. Shape only (SNP / MOD)."""

    asset_id: str
    observed_at: datetime
    sentiment_score: float
    social_heat: float
    mention_count: int
    mention_velocity: float
    organic_score: float
    manipulation_risk: float
    source_coverage: float
    source_agreement: float
    classification: Classification

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _require_nonempty("asset_id", self.asset_id))
        object.__setattr__(self, "observed_at", _aware_utc("observed_at", self.observed_at))
        if not -100.0 <= self.sentiment_score <= 100.0:
            raise ValueError("sentiment_score must be in [-100, +100]")
        if self.mention_count < 0:
            raise ValueError("mention_count must be >= 0")
        if not 0.0 <= self.source_coverage <= 1.0:
            raise ValueError("source_coverage must be in [0.0, 1.0]")
        if not 0.0 <= self.source_agreement <= 1.0:
            raise ValueError("source_agreement must be in [0.0, 1.0]")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderFetchOutcome:
    """Isolation result. available + [] is quiet success, not unavailable (MOD-08)."""

    provider: ProviderId
    status: FetchStatus
    mentions: tuple[SocialMention, ...] = ()
    error_class: ErrorClass | None = None
    freshness_seconds: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mentions", tuple(self.mentions))
        if self.status == "available" and self.error_class is not None:
            raise ValueError("available outcome must not set error_class")
        if self.status == "unavailable" and self.error_class is None:
            raise ValueError("unavailable outcome requires error_class")


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchBatch:
    asset_mentions: tuple[SocialMention, ...] = ()
    market_wide: tuple[SocialMention, ...] = ()
    asset_match_score: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_mentions", tuple(self.asset_mentions))
        object.__setattr__(self, "market_wide", tuple(self.market_wide))


@dataclass(frozen=True, slots=True, kw_only=True)
class BaselineBundle:
    """Priors from cache. None means unknown, not a quiet zero (MOM-04, CCH-14)."""

    observed_at: datetime | None = None
    prior_mentions_1h: int | None = None
    prior_mentions_6h: int | None = None
    prior_mentions_24h: int | None = None
    prior_engagement_1h: float | None = None
    prior_sentiment_score: float | None = None
    prior_observed_at: datetime | None = None

    @classmethod
    def unknown(cls) -> BaselineBundle:
        """Redis miss / cache disabled: all windows unknown, no invented history."""
        return cls()


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoreBundle:
    sentiment_score: float
    classification: Classification
    confidence: float
    social_heat: float
    mention_velocity: float
    social_acceleration: float
    mention_count: int
    unique_authors: int
    horizon: Horizon
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    positive_ratio: float = 0.0
    neutral_ratio: float = 0.0
    negative_ratio: float = 0.0
    engagement_weighted_sentiment: float = 0.0
    bullish_phrase_intensity: float = 0.0
    bearish_phrase_intensity: float = 0.0
    fear_intensity: float = 0.0
    greed_excitement_intensity: float = 0.0
    uncertainty_intensity: float = 0.0
    euphoria_intensity: float = 0.0
    mentions_1h: int = 0
    mentions_1h_previous: int | None = None
    mentions_6h: int = 0
    mentions_24h: int = 0
    mentions_6h_change: float = 0.0
    mentions_24h_change: float = 0.0
    engagement_velocity: float = 0.0
    acceleration_state: AccelerationState = "holding"
    source_agreement: float = 0.0
    source_disagreement: float = 0.0
    freshness_seconds: float | None = None
    activity_coverage: float = 0.0
    sentiment_change: float | None = None
    sentiment_change_hours: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ManipulationAssessment:
    duplicate_content_ratio: float
    unique_author_ratio: float
    engagement_concentration: float
    author_concentration: float
    suspicious_burst_score: float
    spam_bot_suspicion: float
    manipulation_risk: float
    organic_score: float
    anomaly_score: float


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceBundle:
    provider: ProviderId
    status: FetchStatus
    mention_count: int = 0
    unique_authors: int = 0
    sentiment_score: float | None = None
    freshness_seconds: float | None = None
    error_class: ErrorClass | None = None

    def __post_init__(self) -> None:
        if self.status == "unavailable" and self.error_class is None:
            raise ValueError("unavailable source row requires error_class")
        if self.status == "available" and self.error_class is not None:
            raise ValueError("available source row must not set error_class")


@dataclass(frozen=True, slots=True, kw_only=True)
class AnomalyRecord:
    """JSON-safe anomaly row (AGG-15). Never trade language."""

    type: str
    provider: ProviderId | None
    score: float
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AggregateResult:
    scope: Scope
    classification: Classification
    confidence: float
    social_heat: float
    mention_velocity: float
    organic_score: float
    manipulation_risk: float
    source_agreement: float
    source_disagreement: float = 0.0
    mention_count: int
    sentiment_score: float | None = None
    sources: tuple[SourceBundle, ...] = ()
    anomalies: tuple[AnomalyRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "anomalies", tuple(self.anomalies))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProvenanceRecord:
    providers_available: tuple[ProviderId, ...]
    providers_unavailable: tuple[ProviderId, ...]
    observation_count: int
    unique_authors: int
    duplicate_content_ratio: float | None
    freshness_seconds: float | None
    served_from_cache: bool
    asset_match_score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "providers_available", tuple(self.providers_available))
        object.__setattr__(self, "providers_unavailable", tuple(self.providers_unavailable))


@dataclass(frozen=True, slots=True, kw_only=True)
class DataQuality:
    provider_coverage: float
    freshness_score: float | None
    asset_match_score: float
    organic_data_ratio: float | None
    providers_available: tuple[ProviderId, ...]
    providers_unavailable: tuple[ProviderId, ...]
    observation_count: int
    unique_authors: int
    duplicate_content_ratio: float | None
    freshness_seconds: float | None
    served_from_cache: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "providers_available", tuple(self.providers_available))
        object.__setattr__(self, "providers_unavailable", tuple(self.providers_unavailable))
