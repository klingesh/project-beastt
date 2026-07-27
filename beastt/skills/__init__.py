"""BEASTT's skills -- small, deterministic abilities that run before the LLM.

A skill can *handle* an utterance directly (e.g. "what time is it") and return
an instant answer, saving a round-trip to the model. Skills are easy to add:
subclass `Skill`, implement `matches` and `run`, and register it below.
"""

from typing import List

from .base import Skill
from .time_skill import TimeSkill
from .datetime_skill import DateSkill
from .identity_skill import IdentitySkill


def default_skills(config) -> List[Skill]:
    """Return the set of skills BEASTT starts with."""
    return [
        IdentitySkill(config),
        TimeSkill(),
        DateSkill(),
    ]


__all__ = ["Skill", "TimeSkill", "DateSkill", "IdentitySkill", "default_skills"]
