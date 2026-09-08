"""Public orchestrator. Pipeline not implemented in this slice (LLD-04)."""

from zynost_social.models import AssetIdentity


async def build_social_sentiment(
    asset: AssetIdentity,
    horizon: str = "swing",
) -> dict[str, object]:
    """Return a JSON-safe contract dict. Implementation follows later modules."""
    raise NotImplementedError("engine pipeline not implemented yet")
