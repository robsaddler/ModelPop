"""Where the licensing tick is remembered.

A single small JSON file next to the user's other application data. Not the
registry, not a database, not keyring - this is neither a secret nor a setting
the user would want to edit, and a file they can delete to be asked again is a
feature rather than a leak.

Every failure here is non-fatal by design. A corrupt file, an unreadable
directory or a read-only disk means the user is asked to tick the box again,
which is mildly annoying; refusing to start, or crashing the gallery, would be
far worse for something this trivial.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from modelpop.domain.licensing import Acceptance
from modelpop.domain.result import Result, failure, success

__all__ = ["JsonAcceptanceStore", "app_data_dir"]

_FILE = "licence-acceptance.json"


def app_data_dir(windows: bool | None = None) -> Path:
    """Where ModelPop keeps per-user data.

    ``LOCALAPPDATA`` on Windows, ``XDG_DATA_HOME`` or its documented default
    elsewhere. Resolved at call time rather than at import so a test can point
    it somewhere harmless.

    Args:
        windows: which convention to follow. Detected from the platform when
            not given. It is a parameter because patching ``os.name`` for a
            test also changes which ``Path`` subclass gets built, which fails
            the test for a reason that has nothing to do with the code.
    """
    on_windows = os.name == "nt" if windows is None else windows
    if on_windows:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "ModelPop"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "modelpop"
    return Path.home() / ".local" / "share" / "modelpop"


@dataclass(frozen=True, slots=True)
class JsonAcceptanceStore:
    """Satisfies the ``AcceptanceStore`` port with one JSON file."""

    directory: Path | None = None
    """Where to write. Defaults to the per-user application data directory."""

    @property
    def path(self) -> Path:
        """The file this store reads and writes."""
        return (self.directory or app_data_dir()) / _FILE

    def load(self) -> Acceptance:
        """What the user has accepted, or an empty record.

        Anything unreadable, malformed or from the future is treated as "not
        accepted". Being asked once more is a small cost; wrongly believing a
        clause was accepted is not.
        """
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Acceptance()

        if not isinstance(raw, dict):
            return Acceptance()

        version = raw.get("version")
        stamp = raw.get("accepted_at")
        if not isinstance(version, int) or not isinstance(stamp, str):
            return Acceptance()

        try:
            accepted_at = datetime.fromisoformat(stamp)
        except ValueError:
            return Acceptance()

        if accepted_at.tzinfo is None:
            accepted_at = accepted_at.replace(tzinfo=UTC)
        return Acceptance(version=version, accepted_at=accepted_at)

    def save(self, acceptance: Acceptance) -> Result[None]:
        """Record an acceptance.

        Written to a neighbouring temporary file and then moved into place, so
        a crash mid-write leaves the previous record intact rather than a
        half-written file that reads as "never accepted".
        """
        if acceptance.accepted_at is None:
            return failure("Nothing to record", "the acceptance has no timestamp.")

        target = self.path
        payload = json.dumps(
            {
                "version": acceptance.version,
                "accepted_at": acceptance.accepted_at.isoformat(),
            },
            indent=2,
        )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            scratch = target.with_suffix(".tmp")
            scratch.write_text(payload, encoding="utf-8")
            scratch.replace(target)
        except OSError as error:
            return failure(
                "The licensing acceptance could not be saved",
                f"{error}. You will be asked again next time.",
            )
        return success(None)
