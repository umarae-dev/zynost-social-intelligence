"""Provider package. Concrete adapters implement SocialProvider (LLD-01)."""

from zynost_social.providers.base import SocialProvider, collect_isolated

__all__ = ["SocialProvider", "collect_isolated"]
