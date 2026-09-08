"""Deterministic polar + momentum scoring. Formulas: docs/08, 09, 15."""

from collections.abc import Sequence

from zynost_social.models import BaselineBundle, ScoreBundle, SocialMention


def score(
    mentions: Sequence[SocialMention],
    horizon: str,
    baselines: BaselineBundle,
) -> ScoreBundle:
    raise NotImplementedError("scoring not implemented yet")
