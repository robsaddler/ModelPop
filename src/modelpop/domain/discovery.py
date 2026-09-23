"""Finding an existing model to start from.

Starting from a good human-made model and changing it beats generating one from
scratch on quality almost every time, and costs no GPU time and no credits. So
for most requests this should be the *first* thing the app tries.

Everything here is pure: a query, a candidate, and the arithmetic that orders
them. No network, no file I/O. The sources live behind a port in the
application layer, which is what lets the ranking be tested against a handful of
hand-written candidates instead of a live API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import log10

from modelpop.domain.licensing import Licence
from modelpop.domain.units import Length

__all__ = ["Candidate", "Scored", "SearchQuery", "merge", "rank"]

# Words that say *what* rather than *which*, and so should not be matched on.
# "a model of a dragon about six inches tall" is a request for a dragon.
_NOISE_WORDS = """
    a an the of my me i we want need like some any thing something please can you
    model models print printable printed stl 3mf file files design make made create
    for with and or in on at to about roughly around approximately
    tall high wide deep long size sized big small his her its that this
"""
_NOISE = frozenset(_NOISE_WORDS.split())

_SIZE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mm|millimetre|millimeter|cm|centimetre|centimeter|m\b|"
    r"in|inch|inches|ft|foot|feet)\b",
    re.IGNORECASE,
)

_UNITS = {
    "mm": "mm",
    "millimetre": "mm",
    "millimeter": "mm",
    "cm": "cm",
    "centimetre": "cm",
    "centimeter": "cm",
    "m": "m",
    "in": "in",
    "inch": "in",
    "inches": "in",
    "ft": "ft",
    "foot": "ft",
    "feet": "ft",
}

_COLOUR_WORDS = """
    black white grey gray red orange yellow green blue purple pink brown
    silver gold bronze copper transparent clear glow matte
