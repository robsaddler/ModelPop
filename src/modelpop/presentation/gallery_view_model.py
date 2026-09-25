"""The view-model behind the gallery.

Like the workspace view-model, it imports no UI framework, so the whole journey
- tick the clause, search, look at cards, pick one, download it, open it - is
drivable by a test with no display and no network.

The states it can be in are named rather than inferred from a pile of booleans,
because the gallery has genuinely different empty states and they need different
words. "Nothing matched" and "every source is down" look identical if you only
count results, and telling the user the wrong one is worse than saying nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

from modelpop.application.discovery_service import Discovery, SearchOutcome
from modelpop.domain.discovery import Candidate

if TYPE_CHECKING:
    from pathlib import Path

    from modelpop.application.repository_ports import Download

__all__ = ["Card", "GalleryState", "GalleryViewModel", "Phase"]

type Runner = Callable[[Callable[[], None]], None]


def run_inline(work: Callable[[], None]) -> None:
    """Run work on the calling thread. The default, and what tests use."""
    work()


class Phase(Enum):
    """Which face the gallery is currently showing."""

    NEEDS_ACCEPTANCE = "needs-acceptance"
    """The licensing clause has not been ticked yet."""

    READY = "ready"
    """Waiting for a search."""

    SEARCHING = "searching"
    RESULTS = "results"
    NOTHING_MATCHED = "nothing-matched"
    """Sources answered, and none had anything. Offer generation instead."""

    ALL_SOURCES_DOWN = "all-sources-down"
    """Nobody answered. Not the same thing, and not the user's problem to solve."""

    NO_SOURCES = "no-sources"
    """Nothing is configured. Point at Settings."""


@dataclass(frozen=True, slots=True)
class Card:
    """One gallery tile, with everything needed to draw it already worked out.

    The view-model does this rather than the widget so the wording can be
    tested. A licence badge that says the wrong thing is a real problem, and
    finding out by looking at a screenshot is not a test strategy.
    """

    candidate: Candidate
    reasons: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        """What to print across the top of the tile."""
        return self.candidate.title

    @property
    def subtitle(self) -> str:
        """Creator and source, on one line."""
        who = self.candidate.creator or "unknown creator"
        return f"{who} on {self.candidate.source}"

    @property
    def licence_badge(self) -> str:
        """The short licence name for the corner of the tile."""
        return self.candidate.licence.badge

    @property
    def licence_detail(self) -> str:
        """The full sentence, for a tooltip or the detail panel."""
        return self.candidate.licence.describe()

    @property
    def warns_about_derivatives(self) -> bool:
        """Whether to mark the tile as one the user may not modify.

        A mark, not a block. Rob was explicit: inform, do not police.
        """
        return not self.candidate.licence.is_remixable

    @property
    def popularity(self) -> str:
        """Downloads, in a form a human reads at a glance."""
        count = self.candidate.downloads
        if count <= 0:
            return ""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M downloads"
        if count >= 1_000:
            return f"{count / 1_000:.0f}k downloads"
        return f"{count} downloads"

    @property
    def why(self) -> str:
        """Why this tile is where it is, shown under the thumbnail."""
        return ", ".join(self.reasons)


@dataclass(frozen=True, slots=True)
class GalleryState:
    """Everything the gallery shows."""

    phase: Phase = Phase.READY
    cards: tuple[Card, ...] = ()
    request: str = ""
    """What the user typed, kept so generation can be offered the same words."""

    selected: int = -1
    message: str = ""
    problems: tuple[str, ...] = ()
    """Sources that could not be reached, in words fit for the screen."""

    unconfigured: tuple[str, ...] = ()

    fetching: float = -1.0
    """How far a download has got, 0 to 1. Negative when nothing is downloading.

    A model can be tens of megabytes over somebody else's CDN, and a window
    that says "Downloading..." for a minute with nothing moving is
    indistinguishable from one that has hung.
    """

    @property
    def is_fetching(self) -> bool:
        """Whether a download is in flight."""
        return self.fetching >= 0.0

    @property
    def has_results(self) -> bool:
        """Whether there is anything to click."""
        return bool(self.cards)

    @property
    def chosen(self) -> Card | None:
        """The selected card, if one is selected."""
        if 0 <= self.selected < len(self.cards):
            return self.cards[self.selected]
        return None

    @property
    def can_use_selection(self) -> bool:
        """Whether "use as a base" should be enabled."""
        return self.chosen is not None

    @property
    def offers_generation(self) -> bool:
        """Whether to show the "generate it instead" way out.

        Offered when the search worked and found nothing, and *not* when the
        sources were unreachable - the user has learned nothing in that case,
        and pushing them to spend credits on it would be wrong.
        """
        return self.phase is Phase.NOTHING_MATCHED


