"""Commands, the document history, and the bus that applies them.

This is ADR-0001 made concrete, and it is the load-bearing decision in ModelPop.
Three different actors change a model - the user through the UI, an LLM
responding to a prompt, and replay or test code - and all three emit the *same*
commands through the *same* bus.

What that buys:

* an AI edit is undoable, because it is an ordinary command;
* parametric rebuild is a replay of the history;
* the LLM is confined to a vocabulary it cannot escape, which is a security
  boundary as much as a quality one;
* a test is "apply these commands, assert these invariants" - no UI, no mocks.

Nothing may mutate a model outside this bus.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Self

__all__ = [
    "FIRST_BODY",
    "Command",
    "CommandBus",
    "Document",
    "DocumentHistory",
    "Feature",
    "Origin",
]


FIRST_BODY = "body-1"
"""The object a document starts with, so a single-object scene names nothing."""


class Origin(Enum):
    """Who asked for a change.

    Recorded on every feature so the UI can show what the AI did, and so an
    "undo everything the assistant just did" action is possible.
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class Feature:
    """One applied command, recorded in the document history.

    A feature is the *intent* ("fillet these edges at 2 mm"), not the resulting
    geometry. Geometry is derived by rebuilding, which is what makes the model
    parametric.
    """

    name: str
    parameters: dict[str, Any]
    origin: Origin = Origin.USER
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    suppressed: bool = False

    body: str = FIRST_BODY
    """Which object in the scene this feature belongs to.

    A scene holds several independent objects - a maker adds a cube, then a
    sphere, and expects to pick either one up. Every feature names the object
    it shapes, and each object is built from its own features alone. Without
    this the whole document compiled to a single boolean union, so two shapes
    were one part with two lumps and moving either moved both.
    """

    def canonical(self) -> str:
        """A stable string form, used for hashing and diffing.

        Excludes the timestamp, so two documents built the same way by different
        routes hash identically.
        """
        payload = {
            "name": self.name,
            "parameters": self.parameters,
            "suppressed": self.suppressed,
            "body": self.body,
        }
        return json.dumps(payload, sort_keys=True, default=str)


@dataclass(frozen=True, slots=True)
class Document:
    """An ordered list of features, plus a content hash.

    The document *is* the feature history. It holds no geometry; geometry is
    produced by rebuilding the features through a kernel.
    """

    features: tuple[Feature, ...] = ()
    name: str = "Untitled"
    labels: dict[str, str] = field(default_factory=dict)
    """What each object is called, by body id. Absent means "use the default"."""

    @property
    def body_ids(self) -> tuple[str, ...]:
        """The objects in this document, in the order they were started."""
        seen: list[str] = []
        for feature in self.active_features:
            if feature.body not in seen:
                seen.append(feature.body)
        return tuple(seen)

    def features_for(self, body: str) -> tuple[Feature, ...]:
        """The active features that shape one object."""
        return tuple(f for f in self.active_features if f.body == body)

    def label_for(self, body: str) -> str:
        """What to call an object in a list or a menu."""
        if body in self.labels:
            return self.labels[body]
        features = self.features_for(body)
        if not features:
            return body
        # Named after whatever started it, which is what a maker would call
        # it: the sphere, the box. Better than "Body 2".
        first = features[0].name.replace("create-", "").replace("-", " ")
        return first.capitalize()

    def named(self, body: str, label: str) -> Document:
        """A copy with one object renamed."""
        return replace(self, labels={**self.labels, body: label})

    def without_body(self, body: str) -> Document:
        """A copy with one object and everything shaping it removed."""
        kept = tuple(f for f in self.features if f.body != body)
        labels = {key: value for key, value in self.labels.items() if key != body}
        return replace(self, features=kept, labels=labels)

    @property
    def content_hash(self) -> str:
        """A stable hash of the feature list, ignoring timestamps."""
        digest = hashlib.sha256()
        for feature in self.features:
            digest.update(feature.canonical().encode())
        return digest.hexdigest()[:32]

    @property
    def active_features(self) -> tuple[Feature, ...]:
        """The features that are not suppressed."""
        return tuple(f for f in self.features if not f.suppressed)

    def with_feature(self, feature: Feature) -> Document:
        """A copy with ``feature`` appended."""
        return replace(self, features=(*self.features, feature))

    def without_last(self) -> Document:
        """A copy with the final feature removed."""
        if not self.features:
            return self
        return replace(self, features=self.features[:-1])

    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self) -> Iterator[Feature]:
        return iter(self.features)


