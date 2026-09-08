"""Shared adapter contract and isolation helpers (LLD-01, LLD-05)."""

from abc import ABC, abstractmethod

from zynost_social.models import AssetIdentity, ProviderFetchOutcome, SocialMention


class SocialProvider(ABC):
    @abstractmethod
    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        """Normalized mentions only. [] means success with zero mentions (LLD-02)."""


async def collect_isolated(
    provider: SocialProvider,
    asset: AssetIdentity,
    since_minutes: int,
    timeout_s: float,
) -> ProviderFetchOutcome:
    raise NotImplementedError("provider isolation not implemented yet")
