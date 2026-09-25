"""What the printer last said about itself, kept between runs.

A printer that is switched off should not cost the application everything it
knows about it. The nozzle fitted a week ago is still the best guess today, and
a far better one than a default - so it is written down, and read back marked
as remembered rather than passed off as current.

One small JSON file beside the other per-user settings. Nothing secret goes in
it: the address and the access code live in the credential store, and what is
here is the model name, the nozzle size and when the printer last answered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from modelpop.domain.printer import Nozzle
from modelpop.domain.which_printer import WhichPrinter
from modelpop.paths import app_data_dir

__all__ = ["JsonPrinterMemory"]

_FILE = "printer.json"


@dataclass
class JsonPrinterMemory:
    """Remembers the printer across runs, in one JSON file."""

    directory: Path | None = None
    """Where to write. Defaults to the per-user application data directory."""

    @property
    def path(self) -> Path:
        """The file this reads and writes."""
        return (self.directory or app_data_dir()) / _FILE

    def load(self, known: WhichPrinter, catalogue: object) -> WhichPrinter:
        """Whatever was remembered, folded into what is known now.

        Args:
            known: the starting point, usually the built-in default.
            catalogue: anything with a ``named`` method that turns a model name
                into a profile. Passed in rather than imported so this stays a
                repository and does not reach across into the printing adapter.

        A file that is missing, unreadable, or written by a version that
        arranged it differently is not an error worth reporting - it means
        nothing is remembered, which is exactly the state before anything was
        ever written.
        """
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return known

        model = saved.get("model", "")
        profile = getattr(catalogue, "named", lambda _: None)(model) if model else None
        if profile is not None:
            known = known.chosen_in_settings(profile)

        nozzle = _nozzle_of(saved.get("nozzle_mm"))
        heard = _when(saved.get("heard_at"))
        if nozzle is not None or heard is not None:
            known = known.told_by_the_printer(profile, nozzle, heard)
        return known

    def save(self, known: WhichPrinter) -> None:
        """Write down what is known now.

        Failures are swallowed on purpose. Not being able to remember the
        printer is not a reason to interrupt somebody who is trying to print.
        """
        record = {
            "model": known.profile.model,
            "nozzle_mm": known.profile.nozzle.value,
            "heard_at": known.heard_at.isoformat() if known.heard_at else None,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        except OSError:
            return


def _nozzle_of(millimetres: object) -> Nozzle | None:
    try:
        wanted = float(millimetres)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return next((n for n in Nozzle if abs(n.value - wanted) < 1e-6), None)


def _when(stamp: object) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        read = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return read if read.tzinfo else read.replace(tzinfo=UTC)
