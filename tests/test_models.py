"""Frozen model invariants (docs/05). No provider I/O."""

from datetime import UTC, datetime

import pytest

from zynost_social.models import (
    AssetIdentity,
    BaselineBundle,
    ProviderFetchOutcome,
    SocialMention,
)


def test_asset_identity_requires_canonical_fields() -> None:
    with pytest.raises(ValueError):
        AssetIdentity(asset_id=" ", symbol="BTC", name="Bitcoin")
    with pytest.raises(ValueError):
        AssetIdentity(asset_id="btc", symbol="", name="Bitcoin")
    with pytest.raises(ValueError):
        AssetIdentity(asset_id="btc", symbol="BTC", name="  ")


def test_asset_identity_is_frozen() -> None:
    asset = AssetIdentity(asset_id="btc", symbol="BTC", name="Bitcoin", aliases=["bitcoin"])
    assert asset.aliases == ("bitcoin",)
    with pytest.raises(AttributeError):
        asset.symbol = "ETH"  # type: ignore[misc]


def test_mention_match_rationale_is_first_class() -> None:
    mention = SocialMention(
        provider="x",
        external_id="1",
        asset_id="btc",
        text="bitcoin hash rate",
        author_id_hash="abc",
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        engagement=0.0,
        matched_terms=("bitcoin",),
        match_confidence=0.8,
        match_rationale="official name in text",
    )
    assert mention.match_rationale == "official name in text"


def test_available_empty_mentions_is_not_unavailable() -> None:
    outcome = ProviderFetchOutcome(provider="reddit", status="available", mentions=())
    assert outcome.status == "available"
    assert outcome.mentions == ()
    assert outcome.error_class is None


def test_unavailable_requires_error_class() -> None:
    with pytest.raises(ValueError):
        ProviderFetchOutcome(provider="x", status="unavailable")


def test_baseline_unknown_is_all_none() -> None:
    bundle = BaselineBundle.unknown()
    assert bundle.prior_mentions_1h is None
    assert bundle.prior_mentions_6h is None
    assert bundle.prior_mentions_24h is None
    assert bundle.prior_engagement_1h is None
    assert bundle.prior_sentiment_score is None
    assert bundle.observed_at is None
