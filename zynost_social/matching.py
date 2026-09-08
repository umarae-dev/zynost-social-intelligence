"""Asset matching. Algorithms: docs/07."""

from collections.abc import Sequence

from zynost_social.models import AssetIdentity, MatchBatch, SocialMention


def match_mentions(
    asset: AssetIdentity,
    mentions: Sequence[SocialMention],
) -> MatchBatch:
    raise NotImplementedError("matching not implemented yet")
