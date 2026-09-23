"""The ModelPop project file: a feature tree as JSON.

Plain JSON, one object per feature, in the order they were applied. No geometry
and no binary blob, because the tree *is* the model and everything else is
derived from it. A user can read the file, diff two of them, and see what
changed - which is worth more here than any saving in size.

Three rules the format follows, all of which exist because of what happens when
a file outlives the build that wrote it:

**Unknown features are kept, not dropped.** A tree holding an operation this
build does not recognise still loads, still saves, and still carries that
operation. Dropping it would silently destroy the user's work the first time
they opened an old project in a new build and pressed save.

**Every field is optional on read.** A missing origin becomes "user", a missing
timestamp becomes nothing. A file that is *slightly* wrong should open.

**Writing is atomic.** Written beside the target and moved into place, so a
crash halfway through leaves the previous version rather than a half-file that
looks like a project and is not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modelpop.application.project_ports import SavedProject
from modelpop.domain.cad_commands import command_from
from modelpop.domain.commands import Document, Feature, Origin
from modelpop.domain.result import Result, failure, success

__all__ = ["EXTENSION", "FORMAT_VERSION", "JsonProjectStore"]

EXTENSION = ".modelpop"
FORMAT_VERSION = 1

# Refuse rather than read. A tree of a hundred thousand features is a corrupt
# file or a generated one, and either way loading it is not what the user wants.
MAX_FEATURES = 5_000

# A guard against pointing this at a video file by mistake. A real project of a
# few hundred features is tens of kilobytes.
MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class JsonProjectStore:
    """Satisfies the ``ProjectStore`` port with one JSON file."""

    written_by: str = "ModelPop"

    def save(self, document: Document, path: Path) -> Result[Path]:
        """Write a feature tree to disk.

        Through a temporary file and a move, so an interrupted save leaves the
        previous version intact rather than something that opens as an empty
        model.
        """
        target = path if path.suffix else path.with_suffix(EXTENSION)
        payload = {
            "format": FORMAT_VERSION,
            "written_by": self.written_by,
            "written_at": datetime.now(UTC).isoformat(),
            "name": document.name,
            "features": [_write_feature(f) for f in document.features],
        }

        scratch = target.with_suffix(target.suffix + ".tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            scratch.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            scratch.replace(target)
        except (OSError, TypeError, ValueError) as error:
            scratch.unlink(missing_ok=True)
            return failure("The project could not be saved", str(error))

        return success(target)

    def load(self, path: Path) -> Result[SavedProject]:
        """Read a feature tree back."""
        try:
            if path.stat().st_size > MAX_BYTES:
                return failure(
                    "That file is too large to be a project",
                    f"{path.stat().st_size // 1024} KB; a project is usually a few dozen.",
                )
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            return failure("The project could not be opened", str(error))
        except (json.JSONDecodeError, ValueError):
            return failure(
                "That file is not a ModelPop project",
                "It is not readable as JSON.",
            )

        if not isinstance(raw, dict):
            return failure("That file is not a ModelPop project")

        version = raw.get("format")
        if not isinstance(version, int) or version < 1:
            return failure(
                "That file is not a ModelPop project",
                "It does not say which format it is.",
            )

        entries = raw.get("features")
        if not isinstance(entries, list):
            return failure("That project has no steps in it")
        if len(entries) > MAX_FEATURES:
            return failure(
                "That project has too many steps to open",
                f"{len(entries)} steps; the limit is {MAX_FEATURES}.",
            )

        features: list[Feature] = []
        unknown: list[str] = []
        for entry in entries:
            feature = _read_feature(entry)
            if feature is None:
                continue
            features.append(feature)
            if command_from(feature) is None:
                unknown.append(feature.name)

        if not features:
            return failure(
                "That project has no steps this version can read",
                f"Written by {raw.get('written_by') or 'an unknown version'}.",
            )

        name = raw.get("name")
        document = Document(
            features=tuple(features),
            name=name if isinstance(name, str) and name else "Untitled",
        )
        return success(
            SavedProject(
                document=document,
                unknown=tuple(dict.fromkeys(unknown)),
                written_by=str(raw.get("written_by") or ""),
            )
        )


def _write_feature(feature: Feature) -> dict[str, Any]:
    """One feature as JSON."""
    return {
        "name": feature.name,
        "parameters": feature.parameters,
        "origin": feature.origin.value,
        "created_at": feature.created_at.isoformat(),
        "suppressed": feature.suppressed,
    }


def _read_feature(entry: Any) -> Feature | None:
    """One feature from JSON, or ``None`` when it is not one.

    Everything but the name is optional. A file that is slightly wrong should
    open; a file with a nameless step has nothing to open.
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        return None

    parameters = entry.get("parameters")
    return Feature(
        name=name,
        parameters=parameters if isinstance(parameters, dict) else {},
        origin=_read_origin(entry.get("origin")),
        created_at=_read_time(entry.get("created_at")),
        suppressed=bool(entry.get("suppressed", False)),
    )


def _read_origin(value: Any) -> Origin:
    """Who asked for a change, defaulting to the user."""
    if isinstance(value, str):
        try:
            return Origin(value)
        except ValueError:
            pass
    return Origin.USER


def _read_time(value: Any) -> datetime:
    """When a change was made, defaulting to now.

    A wrong timestamp is cosmetic - it is excluded from the content hash - so
    guessing beats refusing to open the file over it.
    """
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
