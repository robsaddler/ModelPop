"""Searching every configured source at once, and ranking what comes back.

The use case behind the gallery. Three things it must get right, none of them
obvious from the happy path:

**One dead source must not kill the search.** Sources fail independently and
often - an expired key, a rate limit, a site having a bad afternoon. Anything
that answered is shown, and anything that did not is reported by name.

**The licensing clause is asked once, and then never again.** It gates the
feature, not the results: once ticked, nothing is blocked, filtered or hidden.

**Nothing found is not an error.** It is the signal to fall through to
generation, carrying what the user typed, because they have just told us what
they did *not* mean.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from modelpop.domain.discovery import Candidate, Scored, SearchQuery, rank
from modelpop.domain.licensing import CLAUSE_VERSION, Acceptance
from modelpop.domain.result import Result, failure, success

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from modelpop.application.repository_ports import (
        AcceptanceStore,
        Download,
        ModelRepository,
    )

__all__ = ["Discovery", "SearchOutcome"]

# Enough to fill a gallery without making the user scroll for an hour, and
# enough that ranking has something to do.
DEFAULT_LIMIT = 40

# Per source. Asking four sources for forty each and then ranking beats asking
# four for ten, because a source that is strong on this particular query should
# be allowed to dominate the gallery on merit.
PER_SOURCE_LIMIT = 25


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """Everything a search produced, including what went wrong.

    Failures are carried alongside results rather than replacing them. A search
    where Thingiverse answered and Printables timed out has real results *and* a
    real problem, and collapsing that into either "success" or "failure" throws
    away half of what the user needs to know.
    """

    results: tuple[Scored, ...] = ()
    query: SearchQuery = field(default_factory=SearchQuery)
    searched: tuple[str, ...] = ()
    """Sources that answered."""

    skipped: tuple[str, ...] = ()
    """Sources that are not set up, and so were not asked."""

    failed: tuple[tuple[str, str], ...] = ()
    """Sources that were asked and could not answer, with the reason."""

    @property
    def found_anything(self) -> bool:
        """Whether there is something to show."""
        return bool(self.results)

    @property
    def nothing_worked(self) -> bool:
        """Whether every source that was asked failed.

        Different from finding nothing: an empty gallery because four sources
        are down should not be read as "no such model exists".
        """
        return bool(self.failed) and not self.searched

    def summary(self) -> str:
        """A line for the status bar."""
        if self.nothing_worked:
            return "No source could be reached."
        if not self.found_anything:
            where = " and ".join(self.searched) if self.searched else "any source"
            return f"Nothing on {where} matched. Try generating it instead."
        line = f"{len(self.results)} found across {len(self.searched)} source(s)."
        if self.failed:
            line += f" {len(self.failed)} source(s) could not be reached."
        return line


class Discovery:
    """Search every configured source, rank the results, fetch a chosen one."""

    def __init__(
        self,
        repositories: list[ModelRepository] | None = None,
        acceptance: AcceptanceStore | None = None,
    ) -> None:
        """Wire the service to its sources.

        Args:
            repositories: every source, configured or not. Unconfigured ones
                are skipped at search time rather than filtered out here, so
                the user can be told which are missing a key.
            acceptance: where the licensing tick is remembered.
        """
        self._repositories = repositories or []
        self._acceptance = acceptance

    # ------------------------------------------------------------ licensing

    @property
    def needs_acceptance(self) -> bool:
        """Whether the clause must be shown before searching.

        True when nothing is stored, and true again if the wording changes.
        """
        return not self.acceptance().is_current

    def acceptance(self) -> Acceptance:
        """What the user has accepted so far."""
        if self._acceptance is None:
            return Acceptance()
        return self._acceptance.load()

    def accept(self) -> Result[None]:
        """Record that the user ticked the box."""
        if self._acceptance is None:
            return failure(
                "Acceptance cannot be saved",
                "no store was configured, so the clause would be shown every time.",
            )
        return self._acceptance.save(Acceptance.now(CLAUSE_VERSION))

    # --------------------------------------------------------------- search

    @property
    def sources(self) -> tuple[str, ...]:
        """The names of every source, whether configured or not."""
        return tuple(repo.name for repo in self._repositories)

    @property
    def searchable_sources(self) -> tuple[str, ...]:
        """The sources that can actually be queried right now."""
        return tuple(r.name for r in self._repositories if r.is_configured() and r.can_search())

    def search(self, request: str, limit: int = DEFAULT_LIMIT) -> Result[SearchOutcome]:
        """Search every configured source at once and rank what comes back.

        Sources are queried in parallel because they are network-bound and
        independent, and the slowest would otherwise set the pace for all of
        them. Failures are collected rather than raised.
        """
        if self.needs_acceptance:
            return failure(
                "The licensing terms have not been accepted",
                "Show the clause and record the tick before searching.",
            )

        query = SearchQuery.parse(request)
        if query.is_empty:
            return failure(
                "There is nothing to search for",
                "Describe the model you want - 'a dragon about six inches tall'.",
            )

        usable = [r for r in self._repositories if r.is_configured() and r.can_search()]
        skipped = tuple(
            r.name for r in self._repositories if not (r.is_configured() and r.can_search())
        )
        if not usable:
            return success(SearchOutcome(query=query, skipped=skipped))

        found: list[Candidate] = []
        searched: list[str] = []
        failed: list[tuple[str, str]] = []

        with ThreadPoolExecutor(max_workers=len(usable)) as pool:
            jobs = {pool.submit(self._ask, repo, query): repo for repo in usable}
            for job in jobs:
                repo = jobs[job]
                outcome = job.result()
                if outcome.ok:
                    found.extend(outcome.unwrap())
                    searched.append(repo.name)
                else:
                    failed.append((repo.name, outcome.error))

        return success(
            SearchOutcome(
                results=tuple(rank(found, query, limit)),
                query=query,
                searched=tuple(sorted(searched)),
                skipped=skipped,
                failed=tuple(sorted(failed)),
            )
        )

    @staticmethod
    def _ask(repo: ModelRepository, query: SearchQuery) -> Result[list[Candidate]]:
        """Query one source, turning any surprise into a ``Failure``.

        An adapter should already return a ``Result``, but a third-party HTTP
        client throwing something undocumented must not take out the pool
        thread and, with it, every other source's results.
        """
        try:
            return repo.search(query, PER_SOURCE_LIMIT)
        except Exception as error:
            return failure(f"{repo.name} could not be searched", str(error))

    # ---------------------------------------------------------------- fetch

    def resolve(self, url: str) -> Result[Candidate]:
        """Turn a pasted page URL into a candidate.

        Tried against every source in turn: the URL itself says which site it
        belongs to, and asking each is simpler than maintaining a table of
        domain patterns here that would drift from the adapters.
        """
        if not url.strip():
            return failure("Nothing was pasted")
        if self.needs_acceptance:
            return failure(
                "The licensing terms have not been accepted",
                "Show the clause and record the tick first.",
            )

        problems: list[str] = []
        for repo in self._repositories:
            if not repo.is_configured():
                continue
            outcome = repo.resolve(url)
            if outcome.ok:
                return outcome
            problems.append(f"{repo.name}: {outcome.error}")

        return failure(
            "That link was not recognised",
            "; ".join(problems) if problems else "no source is configured.",
        )

    def fetch(
        self,
        candidate: Candidate,
        into: Path,
        on_progress: Callable[[float], None] | None = None,
    ) -> Result[Download]:
        """Download a chosen candidate."""
        for repo in self._repositories:
            if repo.name == candidate.source:
                return repo.fetch(candidate, into, on_progress)
        return failure(
            f"{candidate.source} is no longer available",
            "The source this model came from is not configured.",
        )