"""
_COLOURS = frozenset(_COLOUR_WORDS.split())

# Words that describe a change rather than a thing. A request full of them is a
# remix, which should nudge permissively licensed models up the gallery.
_EDITING_WORDS = (
    "hollow",
    "engrav",
    "emboss",
    "across",
    "my name",
    "custom",
    "personalis",
    "personaliz",
    "remove",
    "modif",
    "change",
    "remix",
    "split",
    "instead of",
)


def _is_measurement(word: str) -> bool:
    """Whether a token is a number, with or without a unit stuck to it.

    "200mm" arrives as one token, so stripping bare digits is not enough. A
    measurement is a filter on the results, never the subject of the search.
    """
    stripped = word.rstrip("".join(_UNITS))
    return stripped.replace(".", "", 1).isdigit() or word.replace(".", "", 1).isdigit()


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """What the user asked for, pulled apart into things a search can use.

    Built by :meth:`parse` from one sentence, because that is how people
    actually ask. The parsing is deliberately shallow - no model call, no
    cost - and anything it gets wrong the user can correct in the search box.
    """

    text: str = ""
    """The words to search on, noise removed."""

    original: str = ""
    """What the user typed, kept for the prompt if the search finds nothing."""

    height: Length | None = None
    """A stated size, when one was given."""

    colours: tuple[str, ...] = ()
    wants_editing: bool = False
    """Whether the request implies changing the model, which affects ranking."""

    @classmethod
    def parse(cls, request: str) -> SearchQuery:
        """Read a plain-English request.

        Deliberately cheap and deliberately shallow. Getting "six inches" out of
        a sentence is worth doing with a regular expression; understanding the
        sentence is not, and would cost a model call on every keystroke.
        """
        lowered = request.lower()
        height = cls._height_in(lowered)
        colours = tuple(c for c in sorted(_COLOURS) if re.search(rf"\b{c}\b", lowered))

        words = [w for w in re.findall(r"[a-z0-9-]+", lowered) if w not in _NOISE]
        # a size or a colour is a filter, not a subject: "black dragon" searches
        # for a dragon, and a search for "black" returns filament spools
        words = [w for w in words if w not in colours]
        words = [w for w in words if w not in _UNITS and not _is_measurement(w)]

        return cls(
            text=" ".join(dict.fromkeys(words)),
            original=request.strip(),
            height=height,
            colours=colours,
            wants_editing=any(word in lowered for word in _EDITING_WORDS),
        )

    @staticmethod
    def _height_in(lowered: str) -> Length | None:
        """The first stated size in the sentence, if any."""
        match = _SIZE.search(lowered)
        if match is None:
            return None
        unit = _UNITS.get(match.group(2).lower())
        if unit is None:
            return None
        parsed = Length.parse(f"{match.group(1)}{unit}")
        return parsed if parsed.millimetres > 0 else None

    @property
    def is_empty(self) -> bool:
        """Whether there is anything to search on."""
        return not self.text.strip()


@dataclass(frozen=True, slots=True)
class Candidate:
    """One model a repository offered, normalised across sources.

    Sources disagree about almost everything - what a download count means,
    whether a licence is a string or a code, whether dimensions are known at
    all. Normalising here means the gallery and the ranking deal with one
    shape, and a new source is a translation rather than a special case.
    """

    source: str
    """Which repository this came from, shown on the card."""

    source_id: str
    title: str
    url: str
    thumbnail_url: str = ""
    creator: str = ""
    licence: Licence = field(default_factory=Licence)
    downloads: int = 0
    likes: int = 0
    description: str = ""
    file_count: int = 0
    has_print_profile: bool = False
    """Whether the source ships slicer settings with it - a strong quality signal."""

    tags: tuple[str, ...] = ()

    @property
    def identity(self) -> str:
        """A key unique across sources, for de-duplicating a merged gallery."""
        return f"{self.source}:{self.source_id}"

    @property
    def attribution(self) -> str:
        """The credit line to carry into the document and the exported 3MF.

        Provenance should survive a remix without the user having to remember
        it, which is the whole reason this is computed rather than typed.
        """
        who = self.creator or "an unnamed creator"
        return f"{self.title} by {who} ({self.licence.badge}) - {self.url}"


@dataclass(frozen=True, slots=True)
class Scored:
    """A candidate with its score and the reason for it.

    The reasons are shown on the card. A gallery that ranks without explaining
    itself trains the user to distrust it, and there is no cost to saying why.
    """

    candidate: Candidate
    score: float
    reasons: tuple[str, ...] = ()


def rank(candidates: list[Candidate], query: SearchQuery, limit: int = 40) -> list[Scored]:
    """Order candidates best-first, and say why.

    Four axes, from ``docs/07-discovery-and-remix.md``: relevance, remixability,
    printability signal and popularity. Popularity is damped with a logarithm so
    one viral model cannot bury forty better ones - raw download counts span
    five orders of magnitude and would otherwise be the only axis that mattered.
    """
    seen: set[str] = set()
    unique: list[Candidate] = []
    for candidate in candidates:
        if candidate.identity in seen:
            continue
        seen.add(candidate.identity)
        unique.append(candidate)

    scored = [_score(c, query) for c in unique]
    scored.sort(key=lambda s: (-s.score, s.candidate.title.lower()))
    return scored[:limit]


def merge(left: list[Scored], right: list[Scored], limit: int = 40) -> list[Scored]:
    """Combine two ranked lists, keeping the better entry for a duplicate."""
    best: dict[str, Scored] = {}
    for entry in (*left, *right):
        key = entry.candidate.identity
        if key not in best or entry.score > best[key].score:
            best[key] = entry
    ordered = sorted(best.values(), key=lambda s: (-s.score, s.candidate.title.lower()))
    return ordered[:limit]


def _score(candidate: Candidate, query: SearchQuery) -> Scored:
    """One candidate's score, and the reasons worth showing on its card."""
    total = 0.0
    reasons: list[str] = []

    relevance = _relevance(candidate, query)
    total += relevance * 4.0
    if relevance >= 0.75:
        reasons.append("matches what you asked for")

    if query.wants_editing and candidate.licence.is_remixable:
        total += 1.0
        reasons.append("licence allows changes")
    elif query.wants_editing:
        total -= 1.0
        reasons.append("licence forbids changes")

    if candidate.has_print_profile:
        total += 0.8
        reasons.append("comes with print settings")

    total += _popularity(candidate)
    if candidate.downloads >= 10_000:
        reasons.append("widely printed")

    return Scored(candidate, round(total, 4), tuple(reasons))


def _relevance(candidate: Candidate, query: SearchQuery) -> float:
    """Fraction of the query's words that appear in the candidate's text.

    A match in the title counts double. "Dragon" in a title is a dragon; the
    same word buried in a paragraph of description usually is not.
    """
    wanted = [w for w in query.text.split() if len(w) > 2]
    if not wanted:
        return 0.0
    haystack = " ".join((candidate.title, candidate.description, " ".join(candidate.tags))).lower()
    hits = sum(1 for word in wanted if word in haystack)
    in_title = sum(1 for word in wanted if word in candidate.title.lower())
    return min(1.0, (hits + in_title) / (2 * len(wanted)))


def _popularity(candidate: Candidate) -> float:
    """Downloads and likes, damped hard.

    Ten times the downloads is worth about one extra point, not ten. Without
    that, popularity swamps every other axis and the gallery becomes a
    bestseller list.
    """
    signal = candidate.downloads + candidate.likes * 5
    if signal <= 0:
        return 0.0
    return min(2.0, log10(1 + signal) / 2.5)
