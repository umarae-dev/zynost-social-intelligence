"""Discord adapter (authorized API only). Mapping: docs/06."""

from zynost_social.models import AssetIdentity, ProviderId, SocialMention
from zynost_social.providers.base import SocialProvider


class DiscordProvider(SocialProvider):
    @property
    def provider(self) -> ProviderId:
        return "discord"

    @property
    def credential_names(self) -> tuple[str, ...]:
        return ("DISCORD_BOT_TOKEN",)

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        raise NotImplementedError("Discord adapter fetch not implemented yet")