class GalleryViewModel:
    """Observable state and commands for the discovery gallery."""

    def __init__(self, discovery: Discovery, runner: Runner = run_inline) -> None:
        """Create the view-model.

        Args:
            discovery: the use-case layer.
            runner: how to execute long work. Defaults to running inline, which
                is what tests use; the UI supplies a threaded one.
        """
        self._discovery = discovery
        self._runner = runner
        self._state = GalleryState(phase=self._opening_phase())
        self._listeners: list[Callable[[GalleryState], None]] = []

    # ------------------------------------------------------------- observing

    @property
    def state(self) -> GalleryState:
        """What the gallery is showing."""
        return self._state

    @property
    def clause_needs_showing(self) -> bool:
        """Whether to put the licensing clause in front of the user."""
        return self._discovery.needs_acceptance

    def on_change(self, listener: Callable[[GalleryState], None]) -> None:
        """Be told whenever the state changes."""
        self._listeners.append(listener)

    # -------------------------------------------------------------- commands

    def accept_terms(self) -> None:
        """Record the tick and open the gallery."""
        outcome = self._discovery.accept()
        if not outcome.ok:
            # The clause was read and ticked. Failing to *write that down* is
            # our problem, not a reason to make the user read it again now.
            self._set(
                replace(
                    self._state,
                    phase=self._phase_for_sources(),
                    message=outcome.error,
                )
            )
            return
        self._set(replace(self._state, phase=self._phase_for_sources(), message=""))

    def search(self, request: str) -> None:
        """Search every configured source and fill the gallery."""
        if not request.strip():
            return

        self._set(
            replace(
                self._state,
                phase=Phase.SEARCHING,
                request=request,
                cards=(),
                selected=-1,
                problems=(),
                message="Searching...",
            )
        )
        self._runner(lambda: self._finish_search(request))

    def select(self, index: int) -> None:
        """Pick a card, or clear the selection with a negative index."""
        if index >= len(self._state.cards):
            return
        self._set(replace(self._state, selected=index))

    def clear(self) -> None:
        """Empty the gallery and go back to waiting."""
        self._set(
            GalleryState(
                phase=self._phase_for_sources(),
                unconfigured=self._state.unconfigured,
            )
        )

    def download_selected(self, into: Path, then: Callable[[Download], None] | None = None) -> None:
        """Fetch the chosen model, and hand it on when it arrives."""
        chosen = self._state.chosen
        if chosen is None:
            return

        def moved(fraction: float) -> None:
            # Announced as it goes, not just at the end: the file comes over
            # somebody else's CDN and can take a minute, and a window that
            # says "Downloading..." with nothing moving looks hung.
            self._set(
                replace(
                    self._state,
                    fetching=fraction,
                    message=f"Downloading {chosen.title}... {fraction * 100:.0f}%",
                )
            )

        def work() -> None:
            outcome = self._discovery.fetch(chosen.candidate, into, moved)
            if not outcome.ok:
                self._set(replace(self._state, fetching=-1.0, message=outcome.error))
                return
            download = outcome.unwrap()
            self._set(replace(self._state, fetching=-1.0, message=f"Downloaded {chosen.title}."))
            if then is not None:
                then(download)

        self._set(replace(self._state, fetching=0.0, message=f"Downloading {chosen.title}..."))
        self._runner(work)

    def paste_link(self, url: str) -> None:
        """Turn a pasted page URL into a single-card gallery.

        The whole integration for a source with no sanctioned search API, and
        useful everywhere else too: people find models in a browser.
        """
        if not url.strip():
            return

        def work() -> None:
            outcome = self._discovery.resolve(url)
            if not outcome.ok:
                self._set(
                    replace(
                        self._state,
                        phase=Phase.NOTHING_MATCHED,
                        message=outcome.error,
                    )
                )
                return
            card = Card(outcome.unwrap(), ("you pasted this link",))
            self._set(
                replace(
                    self._state,
                    phase=Phase.RESULTS,
                    cards=(card,),
                    selected=0,
                    message="",
                )
            )

        self._set(replace(self._state, phase=Phase.SEARCHING, message="Looking that up..."))
        self._runner(work)

    # -------------------------------------------------------------- internal

    def _finish_search(self, request: str) -> None:
        outcome = self._discovery.search(request)
        if not outcome.ok:
            self._set(
                replace(
                    self._state,
                    phase=self._opening_phase(),
                    message=outcome.error,
                )
            )
            return
        self._set(self._state_for(outcome.unwrap(), request))

    def _state_for(self, found: SearchOutcome, request: str) -> GalleryState:
        cards = tuple(Card(s.candidate, s.reasons) for s in found.results)
        problems = tuple(f"{name} could not be reached: {why}" for name, why in found.failed)

        if cards:
            phase = Phase.RESULTS
        elif found.nothing_worked:
            phase = Phase.ALL_SOURCES_DOWN
        elif not found.searched:
            phase = Phase.NO_SOURCES
        else:
            phase = Phase.NOTHING_MATCHED

        return GalleryState(
            phase=phase,
            cards=cards,
            request=request,
            selected=0 if cards else -1,
            message=found.summary(),
            problems=problems,
            unconfigured=found.skipped,
        )

    def _opening_phase(self) -> Phase:
        if self._discovery.needs_acceptance:
            return Phase.NEEDS_ACCEPTANCE
        return self._phase_for_sources()

    def _phase_for_sources(self) -> Phase:
        return Phase.READY if self._discovery.searchable_sources else Phase.NO_SOURCES

    def _set(self, state: GalleryState) -> None:
        self._state = state
        for listener in self._listeners:
            listener(state)