class Command(ABC):
    """Something that changes a document.

    Subclasses describe an *intent* and know how to express it as a
    :class:`Feature`. They must be pure: applying a command twice to the same
    document must give the same result both times.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """A stable identifier, such as ``"fillet"``. Used in the history."""

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """The command's arguments, as JSON-serialisable values.

        A property, to match ``name``. It was a method originally, and the
        asymmetry caught the first subclass written against it: calling
        ``self.parameters()`` on one that made it a property fails with "dict
        object is not callable", which names neither the class nor the mistake.
        """

    def describe(self) -> str:
        """A short human-readable label for the undo stack and the history panel."""
        return self.name

    def to_feature(self, origin: Origin = Origin.USER, body: str = FIRST_BODY) -> Feature:
        """Record this command as a feature of one object."""
        return Feature(name=self.name, parameters=self.parameters, origin=origin, body=body)

    def apply(
        self,
        document: Document,
        origin: Origin = Origin.USER,
        body: str = FIRST_BODY,
    ) -> Document:
        """Return a new document with this command appended to one object.

        Override only if a command does something other than append - for
        example, editing an existing feature's parameters in place.
        """
        return document.with_feature(self.to_feature(origin, body))


@dataclass(frozen=True, slots=True)
class _Entry:
    """One step in the history: the document *after* a command was applied."""

    document: Document
    label: str


class DocumentHistory:
    """An undo/redo stack over document states.

    Holds whole documents rather than inverse operations. Documents are small
    (they are feature lists, not geometry), so this is cheap and, crucially,
    cannot drift out of sync the way paired do/undo implementations do.
    """

    def __init__(self, initial: Document | None = None, limit: int = 200) -> None:
        """Create a history.

        Args:
            initial: the starting document; an empty one by default.
            limit: how many past states to retain before discarding the oldest.
        """
        if limit < 1:
            raise ValueError("history limit must be at least 1")
        self._entries: list[_Entry] = [_Entry(initial or Document(), "new")]
        self._index = 0
        self._limit = limit

    @property
    def current(self) -> Document:
        """The document as it stands."""
        return self._entries[self._index].document

    @property
    def can_undo(self) -> bool:
        """Whether there is a state to go back to."""
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        """Whether there is a state to go forward to."""
        return self._index < len(self._entries) - 1

    @property
    def undo_label(self) -> str | None:
        """What undo would reverse, for the menu item."""
        return self._entries[self._index].label if self.can_undo else None

    @property
    def redo_label(self) -> str | None:
        """What redo would reapply, for the menu item."""
        return self._entries[self._index + 1].label if self.can_redo else None

    @property
    def undone_labels(self) -> tuple[str, ...]:
        """What has been undone, oldest first - what redo would put back.

        The view shows these greyed under the tree. Without them an undone
        step simply vanishes, and the only evidence that redo would bring
        something back is whether a button happens to be enabled.
        """
        return tuple(entry.label for entry in self._entries[self._index + 1 :])

    def push(self, document: Document, label: str) -> None:
        """Record a new state, discarding any redo branch."""
        del self._entries[self._index + 1 :]
        self._entries.append(_Entry(document, label))
        if len(self._entries) > self._limit:
            self._entries.pop(0)
        else:
            self._index += 1

    def undo(self) -> Document:
        """Step back one state, or stay put if there is none."""
        if self.can_undo:
            self._index -= 1
        return self.current

    def redo(self) -> Document:
        """Step forward one state, or stay put if there is none."""
        if self.can_redo:
            self._index += 1
        return self.current

    def __len__(self) -> int:
        return len(self._entries)


class CommandBus:
    """The single path through which a document changes.

    Every actor - user, assistant, replay - goes through :meth:`execute`.
    Listeners are notified after each change so the UI and the viewport can
    react without knowing who made the edit.
    """

    def __init__(self, history: DocumentHistory | None = None) -> None:
        """Create a bus over a history (a fresh one by default)."""
        self._history = history or DocumentHistory()
        self._listeners: list[Any] = []

    @property
    def document(self) -> Document:
        """The current document."""
        return self._history.current

    @property
    def history(self) -> DocumentHistory:
        """The underlying undo/redo stack."""
        return self._history

    def subscribe(self, listener: Any) -> None:
        """Register a callable invoked with the new document after each change."""
        self._listeners.append(listener)

    def execute(
        self,
        command: Command,
        origin: Origin = Origin.USER,
        body: str = FIRST_BODY,
    ) -> Document:
        """Apply a command to one object and record it in the history."""
        updated = command.apply(self.document, origin, body)
        self._history.push(updated, command.describe())
        self._notify(updated)
        return updated

    def execute_all(
        self,
        commands: Sequence[Command],
        origin: Origin = Origin.ASSISTANT,
        body: str = FIRST_BODY,
    ) -> Document:
        """Apply several commands as one undoable step.

        This is how an assistant edit lands: "make the walls 3 mm and round the
        top edges" is one entry in the undo stack, not five.
        """
        if not commands:
            return self.document
        document = self.document
        for command in commands:
            document = command.apply(document, origin, body)
        label = (
            commands[0].describe()
            if len(commands) == 1
            else f"{len(commands)} changes ({commands[0].describe()}, ...)"
        )
        self._history.push(document, label)
        self._notify(document)
        return document

    def set_document(self, document: Document, label: str) -> Document:
        """Record a document arrived at some other way than by one command.

        Deleting an object removes several features at once, and renaming one
        touches no feature at all. Neither is a command in the vocabulary, and
        both still have to land in the history so they undo like everything
        else.
        """
        self._history.push(document, label)
        self._notify(document)
        return document

    def undo(self) -> Document:
        """Step back one change."""
        document = self._history.undo()
        self._notify(document)
        return document

    def redo(self) -> Document:
        """Reapply the last undone change."""
        document = self._history.redo()
        self._notify(document)
        return document

    def _notify(self, document: Document) -> None:
        for listener in self._listeners:
            listener(document)


@dataclass(frozen=True, slots=True)
class GenericCommand(Command):
    """A command defined by name and parameters alone.

    Useful for tests, for replaying a serialised history, and as the landing
    point for assistant-generated commands once they have been validated.
    """

    command_name: str
    args: dict[str, Any] = field(default_factory=dict)
    label: str = ""

    @property
    def name(self) -> str:
        """The command identifier."""
        return self.command_name

    @property
    def parameters(self) -> dict[str, Any]:
        """The arguments."""
        return dict(self.args)

    def describe(self) -> str:
        """The label, falling back to the name."""
        return self.label or self.command_name

    @classmethod
    def from_feature(cls, feature: Feature) -> Self:
        """Reconstruct a command from a recorded feature, for replay."""
        return cls(feature.name, dict(feature.parameters))
