"""Reddit adapter. Mapping: docs/06. Credentials: CFG-03 Reddit set."""

from zynost_social.models import AssetIdentity, SocialMention
from zynost_social.providers.base import SocialProvider


class RedditProvider(SocialProvider):
    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        raise NotImplementedError("Reddit adapter fetch not implemented yet")
