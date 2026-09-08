"""Telegram public-channel adapter. Mapping: docs/06."""

from zynost_social.models import AssetIdentity, ProviderId, SocialMention
from zynost_social.providers.base import SocialProvider


class TelegramProvider(SocialProvider):
    @property
    def provider(self) -> ProviderId:
        return "telegram"

    @property
    def credential_names(self) -> tuple[str, ...]:
        return ("TELEGRAM_API_ID", "TELEGRAM_API_HASH")

    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        raise NotImplementedError("Telegram adapter fetch not implemented yet")
