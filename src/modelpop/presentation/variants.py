"""Several answers to the same question, and the ones already given.

A generative model asked twice gives two different shapes. That is not a fault
to be hidden behind a single result - it is the *useful* property, because the
first shape is rarely the best one and there is no way to tell without seeing
the others. So a generation can produce several candidates, and this is what
holds them.

It is also the history. A variant and an earlier generation are the same thing
from the interface's point of view - a shape the application made, that you
might want back - so there is one list rather than two, and a batch of variants
is simply several entries arriving together.

Deliberately **not saved to disk**. These are meshes, several megabytes each,
produced by a run the user has not yet decided anything about. What survives a
restart is what was saved on purpose; everything else is the cost of one more
generation, which is a minute rather than an afternoon.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modelpop.domain.mesh import Mesh

__all__ = ["KEEP_AT_MOST", "GenerationHistory", "Variant"]

# Each of these is a mesh of a few megabytes held in memory. Ten is more than
# anybody compares at once and small enough not to matter; past it the oldest
# goes, because the one from four generations ago is not what anybody means by
# "go back".
KEEP_AT_MOST = 10


@dataclass(frozen=True, slots=True)
class Variant:
    """One shape the application produced, and where it came from."""

    mesh: Mesh
    provenance: str
    """Where this came from, in the same words the document would record."""

    label: str = ""
    """What to call it in the list. Falls back to something from the facts."""

    seed: int = 0
    """Which seed produced it, so a good one can be asked for again.

    The single most useful thing to keep. A shape you liked and cannot
    reproduce is worse than never having seen it.
    """

    seconds: float = 0.0
    made_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def title(self) -> str:
        """What to show in the list."""
        return self.label or f"{self.mesh.triangle_count:,} triangles"

    @property
    def subtitle(self) -> str:
        """The facts worth comparing two candidates on.

        Size and triangle count, because those are what differ between
        variants in a way that matters for printing one.
        """
        bounds = self.mesh.bounds
        size = (
            f"{bounds.width.format(places=0)} x "
            f"{bounds.depth.format(places=0)} x "
            f"{bounds.height.format(places=0)}"
        )
        said = f"{self.mesh.triangle_count:,} triangles, {size}"
        return f"{said}, seed {self.seed}" if self.seed else said

    def describe(self) -> str:
        """The whole thing, for a tooltip or the status bar."""
        return f"{self.title} - {self.subtitle}. {self.provenance}"


class GenerationHistory:
    """What the application has made this session, newest first.

    No UI framework and no adapter, so the whole of "generate three, look at
    the second, go back to the first" is drivable by a test with no display and
    no GPU.
    """

    def __init__(self, keep: int = KEEP_AT_MOST) -> None:
        """Start empty, keeping at most so many."""
        self._keep = max(1, keep)
        self._variants: list[Variant] = []
        self._chosen: int | None = None

    def __len__(self) -> int:
        """How many are being held."""
        return len(self._variants)

    def __iter__(self) -> Iterator[Variant]:
        """Newest first, which is the order they are shown in."""
        return iter(self._variants)

    @property
    def variants(self) -> tuple[Variant, ...]:
        """Everything held, newest first."""
        return tuple(self._variants)

    @property
    def is_empty(self) -> bool:
        """Whether anything has been made yet."""
        return not self._variants

    # --------------------------------------------------------------- adding

    def add(self, variant: Variant) -> None:
        """Record one shape, and select it.

        Selecting it is the point: something the application has just made is
        what the user is looking at, so the list must agree with the viewport
        rather than needing a click to catch up.
        """
        self._variants.insert(0, variant)
        del self._variants[self._keep :]
        self._chosen = 0

    def add_all(self, variants: Iterable[Variant]) -> None:
        """Record a batch, and select the first of them.

        The first rather than the last, because a batch is shown newest-first
        and the first one asked for is the one at the top.
        """
        added = list(variants)
        for variant in reversed(added):
            self.add(variant)
        if added:
            self._chosen = 0

    # -------------------------------------------------------------- choosing

    @property
    def chosen(self) -> Variant | None:
        """The one currently being looked at."""
        if self._chosen is None or not 0 <= self._chosen < len(self._variants):
            return None
        return self._variants[self._chosen]

    @property
    def chosen_index(self) -> int | None:
        """Which one is selected, for a list widget to highlight."""
        return self._chosen if self.chosen is not None else None

    def choose(self, index: int) -> Variant | None:
        """Select one by position, and hand it back.

        Returns ``None`` for an index that is not there rather than raising: a
        stale click from a list that has since been trimmed is an ordinary
        thing, not a programmer error.
        """
        if not 0 <= index < len(self._variants):
            return None
        self._chosen = index
        return self._variants[index]

    def clear(self) -> None:
        """Forget everything."""
        self._variants.clear()
        self._chosen = None

    # -------------------------------------------------------------- reading

    def describe(self) -> str:
        """A line for the panel."""
        if self.is_empty:
            return "Nothing generated yet."
        count = len(self._variants)
        noun = "shape" if count == 1 else "shapes"
        at_cap = " (oldest are dropped)" if count >= self._keep else ""
        return f"{count} {noun} made this session{at_cap}."
