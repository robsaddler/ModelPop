"""Teaching a model the CAD vocabulary, and reading back what it asks for.

The completion of ADR-0001. Until now a language model changed a part by
rewriting a script; here it asks for the same typed commands the toolbar emits,
so an AI edit lands on the bus, joins the feature tree and is undoable like any
other change.

The vocabulary is generated from the commands themselves rather than typed out
here, so a command added to the domain is a command the model can ask for on the
next run. A prompt that drifts from the code is the standard failure of this
pattern and it is silent: the model asks for something that no longer exists and
every request fails for a reason nobody can see.

**Everything the model sends is untrusted.** It is parsed as JSON, matched
against the known names, and rebuilt through ``command_from``, which validates
and clamps. Anything unrecognised is dropped and reported. At no point is a
string from a model executed.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from modelpop.domain.cad_commands import (
    EdgeSelector,
    Face,
    Plane,
    command_from,
    known_commands,
)
from modelpop.domain.commands import Command, Feature
from modelpop.domain.result import Result, failure, success

__all__ = ["SYSTEM_PROMPT", "build_edit_request", "read_commands"]

# How many changes one instruction may ask for. A request like "make it look
# nicer" can otherwise come back as thirty operations the user never asked for
# and has to undo one at a time.
MAX_COMMANDS = 12

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _vocabulary() -> str:
    """The command list, written out for the prompt.

    Built from the code so it cannot drift. Each line is a name and the
    parameters it takes, which is all a model needs to emit valid JSON.
    """
    placed = "x, y, z (mm from the centre, optional), cut (true to remove it)"
    shapes = {
        "create-box": f"width, depth, height (mm), {placed}",
        "create-cylinder": f"radius, height (mm), {placed}",
        "create-sphere": f"radius (mm), {placed}",
        "fillet": f"radius (mm), edges ({_choices(EdgeSelector)})",
        "chamfer": f"distance (mm), edges ({_choices(EdgeSelector)})",
        "hollow": f"wall_thickness (mm), opening ({_choices(Face)} or null)",
        "move": "dx, dy, dz (mm)",
        "rotate": "degrees, axis (X, Y or Z)",
        "scale-to": "height_mm (the finished height of the whole part)",
        "text-on-surface": (
            f"text, face ({_choices(Face)}), size (mm), depth (mm), raised (true or false)"
        ),
        "extrude": (
            f"points (a list of [x, y] corners in mm), height (mm), "
            f"plane ({_choices(Plane)}), cut (true to remove it)"
        ),
    }
    missing = set(known_commands()) - set(shapes)
    lines = [f"- {name}: {shapes[name]}" for name in known_commands() if name in shapes]
    if missing:
        # A command added to the domain and not described here would be
        # invisible to the model. Say so rather than quietly omitting it.
        lines.append(f"- (undocumented, do not use: {', '.join(sorted(missing))})")
    return "\n".join(lines)


def _choices(enum: type[Enum]) -> str:
    """The values of an enum, as the model should spell them."""
    return " | ".join(str(member.value) for member in enum)


SYSTEM_PROMPT = f"""\
You change a 3D model for printing by asking for operations from a fixed list.
You do not write code. You reply with JSON and nothing else.

The operations available, and the parameters each takes:

{_vocabulary()}

Reply with a JSON array of objects, each shaped:

  {{"name": "<operation>", "parameters": {{...}}}}

Rules:

- Use only the operations listed. Anything else is discarded.
- Every measurement is in millimetres. Convert inches yourself: 6 inches is
  152.4 mm.
- "scale-to" sets the height of the whole finished part, so it belongs last.
- "hollow" needs a wall thickness of at least 0.4 mm, and an opening so the
  inside can drain. A wall exactly equal to an existing fillet radius fails, so
  offset it slightly.
- A hole is a cylinder with "cut": true. Make it longer than the part it passes
  through, so it goes all the way. Position it with x, y and z, which are
  measured from the centre of the part.
- "extrude" is how to make any shape the three primitives cannot describe - a
  bracket, a gasket, a nameplate, a hexagon, anything with a constant
  cross-section. Give at least three corners, in order round the outline, and
  do not repeat the first corner at the end: it closes itself. The corners
  describe the *shape*, not where it sits: the finished profile is centred on
  the origin like every other shape, so use "move" to place it.
- The first operation must add a shape. There is nothing to cut from yet.
- Ask for the fewest operations that do what was requested. At most \
{MAX_COMMANDS}.
- If the request cannot be done with these operations, reply with an empty
  array and nothing else. Do not approximate with something the user did not
  ask for.

Reply with the JSON array only. No prose, no explanation, no code fence.
"""


def build_edit_request(instruction: str, tree: str, measurements: str = "") -> str:
    """The user message: what the model is, and what to change about it.

    The current tree is included because an edit is relative to it - "make it
    taller" means nothing without knowing what "it" currently is - and because
    it stops the model re-creating a shape that already exists.
    """
    parts = [f"The model is currently built from these steps:\n\n{tree or '(nothing yet)'}"]
    if measurements:
        parts.append(f"It currently measures {measurements}.")
    parts.append(f"Change it so that: {instruction.strip()}")
    return "\n\n".join(parts)


def read_commands(reply: str) -> Result[tuple[list[Command], tuple[str, ...]]]:
    """Turn a model's reply into typed commands.

    Returns the commands it asked for, and the names of anything discarded, so
    the user can be told what was ignored rather than quietly getting less than
    they asked for.

    Every failure here is a ``Failure`` rather than an exception: a model
    replying with prose, or with JSON of the wrong shape, is an ordinary
    outcome of asking a model for something.
    """
    payload = _json_in(reply)
    if payload is None:
        return failure(
            "The model did not reply with usable JSON",
            (reply.strip()[:200] or "it replied with nothing"),
        )

    if not isinstance(payload, list):
        return failure("The model replied with the wrong shape", "expected a list of operations.")

    if not payload:
        return failure(
            "That cannot be done with the tools available",
            "The model was asked to use only the built-in operations and found none that fit.",
        )

    commands: list[Command] = []
    discarded: list[str] = []

    for entry in payload[:MAX_COMMANDS]:
        command = _command_in(entry)
        if command is None:
            discarded.append(_name_in(entry))
            continue
        commands.append(command)

    if not commands:
        return failure(
            "None of what the model asked for is possible",
            f"It asked for: {', '.join(discarded)}." if discarded else "It asked for nothing.",
        )

    return success((commands, tuple(discarded)))


def _json_in(reply: str) -> Any:
    """The JSON in a reply, fenced or bare.

    Models wrap JSON in a fence most of the time and occasionally do not, and
    occasionally add a sentence before it. Taking the first bracketed array is
    more reliable than insisting on a shape.
    """
    fenced = _FENCE.search(reply)
    text = fenced.group(1) if fenced else reply

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _command_in(entry: Any) -> Command | None:
    """One entry as a typed command, or ``None`` if it is not one.

    Goes through ``command_from``, which is the same path a saved document
    takes, so a model cannot reach any construction the file format cannot.
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    parameters = entry.get("parameters", {})
    if not isinstance(name, str) or not isinstance(parameters, dict):
        return None
    return command_from(Feature(name=name, parameters=parameters))


def _name_in(entry: Any) -> str:
    """What a rejected entry called itself, for the report."""
    if isinstance(entry, dict):
        name = entry.get("name")
        if isinstance(name, str) and name:
            return name
    return "an unnamed operation"
