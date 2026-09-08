"""Manipulation / organic-quality assessment. Formulas: docs/10."""

from collections.abc import Sequence

from zynost_social.models import ManipulationAssessment, SocialMention


def assess(mentions: Sequence[SocialMention]) -> ManipulationAssessment:
    raise NotImplementedError("manipulation assessment not implemented yet")
