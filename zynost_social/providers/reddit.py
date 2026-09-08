"""Reddit adapter. Mapping: docs/06. Credentials: CFG-03 Reddit set."""

from zynost_social.models import AssetIdentity, ProviderId, SocialMention
from zynost_social.providers.base import SocialProvider


class RedditProvider(SocialProvider):
    @property
    def provider(self) -> ProviderId:
        return "reddit"

    @property
    def credential_names(self) -> tuple[str, ...]:
        return ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        raise NotImplementedError("Reddit adapter fetch not implemented yet")
