"""Shared Redis adapter. Keys/TTLs: docs/12. No process-memory truth (LLD-08)."""

from collections.abc import Sequence

from zynost_social.models import AggregateResult, AssetIdentity, BaselineBundle, SocialMention


async def get_raw(asset: AssetIdentity, provider: str) -> Sequence[SocialMention] | None:
    raise NotImplementedError("cache not implemented yet")


async def set_raw(asset: AssetIdentity, provider: str, payload: Sequence[SocialMention]) -> None:
    raise NotImplementedError("cache not implemented yet")


async def get_aggregate(asset: AssetIdentity, horizon: str) -> AggregateResult | None:
    raise NotImplementedError("cache not implemented yet")


async def set_aggregate(asset: AssetIdentity, horizon: str, payload: AggregateResult) -> None:
    raise NotImplementedError("cache not implemented yet")


async def get_baselines(asset: AssetIdentity) -> BaselineBundle:
    raise NotImplementedError("cache not implemented yet")


async def set_baselines(asset: AssetIdentity, baselines: BaselineBundle) -> None:
    raise NotImplementedError("cache not implemented yet")
