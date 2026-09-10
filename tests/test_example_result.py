"""Handoff example contract (docs/21 HAN-06, INT-03, INT-06)."""

from __future__ import annotations

import json
from pathlib import Path

from zynost_social.engine import REQUIRED_KEYS

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "example_result.json"
TRADE_TOKENS = ("BUY", "SELL", "LONG", "SHORT")


def test_example_result_is_contract_shaped_and_secret_free() -> None:
    blob = EXAMPLE.read_text(encoding="utf-8")
    result = json.loads(blob)
    assert isinstance(result, dict)
    assert set(result) == set(REQUIRED_KEYS)
    assert result["name"] == "social_sentiment"
    assert result["role"] == "context"
    assert result["source_class"] == "multi_source_social_intelligence"
    assert result["status"] == "available"
    assert result["scope"] == "asset_specific"
    sources = result["sources"]
    assert isinstance(sources, dict)
    assert set(sources) == {"x", "reddit", "telegram", "discord"}
    quality = result["data_quality"]
    assert isinstance(quality, dict)
    for key in (
        "provider_coverage",
        "freshness_score",
        "asset_match_score",
        "organic_data_ratio",
    ):
        assert key in quality
    json.dumps(result)
    for token in TRADE_TOKENS:
        assert token not in blob
    assert "http://" not in blob.lower()
    assert "https://" not in blob.lower()
