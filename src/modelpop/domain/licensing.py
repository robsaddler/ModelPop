"""Licences on third-party models, and the user's acceptance of the risk.

ModelPop's job here is to **inform, not police**. A licence badge on a gallery
card is genuinely useful; a file the app refuses to open is not. Rob was
explicit about this: one clearly worded clause, one tick, then get out of the
way.

So nothing in this module blocks anything. :class:`Licence` exists to be *shown*
and to nudge ranking - a model you are allowed to modify should sort above one
you are not, when you have said you intend to modify it. That is a nudge, not a
gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

__all__ = ["CLAUSE_VERSION", "THE_CLAUSE", "Acceptance", "Licence", "Permission"]


class Permission(Enum):
    """What a licence allows, as far as we can tell from the metadata.

    ``UNKNOWN`` is a real answer and the honest default. A source that does not
    say is different from one that says no, and showing "unknown" is better
    than guessing either way.
    """

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"

    @property
    def badge(self) -> str:
        """A word for the gallery card."""
        return {Permission.YES: "allowed", Permission.NO: "not allowed", Permission.UNKNOWN: "?"}[
            self
        ]


@dataclass(frozen=True, slots=True)
class Licence:
    """What a repository told us about a model's terms.

    Stored as the source's own wording plus our reading of it. Keeping the raw
    string matters: our reading can be wrong, and the user deserves to see what
    the site actually said rather than only our summary of it.
    """

    raw: str = ""
    """Exactly what the source called it, e.g. "Creative Commons - Attribution"."""

    short: str = "Unknown"
    """A badge-sized name, e.g. "CC BY-NC-ND"."""

    derivatives: Permission = Permission.UNKNOWN
    """Whether the terms appear to allow modification."""

    commercial: Permission = Permission.UNKNOWN
    """Whether the terms appear to allow commercial use."""

    attribution_required: bool = False
    url: str = ""
    """Where the terms are stated, when the source gives a link."""

    @property
    def is_remixable(self) -> bool:
        """Whether modifying this model looks permitted.

        Unknown counts as remixable for *ranking* purposes, because burying
        everything a source failed to label would empty the gallery. The badge
        still says unknown, so the user is not misled.
        """
        return self.derivatives is not Permission.NO

    @property
    def badge(self) -> str:
        """The short label for a gallery card."""
        return self.short or "Unknown"

    def describe(self) -> str:
        """A sentence for the detail panel."""
        if not self.raw and self.short == "Unknown":
            return "This source did not state a licence."
        parts = [self.raw or self.short]
        if self.derivatives is Permission.NO:
            parts.append("modification is not permitted")
        elif self.derivatives is Permission.YES:
            parts.append("modification is permitted")
        if self.attribution_required:
            parts.append("attribution required")
        return "; ".join(parts) + "."


CLAUSE_VERSION = 1
"""Bumped only when the wording below changes, which re-prompts the user."""

THE_CLAUSE = """\
Models found through ModelPop are published by third parties under their own \
licences. Some licences permit modification and sharing; others, including any \
marked ND (No Derivatives), do not. Brand, character and franchise designs may \
also be protected by trademark or copyright regardless of the file's licence.

ModelPop shows you the licence it was given. It does not verify that licence, \
and it cannot give legal advice.

You are responsible for ensuring your use of any model - printing, modifying, \
sharing or selling - complies with its licence and with applicable law."""


@dataclass(frozen=True, slots=True)
class Acceptance:
    """A record that the user read the clause and ticked the box.

    Stored with the version so a change of wording asks again, and with the
    timestamp so there is an audit trail. Nothing is blocked once it exists;
    its only job is to stop the app asking twice.
    """

    version: int = 0
    accepted_at: datetime | None = None

    @classmethod
    def now(cls, version: int = CLAUSE_VERSION) -> Acceptance:
        """Record acceptance of the current clause."""
        return cls(version=version, accepted_at=datetime.now(UTC))

    @property
    def is_current(self) -> bool:
        """Whether this acceptance covers the clause as it is worded today."""
        return self.accepted_at is not None and self.version >= CLAUSE_VERSION
