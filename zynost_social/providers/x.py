"""X/Twitter adapter. Mapping: docs/06. Credentials: X_API_KEY + X_BEARER_TOKEN."""

from zynost_social.models import AssetIdentity, ProviderId, SocialMention
from zynost_social.providers.base import SocialProvider


class XProvider(SocialProvider):
    @property
    def provider(self) -> ProviderId:
        return "x"

    @property
    def credential_names(self) -> tuple[str, ...]:
        return ("X_API_KEY", "X_BEARER_TOKEN")

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        raise NotImplementedError("X adapter fetch not implemented yet")
