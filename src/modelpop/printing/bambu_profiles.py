"""Which Bambu printers exist, and how big their beds are.

Read from **Bambu Studio's own installed profiles**, not from documentation and
not from memory. The profiles are the same files the slicer works from, so a
build volume taken from them is the one the slice will be checked against, and
a model Bambu ships next year arrives without a code change.

Reading them rather than typing them out is not fussiness. The P1S, P1P and
every X1 are **250 mm** tall, not 256 - a detail that is easy to get wrong from
memory and that would silently pass a model 254 mm high as printable.

Each model also carries a ``model_id`` - ``N7`` for the P2S, ``BL-P001`` for
the X1 Carbon - which is the code the printer announces about itself. That is
what makes recognising a printer possible without asking the user which one
they own.

Profiles inherit, and the bed is defined on a shared parent (``256 x 256`` for
most of the range, ``345 x 320`` for the H2 family), so the chain has to be
resolved before anything useful can be read off one.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from modelpop.domain.printer import Nozzle, PrinterProfile
from modelpop.domain.units import Length

__all__ = ["KNOWN_PRINTERS", "KnownPrinters", "profiles_beside"]

# Where Bambu Studio keeps them, relative to the executable.
_PROFILES = Path("resources") / "profiles"
_VENDOR = "BBL"

# Read from the installed profiles on 2026-09-25 and kept as the answer for a
# machine with no Bambu Studio on it. Every number here came from the files
# rather than from anybody's recollection; see the module docstring.
KNOWN_PRINTERS: tuple[tuple[str, str, float, float, float], ...] = (
    ("BL-P001", "Bambu Lab X1 Carbon", 256.0, 256.0, 250.0),
    ("BL-P002", "Bambu Lab X1", 256.0, 256.0, 250.0),
    ("C13", "Bambu Lab X1E", 256.0, 256.0, 250.0),
    ("C11", "Bambu Lab P1P", 256.0, 256.0, 250.0),
    ("C12", "Bambu Lab P1S", 256.0, 256.0, 250.0),
    ("N1", "Bambu Lab A1 mini", 180.0, 180.0, 180.0),
    ("N2S", "Bambu Lab A1", 256.0, 256.0, 256.0),
    ("N6", "Bambu Lab X2D", 256.0, 256.0, 261.0),
    ("N7", "Bambu Lab P2S", 256.0, 256.0, 256.0),
    ("N9", "Bambu Lab A2L", 330.0, 320.0, 325.0),
    ("O1C2", "Bambu Lab H2C", 330.0, 320.0, 325.0),
    ("O1D", "Bambu Lab H2D", 350.0, 320.0, 325.0),
    ("O1E", "Bambu Lab H2D Pro", 350.0, 320.0, 325.0),
    ("O1S", "Bambu Lab H2S", 340.0, 320.0, 340.0),
)


def profiles_beside(executable: Path | None) -> Path | None:
    """Where the profiles live, given where Bambu Studio is."""
    if executable is None:
        return None
    here = executable.parent / _PROFILES
    return here if here.is_dir() else None


class KnownPrinters:
    """Every Bambu printer this machine knows about, and its build volume."""

    def __init__(self, profiles: Path | None = None) -> None:
        """Wire the catalogue.

        Args:
            profiles: Bambu Studio's ``resources/profiles`` directory. Without
                it the built-in table is used, which is the same answer taken
                from the same files on a machine that had them.
        """
        self._profiles = profiles

    def all(self) -> tuple[PrinterProfile, ...]:
        """Every model, in the order Bambu lists them."""
        return tuple(profile for _, profile in self._catalogue())

    def named(self, model: str) -> PrinterProfile | None:
        """One model by its full name, or ``None`` if it is not a Bambu."""
        for _, profile in self._catalogue():
            if profile.model == model:
                return profile
        return None

    def recognise(self, model_id: str) -> PrinterProfile | None:
        """The printer that announces itself by this code.

        ``N7`` is a P2S and ``BL-P001`` an X1 Carbon. Matched case-insensitively
        because the code arrives over the network and its case is not a promise.
        """
        wanted = model_id.strip().upper()
        if not wanted:
            return None
        for code, profile in self._catalogue():
            if code.upper() == wanted:
                return profile
        return None

    def _catalogue(self) -> tuple[tuple[str, PrinterProfile], ...]:
        found = _read_profiles(self._profiles) if self._profiles else ()
        return found or _built_in()


@lru_cache(maxsize=4)
def _read_profiles(root: Path) -> tuple[tuple[str, PrinterProfile], ...]:
    """Every model in the installed profiles, with its bed resolved.

    Cached: the files do not change while the application is running, and
    walking a hundred of them on every question would be silly.

    Any failure here - a missing directory, a profile Bambu has restructured,
    a file that will not parse - comes back as an empty catalogue rather than
    an exception, and the built-in table takes over. Not being able to list
    printers is not a reason to refuse to start.
    """
    try:
        machines = root / _VENDOR / "machine"
        vendor = json.loads((root / f"{_VENDOR}.json").read_text(encoding="utf-8"))
        files = {f.stem: f for f in machines.glob("*.json")}
        sizes = _beds_in(files)

        found: list[tuple[str, PrinterProfile]] = []
        for entry in vendor.get("machine_model_list", ()):
            model = json.loads((root / _VENDOR / entry["sub_path"]).read_text(encoding="utf-8"))
            name, code = model.get("name", ""), model.get("model_id", "")
            bed = sizes.get(name)
            if name and code and bed:
                found.append((code, _profile_for(name, *bed)))
        return tuple(found)
    except Exception:
        return ()


def _beds_in(files: dict[str, Path]) -> dict[str, tuple[float, float, float]]:
    """The build volume of every machine profile, by the model it belongs to."""
    beds: dict[str, tuple[float, float, float]] = {}
    for stem in files:
        if "template" in stem or "common" in stem:
            continue
        settings = _inherited(stem, files)
        area, height = settings.get("printable_area"), settings.get("printable_height")
        model = settings.get("printer_model")
        if not area or not height or not model:
            continue
        corners = [(float(p.split("x")[0]), float(p.split("x")[1])) for p in area]
        wide = max(x for x, _ in corners) - min(x for x, _ in corners)
        deep = max(y for _, y in corners) - min(y for _, y in corners)
        beds[str(model)] = (wide, deep, float(height))
    return beds


def _inherited(stem: str, files: dict[str, Path], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """One profile with its parents folded in, nearest wins.

    The bed lives on a shared parent for most of the range, so a profile read
    on its own says nothing about how big the printer is.
    """
    if stem in seen or stem not in files:
        return {}
    settings: dict[str, Any] = json.loads(files[stem].read_text(encoding="utf-8"))
    parent = _inherited(str(settings.get("inherits", "")), files, (*seen, stem))
    return {**parent, **settings}


def _profile_for(name: str, wide: float, deep: float, high: float) -> PrinterProfile:
    return PrinterProfile(
        model=name,
        nozzle=Nozzle.STANDARD,
        build_width=Length.mm(wide),
        build_depth=Length.mm(deep),
        build_height=Length.mm(high),
    )


def _built_in() -> tuple[tuple[str, PrinterProfile], ...]:
    return tuple(
        (code, _profile_for(name, wide, deep, high))
        for code, name, wide, deep, high in KNOWN_PRINTERS
    )
