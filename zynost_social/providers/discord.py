"""Discord adapter (authorized API only). Mapping: docs/06."""

from zynost_social.models import AssetIdentity, SocialMention
from zynost_social.providers.base import SocialProvider


class DiscordProvider(SocialProvider):
    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        raise NotImplementedError("Discord adapter fetch not implemented yet")
