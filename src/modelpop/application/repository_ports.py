"""The ports for finding and fetching existing models.

Two capabilities, deliberately separate. :class:`ModelRepository` searches a
source and hands back normalised candidates; :class:`AcceptanceStore` remembers
that the user ticked the licensing box. They are apart because they fail
independently: a source can be down, or the user can simply not have agreed yet,
and the application must tell those two things apart.

A repository adapter is also allowed to be *partial*. Some sources have no
sanctioned API at all, in which case the adapter accepts a pasted link rather
than running a query. ``can_search`` says which kind it is, so the UI can offer
a search box or a paste box rather than offering both and failing at one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from modelpop.domain.discovery import Candidate, SearchQuery
from modelpop.domain.licensing import Acceptance
from modelpop.domain.result import Result

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "AcceptanceStore",
    "Download",
    "ModelRepository",
    "ProgressReport",
]


@dataclass(frozen=True, slots=True)
class Download:
    """A file fetched from a repository, and where it came from.

    The attribution travels with the bytes. A remix that loses its provenance
    two operations later is the normal outcome unless provenance is part of the
    payload rather than something the user is asked to remember.
    """

    path: Path
    candidate: Candidate

    @property
    def attribution(self) -> str:
        """The credit line to carry into the document and any export."""
        return self.candidate.attribution


@dataclass(frozen=True, slots=True)
class ProgressReport:
    """How a fan-out across several sources is going.

    Reported per source rather than as one number, because a search where three
    sources answered and one timed out is a different thing from a search that
    found nothing, and the user should be able to see which happened.
    """

    source: str
    finished: bool = False
    found: int = 0
    error: str = ""


@runtime_checkable
class ModelRepository(Protocol):
    """One source of existing models."""

    @property
    def name(self) -> str:
        """What to call this source on a gallery card."""
        ...

    def is_configured(self) -> bool:
        """Whether this source can be used right now.

        False when it needs an API key the user has not supplied. A source that
        is not configured is skipped silently rather than reported as an error:
        it is not broken, it is just not set up.
        """
        ...

    def can_search(self) -> bool:
        """Whether this source supports querying, as opposed to link import only.

        Sources with no sanctioned API are integrated by letting the user paste
        a link. That is a real integration, not a failure, and the UI needs to
        know the difference.
        """
        ...

    def search(self, query: SearchQuery, limit: int = 20) -> Result[list[Candidate]]:
        """Find candidates matching a query.

        A source that is down is a ``Failure``, not an exception: one dead
        source must not take the whole gallery with it.
        """
        ...

    def fetch(
        self,
        candidate: Candidate,
        into: Path,
        on_progress: Callable[[float], None] | None = None,
    ) -> Result[Download]:
        """Download a candidate's model file."""
        ...

    def resolve(self, url: str) -> Result[Candidate]:
        """Turn a pasted page URL into a candidate.

        The whole integration for a source with no search API, and useful even
        where there is one: people find models in a browser and arrive with a
        link.
        """
        ...


@runtime_checkable
class AcceptanceStore(Protocol):
    """Remembers that the user accepted the licensing clause."""

    def load(self) -> Acceptance:
        """What the user has accepted, or an empty record if nothing yet."""
        ...

    def save(self, acceptance: Acceptance) -> Result[None]:
        """Record an acceptance."""
        ...
